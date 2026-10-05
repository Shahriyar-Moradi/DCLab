"""P6.3-B2: assistant threads over HTTP (ADR 0009 §7.2, §7.3, §7.6).

Scripted fake-LLM sessions through the real API with a browser session: a thread turn
streams typed SSE events (rejected steps and refused calls are distinct types), writes only
propose (the champion move is absent from the assistant surface), the same Idempotency-Key
replays the stored turn, the next turn's transcript is rebuilt from events; one live turn per
thread (409), thread limits narrow a turn and end the thread (429); threads are owner-only and
workspace-scoped (404), human-session-only (403) and CSRF-checked; with AI off a turn is a
deterministic template with no model call. No network.
"""

from __future__ import annotations

import json
import runpy
import threading
import time
from pathlib import Path
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

from app.agents.assistant import service as assistant
from app.agents.contracts import AssistantStep, Citation, LeadTurn, Untrusted
from app.agents.gateway.service import GatewayService
from app.agents.harness import service as svc
from app.agents.harness.service import AgentRunRefused
from app.agents.lead import runtime as lead_runtime
from app.agents.lead.fake_driver import ScriptedLeadDriver, answer, tools
from app.agents.runtime.base import RuntimeOutput
from app.agents.tools.shaping import HOLDOUT_KEY
from app.api import v1_assistant
from app.db.models import AgentProposal, AgentRun, LlmInvocation, UserRole, WorkspaceLlmBudget
from app.domain.agent_records import AGENT_RUN_TERMINAL_STATUSES
from app.domain.idempotency import IdempotencyBinding
from app.main import app
from app.services.auth_service import create_access_token, create_user
from app.services.idempotency_service import KeyScope
from app.services.ml_job_service import process_next_job
from app.services.service_token_service import create_service_token
from conftest import TRUSTED_ORIGIN, browser_login, csrf_headers
from test_decision_record_service import g, setup  # noqa: F401  (fixtures)
from test_lead_agent import lead  # noqa: F401  (fixture)
from test_v1_contract_conventions import _assert_envelope

PASSWORD = "test-password"


