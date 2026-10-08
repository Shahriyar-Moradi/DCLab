"""P6.4-A: the specialist agent classes — ExperimentCriticAgent (AI-after), DatasetInvestigatorAgent
and ExperimentPlannerAgent (AI-before) on the harness (AGENTS_NOOA_JEV.md §3; ADR 0008 §1, §2,
§2b, §6; ADR 0009 §5).

Golden fake transcripts (fake runtime ``default`` cases, the gateway's fake provider), the
deterministic validators (fabricated metric, unknown column, non-allowlisted action ...), the
Critic's enqueue gates (AI off, kill switch, one per run) and holdout blindness (no holdout and
no stored advisory report in any context). No network, no NOOA.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.agents.classes import dataset_investigator as inv
from app.agents.classes import experiment_critic as critic
from app.agents.classes import experiment_planner as planner
from app.agents.contracts import ContextField
from app.agents.gateway.limits import GatewayLimits
from app.agents.gateway.providers.fake import FakeProvider
from app.agents.gateway.service import GatewayService
from app.agents.governance.seed import seed_platform_governance
from app.agents.governance.switches import flip_off, re_enable
from app.agents.harness import service as svc
from app.agents.harness.context import within
from app.agents.harness.recorder import load_events
from app.agents.harness.service import AgentService, enqueue_experiment_review, spec_for_job, specialist_spec
from app.agents.prompt_releases import PROMPTS_ROOT, discover, output_schema_digest, sync_prompt_releases
from app.agents.runtime import nooa_runtime
from app.agents.runtime.base import AGENT_CLASSES, RuntimeOutput
from app.db.models import (
    AgentProposal,
    AgentRun,
    DatasetColumn,
    EvaluationMetric,
    Experiment,
    MlJob,
    ModelEvaluation,
    ModelSelectionDecision,
)
from app.domain.run_plans import ExperimentPlan
from app.services.run_plan_service import plan_for_request
from test_decision_record_service import _force_lock, g, setup  # noqa: F401  (fixtures)

ON = SimpleNamespace(ai_enabled=True, service_tokens_enabled=True, dclab_env="test")
GOLDEN = ["run_started", "context_built", "budget_reserved", "llm_call_started", "llm_call_finished",
          "proposal_created", "budget_settled", "run_finished"]
CV = {"roc_auc": 0.8123, "pr_auc": 0.6511}


class _Uncached(GatewayService):
    """Several answers for one input within a test: the completion cache is bypassed."""

    def complete(self, db, request):
        return super().complete(db, request.model_copy(update={"cache": False}))


@pytest.fixture
def ac(db_session, g):  # noqa: F811
    db = db_session
    seed_platform_governance(db, environment="test")
    sync_prompt_releases(db)
    db.commit()
    ns = SimpleNamespace(db=db, g=g, answer={})
    ns.fake = FakeProvider(handler=lambda _call: dict(ns.answer), resolved_model="gpt-6.1-sol", environment="test")
    ns.gateway = _Uncached(providers={"openai": ns.fake}, limits=GatewayLimits(), settings=lambda: ON)
    ns.service = AgentService(gateway=ns.gateway, settings=lambda: ON)
    _seed_evidence(db, g)
    _seed_columns(db, g)
    return ns


def _seed_evidence(db, g) -> None:  # noqa: F811
    """exp[1]: a locked winner with CV metrics (and a holdout one), trust checks and verifier checks."""

    candidate = db.scalar(text("SELECT id FROM experiment_candidates WHERE experiment_id = :e"), {"e": g.exp[1]})
    db.add(ModelSelectionDecision(
        workspace_id=g.ws, project_id=g.project.id, pipeline_run_id=g.exp[1], selected_candidate_id=candidate,
        selection_metric="roc_auc", selected_score=CV["roc_auc"], selection_policy="cv_best", reason="best CV",
        evidence={}, locked_at=datetime(2026, 9, 2, tzinfo=UTC)))
    for scope, values in (("cv_aggregate", CV), ("final_holdout", {"roc_auc": 0.7001})):
        evaluation = ModelEvaluation(workspace_id=g.ws, project_id=g.project.id, candidate_id=candidate,
                                     evaluation_type="cross_validation" if scope == "cv_aggregate" else scope,
                                     evaluation_scope=scope, dataset_id=g.prepared.id,
                                     status="completed", summary={})
        db.add(evaluation)
        db.flush()
        for name, value in values.items():
            db.add(EvaluationMetric(model_evaluation_id=evaluation.id, metric_name=name, metric_value=value))
    result = {
        "investigation": {"checks": [{"check": "target_leakage", "status": "pass", "severity": "info"},
                                     {"check": "overfit_gap", "status": "warning", "severity": "warning"}]},
        "deterministic_verification": {"checks": [
            {"check_id": "split_strategy_executed", "stage": "splitting", "status": "PASS"},
            {"check_id": "objective_constraints_met_holdout", "stage": "final_holdout", "status": "FAIL",
             "outcome_scope": "holdout"}]},
        "openai_audit": {"overall_status": "FAILED"},  # a holdout-floored advisory report: never in a context
    }
    db.execute(text("UPDATE experiments SET result = CAST(:r AS jsonb) WHERE id = :e"),
               {"r": json.dumps(result), "e": g.exp[1]})
    db.commit()
    _force_lock(db, g.exp[1])  # the model card needs the run's locked evidence


def _seed_columns(db, g) -> None:  # noqa: F811
    db.execute(text("DELETE FROM dataset_columns WHERE dataset_id = :d"), {"d": g.source.id})
    for position, (name, dtype, missing) in enumerate((("feature", "int64", 2), ("region", "object", 0),
                                                       ("signup_date", "datetime64[ns]", 0), ("target", "int64", 0))):
        db.add(DatasetColumn(workspace_id=g.ws, dataset_id=g.source.id, ordinal_position=position, name=name,
                             physical_dtype=dtype, missing_count=missing, missing_fraction=missing / 10))
    db.commit()


def _spec(ac, agent_key, **overrides):
    g = ac.g
    subject = ("experiment", g.exp[1], "experiment.review", "cv") if agent_key == critic.AGENT_KEY else (
        "dataset_version", g.source.id, "target.column" if agent_key == inv.AGENT_KEY else "spec.objective", "none")
    spec = specialist_spec(ac.db, agent_key=agent_key, workspace_id=g.ws, project_id=g.project.id, user_id=g.actor.id,
                           subject_kind=subject[0], subject_id=subject[1], decision_point_key=subject[2],
                           outcome_scope=subject[3], settings=ON)
    return spec.model_copy(update=overrides)


def _review(**overrides) -> dict:
    return {"verdict": "needs_work", "summary": "CV ROC AUC 0.8123 beats the baseline; the overfit gap warns.",
            "cv_metrics": [{"experiment_id": "{exp}", "metric": "roc_auc", "value": 0.8123}],
            "findings": [{"check": "overfit_gap", "status": "warning", "note": "train vs CV gap"}],
            "confidence": 0.7, **overrides}


def _run(ac, agent_key, answer, **spec):
    ac.answer = json.loads(json.dumps(answer).replace("{exp}", str(ac.g.exp[1])))
    result = ac.service.run(ac.db, _spec(ac, agent_key, **spec))
    ac.db.expire_all()
    return result


def _events(ac, run_id) -> list:
    return load_events(ac.db, workspace_id=ac.g.ws, run_id=run_id)


# --- registry, prompts ----------------------------------------------------------------------------


def test_classes_are_registered_predict_only_with_released_prompts(ac):
    assert {critic.AGENT_KEY, inv.AGENT_KEY, planner.AGENT_KEY} <= set(AGENT_CLASSES)
    files = {item.agent_key: item for item in discover(PROMPTS_ROOT)}
    for module in (critic, inv, planner):
        item = AGENT_CLASSES[module.AGENT_KEY]
        assert item.strategy == "predict" and item.check_output and item.context and item.task.strip()
        assert files[item.agent_key].output_schema_digest == output_schema_digest(item.output_schema)
        assert svc.released_prompt_id(ac.db, item.agent_key, item.prompt_version) is not None
    assert AGENT_CLASSES[critic.AGENT_KEY].batch_eligible and not AGENT_CLASSES[inv.AGENT_KEY].batch_eligible
    if not nooa_runtime.available():  # founder: waiting for a litellm release; fake runtime in development
        assert _spec(ac, critic.AGENT_KEY).runtime == "fake"
    assert svc.specialist_runtime(SimpleNamespace(dclab_env="production")) in (None, ("nooa_predict",
                                                                                      nooa_runtime.VERSION))


# --- golden fake transcripts ------------------------------------------------------------------------


def test_critic_golden_transcript_is_holdout_blind_and_advice_only(ac):
    g, db = ac.g, ac.db
    before = db.execute(text("SELECT status, result::text FROM experiments WHERE id = :e"), {"e": g.exp[1]}).one()
    result = _run(ac, critic.AGENT_KEY, _review())
    assert (result.status, result.error_code) == ("completed", None), result
    events = _events(ac, result.run_id)
    assert [e.type for e in events] == GOLDEN
    reads = events[1].payload["reads"]
    assert reads[:3] == ["inspect_project", "get_experiment", "get_findings"]
    assert "get_model_card" in reads and "verifier_checks" in reads
    run = db.get(AgentRun, result.run_id)
    assert (run.agent_key, run.agent_version, run.decision_point_key, run.outcome_scope) == (
        "experiment_critic", "1", "experiment.review", "cv")
    [proposal] = db.scalars(select(AgentProposal).where(AgentProposal.run_id == run.id))
    assert (proposal.proposal_type, proposal.status, proposal.level_at_proposal) == (
        "ExperimentReviewProposal", "shadow", 0)  # L0 until R3 evidence (ADR 0008 §3)
    assert proposal.payload["verdict"] == "needs_work" and proposal.experiment_id == g.exp[1]
    assert proposal.citations == [{"kind": "experiment", "id": str(g.exp[1])}]
    sent = ac.fake.calls[0].input_json
    for banned in ("holdout", "final_test", "0.7001", "openai_audit", "llm_report", "advisory_status"):
        assert banned not in sent, banned
    after = db.execute(text("SELECT status, result::text FROM experiments WHERE id = :e"), {"e": g.exp[1]}).one()
    assert before == after  # never changes state


def test_critic_proposal_is_proposed_at_l1_and_never_applied(ac, monkeypatch):
    monkeypatch.setattr(svc, "effective_level", lambda *a, **k: 1)
    result = _run(ac, critic.AGENT_KEY, _review(verdict="promote_candidate", findings=[]))
    assert result.status == "completed", result
    [proposal] = ac.db.scalars(select(AgentProposal).where(AgentProposal.run_id == result.run_id))
    assert (proposal.status, proposal.level_at_proposal, proposal.answer_ceiling) == ("proposed", 1, 1)


def test_investigator_and_planner_golden_transcripts(ac, monkeypatch):
    g, db = ac.g, ac.db
    investigation = {"target_candidates": [{"column": "target", "rank": 1, "reason": "binary outcome name"}],
                     "missing_values": [{"column": "feature", "action": "impute_median", "reason": "numeric"}],
                     "leakage_suspects": [], "questions": ["Is signup_date known at prediction time?"],
                     "confidence": 0.6}
    result = _run(ac, inv.AGENT_KEY, investigation)
    assert result.status == "completed", result
    events = _events(ac, result.run_id)
    assert [e.type for e in events] == GOLDEN and "dataset_columns" in events[1].payload["reads"]
    [proposal] = db.scalars(select(AgentProposal).where(AgentProposal.run_id == result.run_id))
    assert (proposal.proposal_type, proposal.decision_point_key, proposal.dataset_id) == (
        "DatasetInvestigationProposal", "target.column", g.source.id)
    assert db.get(AgentRun, result.run_id).data_class == "metadata"  # target.column: metadata partition

    plan = {"task_type": "binary", "primary_metric": "pr_auc", "split": None, "families": ["logistic_regression"],
            "max_training_seconds": 300.0, "rationale": "Imbalanced outcome: rank by precision-recall.",
            "confidence": 0.7}
    monkeypatch.setattr(svc, "effective_level", lambda *a, **k: 1)  # L1: a person accepts (P6.6-A)
    result = _run(ac, planner.AGENT_KEY, plan)
    assert result.status == "completed", result
    events = _events(ac, result.run_id)
    assert [e.type for e in events] == GOLDEN
    assert {"dataset_columns", "problem_spec", "plan_allowlists"} <= set(events[1].payload["reads"])
    [row] = db.scalars(select(AgentProposal).where(AgentProposal.run_id == result.run_id))
    assert row.proposal_type == "ExperimentPlanProposal" and row.decision_point_key == "spec.objective"
    assert ExperimentPlan.model_validate(row.payload).primary_metric == "pr_auc"  # run_plan_service's shape
    # A person accepts it (P6.6-A): it is then a consumable run ``plan``.
    db.execute(text("UPDATE agent_proposals SET status = 'accepted', decided_by_user_id = :u, decided_at = now() "
                    "WHERE id = :id"), {"u": g.actor.id, "id": row.id})
    db.commit()
    assert plan_for_request(db, workspace_id=g.ws, project_id=g.project.id, plan_id=row.id) == (row.id, None)


# --- validator rejections ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer, code", [
    (_review(cv_metrics=[{"experiment_id": "{exp}", "metric": "roc_auc", "value": 0.91}],
             summary="CV ROC AUC 0.91."), "metric_not_in_cv_evidence"),  # fabricated metric
    (_review(cv_metrics=[{"experiment_id": "{exp}", "metric": "roc_auc", "value": 0.7001}],
             summary="ROC AUC 0.7001."), "metric_not_in_cv_evidence"),  # the holdout value is not CV evidence
    (_review(summary="CV ROC AUC 0.8123 and recall 0.55."), "uncited_number"),
    (_review(findings=[{"check": "duplicate_rows", "status": "fail", "note": "x"}]), "finding_not_found"),
    (_review(verdict="promote_candidate", cv_metrics=[], summary="Looks good."), "promote_without_cv_evidence"),
    (_review(cv_metrics=[{"experiment_id": str(uuid4()), "metric": "roc_auc", "value": 0.8123}]),
     "citation_not_found"),  # a node that does not exist
    (_review(summary="Holdout looks fine."), "holdout_in_output"),
])
def test_critic_validator_rejections(ac, answer, code):
    result = _run(ac, critic.AGENT_KEY, answer)
    assert (result.status, result.error_code) == ("rejected_by_validator", code)
    assert ac.db.scalar(select(func.count()).select_from(AgentProposal).where(
        AgentProposal.run_id == result.run_id)) == 0


def test_critic_reads_and_cites_every_trust_check_and_rejects_unknown_ones(ac):
    """P5.1-A: the Critic's get_findings context carries all fifteen checks (FINDING_CHECKS order,
    past the old ten-check cap); a citation of a stored new check validates, an unknown one does not."""
    from app.domain.findings import FINDING_CHECKS

    g, db = ac.g, ac.db
    checks = [{"check": c, "status": "pass", "severity": "info", "evidence": {}, "recommendation_kind": None,
               "message_keys": [f"{c}.pass"]} for c in FINDING_CHECKS]
    checks[FINDING_CHECKS.index("time_travel")].update(status="fail", severity="critical",
                                                       recommendation_kind="review_columns")
    db.execute(text("UPDATE experiments SET result = jsonb_set(result, '{investigation}', CAST(:i AS jsonb)) "
                    "WHERE id = :e"), {"i": json.dumps({"version": "investigate.v1", "checks": checks}), "e": g.exp[1]})
    db.commit()
    cited = _run(ac, critic.AGENT_KEY, _review(findings=[{"check": "time_travel", "status": "fail", "note": "replay"}]))
    assert (cited.status, cited.error_code) == ("completed", None), cited
    from app.agents.tools.definitions.reads import FINDING_LIMIT

    # Every stored check reaches the Critic's findings read (summary over all fifteen); the tool's
    # per-check list is capped at FINDING_LIMIT, which now holds the whole library.
    assert 'get_findings.summary","outcome_scope":"none","value":{"failures":1,"not_evaluated":0,"passed":14,' in \
        ac.fake.calls[-1].input_json
    assert FINDING_LIMIT >= len(FINDING_CHECKS)
    for answer in (_review(findings=[{"check": "made_up_check", "status": "pass", "note": "x"}]),
                   _review(findings=[{"check": "time_travel", "status": "pass", "note": "x"}])):
        rejected = _run(ac, critic.AGENT_KEY, answer)
        assert (rejected.status, rejected.error_code) == ("rejected_by_validator", "finding_not_found")
    promote = _run(ac, critic.AGENT_KEY, _review(verdict="promote_candidate", findings=[]))
    assert promote.error_code == "promote_despite_failed_check"  # a failed new check blocks promotion too


def test_critic_provider_input_never_carries_holdout_feature_statistics(ac, monkeypatch):
    """P5.1-A review canary: a real investigation payload (with a label-proxy column, so a test-row
    missing share is the test negative rate) on the reviewed run; aggregates may reach the model,
    holdout-scoped numbers and test timestamps never do."""

    from ai_harness.kit import numbers_in
    from app.agents.gateway import redaction
    from app.services.auto_train.persistence import _investigation
    from test_investigate_library import _run_inputs, holdout_scope_values

    monkeypatch.setattr(redaction._Labels, "exposure", lambda self, source: ("allow", "internal"))
    g, db = ac.g, ac.db
    stored = _investigation(*_run_inputs())
    numbers, texts = holdout_scope_values(stored)
    assert numbers and texts
    db.execute(text("UPDATE experiments SET result = jsonb_set(result, '{investigation}', CAST(:i AS jsonb)) "
                    "WHERE id = :e"), {"i": json.dumps(stored), "e": g.exp[1]})
    db.commit()
    result = _run(ac, critic.AGENT_KEY, _review(findings=[]))
    assert result.status == "completed", result
    sent = ac.fake.calls[-1].input_json
    training_side = {row["evidence"]["ece"] for row in stored["checks"] if row["check"] == "calibration"}
    assert training_side & numbers_in(sent), "positive control: aggregates reach the model"
    assert not numbers_in(sent) & numbers, sorted(numbers_in(sent) & numbers)
    assert not [t for t in texts if t in sent]
    from app.domain.findings import AGENT_MESSAGES, HOLDOUT_FEATURE_CHECKS, findings_read

    human = {item.check: item.message for item in findings_read(uuid4(), stored).checks}
    for check in HOLDOUT_FEATURE_CHECKS:  # rounded renderings in the human message never reach the model
        assert human[check][:60] not in sent and AGENT_MESSAGES[check] in sent, check


def test_investigator_and_planner_validator_rejections(ac):
    unknown = {"target_candidates": [{"column": "churned", "rank": 1, "reason": "outcome"}], "missing_values": [],
               "leakage_suspects": [], "questions": [], "confidence": 0.5}
    assert _run(ac, inv.AGENT_KEY, unknown).error_code == "unknown_column"
    no_gaps = {**unknown, "target_candidates": [], "missing_values": [
        {"column": "region", "action": "impute_most_frequent", "reason": "category"}]}
    assert _run(ac, inv.AGENT_KEY, no_gaps).error_code == "no_missing_values"
    made_up = {**unknown, "target_candidates": [{"column": "target", "rank": 1, "reason": "41.5% positive"}]}
    assert _run(ac, inv.AGENT_KEY, made_up).error_code == "uncited_number"
    # A non-allowlisted action never passes the schema; the deterministic validator refuses it too.
    bad = inv.MissingValueAction.model_construct(column="feature", action="impute_mean", reason="")
    out = inv.DatasetInvestigation.model_construct(target_candidates=[], missing_values=[bad], leakage_suspects=[],
                                                   questions=[], confidence=0.5)
    assert inv.validate_output(RuntimeOutput(output=out)) == ["action_not_allowed"]

    base = {"task_type": "binary", "primary_metric": None, "split": None, "families": None,
            "max_training_seconds": None, "rationale": "", "confidence": 0.5}
    cases = [({**base, "families": ["svm"]}, "family_not_allowed"),
             ({**base, "primary_metric": "rmse"}, "metric_not_allowed"),
             ({**base, "split": {"strategy": "group_disjoint", "group_column": "customer", "time_column": None,
                                 "test_size": None}}, "unknown_column"),
             ({**base, "split": {"strategy": "temporal_future", "group_column": None, "time_column": "target",
                                 "test_size": None}}, "unknown_column"),
             ({**base, "split": {"strategy": "random", "group_column": None, "time_column": None,
                                 "test_size": 0.1}}, "below_holdout_fraction_floor"),
             ({**base, "task_type": "regression", "primary_metric": "rmse"}, "task_type_conflicts_with_spec"),
             (base, "empty_plan")]
    for answer, code in cases:
        assert _run(ac, planner.AGENT_KEY, answer).error_code == code, code


def test_no_context_field_carries_a_holdout_scoped_report():
    fields = (ContextField(key="get_evidence.openai_audit", value={"x": 1}, data_class="metadata",
                           outcome_scope="none"),
              ContextField(key="x.checks", value=[{"llm_report": 1}], data_class="metadata", outcome_scope="none"),
              ContextField(key="x.ok", value={"status": 1}, data_class="metadata", outcome_scope="none"))
    assert [item.key for item in within(fields, data_class="aggregates", outcome_scope="cv")] == ["x.ok"]


# --- the Critic's trigger ----------------------------------------------------------------------------


def _review_rows(db, experiment_id) -> tuple[int, int]:
    runs = db.scalar(select(func.count()).select_from(AgentRun).where(
        AgentRun.experiment_id == experiment_id, AgentRun.agent_key == critic.AGENT_KEY))
    jobs = db.scalar(select(func.count()).select_from(MlJob).where(MlJob.handler_key == "agents.run"))
    return runs, jobs


def test_review_enqueue_gates_and_job(ac):
    g, db = ac.g, ac.db
    off = SimpleNamespace(ai_enabled=False, dclab_env="test")
    assert enqueue_experiment_review(db, experiment_id=g.exp[1], user_id=g.actor.id, settings=off) is None
    assert _review_rows(db, g.exp[1]) == (0, 0)  # AI off: nothing enqueued, zero calls
    flip_off(db, workspace_id=g.ws, switch_key="agent:experiment_critic", actor=g.actor, reason="test")
    db.commit()
    assert enqueue_experiment_review(db, experiment_id=g.exp[1], user_id=g.actor.id, settings=ON) is None
    assert _review_rows(db, g.exp[1]) == (0, 0)
    re_enable(db, workspace_id=g.ws, switch_key="agent:experiment_critic", actor=g.actor, reason="test")
    db.commit()

    run_id = enqueue_experiment_review(db, experiment_id=g.exp[1], user_id=g.actor.id, settings=ON)
    assert run_id is not None and _review_rows(db, g.exp[1]) == (1, 1)
    assert enqueue_experiment_review(db, experiment_id=g.exp[1], user_id=g.actor.id, settings=ON) is None  # one
    assert ac.fake.calls == []  # queued only: the worker runs it
    job = db.scalar(select(MlJob).where(MlJob.target_id == run_id))
    assert job.payload == {"agent_run_id": str(run_id)}
    ac.answer = json.loads(json.dumps(_review()).replace("{exp}", str(g.exp[1])))
    result = ac.service.run(db, spec_for_job(db, job))
    assert (result.status, len(result.proposal_ids)) == ("completed", 1)
