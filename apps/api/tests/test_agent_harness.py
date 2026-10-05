"""P6.10-A2: the agent harness (ADR 0009 §1 CI rules b-d, §5; ADR 0008 §2b, §6).

A scripted runtime defined here (the real fake and NOOA runtimes are P6.3-A) drives the
harness against the gateway's fake provider: lifecycle order, every hook effect and its
closed set, denial paths, budget reserve / settle / release, replay equality and
mismatch incidents, the ``agents.run`` job, the 0073 release marker and the import /
entry-point rules. No network.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.agents.contracts import AgentRunSpec, Citation, RunLimits
from app.agents.gateway import budget, redaction
from app.agents.gateway.limits import GatewayLimits
from app.agents.gateway.providers.fake import FakeProvider
from app.agents.gateway.service import GatewayService
from app.agents.governance.seed import seed_platform_governance
from app.agents.governance.switches import flip_off
from app.agents.harness import hooks as h
from app.agents.harness import service as svc
from app.agents.harness.recorder import digest, load_events
from app.agents.harness.replay import ReplayRefused, replay
from app.agents.harness.service import AgentService
from app.agents.harness.validation import parse_tool_arguments, proposal_payload
from app.agents.prompt_releases import sync_prompt_releases
from app.agents.runtime.base import ProposalDraft, RuntimeOutput
from app.agents.tools.catalog import catalog, catalog_digest
from app.agents.tools.render import to_mcp
from app.agents.tools.shaping import HOLDOUT_KEY, Code, Shaped, data_text
from app.agents.tools.shaping import text as workspace_text
from app.db.models import (
    AgentProposal,
    AgentRun,
    AiIncident,
    LlmInvocation,
    MlJob,
    PromptRelease,
    UserRole,
    WorkspaceLlmBudget,
)
from app.services.auth_service import create_user
from app.services.ml_job_service import process_next_job
from app.services.service_token_service import create_service_token, revoke_service_token
from test_agent_persistence import agent_run, agents  # noqa: F401  (fixture)
from test_decision_record_service import _force_lock, g, setup  # noqa: F401  (fixtures)

APP = Path(__file__).resolve().parents[1] / "app"
HARNESS_PROMPTS = Path(__file__).resolve().parent / "fixtures" / "harness_prompts"
OK = {"verdict": "keep", "confidence": 0.9}


class HarnessStep(BaseModel):  # must match fixtures/harness_prompts/harness_fixture/v1.schema.json (no docstring)
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["keep", "revise"]
    confidence: float = Field(ge=0, le=1)


class Scripted:
    """A deterministic runtime: ``llm`` / ``tool`` / ``raise`` / callable steps."""

    name, version, output_schema = "fake", "scripted==1", HarnessStep

    def __init__(self, script, log, final=None):
        self.script, self.log, self.final = script, log, final

    def run(self, session):
        output = None
        for step in self.script:
            if callable(step):
                step(session)
            elif step[0] == "llm":
                response = session.complete(output_schema=HarnessStep, max_output_tokens=200)
                self.log.append(response)
                output = response.output if response.ok else output
            elif step[0] == "tool":
                self.log.append(session.call_tool(step[1], step[2], reason=step[3] if len(step) > 3 else ""))
            elif step[0] == "raise":
                raise RuntimeError("runtime crashed")
        return self.final if self.final is not None else RuntimeOutput(output=output)


@pytest.fixture
def hz(db_session, g):  # noqa: F811
    db = db_session
    seed_platform_governance(db, environment="test")
    sync_prompt_releases(db, HARNESS_PROMPTS)
    db.commit()
    ns = SimpleNamespace(db=db, g=g, log=[], settings=SimpleNamespace(ai_enabled=True, service_tokens_enabled=True,
                                                                      dclab_env="test"))
    ns.release = db.scalar(select(PromptRelease.id).where(PromptRelease.agent_key == "harness_fixture"))
    ns.fake = FakeProvider(handler=lambda _call: dict(OK), resolved_model="gpt-6.1-sol-2026-09-30", environment="test")
    ns.gateway = GatewayService(providers={"openai": ns.fake}, limits=GatewayLimits(),
                                settings=lambda: ns.settings, prompts_root=HARNESS_PROMPTS)

    def service(script=(("llm",),), *, hooks=None, final=None):
        return AgentService(gateway=ns.gateway, settings=lambda: ns.settings, hooks=hooks,
                            runtimes={"fake": lambda _spec: Scripted(script, ns.log, final)})

    def spec(**overrides):
        values = dict(workspace_id=g.ws, project_id=g.project.id, kind="lead", agent_key="harness_fixture",
                      agent_version="1", runtime="fake", runtime_version="scripted==1", purpose="harness.fixture",
                      subject_kind="experiment", subject_id=g.exp[0], prompt_release_id=ns.release,
                      user_id=g.actor.id, outcome_scope="cv", tool_surface="assistant")
        values.update(overrides)
        return AgentRunSpec(**values)

    ns.service, ns.spec = service, spec
    return ns


def _events(db, run_id) -> list:
    db.expire_all()
    run = db.get(AgentRun, run_id)
    return load_events(db, workspace_id=run.workspace_id, run_id=run_id)


def _types(db, run_id) -> list[str]:
    return [event.type for event in _events(db, run_id)]


def _token(db, g, scopes):  # noqa: F811
    row, raw = create_service_token(db, creator=g.actor, workspace_id=g.ws, name="harness", scopes=list(scopes),
                                    expires_in_days=30, current_password="test-password")
    db.commit()
    return row


def _record_args(g, **extra):  # noqa: F811
    return {"project_id": str(g.project.id), "rationale": "branch keeps CV quality", "decision_type":
            "experiment_accepted", "subject_kind": "experiment", "subject_id": str(g.exp[1]),
            "evidence_refs": [{"kind": "experiment", "id": str(g.exp[1])}], **extra}


# --- lifecycle, events, budget ------------------------------------------------------------------


def test_lifecycle_order_events_budget_and_proposal(hz):
    g, db = hz.g, hz.db
    script = (("llm",), ("tool", "get_experiment", {"experiment_id": str(g.exp[0])}),
              ("tool", "record_decision", _record_args(g), "the branch is better"), ("llm",))
    result = hz.service(script).run(db, hz.spec())
    assert result.status == "completed" and result.error_code is None and len(result.proposal_ids) == 1
    events = _events(db, result.run_id)
    assert [e.type for e in events] == [
        "run_started", "context_built", "budget_reserved", "llm_call_started", "llm_call_finished",
        "tool_call_requested", "tool_call_finished", "tool_call_requested", "proposal_created",
        "tool_call_finished", "llm_call_started", "llm_call_finished", "budget_settled", "run_finished"]
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert all(e.payload_digest == digest(e.payload) for e in events)
    assert not any(HOLDOUT_KEY.search(json.dumps(e.payload)) for e in events)
    started = events[0].payload
    assert started["hooks"] == h.default_chain().listing() and started["tool_catalog_digest"] == catalog_digest()
    assert "accept_proposal" not in started["tools"] and "get_impact" in started["tools"]

    run = db.get(AgentRun, result.run_id)
    rows = list(db.scalars(select(LlmInvocation).where(LlmInvocation.agent_run_id == run.id)))
    assert len(rows) == 2 and all(row.budget_settled and row.prompt_release_id == hz.release for row in rows)
    assert {e.llm_invocation_id for e in events if e.type == "llm_call_finished"} == {row.id for row in rows}
    assert (run.status, run.held_micros, run.cost_micros) == ("completed", 250_000, sum(r.cost_micros for r in rows))
    assert run.budget_released_at is not None and run.finished_at is not None and run.started_at is not None
    assert run.context_digest == events[1].payload["context_digest"] and run.tool_catalog_digest == catalog_digest()
    assert (run.data_class, run.outcome_scope, run.limits["tool_calls"]) == ("aggregates", "cv", 20)
    assert run.usage["calls"] == 2 and run.usage["tool_calls"] == 2 and run.created_by_user_id == g.actor.id
    counters = {row.scope: row for row in db.scalars(select(WorkspaceLlmBudget).where(
        WorkspaceLlmBudget.workspace_id == g.ws))}
    assert all(row.reserved_micros == 0 for row in counters.values())
    assert counters["workspace"].spent_micros == run.cost_micros
    assert events[-2].payload == {"freed_micros": 250_000 - run.cost_micros, "cost_micros": run.cost_micros}

    [proposal] = db.scalars(select(AgentProposal).where(AgentProposal.run_id == run.id))
    definition = catalog()["record_decision"]
    args = parse_tool_arguments(definition, _record_args(g))
    assert (proposal.proposal_type, proposal.level_at_proposal, proposal.status) == ("ToolCallProposal", 1, "proposed")
    assert (proposal.tool_name, proposal.tool_arguments, proposal.decision_point_key) == (
        "record_decision", args, "lead.record_decision")
    assert proposal.payload_digest == proposal_payload(definition, args)[1]
    assert proposal.proposed_rationale == "the branch is better"
    assert (proposal.subject_kind, proposal.experiment_id) == ("experiment", g.exp[0])
    assert timedelta(days=6) < proposal.expires_at - datetime.now(UTC) <= timedelta(days=7)
    # The read result reached the runtime as tagged, redaction-ready fields (holdout-free).
    read = hz.log[1]
    assert read.ok and read.status == "result" and read.fields
    assert read.citations == (Citation(kind="experiment", id=g.exp[0]),)
    for item in read.fields:
        redaction._structural_check(item)
        redaction.render_value(item.value, untrusted=redaction._text_mode(item))
    assert hz.log[2].status == "proposed" and hz.log[2].proposal_id == proposal.id
    # The model saw the envelope; no field above the run's class or scope.
    sent = json.loads(hz.fake.calls[0].input_json)
    assert sent["context"] and all(item["outcome_scope"] in ("none", "cv") for item in sent["context"])


def test_output_proposal_levels_and_validator_rejection(hz):
    g, db = hz.g, hz.db
    draft = ProposalDraft(proposal_type="ExperimentReviewProposal", decision_point_key="experiment.review",
                          payload={"verdict": "keep"}, rationale="looks fine")
    shadow = hz.service(final=RuntimeOutput(output=HarnessStep(**OK), proposal=draft)).run(
        db, hz.spec(kind="specialist", tool_surface=None, decision_point_key="experiment.review"))
    assert shadow.status == "completed"
    [row] = db.scalars(select(AgentProposal).where(AgentProposal.run_id == shadow.run_id))
    assert (row.status, row.level_at_proposal, row.expires_at) == ("shadow", 0, None)  # L0 by default
    # Holdout in the output, a fabricated citation, a bad draft: rejected_by_validator, never an exception.
    for final, code in (
        (RuntimeOutput(output=HarnessStep(**OK), message="final_holdout says 0.9"), "holdout_in_output"),
        (RuntimeOutput(output=HarnessStep(**OK), citations=(Citation(kind="experiment", id=uuid4()),)),
         "citation_not_found"),
        (RuntimeOutput(output=HarnessStep(**OK), proposal=replace(draft, payload={"holdout_auc": 1})),
         "holdout_in_proposal"),
        (RuntimeOutput(output=None), "no_output"),
    ):
        result = hz.service(final=final).run(db, hz.spec(kind="specialist", tool_surface=None,
                                                          subject_kind="experiment", subject_id=g.exp[1]))
        assert (result.status, result.error_code) == ("rejected_by_validator", code)
        assert db.get(AgentRun, result.run_id).budget_released_at is not None
        assert _types(db, result.run_id)[-1] == "run_failed"


# --- hooks and their closed effect sets ------------------------------------------------------------


def _ctx(**overrides):
    values = dict(db=None, run_id=uuid4(), workspace_id=uuid4(), project_id=uuid4(), agent_key="a", purpose="p",
                  principal=None, may_propose=True, data_class="aggregates", outcome_scope="cv",
                  limits=RunLimits(steps=4, tokens=100, wall_s=60, cost_micros=10, tool_calls=2), tools=(),
                  ai_enabled=True)
    values.update(overrides)
    return h.HookContext(**values)


def test_hook_chain_enforces_closed_effect_sets():
    ctx, called = _ctx(), []
    wide = RunLimits(steps=99, tokens=999, wall_s=600, cost_micros=99, tool_calls=99)
    narrow = h.HookChain((h.Hook("pre_run", "x", lambda c, s: h.NarrowLimits(wide)),)).run("pre_run", ctx, None)
    assert narrow == [h.NarrowLimits(ctx.limits)]  # never widened
    for stage, effect in (("pre_run", h.ModifyResult(Shaped({}))), ("pre_call", h.NarrowLimits(wide)),
                          ("post_tool", h.Deny("x")), ("post_run", h.ModifyArguments({})),
                          ("pre_tool", h.AttachCitation(()))):
        with pytest.raises(h.HookViolation):
            h.HookChain((h.Hook(stage, "x", lambda c, s, e=effect: e),)).run(stage, ctx, None)
    with pytest.raises(h.HookViolation, match="validator"):  # only the validator modifies arguments
        h.HookChain((h.Hook("pre_tool", "other", lambda c, s: h.ModifyArguments({})),)).run(
            "pre_tool", ctx, h.ToolCallInput(1, "t", None, {}))
    result = h.ToolResult(1, "t", {}, "d", Shaped({"a": 1}, data_class="metadata", source_datasets=(uuid4(),)))
    sources = result.shaped.source_datasets
    for bad in (Shaped({"a": 1, "holdout": 2}, data_class="metadata", source_datasets=sources),
                Shaped({"a": 1}, data_class="aggregates", source_datasets=sources),
                Shaped({"a": 1}, data_class="metadata")):  # holdout key / wider class / a dropped source
        with pytest.raises(h.HookViolation):
            h.HookChain((h.Hook("post_tool", "x", lambda c, s, b=bad: h.ModifyResult(b)),)).run(
                "post_tool", ctx, result)

    # Follow-up 3: ModifyResult only removes or bounds; it keeps aggregate paths and text partitions.
    agg = Shaped({"m": {"cv": {"auc": 0.81}}, "t": data_text("tenure", aggregate=True), "n": "note"},
                 data_class="aggregates", outcome_scope="cv", source_datasets=sources, aggregates=("m",))
    agg_result = h.ToolResult(1, "t", {}, "d", agg)
    for bad in (replace(agg, aggregates=()),  # the CV numbers would render as system metadata
                replace(agg, payload={**agg.payload, "moved": 0.81}),  # a new key
                replace(agg, payload={**agg.payload, "t": workspace_text("tenure")}),  # dataset -> workspace text
                replace(agg, payload={**agg.payload, "t": data_text("tenure")}),  # drops the aggregate flag
                replace(agg, payload={**agg.payload, "t": replace(agg.payload["t"], limit=agg.payload["t"].limit + 1)}),
                replace(agg, payload={**agg.payload, "m": {"cv": {"auc": 0.9}}})):  # a changed number
        with pytest.raises(h.HookViolation):
            h.HookChain((h.Hook("post_tool", "x", lambda c, s, b=bad: h.ModifyResult(b)),)).run(
                "post_tool", ctx, agg_result)
    bounded = replace(agg, payload={"m": {"cv": {}}, "t": data_text("ten", aggregate=True)})
    assert h.HookChain((h.Hook("post_tool", "x", lambda c, s: h.ModifyResult(bounded)),)).run(
        "post_tool", ctx, agg_result) == [h.ModifyResult(bounded)]

    def boom(c, s):
        raise RuntimeError("secret")

    chain = h.HookChain((h.Hook("pre_call", "boom", boom), h.Hook("pre_call", "later", lambda c, s: called.append(1))))
    assert chain.run("pre_call", ctx, None) == [h.Deny("hook_failed", "boom")] and called == []  # fail closed
    note = h.HookChain((h.Hook("pre_call", "n", lambda c, s: h.AddSystemNote("x" * 900)),)).run("pre_call", ctx, None)
    assert len(note[0].text) == h.SYSTEM_NOTE_MAX_CHARS


def test_each_hook_effect_in_a_run(hz):
    g, db = hz.g, hz.db
    stripped = []

    def bound_result(ctx, result):
        stripped.append(result.name)
        kept = {key: value for key, value in result.shaped.payload["experiment"].items() if key in ("id", "status")}
        stripped.append({"experiment": kept})
        return h.ModifyResult(replace(result.shaped, payload={"experiment": kept}))

    extra = (
        h.Hook("pre_call", "note", lambda c, s: h.AddSystemNote("cite CV only")),
        h.Hook("pre_tool", "no_findings", lambda c, s: h.Deny("policy_no_findings") if s.name == "get_findings" else None),
        h.Hook("post_tool", "bound", bound_result),
        h.Hook("post_tool", "cite", lambda c, s: h.AttachCitation((Citation(kind="model_version", id=g.mv[0]),))),
        h.Hook("post_run", "observer", lambda c, s: h.Observe({"observed": True})),
    )
    script = (("llm",), ("tool", "get_findings", {"experiment_id": str(g.exp[0])}),
              ("tool", "get_experiment", {"experiment_id": str(g.exp[0])}))
    result = hz.service(script, hooks=h.default_chain(extra)).run(db, hz.spec())
    assert result.status == "completed"
    assert "[harness note] cite CV only" in hz.fake.calls[0].input_json  # bounded untrusted note, never the prompt
    denied, read = hz.log[1], hz.log[2]
    assert (denied.status, denied.code) == ("denied", "policy_no_findings")
    assert stripped[0] == "get_experiment" and read.result_digest == digest(to_mcp(Shaped(stripped[1]))[1])
    assert set(stripped[1]["experiment"]) == {"id", "status"} and isinstance(stripped[1]["experiment"]["status"], Code)
    events = _events(db, result.run_id)
    assert next(e for e in events if e.type == "tool_call_denied").payload["hook"] == "no_findings"
    finished = events[-1].payload
    assert finished["observed"] is True and finished["eval_sample"] == {"proposal_ids": [], "llm_calls": 1}
    assert {"kind": "model_version", "id": str(g.mv[0])} in finished["citations"]
    rejected = hz.service(hooks=h.default_chain((h.Hook("post_run", "veto", lambda c, s: h.DenyOutput("vetoed")),))).run(
        db, hz.spec(subject_id=g.exp[1]))
    assert (rejected.status, rejected.error_code) == ("rejected_by_validator", "vetoed")


# --- authorization, capabilities, validators -------------------------------------------------------


def test_principal_capabilities_and_token_scopes(hz, setup):  # noqa: F811
    g, db = hz.g, hz.db
    viewer = create_user(db, email=f"viewer-{uuid4().hex[:6]}@test.invalid", password="test-password",
                         role=UserRole.VIEWER, workspace_id=g.ws)
    db.commit()
    refused = hz.service().run(db, hz.spec(user_id=viewer.id))
    assert (refused.status, refused.error_code) == ("failed", "ml_write_required")  # may propose: ML write
    run = db.get(AgentRun, refused.run_id)
    assert run.held_micros == 0 and run.budget_released_at is not None and hz.fake.calls == []
    write = ("tool", "record_decision", _record_args(g))
    read_only = hz.service((write, ("tool", "inspect_project", {"project_id": str(g.project.id)}), ("llm",))).run(
        db, hz.spec(user_id=viewer.id, may_propose=False, subject_id=g.exp[1]))
    assert read_only.status == "completed" and [o.code for o in hz.log[:2]] == ["ml_write_required", None]

    hz.log.clear()
    reader = _token(db, g, ["read"])
    as_token = hz.service((write, ("tool", "get_model", {"model_version_id": str(g.mv[0])}), ("llm",))).run(
        db, hz.spec(user_id=None, service_token_id=reader.id, may_propose=False, subject_id=g.exp[3]))
    assert as_token.status == "completed" and [o.code for o in hz.log[:2]] == ["insufficient_scope", None]
    run = db.get(AgentRun, as_token.run_id)
    assert (run.created_by_service_token_id, run.created_by_user_id) == (reader.id, None)
    hz.log.clear()
    proposer = _token(db, g, ["read", "decisions:propose"])
    proposed = hz.service((write, ("llm",))).run(db, hz.spec(user_id=None, service_token_id=proposer.id))
    assert proposed.status == "completed" and hz.log[0].status == "proposed"
    revoke_service_token(db, actor=g.actor, workspace_id=g.ws, token_id=proposer.id)
    db.commit()
    gone = hz.service().run(db, hz.spec(user_id=None, service_token_id=proposer.id, subject_id=g.exp[1]))
    assert (gone.status, gone.error_code, gone.run_id) == ("refused", "principal_inactive", None)
    for overrides, code in (({"subject_id": uuid4()}, "subject_not_found"),
                            ({"workspace_id": setup["beta"].id}, "forbidden"),
                            ({"kind": "assistant", "agent_key": "lead"}, "assistant_turns_unsupported")):
        assert hz.service().run(db, hz.spec(**overrides)).error_code == code


def test_tool_denials_and_rejected_by_validator_proposals(hz):
    g, db = hz.g, hz.db
    holdout = _record_args(g, evidence_refs=[{"kind": "candidate", "id": str(g.cand[1]), "scope": "final_holdout"}])
    script = (
        ("tool", "nope", {}), ("tool", "read_holdout", {}), ("tool", "accept_proposal", {"proposal_id": str(uuid4())}),
        ("tool", "record_decision", holdout),
        ("tool", "record_decision", _record_args(g, subject_id=str(uuid4()))),
        ("tool", "branch_experiment", {"experiment_id": str(g.exp[0]), "intent": "x",
                                       "changes": [{"kind": "dataset_swap"}]}),
        ("tool", "record_decision", {"project_id": str(g.project.id), "rationale": "promote mv1",
                                     "ref_moves": {"champion_model": str(g.mv[1]), "feature_recipe": str(g.fsv[1])},
                                     "evidence_refs": [{"kind": "experiment", "id": str(g.exp[1])}]}),
        ("tool", "get_experiment", {"experiment_id": "not-a-uuid"}), ("llm",),
    )
    result = hz.service(script).run(db, hz.spec())
    assert result.status == "completed"
    assert [(o.status, o.code) for o in hz.log[:8]] == [
        ("denied", "unknown_tool"), ("denied", "forbidden_operation"), ("denied", "tool_not_available"),
        ("rejected_by_validator", "holdout_not_allowed"), ("rejected_by_validator", "subject_not_found"),
        ("rejected_by_validator", "invalid_change_set"),
        ("rejected_by_validator", "champion_evidence_unavailable"),  # exp1 is not evidence-locked yet
        ("denied", "invalid_arguments")]
    rows = {row.id: row for row in db.scalars(select(AgentProposal).where(AgentProposal.run_id == result.run_id))}
    assert len(rows) == 4 and all(r.status == "rejected_by_validator" and r.validator_verdict == "rejected"
                                  for r in rows.values())
    holdout_row = rows[hz.log[3].proposal_id]
    assert set(holdout_row.tool_arguments) == {"argument_digest"} and "final_holdout" not in json.dumps(holdout_row.payload)
    # The champion move proposes once the model is eligible; DCLab, not the agent, attaches the evidence.
    _force_lock(db, g.exp[1])
    hz.log.clear()
    ok = hz.service(script[6:7] + (("llm",),)).run(db, hz.spec(subject_id=g.exp[1]))
    [row] = db.scalars(select(AgentProposal).where(AgentProposal.run_id == ok.run_id))
    assert (row.status, row.payload["server_attaches"]) == ("proposed", ["champion_final_evaluation"])
    assert not HOLDOUT_KEY.search(json.dumps(row.payload))
    limited = hz.service((("tool", "inspect_project", {}), ("tool", "inspect_project", {}), ("llm",))).run(
        db, hz.spec(subject_id=g.exp[3], limits=RunLimits(steps=8, tokens=60000, wall_s=120, cost_micros=250_000,
                                                          tool_calls=1)))
    assert limited.status == "completed" and hz.log[-2].code == "tool_call_limit"


# --- budget, switches, limits, failures ------------------------------------------------------------


def test_switches_budget_limits_and_crashes_end_through_release(hz):
    g, db = hz.g, hz.db
    hz.settings.ai_enabled = False
    off = hz.service().run(db, hz.spec())
    assert (off.status, off.error_code) == ("failed", "kill_switch") and hz.fake.calls == []
    hz.settings.ai_enabled = True
    flip_off(db, workspace_id=g.ws, switch_key="all_ai", reason="incident drill", actor=g.actor)
    db.commit()
    assert hz.service().run(db, hz.spec(subject_id=g.exp[1])).error_code == "kill_switch"
    db.execute(text("TRUNCATE ai_switches CASCADE"))
    seed_platform_governance(db, environment="test")
    db.commit()
    # Most of the workspace month is held elsewhere: the run's hold is refused.
    hold = hz.gateway.reserve(db, workspace_id=g.ws, estimate_micros=24_900_000)
    over = hz.service().run(db, hz.spec(subject_id=g.exp[3]))
    assert (over.status, over.error_code) == ("over_budget", "budget_exhausted")
    assert "budget_exhausted" in _types(db, over.run_id) and db.get(AgentRun, over.run_id).held_micros == 0
    hz.gateway.release(db, hold)
    steps = hz.service((("llm",), ("llm",), ("tool", "inspect_project", {}))).run(
        db, hz.spec(limits=RunLimits(steps=1, tokens=60000, wall_s=120, cost_micros=250_000, tool_calls=20)))
    assert (steps.status, steps.error_code) == ("over_budget", "step_limit")
    assert [getattr(o, "code", None) or (o.refusal and o.refusal.code) for o in hz.log[-2:]] == [
        "budget_exhausted", "run_stopped"]
    crash = hz.service((("llm",), ("raise",))).run(db, hz.spec(subject_id=g.exp[1]))
    assert (crash.status, crash.error_code) == ("failed", "internal_error")
    run = db.get(AgentRun, crash.run_id)
    assert run.budget_released_at is not None and run.cost_micros > 0
    assert all(row.reserved_micros == 0 for row in db.scalars(select(WorkspaceLlmBudget).where(
        WorkspaceLlmBudget.workspace_id == g.ws)))
    assert db.scalar(select(func.count()).select_from(AgentRun).where(
        AgentRun.workspace_id == g.ws, AgentRun.status.in_(("queued", "running")))) == 0


# --- replay ----------------------------------------------------------------------------------------


def test_replay_equality_and_mismatch_opens_an_incident(hz):
    g, db = hz.g, hz.db
    script = (("llm",), ("tool", "get_experiment", {"experiment_id": str(g.exp[0])}),
              ("tool", "record_decision", _record_args(g)), ("tool", "nope", {}), ("llm",))
    result = hz.service(script).run(db, hz.spec())
    ledger = db.scalar(select(func.count()).select_from(LlmInvocation))
    proposals = db.scalar(select(func.count()).select_from(AgentProposal))
    provider_calls = len(hz.fake.calls)  # the second model call may be a cache hit
    same = replay(db, workspace_id=g.ws, run_id=result.run_id, actor=g.actor, service=hz.service(script))
    assert same.equal and same.mismatches == () and same.incident_id is None
    assert same.output_digest == _events(db, result.run_id)[-2].payload["output_digest"]
    assert [name for name, _ in same.tool_sequence] == ["get_experiment", "record_decision", "nope"]
    assert db.scalar(select(func.count()).select_from(LlmInvocation)) == ledger  # no live call, no ledger row
    assert db.scalar(select(func.count()).select_from(AgentProposal)) == proposals  # write tools stubbed
    assert len(hz.fake.calls) == provider_calls  # the record feeds the model: nothing leaves the process
    checked = _events(db, result.run_id)[-1]
    assert checked.type == "replay_checked" and checked.payload["equal"] is True
    changed = script[:2] + (("tool", "get_experiment", {"experiment_id": str(g.exp[1])}),) + script[3:]
    diff = replay(db, workspace_id=g.ws, run_id=result.run_id, actor=g.actor, service=hz.service(changed))
    assert not diff.equal and "tool_sequence" in diff.mismatches and "proposal_payloads" in diff.mismatches
    incident = db.get(AiIncident, diff.incident_id)
    assert (incident.kind, incident.subject_key, incident.status) == ("replay_mismatch", "harness_fixture", "open")
    again = replay(db, workspace_id=g.ws, run_id=result.run_id, actor=g.actor, service=hz.service(changed))
    assert again.incident_id == diff.incident_id  # one open incident per run, not one per replay
    assert db.scalar(select(func.count()).select_from(AiIncident).where(AiIncident.kind == "replay_mismatch")) == 1
    viewer = create_user(db, email=f"v-{uuid4().hex[:6]}@test.invalid", password="test-password",
                         role=UserRole.VIEWER, workspace_id=g.ws)
    db.commit()
    with pytest.raises(ReplayRefused, match="forbidden"):
        replay(db, workspace_id=g.ws, run_id=result.run_id, actor=viewer, service=hz.service(script))


# --- the agents.run job ----------------------------------------------------------------------------


def test_agents_run_job_reauthorizes_heartbeats_and_syncs(hz, monkeypatch):
    g, db = hz.g, hz.db
    monkeypatch.setattr(svc, "GatewayService", lambda: hz.gateway)
    monkeypatch.setattr(svc, "get_settings", lambda: hz.settings)
    monkeypatch.setitem(svc.RUNTIMES, "fake", lambda _spec: Scripted((("llm",),), hz.log))
    run_id = AgentService().submit(db, hz.spec())
    job = db.scalar(select(MlJob).where(MlJob.target_id == run_id))
    assert (job.job_type, job.handler_key, job.handler_version, job.payload, job.max_attempts) == (
        "agent_run", "agents.run", "1", {"agent_run_id": str(run_id)}, 1)
    assert db.get(AgentRun, run_id).status == "queued"
    assert process_next_job(db, job_id=job.id, claimed_by="harness-test").status == "completed"
    db.expire_all()
    assert db.get(AgentRun, run_id).status == "completed" and job.heartbeat_at is not None

    # Cancelled mid-run: the heartbeat after the model call stops it; the hold is released.
    def cancel(_session):
        db.execute(update(MlJob).where(MlJob.target_id == cancelled_id).values(cancel_requested_at=datetime.now(UTC)))
        db.commit()

    monkeypatch.setitem(svc.RUNTIMES, "fake", lambda _spec: Scripted((cancel, ("llm",), ("llm",)), hz.log))
    cancelled_id = AgentService().submit(db, hz.spec(subject_id=g.exp[1]))
    cancelled_job = db.scalar(select(MlJob).where(MlJob.target_id == cancelled_id))
    assert process_next_job(db, job_id=cancelled_job.id, claimed_by="harness-test").status == "cancelled"
    db.expire_all()
    run = db.get(AgentRun, cancelled_id)
    assert (run.status, run.error_code, run.usage["calls"]) == ("cancelled", "cancelled", 1)
    assert run.budget_released_at is not None

    # A tampered payload fails the job; the terminal sync ends the run.
    tampered_id = AgentService().submit(db, hz.spec(subject_id=g.exp[3]))
    tampered = db.scalar(select(MlJob).where(MlJob.target_id == tampered_id))
    tampered.payload = {"agent_run_id": str(tampered_id), "extra": 1}
    db.commit()
    assert process_next_job(db, job_id=tampered.id, claimed_by="harness-test").status == "failed"
    db.expire_all()
    assert (db.get(AgentRun, tampered_id).status, db.get(AgentRun, tampered_id).error_code) == ("failed", "job_failed")
    # A run a dead worker left running is never resumed.
    stale = agent_run(db, SimpleNamespace(ws_a=g.ws, project_a=g.project.id), status="running", agent_key="other")
    db.commit()
    assert svc.spec_for_job(db, SimpleNamespace(payload={"agent_run_id": str(stale)}, target_id=stale,
                                                workspace_id=g.ws)) is None
    db.expire_all()
    assert (db.get(AgentRun, stale).status, db.get(AgentRun, stale).error_code) == ("failed", "worker_abandoned")


# --- 0073: release marker and terminal statuses ---------------------------------------------------


def _rejects(db, match, sql, **params):
    with pytest.raises(DBAPIError, match=match):
        db.execute(text(sql), params)
        db.commit()
    db.rollback()


def test_terminal_status_is_final_and_the_release_marker_write_once(db_session, agents):  # noqa: F811
    db, ns = db_session, agents
    live = agent_run(db, ns, status="running")
    db.commit()
    _rejects(db, "ck_agent_runs_released_terminal", "UPDATE agent_runs SET budget_released_at = now() WHERE id = :id",
             id=live)
    # 0074: a live run ends only through the release (the marker in the same UPDATE).
    for status in ("completed", "failed", "cancelled"):
        _rejects(db, "ends only through the budget release", f"UPDATE agent_runs SET status = '{status}' WHERE id = :id",
                 id=live)
    db.execute(text("UPDATE agent_runs SET status = 'completed', budget_released_at = now() WHERE id = :id"), {"id": live})
    db.commit()
    for assignment, match in (("status = 'running'", "is final"), ("status = 'failed'", "is final"),
                              ("budget_released_at = now() + interval '1 day'", "write-once"),
                              ("budget_released_at = NULL", "write-once")):
        _rejects(db, match, f"UPDATE agent_runs SET {assignment} WHERE id = :id", id=live)
    db.execute(text("UPDATE agent_runs SET usage = '{\"steps\": 2}'::jsonb WHERE id = :id"), {"id": live})
    db.commit()  # other mutable columns stay writable
    _rejects(db, "is final", "UPDATE agent_runs SET status = 'queued' WHERE id = :id", id=ns.run_a)
    assert budget.release_run(db, workspace_id=ns.ws_a, agent_run_id=live, final_status="failed") == 0


# --- CI rules (ADR 0009 §1 b-d) --------------------------------------------------------------------


def _modules() -> dict[str, ast.Module]:
    return {str(path.relative_to(APP.parent)): ast.parse(path.read_text(encoding="utf-8"))
            for path in APP.rglob("*.py")}


def _imports(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if called in ("import_module", "__import__"):
                names.add(str(node.args[0].value))
    return names


def _under(name: str, root: str) -> bool:
    return name == root or name.startswith(root + ".")


# CI rule b (ADR 0009 §1; P6.3-A): NOOA's code-executing, tool, tracing and provider surfaces.
NOOA_BANNED_MODULES = ("nooa.strategies.codeact", "nooa.strategies.codeact_lite", "nooa.strategies.reflexion",
                       "nooa.strategies.pure_python", "nooa.experimental", "nooa.runtime.sandbox", "nooa.cli",
                       "nooa.tools", "nooa.mcp", "nooa.viewer", "nooa.trace_explorer", "nooa.tracing",
                       "nooa.unifiedllm")
NOOA_BANNED_NAMES = frozenset({"CodeActStrategy", "CodeActLiteStrategy", "ReflexionStrategy", "PurePythonStrategy",
                               "CodeActConfig", "set_default_strategy", "enable_tracing"})


def nooa_violations(tree: ast.Module) -> list[str]:
    names = _imports(tree)
    found = [n for n in sorted(names) if any(_under(n, b) for b in NOOA_BANNED_MODULES)
             or n.rsplit(".", 1)[-1] in NOOA_BANNED_NAMES]
    for node in ast.walk(tree):  # attribute use (nooa.CodeActStrategy), bare names and getattr strings
        used = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else (
            node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None)
        if used in NOOA_BANNED_NAMES or (isinstance(used, str) and any(_under(used, b) for b in NOOA_BANNED_MODULES)):
            found.append(used)
    return found


def test_nooa_boundary_scan_catches_planted_violations():
    planted = {
        "from nooa.strategies.codeact import CodeActStrategy": ["nooa.strategies.codeact",
                                                                "nooa.strategies.codeact.CodeActStrategy"],
        "from nooa import CodeActStrategy": ["nooa.CodeActStrategy"],
        "import nooa\nnooa.CodeActStrategy()": ["CodeActStrategy"],
        "import nooa.cli": ["nooa.cli"],
        "import importlib\nimportlib.import_module('nooa.runtime.sandbox')": ["nooa.runtime.sandbox"],
        "import nooa\ngetattr(nooa, 'set_default_strategy')": ["set_default_strategy"],
        "from nooa.tracing import enable_tracing": ["nooa.tracing", "nooa.tracing.enable_tracing"],
        "from nooa.strategies import codeact_lite": ["nooa.strategies.codeact_lite"],
        "from nooa import PredictStrategy, strategy": [],
    }
    for source, expected in planted.items():
        assert sorted(set(nooa_violations(ast.parse(source)))) >= sorted(set(expected)), source
        assert bool(nooa_violations(ast.parse(source))) == bool(expected), source


def test_nooa_and_runtime_imports_follow_the_ci_rules():
    for path, tree in _modules().items():
        names = _imports(tree)
        banned = nooa_violations(tree)
        assert not banned, (path, banned)
        if any(_under(n, "nooa") for n in names):
            assert path == "app/agents/runtime/nooa_runtime.py", path
        inside = path.startswith(("app/agents/harness/", "app/agents/runtime/", "app/agents/classes/",
                                  "app/agents/lead/"))  # P6.3-B: the lead loop is a runtime too
        if not inside:
            assert not [n for n in names if _under(n, "app.agents.runtime") or _under(n, "app.agents.classes")
                        or _under(n, "app.agents.lead")], path


ALLOWED_AGENT_SERVICE = {"app/agents/harness/service.py", "app/agents/harness/replay.py",
                         "app/services/job_handlers.py"}
ALLOWED_RUN_CALLERS = {"app/services/job_handlers.py", "app/agents/assistant/service.py"}


def test_agent_service_run_is_called_only_by_the_job_handler():
    for path, tree in _modules().items():
        referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for alias in node.names}
        if "AgentService" in referenced:
            assert path in ALLOWED_AGENT_SERVICE | ALLOWED_RUN_CALLERS, path
        holders = {target.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(n, ast.Name) and n.id == "AgentService" for n in ast.walk(node.value))
                   for target in node.targets if isinstance(target, ast.Name)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run":
                owner = node.func.value
                direct = isinstance(owner, ast.Call) and getattr(owner.func, "id", "") == "AgentService"
                held = isinstance(owner, ast.Name) and owner.id in holders
                if direct or held:
                    assert path in ALLOWED_RUN_CALLERS, path
        if "HANDLER_AGENTS_RUN" in referenced:
            assert path in {"app/domain/ml_jobs.py", "app/services/job_handlers.py", "app/services/ml_job_service.py",
                            "app/agents/harness/service.py"}, path
    handlers = (APP / "services" / "job_handlers.py").read_text(encoding="utf-8")
    assert re.search(r"AgentService\(\)\.run\(db, spec, heartbeat=on_heartbeat\)", handlers)


# --- P6.10-A follow-ups (independent security review of A2) --------------------------------------


def test_a_refused_caller_never_ends_another_principals_run(hz):
    g, db = hz.g, hz.db
    queued = hz.service().submit(db, hz.spec())  # the human's queued run
    other = _token(db, g, ["read", "decisions:propose"])
    mismatch = hz.service().run(db, hz.spec(run_id=queued, user_id=None, service_token_id=other.id))
    assert (mismatch.status, mismatch.error_code) == ("failed", "principal_mismatch")
    revoke_service_token(db, actor=g.actor, workspace_id=g.ws, token_id=other.id)
    db.commit()
    refused = hz.service().run(db, hz.spec(run_id=queued, user_id=None, service_token_id=other.id))
    assert refused.error_code == "principal_inactive"
    db.expire_all()
    run = db.get(AgentRun, queued)
    assert (run.status, run.budget_released_at) == ("queued", None)  # untouched by either refusal
    # The owner's own refused spec (the job path: the row's principal) does end its run.
    owned = hz.service().run(db, hz.spec(run_id=queued, kind="assistant", agent_key="lead"))
    assert owned.error_code == "assistant_turns_unsupported"
    db.expire_all()
    assert db.get(AgentRun, queued).status == "failed"


def test_previews_redacted_levels_use_the_run_model_and_limits_renarrow(hz, monkeypatch):
    g, db = hz.g, hz.db
    seen = {}

    def level(db_, workspace_id, key, answer_kind=None, prompt_release_id=None, model_id=None):
        seen.update(prompt_release_id=prompt_release_id, model_id=model_id)
        return 1

    monkeypatch.setattr(svc, "effective_level", level)
    draft = ProposalDraft(proposal_type="ExperimentReviewProposal", decision_point_key="experiment.review",
                          payload={"verdict": "keep"})
    result = hz.service((("llm",),), final=RuntimeOutput(output=HarnessStep(**OK), proposal=draft)).run(
        db, hz.spec(kind="specialist", tool_surface=None, decision_point_key="experiment.review"))
    [row] = db.scalars(select(AgentProposal).where(AgentProposal.run_id == result.run_id))
    assert seen == {"prompt_release_id": hz.release, "model_id": "gpt-6.1-sol"}
    assert (row.status, row.level_at_proposal) == ("proposed", 1)
    run = db.get(AgentRun, result.run_id)
    assert (run.provider, run.model) == ("fake", "gpt-6.1-sol")

    # Previews: only what the gateway lets through (dataset labels deny here) is stored.
    read = hz.service((("tool", "get_experiment", {"experiment_id": str(g.exp[1])}), ("llm",))).run(
        db, hz.spec(subject_id=g.exp[1]))
    finished = next(e for e in _events(db, read.run_id) if e.type == "tool_call_finished").payload
    dataset_texts = [u.untrusted_text for item in hz.log[-2].fields if any(s.kind == "dataset" for s in item.sources)
                     for u in _untrusted(item.value)]
    assert finished["preview_dropped"] > 0 and dataset_texts
    assert not any(text_ in finished["preview"] for text_ in dataset_texts if len(text_) > 3)

    # Limits are re-narrowed by the policy in force when the run executes.
    calls = []

    def limits(policy, kind):
        calls.append(kind)
        base = svc.RunLimits(steps=8, tokens=60000, wall_s=120, cost_micros=250_000, tool_calls=20)
        return base if len(calls) == 1 else base.model_copy(update={"tool_calls": 0})

    monkeypatch.setattr(svc, "policy_limits", limits)
    narrowed = hz.service((("tool", "inspect_project", {}), ("llm",))).run(db, hz.spec(subject_id=g.exp[3]))
    assert db.get(AgentRun, narrowed.run_id).limits["tool_calls"] == 20  # frozen row: as queued
    assert _events(db, narrowed.run_id)[0].payload["limits"]["tool_calls"] == 0
    assert hz.log[-2].code == "tool_call_limit"


def _untrusted(value) -> list:
    if hasattr(value, "untrusted_text"):
        return [value]
    if isinstance(value, dict):
        return [hit for item in value.values() for hit in _untrusted(item)]
    if isinstance(value, (list, tuple)):
        return [hit for item in value for hit in _untrusted(item)]
    return []