def _sse(response) -> list[tuple[str, dict]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


@pytest.fixture
def chat(lead, client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(assistant, "agent_service", lead.service)
    monkeypatch.setattr(assistant, "ai_settings", lambda: lead.settings)
    assert browser_login(client, lead.g.actor.email, PASSWORD).status_code == 200
    client.headers["X-Workspace-Id"] = str(lead.g.ws)

    def thread(browser=client, key=None):
        return browser.post("/v1/assistant/threads", json={"project_id": str(lead.g.project.id), "title": " Churn  "},
                            headers={**csrf_headers(browser), "Idempotency-Key": key or uuid4().hex})

    def say(thread_id, text, steps=(), key=None, browser=client):
        lead.driver = ScriptedLeadDriver(steps)
        return browser.post(f"/v1/assistant/threads/{thread_id}/messages", json={"text": text},
                            headers={**csrf_headers(browser), "Idempotency-Key": key or uuid4().hex})

    lead.client, lead.thread, lead.say = client, thread, say
    yield lead
    for worker in threading.enumerate():  # no turn outlives its test (the teardown truncates)
        if worker.name.startswith("assistant-turn-"):
            worker.join(timeout=60)


def _browser(user, workspace_id):
    browser = TestClient(app)
    browser.__enter__()
    assert browser_login(browser, user.email, PASSWORD).status_code == 200
    browser.headers["X-Workspace-Id"] = str(workspace_id)
    return browser


def _status(db, run_id):
    db.expire_all()
    return db.get(AgentRun, run_id).status


def _counts(db):
    db.expire_all()
    return [db.scalar(select(func.count()).select_from(model)) for model in (AgentRun, AgentProposal, LlmInvocation)]


def test_a_turn_streams_typed_events_only_proposes_and_replays_by_key(chat):
    g, db, project = chat.g, chat.db, str(chat.g.project.id)
    created = chat.thread(key="thread-1")
    assert created.status_code == 201, created.text
    again = chat.thread(key="thread-1")
    assert (again.status_code, again.headers["Idempotent-Replayed"], again.json()["id"]) == (
        201, "true", created.json()["id"])
    tid = created.json()["id"]
    thread = db.get(AgentRun, UUID(tid))
    assert (thread.kind, thread.status, thread.created_by_user_id, thread.title, thread.subject_kind) == (
        "assistant", "waiting_user", g.actor.id, "Churn", "thread")
    assert {k: thread.limits[k] for k in ("steps", "tokens", "wall_s", "cost_micros")} == {
        "steps": 40, "tokens": 300000, "wall_s": 900, "cost_micros": 1_000_000}
    text = "Set up churn from my upload, run it, compare and promote the winner."
    first = chat.say(tid, text, [
        answer("E1 is the best run."),  # uncited: a rejected step, retried once
        tools(("inspect_dataset", {"dataset_id": str(g.source.id)}), ("inspect_project", {"project_id": project})),
        tools(("propose_problem_spec", {"project_id": project, "task_type": "binary", "target_column": "target",
                                        "business_objective": "Predict churn.", "rationale": "binary target"})),
        tools(("run_experiment", {"project_id": project, "dataset_id": str(g.source.id), "intent": "first model"})),
        tools(("record_decision", {"project_id": project, "rationale": "promote it",
                                   "ref_moves": {"champion_model": str(uuid4())}})),
        tools(("compare_experiments", {"experiment_ids": [str(g.exp[0]), str(g.exp[1])]})),
        answer(f"Spec and run await your confirmation. [The branch](dclab://experiment/{g.exp[1]}) reaches "
               "CV roc_auc 0.812.", ("experiment", g.exp[1])),
    ], key="message-1")
    events = _sse(first)
    types = [kind for kind, _ in events]
    assert types[0] == "turn_started" and types[-2:] == ["assistant_message", "turn_done"]
    assert set(types) <= {"turn_started", "tool_call", "tool_result", "step_rejected", "call_refused",
                          "proposal_created", "assistant_message", "budget_exhausted", "turn_done"}
    assert types.index("step_rejected") < types.index("tool_call")
    assert [d["reasons"] for kind, d in events if kind == "step_rejected"] == [["uncited_answer"]]
    assert [(d["target"], d["tool"], d["code"]) for kind, d in events if kind == "call_refused"] == [
        ("tool", "record_decision", "champion_move_human_only")]  # the champion move is never proposed
    assert [(d["tool"], d["status"]) for kind, d in events if kind == "proposal_created"] == [
        ("propose_problem_spec", "proposed"), ("run_experiment", "proposed"), ("record_decision", "rejected_by_validator")]
    results = {d["tool"]: d for kind, d in events if kind == "tool_result"}
    assert set(results) >= {"inspect_dataset", "inspect_project", "compare_experiments"}
    assert results["inspect_project"]["ok"] and results["inspect_project"]["preview"]
    assert not HOLDOUT_KEY.search(first.text) and "0.7001" not in first.text  # shaped, holdout-free
    said, done = events[-2][1], events[-1][1]
    assert said["citations"] == [{"kind": "experiment", "id": str(g.exp[1])}] and said["llm_used"]
    assert (done["status"], done["error_code"], len(done["proposal_ids"])) == ("completed", None, 3)
    assert "champion_model" not in json.dumps(chat.driver.requests)  # the surface never offers it
    turn = db.get(AgentRun, UUID(done["turn_id"]))
    assert (turn.kind, turn.parent_run_id, turn.runtime, turn.purpose, turn.created_by_user_id) == (
        "lead", thread.id, "lead_loop", "assistant.turn", g.actor.id)
    assert turn.budget_released_at is not None and turn.usage["steps"] == 7 <= turn.limits["steps"]
    rows = list(db.scalars(select(AgentProposal).where(AgentProposal.run_id == turn.id)))
    assert not [r for r in rows if r.status == "proposed" and r.tool_name == "record_decision"]
    # The same key replays the stored turn: no second turn, proposal or model call (no step left).
    counts = _counts(db)
    replay = chat.say(tid, text, key="message-1")
    assert replay.headers["Idempotent-Replayed"] == "true" and _sse(replay) == events and _counts(db) == counts
    _assert_envelope(chat.say(tid, "another message", key="message-1"), 409, "idempotency_key_conflict")
    # The next turn's transcript is rebuilt from the record (the client sends only its message).
    second = _sse(chat.say(tid, "Which one should I confirm?", [
        answer(f"Confirm the spec first; [E1](dclab://experiment/{g.exp[1]}) is the branch.", ("experiment", g.exp[1]))]))
    assert second[-1][1]["status"] == "completed"
    sent = json.dumps(chat.driver.requests[0])
    assert "Set up churn from my upload" in sent and "pending_confirmation" in sent and "0.7001" not in sent
    assert all(pid in sent for pid in done["proposal_ids"][:2]) and done["proposal_ids"][2] not in sent
    detail = chat.client.get(f"/v1/assistant/threads/{tid}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert [e["type"] for e in body["events"] if e["type"] in ("user_message", "assistant_message")] == [
        "user_message", "assistant_message"] * 2
    assert body["events"][0]["data"] == {"message": text}
    assert (body["usage"]["turns"], body["limits"]["steps"], body["ai_available"]) == (2, 40, True)
    listed = chat.client.get("/v1/assistant/threads", params={"project_id": project}).json()
    assert [item["id"] for item in listed] == [tid]


def test_threads_are_owner_only_workspace_scoped_session_only_and_csrf_checked(chat, setup):  # noqa: F811
    g, db = chat.g, chat.db
    tid = chat.thread().json()["id"]
    colleague = create_user(db, email=f"colleague-{uuid4().hex[:6]}@test.invalid", password=PASSWORD,
                            role=UserRole.ML_ENGINEER, workspace_id=g.ws)
    viewer = create_user(db, email=f"viewer-{uuid4().hex[:6]}@test.invalid", password=PASSWORD,
                         role=UserRole.VIEWER, workspace_id=g.ws)
    _row, raw = create_service_token(db, creator=g.actor, workspace_id=g.ws, name="agent", scopes=["read"],
                                     expires_in_days=30, current_password=PASSWORD)
    db.commit()
    for browser in (_browser(colleague, g.ws), _browser(setup["beta_admin"], setup["beta"].id)):
        _assert_envelope(browser.get(f"/v1/assistant/threads/{tid}"), 404, "not_found")  # never 403
        _assert_envelope(chat.say(tid, "hi", browser=browser), 404, "not_found")
        assert browser.get("/v1/assistant/threads").json() == []
        browser.__exit__(None, None, None)
    with TestClient(app) as bare:  # service tokens and API bearers: refused before any lookup
        for bearer in (raw, create_access_token(g.actor)):
            for method, path in (("GET", "/v1/assistant/threads"), ("POST", "/v1/assistant/threads"),
                                 ("GET", f"/v1/assistant/threads/{tid}"),
                                 ("POST", f"/v1/assistant/threads/{tid}/messages")):
                response = bare.request(method, path, json={"text": "hi", "project_id": str(g.project.id)},
                                        headers={"Authorization": f"Bearer {bearer}", "X-Workspace-Id": str(g.ws),
                                                 "Idempotency-Key": uuid4().hex})
                _assert_envelope(response, 403, "human_session_required")
    _assert_envelope(chat.client.post(f"/v1/assistant/threads/{tid}/messages", json={"text": "hi"},
                                      headers={"Origin": TRUSTED_ORIGIN, "Idempotency-Key": uuid4().hex}),
                     403, "csrf_failed")
    _assert_envelope(chat.client.post(f"/v1/assistant/threads/{tid}/messages", json={"text": "hi"},
                                      headers=csrf_headers(chat.client)), 400, "idempotency_key_required")
    _assert_envelope(chat.say(tid, "hi\x00there", key="nul"), 422, "validation_failed")  # never a stranded key
    _assert_envelope(chat.say(tid, "hi\x00there", key="nul"), 422, "validation_failed")
    # A viewer chats read-only: a write tool is refused by capability, nothing is proposed.
    reader = _browser(viewer, g.ws)
    own = chat.thread(browser=reader)
    assert own.status_code == 201, own.text
    events = _sse(chat.say(own.json()["id"], "Run it.", [
        tools(("run_experiment", {"project_id": str(g.project.id), "dataset_id": str(g.source.id)})),
        answer("Nothing was proposed; ask a member with ML write.", ("experiment", g.exp[1]))], browser=reader))
    reader.__exit__(None, None, None)
    assert [(d["tool"], d["code"]) for kind, d in events if kind == "call_refused"] == [
        ("run_experiment", "ml_write_required")]
    assert events[-1][1]["status"] == "completed" and events[-1][1]["proposal_ids"] == []


def test_one_live_turn_per_thread_and_thread_limits(chat):
    g, db = chat.g, chat.db
    tid = UUID(chat.thread().json()["id"])
    spec = svc.lead_spec(db, workspace_id=g.ws, project_id=g.project.id, user_id=g.actor.id, parent_run_id=tid,
                         turn=LeadTurn(user_text=Untrusted(untrusted_text="x")))
    live = chat.service().prepare_turn(db, spec)  # a turn already running on the thread
    _assert_envelope(chat.say(tid, "hi"), 409, "turn_in_progress")
    with pytest.raises(AgentRunRefused, match="turn_in_progress"):
        chat.service().prepare_turn(db, spec)
    # Another thread of the same project is not blocked by it (Alembic 0076).
    other = UUID(chat.thread().json()["id"])
    seen = answer(f"See [E1](dclab://experiment/{g.exp[1]}).", ("experiment", g.exp[1]))
    assert _sse(chat.say(other, "hi", [seen]))[-1][1]["status"] == "completed"
    # A turn whose stream is never read still runs to its end (the worker starts before the response).
    chat.driver = ScriptedLeadDriver([seen])
    unread = assistant.post_message(
        db, user=g.actor, thread=db.get(AgentRun, other), text="hi again",
        scope=KeyScope(g.ws, "user", g.actor.id, "POST /v1/assistant/threads/{thread_id}/messages"),
        binding=IdempotencyBinding(key="never-read", digest="0" * 64, source="header"))
    deadline = time.monotonic() + 30
    while _status(db, unread.turn_id) not in AGENT_RUN_TERMINAL_STATUSES and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _status(db, unread.turn_id) == "completed"
    # A turn this process is running is never swept, however old its last activity.
    db.execute(update(AgentRun).where(AgentRun.id == live).values(
        usage={"steps": 38}, last_activity_at=func.now() - timedelta(minutes=30)))
    db.commit()
    assistant._RUNNING.add(live)
    try:
        _assert_envelope(chat.say(tid, "hi"), 409, "turn_in_progress")
    finally:
        assistant._RUNNING.discard(live)
    # A crashed turn (idle for its wall time + the longest gateway call) is freed by the worker's
    # poll, without its owner posting again: ended through the release, its hold returned.
    held = GatewayService().reserve(db, workspace_id=g.ws, project_id=g.project.id, agent_run_id=live,
                                    estimate_micros=1000, run_kind="assistant_turn")
    assert not hasattr(held, "code"), held

    def reserved():
        db.expire_all()
        return db.scalar(select(func.sum(WorkspaceLlmBudget.reserved_micros)).where(
            WorkspaceLlmBudget.workspace_id == g.ws))

    before = reserved()
    assert process_next_job(db) is None  # no job queued: the poll still sweeps
    run = db.get(AgentRun, live)
    # the hold sat on three counter rows (workspace, project, assistant_turn)
    assert (run.status, run.error_code, before - reserved()) == ("failed", "turn_abandoned", 3000)
    assert run.budget_released_at is not None
    # What the thread has left narrows the next turn: 38 of its 40 steps are used, so it gets 2.
    look = tools(("get_experiment", {"experiment_id": str(g.exp[0])}))
    bounded = _sse(chat.say(tid, "Look three times.", [look] * 3))
    assert [d["limit"] for kind, d in bounded if kind == "budget_exhausted"] == ["steps"]
    assert (bounded[-1][1]["status"], bounded[-1][1]["error_code"]) == ("over_budget", "step_limit")
    # The thread is exhausted: 429 with the typed reason and a thread budget_exhausted event, no turn.
    turns = db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.parent_run_id == tid))
    error = _assert_envelope(chat.say(tid, "More?"), 429, "thread_limit_reached")
    assert error["details"] == {"limit": "steps"}
    assert db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.parent_run_id == tid)) == turns
    history = chat.client.get(f"/v1/assistant/threads/{tid}").json()["events"]
    assert history[-1]["type"] == "budget_exhausted" and history[-1]["data"] == {"scope": "thread", "limit": "steps"}


