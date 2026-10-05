"""``ExperimentCriticAgent`` — ``experiment.review`` (AI-after, cap L1; ADR 0008 §1, §2b).

Runs after a completed run as a non-interactive ``agents.run`` job (batch eligible:
nobody waits on it) and never changes state: its output is an
``ExperimentReviewProposal`` (advice; at most L1 ``proposed``, L0 ``shadow`` without
evidence). Context (consumer mode, CV scope): the project, the experiment's locked CV
metrics and P4.10 findings (generic subject reads), its model card WITHOUT the final
evaluation, and the deterministic verifier checks that do not derive from the holdout
(statuses recomputed from those checks only). The pipeline auditor's stored advisory
report is never read: it is floored with the full, holdout-including status.

Deterministic validators: every cited CV metric must exist with that value in
``evaluation_metrics`` (``cv_aggregate`` scope) of a locked winner of the run's project;
cited findings must exist with that status on the reviewed run; decimal numbers in free
text must be cited values; ``promote_candidate`` needs CV evidence of the reviewed run and
no failed trust check. Cited experiments become citations (node existence: harness).
"""

from __future__ import annotations

import math
from typing import Any, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select

from app.agents.classes.common import Strict, uncited_numbers
from app.agents.contracts import Citation
from app.agents.runtime.base import (
    AgentClass,
    ContextRequest,
    OutputCheck,
    ProposalDraft,
    RuntimeOutput,
    register_agent_class,
)
from app.agents.tools.shaping import Shaped, code

AGENT_KEY = "experiment_critic"
DECISION_POINT = "experiment.review"
PROPOSAL_TYPE = "ExperimentReviewProposal"
CODE = r"^[a-z][a-z0-9_]{0,63}$"
METRIC_TOLERANCE = 5e-5  # a cited value must equal the stored one (4-decimal rounding allowed)


class CvMetricCitation(Strict):
    experiment_id: UUID
    metric: str = Field(pattern=CODE)
    value: float


class FindingCitation(Strict):
    check: str = Field(pattern=CODE)
    status: Literal["pass", "warning", "fail", "not_evaluated"]
    note: str = Field(max_length=500)


class ExperimentReview(Strict):
    verdict: Literal["promote_candidate", "needs_work", "reject"]
    summary: str = Field(max_length=2000)
    cv_metrics: list[CvMetricCitation] = Field(max_length=20)
    findings: list[FindingCitation] = Field(max_length=10)
    confidence: float = Field(ge=0, le=1)


TASK = """You review one completed ML experiment. You only propose; you never change state.
Assess leakage, overfitting, metric validity and the margin over the dummy baseline from the
cross-validation evidence, trust checks and verifier checks you are given. Cite metric names
and values exactly as given; never estimate a number. Say 'insufficient evidence' when unsure."""


# --- context -------------------------------------------------------------------------------------


def _verifier_section(experiment: Any) -> Shaped | None:
    from app.services.pipeline_verifier import holdout_scoped_check_ids, summarize_checks

    result = experiment.result or {}
    stored = result.get("deterministic_verification") or (result.get("technical_report") or {}).get(
        "deterministic_verification") or {}
    rows = [row for row in stored.get("checks") or [] if isinstance(row, dict) and row.get("check_id")]
    if not rows:
        return None
    withheld = holdout_scoped_check_ids(rows)
    checks = [{key: str(row.get(key) or "") for key in ("check_id", "stage", "status")}
              for row in rows if str(row.get("check_id")) not in withheld]
    summary = summarize_checks(checks)  # recomputed: never the full (holdout-including) status
    payload = {"overall_status": code(summary["overall_status"].lower()), "withheld_checks": len(withheld),
               "checks": [{"check": code(row["check_id"].lower()), "stage": code(row["stage"].lower()),
                           "status": code(row["status"].lower())} for row in checks[:60]]}
    sources = (experiment.source_dataset_id,) if experiment.source_dataset_id else ()
    return Shaped(payload, data_class="aggregates", outcome_scope="cv", source_datasets=sources,
                  aggregates=("overall_status", "checks"))


def context(request: ContextRequest) -> list[tuple[str, Any]]:
    from app.agents.tools.catalog import catalog
    from app.agents.tools.definitions.reads import ServiceReads, map_service_errors
    from app.db.models import Experiment, ModelVersion

    if request.subject_kind != "experiment" or request.subject_id is None:
        return []
    ctx = request.tool_ctx
    with map_service_errors():
        ServiceReads(ctx).experiment(request.subject_id)  # authorizes like GET /v1/experiments/{id}
    experiment = ctx.db.scalar(select(Experiment).where(
        Experiment.workspace_id == ctx.workspace_id, Experiment.id == request.subject_id))
    sections: list[tuple[str, Any]] = []
    model_id = ctx.db.scalar(select(ModelVersion.id).where(
        ModelVersion.workspace_id == ctx.workspace_id, ModelVersion.pipeline_run_id == request.subject_id))
    if model_id is not None:  # the card's final evaluation is withheld by its shaper
        sections.append(("get_model_card", lambda: catalog()["get_model_card"].read(
            ctx, {"model_version_id": str(model_id)})))
    verifier = _verifier_section(experiment) if experiment is not None else None
    if verifier is not None:
        sections.append(("verifier_checks", verifier))
    return sections


