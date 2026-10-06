"""P6.3-B1: the lead agent's bounded tool loop on the harness (ADR 0008 §6; ADR 0009 §6, §7.1, §7.3).

Scripted fake-LLM sessions (``ScriptedLeadDriver`` behind the gateway's fake provider): a
spec -> run -> compare session stays within its bounds and only proposes; out-of-capability
tool calls are refused; the registry has no holdout-reading or selection tool; unknown tools,
invalid steps, fabricated / foreign citations, uncited (holdout) numbers and external links are
rejected and end in the honest template; every bound ends the turn with a typed reason. Plus
the P6.4-A review fixes: verifier verdicts withheld from agent views (8a) and results
superseding the Planner's dataset plans (8b). No network.
"""

from __future__ import annotations

import inspect
import json
import re
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update

from app.agents.contracts import AgentRunSpec, Citation, LeadTurn, RunLimits, Untrusted
from app.agents.gateway.limits import GatewayLimits
from app.agents.gateway.providers.fake import FakeProvider
from app.agents.governance.seed import seed_platform_governance
from app.agents.harness import service as svc
from app.agents.harness.recorder import load_events
from app.agents.harness.replay import ReplayRefused, replay
from app.agents.harness.service import AgentRunRefused, AgentService
from app.agents.lead import runtime as lead_runtime
from app.agents.lead.fake_driver import ScriptedLeadDriver, answer, tools
from app.agents.runtime.base import RuntimeRefused
from app.agents.prompt_releases import prompt_text, sync_prompt_releases
from app.agents.tools.catalog import FORBIDDEN_OPERATIONS, export_payload, visible
from app.agents.tools.shaping import HOLDOUT_KEY, withhold_model_build
from app.db.models import (
    AgentProposal,
    AgentRun,
    Experiment,
    LlmInvocation,
    MlRunEvent,
    ProblemSpec,
    UserRole,
    WorkflowRun,
)
from app.domain.model_build import ModelBuildStageRead, PipelineModelBuildRead
from app.domain.run_plans import ExperimentPlan, PlanSplit
from app.services import authorization_service
from app.services.auth_service import create_user
from app.services.auto_train import finalize
from app.services.model_build_service import get_pipeline_model_build
from app.services.problem_spec_service import create_problem_spec
from app.services.project_service import create_project
from app.services.run_plan_service import supersede_for_results
from app.services.service_token_service import create_service_token
from test_agent_classes import _seed_evidence, _Uncached
from test_agent_harness import _token
from test_decision_record_service import g, setup  # noqa: F401  (fixtures)
from test_graph_service import _source_dataset

LIMITS = dict(steps=8, tokens=60000, wall_s=120, cost_micros=250_000, tool_calls=20)


@pytest.fixture
def lead(db_session, g):  # noqa: F811
    db = db_session
    seed_platform_governance(db, environment="test")
    sync_prompt_releases(db)
    db.commit()
    _seed_evidence(db, g)  # exp[1]: locked CV winner roc_auc 0.8123 (its final holdout says 0.7001)
    ns = SimpleNamespace(db=db, g=g, driver=None,
                         settings=SimpleNamespace(ai_enabled=True, service_tokens_enabled=True, dclab_env="test"))
    ns.release = svc.released_prompt_id(db, lead_runtime.AGENT_KEY, lead_runtime.PROMPT_VERSION)
    ns.fake = FakeProvider(handler=lambda call: ns.driver(call), resolved_model="gpt-6.1-sol", environment="test")
    gateway = _Uncached(providers={"openai": ns.fake}, limits=GatewayLimits(), settings=lambda: ns.settings)
    ns.service = lambda: AgentService(gateway=gateway, settings=lambda: ns.settings)

    def spec(**overrides):
        values = dict(workspace_id=g.ws, project_id=g.project.id, kind="lead", agent_key="lead", agent_version="1",
                      runtime="lead_loop", runtime_version=lead_runtime.VERSION, purpose=lead_runtime.PURPOSE,
                      subject_kind="project", prompt_release_id=ns.release, user_id=g.actor.id,
                      outcome_scope="cv", tool_surface="assistant")
        values.update(overrides)
        return AgentRunSpec(**values)

    def run(steps, text="Set up my churn data, run it and compare.", **overrides):
        ns.driver = ScriptedLeadDriver(steps)
        return ns.service().run(db, spec(turn=LeadTurn(user_text=Untrusted(untrusted_text=text)), **overrides))

    ns.run, ns.spec = run, spec
    return ns


