"""P5.4-A3: the improve loop's /v1 routes, token clamps, acceptance and MCP/catalog reach (ADR 0007 §7,
§8, §10; tests (28), (29), (31), (32); (30) lives in test_agent_harness.py / test_proposal_review.py).

One real root and one real sealed probe (its verdict forced to ``accepted`` so the accept path runs on
real locked evidence; the verdict is tested in test_improve_loop.py); the final run is created, never
trained. Every route is also called from another workspace (404) and the read models' key sets are exact.
"""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.agents.harness.validation import check_proposal_nodes
from app.agents.tools.catalog import ToolError, catalog
from app.db.models import (
    ExecutionRequest,
    Experiment,
    ImproveRun,
    ProjectDecisionRecord,
)
from app.domain.decision_records import DecisionActor
from app.domain.errors import InvalidDecisionRecordError
from app.services import decision_record_service as drs
from app.services.auth_service import create_access_token
from app.services.experiment_verdict_service import _selection
from app.services.improve import loop_handler
from app.services.improve import service as svc
from app.services.improve.comparison import TryVerdict
from app.services.service_token_service import create_service_token
from conftest import ENGINEER_PASSWORD
from test_data_model_lineage import make_lineage_setup
from test_experiment_branching import _parent
from test_improve_loop_real import _run_job, _step

RUN_KEYS = {
    "id", "project_id", "split_plan_id", "start_experiment_id", "incumbent_experiment_id", "final_experiment_id",
    "status", "outcome", "stop_reason", "stop_detail", "goal", "budget", "plan", "planned_comparisons",
    "validation_folds", "looks_before", "sealed_set_label", "sealed_set_consulted", "final_attempts",
    "accepted_iteration_seq", "accepted_by_user_id", "created_by_user_id", "started_by", "ai",
    "training_seconds_spent", "llm_micros_spent", "cancel_requested_at", "waiting_user_expires_at", "created_at",
    "started_at", "finished_at", "iterations", "actions",
}
ITERATION_KEYS = {
    "seq", "kind", "status", "reason", "action", "experiment_id", "incumbent_experiment_id",
    "planned_new_candidates", "planned_candidate_ids", "comparison", "agent", "training_seconds", "created_at",
    "finished_at",
}
HOLDOUT = re.compile(r"holdout|final_test", re.IGNORECASE)


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(map(str, value)) | {k for item in value.values() for k in _keys(item)}
    if isinstance(value, list):
        return {k for item in value for k in _keys(item)}
    return set()


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"improve-api-{uuid4().hex}"}