# --- output and validators -------------------------------------------------------------------------


def to_output(out: ExperimentReview) -> RuntimeOutput:
    cited = list(dict.fromkeys(item.experiment_id for item in out.cv_metrics))
    return RuntimeOutput(
        output=out, citations=tuple(Citation(kind="experiment", id=item) for item in cited),
        proposal=ProposalDraft(proposal_type=PROPOSAL_TYPE, decision_point_key=DECISION_POINT,
                               payload={"schema_version": 1, **out.model_dump(mode="json")},
                               rationale=out.summary))


def validate_output(output: RuntimeOutput) -> list[str]:
    out = output.output
    if not isinstance(out, ExperimentReview):
        return ["output_schema_invalid"]
    if any(not math.isfinite(item.value) for item in out.cv_metrics):
        return ["metric_not_in_cv_evidence"]
    if uncited_numbers([out.summary, *(item.note for item in out.findings)], [m.value for m in out.cv_metrics]):
        return ["uncited_number"]
    if len({item.check for item in out.findings}) != len(out.findings):
        return ["duplicate_finding"]
    return []


def cv_evidence(db: Any, workspace_id: UUID, project_id: UUID | None, experiment_id: UUID) -> dict[str, float] | None:
    """The locked winner's ``cv_aggregate`` metrics (never ``final_holdout``); ``None`` when the
    experiment is not of the project or has no locked winner."""

    from app.db.models import EvaluationMetric, Experiment, ModelEvaluation, ModelSelectionDecision

    if project_id is None or db.scalar(select(Experiment.id).where(
            Experiment.workspace_id == workspace_id, Experiment.project_id == project_id,
            Experiment.id == experiment_id)) is None:
        return None
    winner = db.scalar(select(ModelSelectionDecision.selected_candidate_id).where(
        ModelSelectionDecision.workspace_id == workspace_id, ModelSelectionDecision.pipeline_run_id == experiment_id))
    if winner is None:
        return None
    rows = db.execute(select(EvaluationMetric.metric_name, EvaluationMetric.metric_value)
                      .join(ModelEvaluation, ModelEvaluation.id == EvaluationMetric.model_evaluation_id)
                      .where(ModelEvaluation.workspace_id == workspace_id, ModelEvaluation.candidate_id == winner,
                             ModelEvaluation.evaluation_scope == "cv_aggregate")).all()
    return {str(name): float(value) for name, value in rows if value is not None}


def check_output(check: OutputCheck, output: RuntimeOutput) -> list[str]:
    from app.db.models import Experiment

    out = output.output
    if check.subject_kind != "experiment" or check.subject_id is None or not isinstance(out, ExperimentReview):
        return ["subject_not_reviewable"]
    evidence: dict[UUID, dict[str, float] | None] = {}
    for item in out.cv_metrics:
        if item.experiment_id not in evidence:
            evidence[item.experiment_id] = cv_evidence(check.db, check.workspace_id, check.project_id,
                                                       item.experiment_id)
        stored = (evidence[item.experiment_id] or {}).get(item.metric)
        if stored is None or abs(stored - item.value) > METRIC_TOLERANCE:
            return ["metric_not_in_cv_evidence"]
    experiment = check.db.scalar(select(Experiment).where(
        Experiment.workspace_id == check.workspace_id, Experiment.id == check.subject_id))
    stored_checks = {str(row.get("check")): str(row.get("status"))
                     for row in ((experiment.result or {}).get("investigation") or {}).get("checks") or []
                     if isinstance(row, dict)} if experiment is not None else {}
    if any(stored_checks.get(item.check) != item.status for item in out.findings):
        return ["finding_not_found"]
    if out.verdict == "promote_candidate":
        if not any(item.experiment_id == check.subject_id for item in out.cv_metrics):
            return ["promote_without_cv_evidence"]
        if "fail" in stored_checks.values():
            return ["promote_despite_failed_check"]
    return []


CLASS = register_agent_class(AgentClass(
    agent_key=AGENT_KEY, output_schema=ExperimentReview, task=TASK, method="review", max_output_tokens=2000,
    to_output=to_output, validate_output=validate_output, check_output=check_output, context=context,
    version="1", prompt_version=1, batch_eligible=True,
))