def _events(db, run_id):
    db.expire_all()
    return load_events(db, workspace_id=db.get(AgentRun, run_id).workspace_id, run_id=run_id)


def _payloads(db, run_id, kind):
    return [e.payload for e in _events(db, run_id) if e.type == kind]


def _limits(**narrow):
    return RunLimits(**{**LIMITS, **narrow})


def test_scripted_session_proposes_spec_and_run_compares_within_bounds(lead):
    g, db = lead.g, lead.db
    project = str(g.project.id)
    counts = [db.scalar(select(func.count()).select_from(model)) for model in (Experiment, ProblemSpec)]
    result = lead.run([
        tools(("inspect_dataset", {"dataset_id": str(g.source.id)}), ("inspect_project", {"project_id": project})),
        tools(("propose_problem_spec", {"project_id": project, "task_type": "binary", "target_column": "target",
                                        "business_objective": "Predict churn.", "rationale": "binary target",
                                        "primary_metric": "roc_auc"})),
        tools(*[("run_experiment", {"project_id": project, "dataset_id": str(g.source.id), "target_column": "target",
                                    "intent": "first churn model"})] * 2),  # a repeated write reuses the proposal
        tools(("compare_experiments", {"experiment_ids": [str(g.exp[0]), str(g.exp[1])]})),
        answer(f"Spec and run await your confirmation. [The branch](dclab://experiment/{g.exp[1]}) reaches "
               "CV roc_auc 0.812.", ("experiment", g.exp[1])),
    ])
    assert (result.status, result.error_code, len(result.proposal_ids)) == ("completed", None, 2)
    events = _events(db, result.run_id)
    types = [e.type for e in events]
    assert types.count("llm_call_finished") == types.count("step_validated") == 5 and "step_rejected" not in types
    assert types.count("tool_call_finished") == 5 and "tool_call_denied" not in types
    assert types[-3:] == ["assistant_message", "budget_settled", "run_finished"]
    assert not any(HOLDOUT_KEY.search(json.dumps(e.payload)) for e in events)
    message = events[-3].payload
    assert message["citations"] == [{"kind": "experiment", "id": str(g.exp[1])}] and message["llm_used"]
    # One registry, two transports: the lead's tools are the catalog's assistant surface = MCP minus the hand-off.
    surface = events[0].payload["tools"]
    assert set(surface) == {t.name for t in visible("assistant")} == {
        t["name"] for t in export_payload()["tools"]} - {"accept_proposal", "list_proposals", "request_agent_review"}
    assert not [n for n in surface if any(op in n for op in FORBIDDEN_OPERATIONS)
                or re.search(r"holdout|final_test|winner|select|promote|metric|split|rows|sql|code_exec", n)]
    assert all(name in prompt_text("lead", 1) for name in surface)  # the released prompt lists every tool
    # Write tools never act: two pending L1 proposals, no new spec or experiment.
    rows = list(db.scalars(select(AgentProposal).where(AgentProposal.run_id == result.run_id)))
    assert {(r.tool_name, r.proposal_type, r.level_at_proposal, r.status, r.decision_point_key) for r in rows} == {
        ("propose_problem_spec", "ToolCallProposal", 1, "proposed", "lead.propose_problem_spec"),
        ("run_experiment", "ToolCallProposal", 1, "proposed", "lead.run_experiment")}
    assert [db.scalar(select(func.count()).select_from(model)) for model in (Experiment, ProblemSpec)] == counts
    run = db.get(AgentRun, result.run_id)
    assert run.usage["steps"] == 5 <= LIMITS["steps"] and run.usage["tool_calls"] == 5 <= LIMITS["tool_calls"]
    assert 0 < run.cost_micros <= run.held_micros == LIMITS["cost_micros"] and run.model == "gpt-6.1-sol"
    ledger = list(db.scalars(select(LlmInvocation).where(LlmInvocation.agent_run_id == run.id)))
    assert len(ledger) == 5 and {(r.prompt_release_id, r.purpose) for r in ledger} == {(lead.release, "assistant.turn")}
    # The pending proposals reached the model as tool outcomes; no holdout value ever left the process.
    sent = json.dumps(lead.driver.requests)
    assert all(str(pid) in sent for pid in result.proposal_ids) and "pending_confirmation" in sent
    assert "0.7001" not in sent and not HOLDOUT_KEY.search(sent)