def test_llm_off_turns_are_templates_without_a_model_call(chat):
    g, db = chat.g, chat.db
    chat.settings.ai_enabled = False
    tid = chat.thread().json()["id"]
    detail = chat.client.get(f"/v1/assistant/threads/{tid}").json()
    assert (detail["ai_available"], detail["quick_actions"]) == (False, list(assistant.QUICK_ACTIONS))
    counts, said = _counts(db), {}
    for text in ("Summarize this project", "show latest experiment", "What can you do?", "tell me a joke"):
        events = _sse(chat.say(tid, text, key=f"off-{len(said)}"))
        assert [kind for kind, _ in events] == ["turn_started", "assistant_message", "turn_done"]
        item = said[text] = events[1][1]
        assert (item["templated"], item["llm_used"], item["actions"]) == (True, False, list(assistant.QUICK_ACTIONS))
        cited = [Citation(**c) for c in item["citations"]]
        assert lead_runtime.markdown_reasons(item["message"], cited) == []  # the same rendering contract
    assert said["Summarize this project"]["message"].startswith("Project “")
    assert assistant.templates._name("[Approve](dclab://model_version/x) <b>`!") == "Approve dclab://model_version/x b"
    assert said["show latest experiment"]["citations"][0]["kind"] == "experiment"
    assert all(action in said["What can you do?"]["message"] for action in assistant.QUICK_ACTIONS)
    assert said["tell me a joke"]["citations"] == [] and "off" in said["tell me a joke"]["message"]
    assert _counts(db) == counts and chat.driver.requests == []  # no turn run, no gateway row
    replay = chat.say(tid, "Summarize this project", key="off-0")
    assert replay.headers["Idempotent-Replayed"] == "true" and _sse(replay)[1][1] == said["Summarize this project"]
    history = chat.client.get(f"/v1/assistant/threads/{tid}").json()["events"]
    assert [e["type"] for e in history] == ["user_message", "assistant_message"] * 4
    assert assistant._user_limits(db, db.get(AgentRun, UUID(tid)), None, templated=True) is None  # platform default


