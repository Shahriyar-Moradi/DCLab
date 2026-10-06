"""P6.10-B: every recorded fixture replayed through the real gateway path (harness, gateway,
fake provider) to its golden transcript — the three specialist classes, the lead agent and
the Jev decision points — with the runtime properties checked on each run: no raw rows or
secrets in any request body, one complete ledger row per model call, a harness record per
agent run. A fixture recorded under another prompt release, output schema or model is
rejected, never reused. Regenerate: ``DCLAB_RECORD_AI=1 DCLAB_UPDATE_GOLDEN=1 make test-ai``."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from ai_harness import (
    Ids,
    Scenario,
    answers,
    assert_golden,
    check_harness_records,
    check_ledger,
    check_no_raw_rows,
    fixture_cases,
    ledger_summary,
    run_transcript,
)
from ai_harness.kit import _SECRETS, GOLDENS_ROOT, fixture_path, numbers_in, stamp
from ai_harness.scenarios import (
    CANARY_CELL,
    CANARY_FILE_ROW,
    CANARY_HOLDOUT_METRICS,
    CANARY_HOLDOUT_SUMMARY,
    sentinels,
    SPECIALIST_AGENTS,
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
from app.agents.lead.fake_driver import ScriptedLeadDriver
from app.db.models import LlmInvocation, SemanticDecisionAnswer
from test_agent_classes import ON

pytestmark = pytest.mark.ai_harness

SCENARIOS = ("experiment_critic/needs_work", "dataset_investigator/default", "experiment_planner/default",
             "lead/spec_turn", "jev/wrong_at_l0")
EXPECTED = {"experiment_critic": ("completed", 1), "dataset_investigator": ("completed", 1),
            "experiment_planner": ("completed", 1), "lead": ("completed", 1)}
# Holdout values and counts of the seeded graphs and their canaries (a golden may hold neither).
HOLDOUT_STATIC = (0.7001, 0.81, 0.83, *CANARY_HOLDOUT_METRICS.values(), CANARY_FILE_ROW, 1237, 812, 425)
ANSWER_FIELDS = ("purpose", "question_key", "rule_answer", "answer", "agreement", "level", "policy_outcome",
                 "value_used", "evidence_partition", "cache_hit")


def play(name: str, request, scenario: Scenario | None = None) -> SimpleNamespace:
    agent, case = name.split("/")
    if agent in SPECIALIST_AGENTS:
        ns = request.getfixturevalue("ac")
        scenario = scenario or Scenario(agent, case, script=answers(specialist_answer(ns, agent)))
        result = run_specialist(ns, agent, gateway_for(scenario.provider(), ON))
        return SimpleNamespace(db=ns.db, ws=ns.g.ws, scenario=scenario, results=[result], 
                               transcript=lambda: run_transcript(ns.db, result))
    if agent == "lead":
        ns = request.getfixturevalue("lead")
        scenario = scenario or Scenario(agent, case, script=ScriptedLeadDriver(lead_steps(ns.g)))
        result = run_lead(ns, scenario)
        return SimpleNamespace(db=ns.db, ws=ns.g.ws, scenario=scenario, results=[result], 
                               transcript=lambda: run_transcript(ns.db, result))
    ns = request.getfixturevalue("hk")
    scenario = scenario or Scenario(agent, case, script=jev_script())
    values = run_jev(ns, scenario.provider(), 0)

    def transcript():
        ns.db.expire_all()
        rows = ns.db.scalars(select(SemanticDecisionAnswer).where(SemanticDecisionAnswer.workspace_id == ns.ws_a)
                             .order_by(SemanticDecisionAnswer.purpose, SemanticDecisionAnswer.question_key))
        ledger = ns.db.scalars(select(LlmInvocation).where(LlmInvocation.workspace_id == ns.ws_a)
                               .order_by(LlmInvocation.purpose))
        return Ids().norm({"values": {k: values[k] for k in ("numeric", "categorical", "applied", "leakage")},
                           "events": values["ctx"].events,
                           "answers": [{k: getattr(r, k) for k in ANSWER_FIELDS} for r in rows],
                           "ledger": [ledger_summary(r) for r in ledger]})

    return SimpleNamespace(db=ns.db, ws=ns.ws_a, scenario=scenario, results=[], transcript=transcript,
                           values=values)


def test_every_fixture_has_a_scenario_and_fixtures_and_goldens_are_redacted():
    assert set(fixture_cases()) == {tuple(name.split("/")) for name in SCENARIOS}
    goldens = sorted(GOLDENS_ROOT.glob("*.json"))
    assert {path.stem for path in goldens} == {name.replace("/", "__") for name in SCENARIOS}
    for path in [fixture_path(agent, case) for agent, case in fixture_cases()] + goldens:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"holdout|hold-out|final_test", text, re.IGNORECASE), path.name
        assert not numbers_in(text) & set(HOLDOUT_STATIC), path.name
        assert CANARY_CELL not in text, path.name
        assert not any(pattern.search(text) for pattern in _SECRETS), path.name
        assert len(text) < 20_000, path.name  # digests, outputs and event summaries only: no payload


@pytest.mark.parametrize("name", SCENARIOS)
def test_recorded_scenario_replays_to_its_golden(name, request):
    run = play(name, request)
    run.scenario.finish()  # replay: every call matched a fixture and every exchange was used
    agent = name.split("/")[0]
    if agent in EXPECTED:
        [result] = run.results
        assert (result.status, len(result.proposal_ids)) == EXPECTED[agent], result
    else:  # a deliberately wrong Jev at L0 is logged beside the rule and never used
        assert (run.values["numeric"], run.values["categorical"], run.values["applied"]) == (["feature"], [], [])
        assert {used for _key, used in run.values["leakage"]} == {"clear"}
    check_no_raw_rows(run.scenario.calls, columns=columns(run.db, run.ws), **sentinels(run.db, run.ws))
    check_ledger(run.db, run.scenario.calls)
    check_harness_records(run.db, run.results, run.scenario.calls)
    assert_golden(name.replace("/", "__"), run.transcript())


@pytest.mark.parametrize("field, value, reason", [
    ("instructions_digest", "0" * 64, "prompt_release_changed"),
    ("output_schema_digest", "1" * 64, "output_schema_changed"),
    ("model", "gpt-6-luna", "model_changed"),
])
def test_a_fixture_from_another_release_schema_or_model_is_rejected(request, field, value, reason):
    scenario = Scenario("experiment_critic", "needs_work", record=False)
    stale = {**scenario.exchanges[0], field: value}
    scenario.exchanges = [{**stale, "input_digest": stamp(stale)}]  # as recorded under the old release
    ns = request.getfixturevalue("ac")
    plant_canaries(ns, ns.db, ns.g.ws)
    before = graph_state(ns.db, ns.g.ws)
    run = play("experiment_critic/needs_work", request, scenario)
    [result] = run.results
    assert scenario.rejections == [reason] and result.proposal_ids == () and result.status != "completed"
    assert graph_state(ns.db, ns.g.ws) == before  # nothing reused, nothing proposed
    check_ledger(run.db, scenario.calls)  # the refused call still has its (failed) ledger row
    check_harness_records(run.db, run.results, scenario.calls)