def test_tool_calls_outside_the_principals_capabilities_are_refused(lead, monkeypatch):
    g, db = lead.g, lead.db
    call = tools(("run_experiment", {"project_id": str(g.project.id), "dataset_id": str(g.source.id)}))
    viewer = create_user(db, email=f"viewer-{uuid4().hex[:6]}@test.invalid", password="test-password",
                         role=UserRole.VIEWER, workspace_id=g.ws)
    db.commit()
    e1 = ("experiment", g.exp[1])
    read_only = lead.run([call, answer("Nothing was proposed.", e1)], user_id=viewer.id, may_propose=False)
    assert read_only.status == "completed" and read_only.proposal_ids == ()
    assert [p["code"] for p in _payloads(db, read_only.run_id, "tool_call_denied")] == ["ml_write_required"]
    assert lead.run([call], user_id=viewer.id).error_code == "ml_write_required"  # a proposing turn needs ML write
    # A role lost mid-turn: every write call re-checks ML write.
    revoked = {"now": False}
    real = authorization_service.can_execute_workspace_ml
    monkeypatch.setattr(authorization_service, "can_execute_workspace_ml",
                        lambda *args: not revoked["now"] and real(*args))
    demoted = lead.run([lambda _payload: revoked.update(now=True) or call, answer("Nothing was proposed.", e1)])
    assert [p["code"] for p in _payloads(db, demoted.run_id, "tool_call_denied")] == ["ml_write_required"]
    monkeypatch.undo()
    # Human sessions only (ADR 0009 §7.2): a service token never drives the lead loop (refused before a run row).
    token = _token(db, g, ["read", "experiments:write"])
    runs = db.scalar(select(func.count()).select_from(AgentRun))
    by_token = lead.run([call], user_id=None, service_token_id=token.id)
    assert (by_token.status, by_token.error_code, by_token.run_id) == ("refused", "human_session_required", None)
    assert db.scalar(select(func.count()).select_from(AgentRun)) == runs
    turn = LeadTurn(user_text=Untrusted(untrusted_text="hi"))
    for bad in (dict(user_id=None, service_token_id=token.id), dict(purpose="other.purpose"),
                dict(project_id=None, subject_kind=None), dict(decision_point_key="lead.predict")):
        with pytest.raises(RuntimeRefused, match="lead_spec_invalid"):
            lead_runtime.factory(lead.spec(turn=turn, **bad))
    assert db.scalar(select(func.count()).select_from(AgentProposal)) == 0
    # Never queued (the turn is not persisted), never replayed.
    with pytest.raises(AgentRunRefused, match="runtime_not_queueable"):
        lead.service().submit(db, lead.spec())
    with pytest.raises(ReplayRefused, match="not_replayable"):
        replay(db, workspace_id=g.ws, run_id=read_only.run_id, actor=g.actor, service=lead.service())