def test_reply_rendering_contract_and_validator_gaps(chat):
    g = chat.g
    doc = app.openapi()["paths"]["/v1/assistant/threads/{thread_id}/messages"]["post"]["description"]
    assert "linkify off" in doc and "dclab://" in doc and "plain text" in v1_assistant.__doc__
    runtime = lead_runtime.LeadRuntime(LeadTurn(user_text=Untrusted(untrusted_text="x")))
    runtime.tools = ("get_experiment",)
    done = AssistantStep(kind="done", message="All set, it scores best.")
    assert runtime.validate_output(RuntimeOutput(output=done, message=done.message)) == ["uncited_answer"]
    assert runtime.validate_output(RuntimeOutput(output=AssistantStep(kind="done"))) == []
    cited = AssistantStep(kind="done", message="All set.", citations=(Citation(kind="experiment", id=g.exp[1]),))
    assert runtime.validate_output(RuntimeOutput(output=cited, message=cited.message, citations=cited.citations)) == []
    assert lead_runtime.uncited("AUC rose 93 percentage points", [0.8123]) == [93.0]
    assert not lead_runtime._HOLDOUT_WORDS.search("unseen categories are encoded as zeros; an unseen category too")
    assert all(lead_runtime._HOLDOUT_WORDS.search(text) for text in (
        "unseen data", "unseen-rows", "unseen categoryless rows", "categories unseen"))


