"""P6.10-B chaos cases: provider timeout, breaker open, over budget (the real budget path),
invalid output and a kill switch flipped mid-run, each against the auto-train Jev decision
points, an Experiment Critic run and an assistant turn. Every case ends in the rule fallback
and equals the AI-off run: the Jev points use the rule values (at L2, where the no-fault
control shows the fake's answer IS applied), the Critic proposes nothing (the control
proposes), the turn ends in a fixed template with no proposal (the control proposes a spec);
product state is unchanged; the ledger, harness record and request-body properties hold."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from ai_harness import CHAOS_KINDS, Chaos, check_harness_records, check_ledger, check_no_raw_rows
from ai_harness.scenarios import (
    LEAD_TEXT,
    sentinels,
    TIGHT,
    columns,
    gateway_for,
    graph_state,
    jev_script,
    lead_steps,
    plant_canaries,
    run_jev,
    run_lead,
    run_specialist,
    specialist_answer,
)
from app.agents.assistant import templates
from app.agents.classes import experiment_critic as critic
from app.agents.gateway.limits import GatewayLimits
from app.agents.gateway.providers.fake import FakeProvider
from app.agents.governance.switches import flip_off
from app.agents.harness.recorder import load_events
from app.agents.harness.service import enqueue_experiment_review
from app.agents.lead import runtime as lead_runtime
from app.agents.lead.fake_driver import ScriptedLeadDriver
from app.agents.tools.catalog import ToolContext
from app.db.models import AgentProposal, AgentRun
from test_agent_classes import ON

pytestmark = pytest.mark.ai_harness

REFUSAL = {"provider_timeout": "timeout", "breaker_open": "breaker_open", "over_budget": "budget_exhausted",
           "invalid_output": "invalid_output", "kill_switch_mid_run": "kill_switch"}
# The lead's fallback per fault (breaker_open and invalid_output turns end ``completed``, the
# others over_budget / timed_out / failed; all with no proposal).
TEMPLATE = {"provider_timeout": lead_runtime.AT_LIMIT, "over_budget": lead_runtime.AT_LIMIT,
            "breaker_open": lead_runtime.UNAVAILABLE, "kill_switch_mid_run": lead_runtime.UNAVAILABLE,
            "invalid_output": lead_runtime.NOT_VERIFIED}


def flipper(db, workspace_id):
    """Flip the workspace's ``all_ai`` switch from another session (an operator, mid-run)."""

    def flip() -> None:
        with Session(bind=db.get_bind()) as session:
            flip_off(session, workspace_id=workspace_id, switch_key="all_ai", reason="chaos",
                     actor_rule="test.switch.v1")
            session.commit()

    return flip


def _arm(chaos: Chaos, gateway, limits: GatewayLimits, *, provider: str, workspace_id) -> dict:
    if chaos.kind == "breaker_open":
        chaos.trip(limits, provider=provider, workspace_id=workspace_id)
    chaos.wrap(gateway)
    return {"limits": dict(TIGHT)} if chaos.kind == "over_budget" else {}


def _events(db, result, kind):
    db.expire_all()
    run = db.get(AgentRun, result.run_id)
    return [e.payload for e in load_events(db, workspace_id=run.workspace_id, run_id=run.id) if e.type == kind]


def _ai_off_jev(hk):  # through run_jev: the same (canary) training frame as the AI runs
    return _jev_values(run_jev(hk, None))


def _jev_values(values):
    return (values["numeric"], values["categorical"], values["applied"]), values["leakage"]


# --- no-fault controls: a fault-free run differs from AI off, so no chaos case passes trivially ---


def test_controls_without_a_fault_differ_from_the_ai_off_run(request):
    hk = request.getfixturevalue("hk")
    off = _ai_off_jev(hk)
    provider = FakeProvider(handler=jev_script(), environment="test")
    applied = run_jev(hk, provider, 2)
    # Only column.semantic_role can apply at L2 (identifier and leakage are capped at L1): the
    # equality in the chaos cases has teeth for that point; the others are pinned by refusals.
    assert _jev_values(applied) != off and applied["categorical"] == ["feature"]
    assert {e["evidence_partition"] for e in applied["ctx"].events} == {"train"}
    check_no_raw_rows(provider.calls, columns=columns(hk.db, hk.ws_a), **sentinels(hk.db, hk.ws_a))


def test_critic_control_without_a_fault_proposes(ac):
    provider = FakeProvider(handler=lambda _c: specialist_answer(ac, critic.AGENT_KEY), environment="test")
    reviewed = run_specialist(ac, critic.AGENT_KEY, gateway_for(provider, ON))
    assert (reviewed.status, len(reviewed.proposal_ids)) == ("completed", 1)


def test_turn_control_without_a_fault_proposes(lead):
    plant_canaries(lead, lead.db, lead.g.ws)
    off = graph_state(lead.db, lead.g.ws)
    turn = run_lead(lead, ScriptedLeadDriver(lead_steps(lead.g)))
    assert (turn.status, len(turn.proposal_ids)) == ("completed", 1) and graph_state(lead.db, lead.g.ws) != off