def test_rejected_steps_retry_once_then_end_in_the_honest_template(lead, setup):  # noqa: F811
    g, db = lead.g, lead.db
    foreign = create_problem_spec(db, actor=setup["beta_admin"], workspace_id=setup["beta"].id,
                                  project_id=setup["beta_project"].id, task_type="binary",
                                  business_objective="Other tenant.", target_column="target", status="locked")
    db.commit()
    e1 = ("experiment", g.exp[1])
    cases = {
        "unknown_tool": [tools(("drop_table", {})), tools(("execute_code", {}))],
        "citation_not_found": [answer("E9 is best.", ("experiment", uuid4())),
                               answer("See the spec.", ("problem_spec", foreign.id))],
        "uncited_number": [answer("E1 scores 0.7001.", e1),  # the fixture's final-holdout value, also in a label
                           answer(f"[E1 is at 0.7001 roc_auc](dclab://experiment/{g.exp[1]})", e1)],
        "uncited_answer": [answer("E1 is the best run."), answer("Trust me.")],
        "external_link": [answer("Read [this](https://evil.example).", e1), answer("Visit www.evil.example", e1)],
        "markdown_not_allowed": [answer("<img src=x>", e1), answer("![x](dclab://experiment/x)", e1)],
        "link_not_cited": [answer(f"[E0](dclab://experiment/{g.exp[0]})", e1)] * 2,
        "holdout_in_output": [answer("The hold-out says more.", e1), answer("On unseen rows it is fine.", e1)],
        "step_invalid": [{"kind": "answer", "message": " "}, {"kind": "tool_calls", "tool_calls": []}],
    }
    for message in ("> [x]: //evil.com\n\n[here][x]", "> [x]: https&#58;//evil.com\n\n[go][x]",
                    "[a\\]b]: //evil.com", "Mail a@evil.com", "[t](<https://e a>)", "[t](dclab&#58;//x)",
                    "[t](javascript:alert(1))", "[a [b] c](https://x)", "see evil.com/x", "https:evil.com",
                    "go to evil.com", "ev\u200bil.com/x", "\uff48\uff54\uff54\uff50\uff53://x"):
        assert lead_runtime.markdown_reasons(message, ()) in (["external_link"], ["markdown_not_allowed"]), message
    ok = f"See [E1](dclab://experiment/{g.exp[1]}) and E2 (3 runs, 1,000 rows)."
    assert lead_runtime.markdown_reasons(ok, (Citation(kind="experiment", id=g.exp[1]),)) == []
    assert lead_runtime.uncited(ok, []) == []  # integers and node ids are not metric values
    assert not lead_runtime._HOLDOUT_WORDS.search("latest settings, a test setup, the contest settled")
    assert all(lead_runtime._HOLDOUT_WORDS.search(text) for text in (
        "held-back rows", "the final evaluation", "test split", "unseen data", "final_test"))
    assert lead_runtime.uncited("auc.93, AUC_0.93, 0,93, 93 percent, 93 per cent, 930‰, 7e-1, 81%, id "
                                f"{g.exp[0]}, 1e5a", [0.8123]) == [0.93, 0.93, 0.93, 93.0, 93.0, 930.0, 0.7]
    for reason, steps in cases.items():
        result = lead.run(steps)
        assert (result.status, result.proposal_ids) == ("completed", ()), reason
        assert [p["reasons"] for p in _payloads(db, result.run_id, "step_rejected")] == [[reason]] * 2, reason
        [said] = _payloads(db, result.run_id, "assistant_message")
        assert (said["message"], said["citations"], said["fallback"]) == (lead_runtime.NOT_VERIFIED, [],
                                                                          "step_rejected"), reason
        assert not _payloads(db, result.run_id, "tool_call_requested"), reason
    # A schema-invalid output is retried once with the reasons; the corrected step stands.
    fixed = lead.run([{"kind": "explode"}, answer("No experiment needs a branch yet.", e1)])
    assert fixed.status == "completed" and [p["ok"] for p in _payloads(db, fixed.run_id, "llm_call_finished")] == [
        False, True]
    assert "invalid_output" in json.dumps(lead.driver.requests[-1])
    assert _payloads(db, fixed.run_id, "assistant_message")[0]["message"] == "No experiment needs a branch yet."