def test_improve_routes_tokens_and_acceptance(engineer_client, db_session, engineer_user, monkeypatch, tmp_path):
    db, c = db_session, engineer_client
    parent = _parent(c, db, engineer_user, monkeypatch)
    ws = parent.workspace_id
    plain = {"plain": {"metric": _selection(db, ws, parent.id).selection_metric}}
    token, secret = create_service_token(db, creator=engineer_user, workspace_id=ws, name="improve-api",
                                         scopes=["read", "experiments:write"], expires_in_days=1,
                                         current_password=ENGINEER_PASSWORD)
    db.commit()
    tok = {"Authorization": f"Bearer {secret}"}

    def start(body, headers=None):
        return c.post("/v1/improve-runs", json=body, headers={**_key(), **(headers or {})})

    def body(goal=plain, budget=None, **start_fields):
        out = {"start": {"from_experiment_id": str(parent.id), **start_fields}, "goal": goal}
        return out if budget is None else {**out, "budget": budget}

    # --- (29) a token's start: budget only lowered, keyed, principal-scoped, reserved keys refused ----
    wide = body(budget={"max_iterations": 20, "max_wall_seconds": 43_200, "patience": 10, "comparison_bound": 60})
    key = {"Idempotency-Key": "token-start-1"}
    first = start(wide, {**tok, **key})
    assert first.status_code == 202, first.text
    run = first.json()
    assert first.headers["Location"] == f"/v1/improve-runs/{run['id']}" and set(run) == RUN_KEYS
    assert {k: run["budget"][k] for k in ("max_iterations", "max_wall_seconds", "patience", "comparison_bound")} == {
        "max_iterations": 6, "max_wall_seconds": 7_200, "patience": 3, "comparison_bound": 30}
    assert (run["started_by"], run["created_by_user_id"], run["ai"]) == ("service_token", None, "off")
    assert run["actions"] == {"accept": False, "keep_current": False, "cancel": True}
    replay = start(wide, {**tok, **key})
    assert (replay.status_code, replay.json()["id"], replay.headers.get("Idempotent-Replayed")) == (
        202, run["id"], "true")
    assert start(body(budget={"max_iterations": 1}), {**tok, **key}).json()["error"]["code"] == \
        "idempotency_key_conflict"
    planted = start(wide, {**tok, "Idempotency-Key": f"improve:{run['id']}:accept"})
    assert (planted.status_code, planted.json()["error"]["code"]) == (400, "invalid_idempotency_key")
    # A token never asks for a fresh split; a person may, but it is not built yet.
    assert start(body(fresh_split=True), tok).json()["error"]["code"] == "fresh_split_human_only"
    assert start(body(fresh_split=True)).json()["error"]["code"] == "fresh_split_unavailable"
    assert start(body(validation_folds=10)).status_code == 422
    # One live loop per split plan; a token cancels a queued loop (If-Match required, replay-safe).
    assert start(body()).json()["error"]["code"] == "improve_run_live"
    url = f"/v1/improve-runs/{run['id']}"
    seen = c.get(url, headers=tok)
    # Another token of the same workspace may read this loop but never cancel it.
    _other, other_secret = create_service_token(db, creator=engineer_user, workspace_id=ws, name="improve-api-2",
                                                scopes=["read", "experiments:write"], expires_in_days=1,
                                                current_password=ENGINEER_PASSWORD)
    db.commit()
    tok2 = {"Authorization": f"Bearer {other_secret}"}
    seen2 = c.get(url, headers=tok2)
    assert seen2.json()["actions"]["cancel"] is False
    stranger = c.post(f"{url}/cancel", headers={**tok2, "If-Match": seen2.headers["ETag"]})
    assert (stranger.status_code, stranger.json()["error"]["code"]) == (403, "token_cannot_cancel")
    assert seen.status_code == 200 and seen.json() == run
    assert c.post(f"{url}/cancel", headers=tok).status_code == 428
    assert c.post(f"{url}/cancel", headers={**tok, "If-Match": '"stale"'}).status_code == 412
    cancelled = c.post(f"{url}/cancel", headers={**tok, "If-Match": seen.headers["ETag"]})
    assert cancelled.status_code == 200 and (cancelled.json()["status"], cancelled.json()["stop_detail"]) == (
        "cancelled", "cancelled_by_token")
    assert c.post(f"{url}/cancel", headers=tok).status_code == 200  # finished: no precondition
    # DB-counted rate limit: 5 token starts per hour, a sliding window.
    for _ in range(4):
        again = start(body(), tok)
        assert again.status_code == 202, again.text
        svc.cancel_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=again.json()["id"])
    limited = start(body(), tok)
    assert (limited.status_code, limited.json()["error"]["code"]) == (429, "improve_rate_limited")
    db.execute(text("ALTER TABLE improve_runs DISABLE TRIGGER improve_runs_guard"))  # created_at is frozen
    db.execute(update(ImproveRun).where(ImproveRun.created_by_service_token_id == token.id).values(
        created_at=datetime.now(UTC) - timedelta(hours=2)))
    db.execute(text("ALTER TABLE improve_runs ENABLE TRIGGER improve_runs_guard"))
    db.commit()
    later = start(body(), tok)
    assert later.status_code == 202, later.text
    svc.cancel_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=later.json()["id"])
    # An agent's proposal a person accepts is clamped like a token's start (never above the defaults).
    proposed = svc.start_improve_run(db, actor=engineer_user, workspace_id=ws, start_experiment_id=parent.id,
                                     goal=plain, budget=wide["budget"], agent_proposed=True)
    assert (proposed.budget["max_iterations"], proposed.budget["comparison_bound"], proposed.created_by_service_token_id) \
        == (6, 30, None)
    svc.cancel_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=proposed.id)

    # --- a person's loop to waiting_user on a real sealed probe (its verdict forced) ------------------
    real_judge = loop_handler.judge

    def accept_it(db_, run_, live, goal):
        verdict = real_judge(db_, run_, live, goal)
        return TryVerdict("accepted", None, {**verdict.comparison, "status": "accepted", "forced_by_test": True})

    monkeypatch.setattr(loop_handler, "judge", accept_it)
    human = start(body(budget={"max_iterations": 1, "patience": 1}))
    assert human.status_code == 202, human.text
    run_id = human.json()["id"]
    url = f"/v1/improve-runs/{run_id}"
    loop = _step(db, run_id)
    probe = svc.live_iteration(db, loop)
    assert _run_job(db, probe.experiment_id).status == "completed"
    loop = _step(db, run_id)
    monkeypatch.setattr(loop_handler, "judge", real_judge)
    assert loop.status == "waiting_user"
    person = c.get(url)
    agent = c.get(url, headers=tok)
    p_body, a_body = person.json(), agent.json()
    assert set(p_body) == set(a_body) == RUN_KEYS
    assert all(set(it) == ITERATION_KEYS for it in p_body["iterations"] + a_body["iterations"])
    assert p_body["actions"] == {"accept": True, "keep_current": True, "cancel": True}
    assert a_body["actions"] == {"accept": False, "keep_current": False, "cancel": False}
    assert p_body["created_by_user_id"] == str(engineer_user.id) and a_body["created_by_user_id"] is None
    assert person.headers["ETag"] != agent.headers["ETag"]
    page = c.get(f"/v1/projects/{parent.project_id}/improve-runs", headers=tok).json()
    assert page["items"][0]["id"] == run_id and all(item["created_by_user_id"] is None for item in page["items"])

    # --- (32) no key naming the holdout in any read model; holdout values feed no field ---------------
    for value in (p_body, a_body, page):
        assert not {k for k in _keys(value) if HOLDOUT.search(k)}
    db.execute(text("ALTER TABLE evaluation_metrics DISABLE TRIGGER evaluation_metrics_evidence_locked"))
    mutated = db.execute(text(
        "UPDATE evaluation_metrics SET metric_value = metric_value / 3 + 0.0123 WHERE model_evaluation_id IN ("
        "SELECT e.id FROM model_evaluations e JOIN experiment_candidates c ON c.id = e.candidate_id "
        "WHERE c.experiment_id = :id AND e.evaluation_scope = 'final_holdout')"), {"id": parent.id}).rowcount
    db.execute(text("ALTER TABLE evaluation_metrics ENABLE TRIGGER evaluation_metrics_evidence_locked"))
    db.execute(text("UPDATE experiments SET result = jsonb_set(result, '{holdout}', '{\"roc_auc\": 0.111}') "
                    "WHERE id = :id"), {"id": parent.id})
    db.commit()
    db.expire_all()
    assert mutated > 0  # the start's own final-test values changed ...
    assert c.get(url).json() == p_body and c.get(url, headers=tok).json() == a_body

    # --- (29) tokens cannot cancel waiting_user; the generic cancel refuses tokens on loop runs --------
    refused = c.post(f"{url}/cancel", headers={**tok, "If-Match": agent.headers["ETag"]})
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "token_cannot_cancel")
    on_probe = c.post(f"/v1/experiments/{probe.experiment_id}/cancel", headers={**tok, **_key()})
    assert (on_probe.status_code, on_probe.json()["error"]["code"]) == (403, "improve_run_owns_experiment")
    # The agent catalog cannot propose a branch of the sealed probe outside the loop.
    with pytest.raises(ToolError) as sealed:
        check_proposal_nodes(db, workspace_id=ws, project_id=parent.project_id, tool="branch_experiment",
                             arguments={"experiment_id": str(probe.experiment_id), "intent": "x", "changes": []})
    assert sealed.value.code == "sealed_parent_human_only"

    # --- (28) tokens never accept; no tool reaches /accept --------------------------------------------
    accept_body = {"iteration": probe.seq, "rationale": "beats the noise on every fold"}
    token_accept = c.post(f"{url}/accept", json=accept_body, headers={**tok, **_key(), "If-Match": agent.headers["ETag"]})
    assert token_accept.status_code == 403
    assert not [op for item in catalog().values() for op in item.operations if op.endswith("/accept")
                and "improve-runs" in op]
    import dclab_mcp.server as mcp_server

    assert "improve_runs.accept" not in inspect.getsource(mcp_server)

    # --- (31) the person accepts: If-Match, the workspace cap, the final run's actor, the record -------
    assert c.post(f"{url}/accept", json=accept_body, headers=_key()).status_code == 428
    assert c.post(f"{url}/accept", json=accept_body, headers={**_key(), "If-Match": '"stale"'}).status_code == 412
    with monkeypatch.context() as patch:
        patch.setattr(svc, "MAX_LIVE_IMPROVE_RUNS_PER_WORKSPACE", 0)
        capped = c.post(f"{url}/accept", json=accept_body, headers={**_key(), "If-Match": person.headers["ETag"]})
    assert (capped.status_code, capped.json()["error"]["code"]) == (429, "improve_run_quota")
    accept_key = {"Idempotency-Key": "accept-1", "If-Match": person.headers["ETag"]}
    accepted = c.post(f"{url}/accept", json=accept_body, headers=accept_key)
    assert accepted.status_code == 202, accepted.text
    done = accepted.json()
    assert (done["status"], done["accepted_iteration_seq"], done["accepted_by_user_id"]) == (
        "finalizing", probe.seq, str(engineer_user.id))
    again = c.post(f"{url}/accept", json=accept_body, headers=accept_key)
    assert (again.status_code, again.headers.get("Idempotent-Replayed")) == (202, "true")
    # A concurrent request with the same key that missed the first lookup (its If-Match is now stale)
    # still replays instead of answering 412.
    import app.services.idempotency_service as keys

    real_find, calls = keys.find_bound, []

    def first_misses(*args, **kwargs):
        calls.append(1)
        return None if len(calls) == 1 else real_find(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(keys, "find_bound", first_misses)
        raced = c.post(f"{url}/accept", json=accept_body, headers=accept_key)
    assert (raced.status_code, raced.headers.get("Idempotent-Replayed"), len(calls)) == (202, "true", 2)
    db.expire_all()
    loop = db.get(ImproveRun, run_id)
    request = db.scalar(select(ExecutionRequest).where(ExecutionRequest.pipeline_run_id == loop.final_experiment_id))
    assert (request.requested_by_user_id, request.initiated_by_service_token_id) == (engineer_user.id, None)
    assert db.get(Experiment, loop.final_experiment_id).evidence_mode == "scored"
    record = db.get(ProjectDecisionRecord, loop.acceptance_record_id)
    assert (record.actor_kind, record.actor_user_id, record.idempotency_key, record.details["improve_run_id"]) == (
        "human", engineer_user.id, f"improve:{run_id}:accept", run_id)
    assert record.rationale.endswith("Reason: beats the noise on every fold")
    with pytest.raises(InvalidDecisionRecordError) as forged:
        drs.record(db, workspace_id=ws, project_id=parent.project_id, actor=DecisionActor.human(engineer_user),
                   decision_type="experiment_accepted", subject_kind="experiment", subject_id=probe.experiment_id,
                   rationale="forged", state="accepted", details={"improve_run_id": run_id})
    assert forged.value.reason == "reserved_detail_key"
    db.rollback()
    with pytest.raises((IntegrityError, DBAPIError)):  # the acceptance CHECK holds
        db.execute(update(ImproveRun).where(ImproveRun.id == run_id).values(acceptance_record_id=None))
        db.flush()
    db.rollback()
    # Finalizing: a token can neither cancel the loop nor its final run.
    final_seen = c.get(url, headers=tok)
    assert c.post(f"{url}/cancel", headers={**tok, "If-Match": final_seen.headers["ETag"]}).status_code == 403
    on_final = c.post(f"/v1/experiments/{loop.final_experiment_id}/cancel", headers={**tok, **_key()})
    assert (on_final.status_code, on_final.json()["error"]["code"]) == (403, "improve_run_owns_experiment")
    # Inbox: the loop was waiting for a person; finished loops are in "done" (status words, no values).
    # Inbox (§7): only waiting / blocked / failed loops leave notices; the cancelled ones above leave none.
    done_tab = c.get("/v1/inbox", params={"tab": "done"}).json()["items"]
    assert not [item for item in done_tab if item["kind"] == "improve_run"]

    # --- cross-workspace: 404 everywhere ------------------------------------------------------------
    other = make_lineage_setup(db, tmp_path)
    foreign = {"Authorization": f"Bearer {create_access_token(other['alpha_admin'])}",
               "X-Workspace-Id": str(other["alpha"].id)}
    for method, path, payload in (
            ("GET", url, None), ("POST", f"{url}/cancel", None), ("POST", f"{url}/accept", accept_body),
            ("GET", f"/v1/projects/{parent.project_id}/improve-runs", None),
            ("POST", "/v1/improve-runs", body())):
        response = c.request(method, path, json=payload, headers={**foreign, **_key(), "If-Match": "*"})
        assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found"), (method, path)


def test_inbox_lists_a_loop_waiting_for_a_person(engineer_client, db_session, engineer_user, monkeypatch):
    """The Inbox notice of a loop in ``waiting_user`` (needs_decision; status words only)."""

    db, c = db_session, engineer_client
    parent = _parent(c, db, engineer_user, monkeypatch)
    ws = parent.workspace_id
    run = svc.start_improve_run(db, actor=engineer_user, workspace_id=ws, start_experiment_id=parent.id,
                                goal={"plain": {"metric": _selection(db, ws, parent.id).selection_metric}})
    expires = datetime.now(UTC) + timedelta(days=14)
    db.execute(update(ImproveRun).where(ImproveRun.id == run.id).values(status="running"))
    db.execute(update(ImproveRun).where(ImproveRun.id == run.id).values(status="waiting_user",
                                                                        waiting_user_expires_at=expires))
    db.commit()
    items = c.get("/v1/inbox", params={"tab": "needs_decision"}).json()["items"]
    [notice] = [item for item in items if item["kind"] == "improve_run"]
    assert (notice["id"], notice["source"], notice["status"]) == (
        f"improve_run:{run.id}", {"kind": "improve_run", "id": str(run.id)}, "waiting_user")
    assert notice["summary"].startswith("Improve loop waiting for you") and notice["actions"] == []
    counts = c.get("/v1/inbox/counts").json()
    assert counts["needs_decision"] >= 1