# --- chaos ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", CHAOS_KINDS)
def test_jev_decision_points_fall_back_to_the_rule(hk, kind):
    off = _ai_off_jev(hk)  # the AI-off run
    chaos = Chaos(kind, jev_script(), flip=flipper(hk.db, hk.ws_a), flip_at=2)
    provider = FakeProvider(handler=chaos.handler, environment="test")
    service = hk.service(providers={"openai": hk.fake, "typesafe": provider})
    _arm(chaos, service, hk.limits, provider="typesafe", workspace_id=hk.ws_a)
    if kind == "over_budget":
        Chaos.exhaust_budget(service, hk.db, hk.ws_a)
    values = run_jev(hk, provider, 2, service=service)  # L2: the control shows the answer would be applied
    assert _jev_values(values) == off
    refusals = {a.refusal for r in values["ctx"].decisions.resolved.values() for a in r.answers} - {None}
    assert REFUSAL[kind] in refusals, refusals
    assert {e["evidence_partition"] for e in values["ctx"].events} == {"train"}
    decisions = values["ctx"].decisions
    assert decisions.fingerprint_digest == decisions.snapshot.fingerprint_digest  # no AI value applied
    if kind == "kill_switch_mid_run":  # the first point was answered before the flip
        assert len(provider.calls) == 1
    check_ledger(hk.db, provider.calls)
    check_no_raw_rows(provider.calls, columns=columns(hk.db, hk.ws_a), **sentinels(hk.db, hk.ws_a))


@pytest.mark.parametrize("kind", CHAOS_KINDS)
def test_a_critic_run_falls_back_to_no_review(ac, kind):
    g, db = ac.g, ac.db
    plant_canaries(ac, db, g.ws)
    assert enqueue_experiment_review(db, experiment_id=g.exp[1], user_id=g.actor.id,
                                     settings=SimpleNamespace(ai_enabled=False, dclab_env="test")) is None
    off = graph_state(db, g.ws)  # AI off: no review, nothing written
    chaos = Chaos(kind, lambda _call: specialist_answer(ac, critic.AGENT_KEY), flip=flipper(db, g.ws))
    provider = FakeProvider(handler=chaos.handler, environment="test")
    limits = GatewayLimits()
    gateway = gateway_for(provider, ON, limits=limits)
    result = run_specialist(ac, critic.AGENT_KEY, gateway, **_arm(chaos, gateway, limits, provider="openai",
                                                                   workspace_id=g.ws))
    assert result.proposal_ids == () and result.status != "completed", result
    assert [p["refusal"] for p in _events(db, result, "llm_call_finished")] == [REFUSAL[kind]]
    assert graph_state(db, g.ws) == off
    check_harness_records(db, [result], provider.calls)
    check_ledger(db, provider.calls)
    check_no_raw_rows(provider.calls, columns=columns(db, g.ws), **sentinels(db, g.ws))


@pytest.mark.parametrize("kind", CHAOS_KINDS)
def test_an_assistant_turn_falls_back_to_a_template(lead, kind):
    g, db = lead.g, lead.db
    plant_canaries(lead, db, g.ws)
    templated = templates.reply(ToolContext(db=db, actor=g.actor, workspace_id=g.ws), g.project.id, LEAD_TEXT)
    off = graph_state(db, g.ws)  # the AI-off turn: a template, no run, no model, nothing proposed
    chaos = Chaos(kind, ScriptedLeadDriver(lead_steps(g)), flip=flipper(db, g.ws), flip_at=2)  # read, propose, answer
    limits = GatewayLimits()
    gateway = gateway_for(lead.fake, lead.settings, limits=limits)
    result = run_lead(lead, chaos.handler, gateway, **_arm(chaos, gateway, limits, provider="openai",
                                                           workspace_id=g.ws))
    [said] = _events(db, result, "assistant_message")
    # Both are deterministic templates without citations; the texts differ by design (AI off offers
    # the quick-action menu, a failed turn says it could not finish).
    assert said["message"] == TEMPLATE[kind] and said["fallback"] and said["citations"] == templated["citations"] == []
    assert REFUSAL[kind] in [p["refusal"] for p in _events(db, result, "llm_call_finished")]
    assert result.proposal_ids == () and graph_state(db, g.ws) == off  # the control proposes a spec
    check_harness_records(db, [result], lead.fake.calls)
    check_ledger(db, lead.fake.calls)
    check_no_raw_rows(lead.fake.calls, columns=columns(db, g.ws), **sentinels(db, g.ws))


# --- a fault after a write step ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["kill_switch_mid_run", "invalid_output"])
def test_a_fault_after_a_proposing_step_keeps_the_pending_proposal_and_applies_nothing(lead, kind):
    """Policy: an L1 proposal created before the fault stays ``proposed`` for a person to decide
    (P6.6-A); nothing is applied — no spec, no run, the graph's evidence unchanged."""

    g, db = lead.g, lead.db
    plant_canaries(lead, db, g.ws)
    off = graph_state(db, g.ws)
    steps = lead_steps(g)[1:]  # propose_problem_spec, then the answer (call 2) meets the fault
    valid = ScriptedLeadDriver(steps)
    chaos = Chaos(kind, valid, flip=flipper(db, g.ws), flip_at=2)
    handler = chaos.handler if kind == "kill_switch_mid_run" else (
        lambda call, calls=[]: calls.append(1) or (valid(call) if len(calls) == 1 else dict(chaos.invalid)))
    gateway = gateway_for(lead.fake, lead.settings)
    chaos.wrap(gateway)
    result = run_lead(lead, handler, gateway)
    [said] = _events(db, result, "assistant_message")
    assert said["message"] == TEMPLATE[kind] and len(result.proposal_ids) == 1
    after = graph_state(db, g.ws)
    assert after["AgentProposal"] == off["AgentProposal"] + 1  # pending, never applied
    assert db.get(AgentProposal, result.proposal_ids[0]).status == "proposed"
    check_no_raw_rows(lead.fake.calls, columns=columns(db, g.ws), **sentinels(db, g.ws))
    assert {k: v for k, v in after.items() if k != "AgentProposal"} == {
        k: v for k, v in off.items() if k != "AgentProposal"}
    check_harness_records(db, [result], lead.fake.calls)
    check_ledger(db, lead.fake.calls)