def test_bounds_end_the_turn_with_a_typed_reason(lead):
    g, db = lead.g, lead.db
    look = tools(("get_experiment", {"experiment_id": str(g.exp[0])}))
    steps = lead.run([look] * 3, limits=_limits(steps=2))
    assert (steps.status, steps.error_code) == ("over_budget", "step_limit")
    assert _payloads(db, steps.run_id, "budget_exhausted") == [{"call": 3, "limit": "steps"}]
    tokens = lead.run([look] * 2, limits=_limits(tokens=10))
    assert (tokens.status, tokens.error_code) == ("over_budget", "token_limit")
    cost = lead.run([answer("hi")], limits=_limits(cost_micros=1_000))  # the worst case exceeds the hold
    assert (cost.status, cost.error_code) == ("over_budget", "budget_exhausted") and lead.driver.requests == []
    wall = lead.run([lambda _payload: time.sleep(1.05) or look], limits=_limits(wall_s=1))
    assert (wall.status, wall.error_code) == ("timed_out", "wall_limit")
    assert _payloads(db, wall.run_id, "budget_exhausted") == [{"call": 1, "limit": "wall_time"}]
    calls = lead.run([tools(*[("get_experiment", {"experiment_id": str(g.exp[0])})] * 3)],
                     limits=_limits(tool_calls=2))
    assert calls.status == "completed"
    assert [p["code"] for p in _payloads(db, calls.run_id, "tool_call_denied")] == ["tool_call_limit"]
    assert _payloads(db, calls.run_id, "budget_exhausted") == [{"call": 3, "limit": "tool_calls"}]
    for result, code in ((steps, "step_limit"), (tokens, "token_limit"), (cost, "budget_exhausted"),
                         (wall, "wall_limit"), (calls, "tool_call_limit")):
        [said] = _payloads(db, result.run_id, "assistant_message")  # the turn ends with the limit template
        assert (said["message"], said["fallback"], said["citations"]) == (lead_runtime.AT_LIMIT, code, [])
        run = db.get(AgentRun, result.run_id)
        assert run.budget_released_at is not None and run.cost_micros <= run.held_micros
    lead.settings.ai_enabled = False  # AI off: no model call (the threads API answers with templates)
    off = lead.run([answer("hi")])
    assert (off.status, off.error_code, lead.driver.requests) == ("failed", "kill_switch", [])
    assert svc.AgentService().runtime_factory("lead_loop") is lead_runtime.factory


def test_agent_views_withhold_the_verifier_verdicts(client, lead):
    """8a: the deterministic verifier's status (and the advisory audit's) folds in holdout
    checks: a service token never reads it, neither in the model build nor in its events."""

    g, db = lead.g, lead.db
    run = db.get(Experiment, g.exp[2])  # not evidence-locked: its stored result can still be seeded
    verdict = {"overall_status": "FAILED", "status": "FAILED", "schema_version": 1,
               "checks": [{"check_id": "objective_constraints_met_holdout", "status": "FAIL"}]}
    db.execute(update(Experiment).where(Experiment.id == run.id).values(
        result={**dict(run.result or {}), "deterministic_verification": verdict}))
    for sequence, (stage, kind, status, payload) in enumerate((
            ("openai_audit", "openai_audit_completed", "rejected",
             {"verification_id": "v1", "audit_mode": "routine", "deterministic_status": "FAILED",
              "advisory_status": "FAILED", "llm_report": {"overall_status": "FAILED"}}),
            ("deterministic_verification", "operation_completed", "completed",
             {"rows_in": 3, "overall_status": "FAILED"})), start=900):
        db.add(MlRunEvent(workspace_id=g.ws, workflow_run_id=run.workflow_run_id, experiment_id=run.id,
                          sequence=sequence, stage=stage, event_type=kind, status=status,
                          timestamp=datetime.now(UTC), payload=payload))
    _row, raw = create_service_token(db, creator=g.actor, workspace_id=g.ws, name="views", scopes=["read"],
                                     expires_in_days=30, current_password="test-password")
    db.commit()
    bearer = {"Authorization": f"Bearer {raw}"}

    def verifier(body):
        return next(item for item in body["stages"] if item["key"] == "deterministic_verification")

    human = get_pipeline_model_build(db, g.actor, g.ws, run.id).model_dump(mode="json")
    assert verifier(human)["configuration"]["overall_status"] == "FAILED"  # the human view is unchanged
    agent = client.get(f"/v1/model-builds/{run.id}", headers=bearer)
    assert agent.status_code == 200, agent.text
    assert verifier(agent.json())["configuration"] == {"schema_version": 1, "check_count": 1}
    events = client.get(f"/v1/model-builds/{run.id}/events", headers=bearer)
    assert events.status_code == 200, events.text
    seen = {item["stage"]: item for item in events.json()["items"] if item["sequence"] >= 900}
    assert (seen["openai_audit"]["status"], seen["openai_audit"]["payload"]) == (
        "withheld", {"verification_id": "v1", "audit_mode": "routine"})
    assert (seen["deterministic_verification"]["status"], seen["deterministic_verification"]["payload"]) == (
        "completed", {"rows_in": 3})
    attempts = {"attempts": [{"id": "a1", "deterministic_status": "FAILED", "schema_version": 2}]}
    stage = ModelBuildStageRead(key="deterministic_verification", sequence=1, title="Verification",
                                status="completed", configuration=attempts,
                                code_generation_support_status="not_available")
    shaped = withhold_model_build(PipelineModelBuildRead(workspace_id=g.ws, pipeline_run_id=run.id,
                                                         pipeline_run_status="COMPLETED", stages=[stage]))
    assert shaped.stages[0].configuration == {"attempts": [{"id": "a1", "schema_version": 2}]}