def test_turn_lock_indexes_match_the_migration(db_session):
    """Model vs Alembic 0076 drift: the same predicates, and the database has them."""

    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic" / "versions"
                                   / "0076_assistant_turn_lock.py"))
    indexes = {index.name: index for index in AgentRun.__table__.indexes}
    for name, key in (("uq_agent_runs_active_subject", "_ACTIVE"), ("uq_agent_runs_live_turn", "_LIVE_TURN")):
        assert str(indexes[name].dialect_options["postgresql"]["where"]) == migration[key], name
    found = dict(db_session.execute(text(
        "SELECT indexname, pg_get_indexdef(format('%I', indexname)::regclass) FROM pg_indexes "
        "WHERE tablename = 'agent_runs' AND indexname IN ('uq_agent_runs_active_subject', 'uq_agent_runs_live_turn')"
    )).all())
    assert "NULLS NOT DISTINCT" in found["uq_agent_runs_active_subject"]
    assert "(kind)::text <> 'lead'::text) OR (parent_run_id IS NULL)" in found["uq_agent_runs_active_subject"]
    assert "(workspace_id, parent_run_id)" in found["uq_agent_runs_live_turn"]
    assert "(kind)::text = 'lead'::text) AND (parent_run_id IS NOT NULL)" in found["uq_agent_runs_live_turn"]