def test_results_supersede_the_planners_dataset_plans(lead, setup, tmp_path):  # noqa: F811
    """8b: a Planner plan's subject is the dataset (problem_spec_id NULL): a completed run on
    that dataset (same or no target) supersedes its objective / portfolio answers; split-only,
    accepted, other-dataset, other-target and other-project plans stay."""

    g, db = lead.g, lead.db
    other_project = create_project(db, actor=g.actor, workspace_id=g.ws, name="Other", slug=f"o-{uuid4().hex[:6]}")
    other = {name: _source_dataset(db, tmp_path, workspace_id=g.ws, project_id=project, env=setup["env"], name=name)
             for name, project in (("other-ds", g.project.id), ("other-pr", other_project.id))}
    split = PlanSplit(strategy="random")

    def plan(dataset, *, project=g.project.id, accepted=False, **answers):
        row = AgentProposal(
            workspace_id=g.ws, project_id=project, run_id=g.agent.agent_run_id, decision_point_key="spec.objective",
            level_at_proposal=1, answer_ceiling=1, proposal_type="ExperimentPlanProposal", schema_version=1,
            payload=ExperimentPlan(**answers).model_dump(mode="json"), payload_digest="a" * 64, citations=[],
            validator_verdict="accepted", validator_reasons=[], status="proposed", subject_kind="dataset_version",
            dataset_id=dataset)
        db.add(row)
        db.commit()
        if accepted:
            db.execute(text("UPDATE agent_proposals SET status = 'accepted', decided_by_user_id = :u, "
                            "decided_at = now() WHERE id = :id"), {"u": g.actor.id, "id": row.id})
            db.commit()
        return row

    rows = [plan(g.source.id, primary_metric="f1"), plan(g.source.id, families=("random_forest",)),
            plan(g.source.id, max_training_seconds=30.0, target_column="target"),
            plan(g.source.id, split=split), plan(g.source.id, primary_metric="f1", target_column="churn"),
            plan(g.source.id, primary_metric="f1", accepted=True), plan(other["other-ds"].id, primary_metric="f1"),
            plan(other["other-pr"].id, project=other_project.id, primary_metric="f1")]
    run = db.get(Experiment, g.exp[1])
    workflow_run = db.get(WorkflowRun, run.workflow_run_id)
    workflow_run.resolved_target = "target"
    db.commit()
    assert "supersede_for_results(db, experiment, inp.workflow_run)" in inspect.getsource(finalize)  # run_finalize
    assert supersede_for_results(db, run, workflow_run) == 3
    db.commit()
    db.expire_all()
    assert [db.get(AgentProposal, row.id).status for row in rows] == [
        "superseded", "superseded", "superseded", "proposed", "proposed", "accepted", "proposed", "proposed"]
