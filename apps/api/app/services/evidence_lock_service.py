"""Stamp the final scientific-evidence lock on a finished PipelineRun.

The stamp is what arms the PostgreSQL triggers in ``app.db.evidence_lock``, so it
is set only when every canonical child row already exists. A run that is missing
any piece stays unlocked rather than being frozen half-written; nothing here
raises on incompleteness.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    Artifact,
    CodeSnapshot,
    CVFoldRun,
    EvaluationMetric,
    Experiment,
    ExperimentCandidate,
    ExperimentTestPrediction,
    Feature,
    ModelEvaluation,
    ModelHyperparameter,
    ModelSelectionDecision,
    ModelVersion,
    PreprocessingStep,
)
from app.services.scientific_lineage_service import (
    latest_pipeline_run_feature_set_version,
)

FINAL_HOLDOUT_SCOPE = "final_holdout"

# Reported in this order by the admin explorer; also the order they are written.
EVIDENCE_REQUIREMENTS = (
    "preprocessing",
    "features",
    "candidates",
    "hyperparameters",
    "cv_folds",
    "cv_metrics",
    "winner_selection",
    "final_holdout",
    "model_version_links",
    "reproducibility",
)


def _exists(db: Session, statement) -> bool:
    return db.scalar(statement.limit(1)) is not None


def _expected_fold_numbers(candidate: ExperimentCandidate) -> set[int]:
    payload = candidate.payload if isinstance(candidate.payload, dict) else {}
    folds = payload.get("folds") if isinstance(payload.get("folds"), list) else []
    numbers: set[int] = set()
    for fold in folds:
        if not isinstance(fold, dict):
            continue
        try:
            number = int(fold.get("fold_number") or fold.get("fold") or 0)
        except (TypeError, ValueError):
            continue
        if number > 0:
            numbers.add(number)
    return numbers


def scientific_evidence_status(db: Session, experiment: Experiment) -> dict[str, bool]:
    """Which pieces of canonical evidence this run has persisted."""

    run_id = experiment.id
    candidates = list(
        db.scalars(
            select(ExperimentCandidate).where(
                ExperimentCandidate.experiment_id == run_id
            )
        )
    )
    candidate_ids = [candidate.id for candidate in candidates]
    trained = [
        candidate for candidate in candidates if str(candidate.status).lower() == "trained"
    ]
    if candidate_ids:
        parameter_candidates = set(
            db.scalars(
                select(ModelHyperparameter.candidate_id).where(
                    ModelHyperparameter.candidate_id.in_(candidate_ids)
                )
            )
        )
        folds = list(
            db.scalars(
                select(CVFoldRun).where(CVFoldRun.candidate_id.in_(candidate_ids))
            )
        )
    else:
        parameter_candidates = set()
        folds = []
    folds_by_candidate: dict[UUID, set[int]] = {}
    for fold in folds:
        if str(fold.status).lower() != "completed":
            continue
        folds_by_candidate.setdefault(fold.candidate_id, set()).add(fold.fold_number)
    folds_complete = bool(trained) and all(
        bool(_expected_fold_numbers(candidate))
        and folds_by_candidate.get(candidate.id, set()) == _expected_fold_numbers(candidate)
        for candidate in trained
    )
    if candidate_ids:
        evaluations = list(
            db.scalars(
                select(ModelEvaluation).where(
                    ModelEvaluation.candidate_id.in_(candidate_ids)
                )
            )
        )
    else:
        evaluations = []
    if evaluations:
        metric_evaluations = set(
            db.scalars(
                select(EvaluationMetric.model_evaluation_id).where(
                    EvaluationMetric.model_evaluation_id.in_(
                        [evaluation.id for evaluation in evaluations]
                    )
                )
            )
        )
    else:
        metric_evaluations = set()

    def _fold_evaluation_exists(candidate_id: UUID, fold_number: int) -> bool:
        return any(
            evaluation.candidate_id == candidate_id
            and evaluation.evaluation_scope == "cv_fold"
            and str(evaluation.status).lower() == "completed"
            and str((evaluation.summary or {}).get("fold_number")) == str(fold_number)
            and evaluation.id in metric_evaluations
            for evaluation in evaluations
        )

    cv_evaluations = [
        evaluation
        for evaluation in evaluations
        if evaluation.evaluation_scope in {"cv_fold", "cv_aggregate"}
    ]
    cv_metrics_complete = folds_complete and bool(cv_evaluations) and all(
        evaluation.id in metric_evaluations for evaluation in cv_evaluations
    ) and all(
        all(
            _fold_evaluation_exists(candidate.id, fold_number)
            for fold_number in _expected_fold_numbers(candidate)
        )
        and any(
            evaluation.candidate_id == candidate.id
            and evaluation.evaluation_scope == "cv_aggregate"
            and str(evaluation.status).lower() == "completed"
            and evaluation.id in metric_evaluations
            for evaluation in evaluations
        )
        for candidate in trained
    )
    version = latest_pipeline_run_feature_set_version(db, experiment)
    model_version = db.scalar(
        select(ModelVersion).where(ModelVersion.pipeline_run_id == run_id).limit(1)
    )
    selection = db.scalar(
        select(ModelSelectionDecision)
        .where(ModelSelectionDecision.pipeline_run_id == run_id)
        .limit(1)
    )
    selected_candidate = None
    holdout = None
    if selection is not None:
        selected_candidate = next(
            (
                candidate
                for candidate in candidates
                if candidate.id == selection.selected_candidate_id
            ),
            None,
        )
        holdout = db.scalar(
            select(ModelEvaluation)
            .where(
                ModelEvaluation.candidate_id == selection.selected_candidate_id,
                ModelEvaluation.evaluation_scope == FINAL_HOLDOUT_SCOPE,
                ModelEvaluation.status == "completed",
            )
            .limit(1)
        )
    code_export_enabled = get_settings().reproducible_code_export_enabled
    code_snapshot = db.scalar(
        select(CodeSnapshot).where(CodeSnapshot.pipeline_run_id == run_id).limit(1)
    )

    def _has_artifact_role(role: str) -> bool:
        return _exists(
            db,
            select(Artifact.id).where(
                Artifact.pipeline_run_id == run_id,
                Artifact.extra_metadata["role"].astext == role,
            ),
        )

    has_dependency_lock = _has_artifact_role("dependency_lock")
    has_model_artifact = _has_artifact_role("model")
    has_feature_manifest = _has_artifact_role("feature_manifest")
    selection_complete = (
        selection is not None
        and selected_candidate is not None
        and str(selected_candidate.status).lower() == "trained"
    )
    holdout_complete = (
        holdout is not None
        and holdout.id in metric_evaluations
        and _exists(
            db,
            select(ExperimentTestPrediction.id).where(
                ExperimentTestPrediction.experiment_id == run_id
            ),
        )
    )
    # Legacy lab runs do not have a WorkflowRun/ModelAsset release lifecycle. For
    # those runs there is no ModelVersion link to complete; workflow-backed runs
    # must have the full release wiring before the evidence can be frozen.
    model_version_not_applicable = (
        experiment.workflow_run_id is None
        and model_version is None
        and selection_complete
        and holdout_complete
    )
    model_links_complete = model_version_not_applicable or (
        model_version is not None
        and selection_complete
        and model_version.selected_candidate_id == selected_candidate.id
        and model_version.dataset_id == experiment.dataset_id
        and model_version.runtime_environment_id is not None
        and model_version.model_artifact_id is not None
        and model_version.feature_manifest_artifact_id is not None
        and model_version.feature_set_version_id is not None
        and model_version.feature_set_version_id
        == selected_candidate.feature_set_version_id
        and holdout_complete
        and holdout.model_version_id == model_version.id
    )
    reproducibility_complete = (
        model_links_complete
        and has_dependency_lock
        and has_model_artifact
        and has_feature_manifest
        and (
            not code_export_enabled
            or (
                code_snapshot is not None
                and code_snapshot.candidate_id == selected_candidate.id
                and code_snapshot.dependency_lock_artifact_id is not None
                and (
                    model_version is None
                    or (
                        model_version.code_snapshot_id == code_snapshot.id
                        and code_snapshot.runtime_environment_id
                        == model_version.runtime_environment_id
                    )
                )
            )
        )
    )
    features_not_applicable = (
        experiment.project_id is None
        and version is None
        and bool(candidates)
        and all(candidate.feature_set_version_id is None for candidate in candidates)
    )
    return {
        "preprocessing": _exists(
            db,
            select(PreprocessingStep.id).where(
                PreprocessingStep.pipeline_run_id == run_id
            ),
        ),
        "features": features_not_applicable
        or (
            version is not None
            and version.locked_at is not None
            and selected_candidate is not None
            and selected_candidate.feature_set_version_id == version.id
            and _exists(
                db, select(Feature.id).where(Feature.feature_set_version_id == version.id)
            )
        ),
        "candidates": bool(candidate_ids),
        "hyperparameters": bool(candidates)
        and all(candidate.id in parameter_candidates for candidate in candidates),
        "cv_folds": folds_complete,
        "cv_metrics": cv_metrics_complete,
        "winner_selection": selection_complete,
        "final_holdout": holdout_complete,
        # `link_holdout_evaluation_to_model_version` is the last write of the run,
        # so a linked holdout evaluation means the ModelVersion wiring finished.
        "model_version_links": model_links_complete,
        "reproducibility": reproducibility_complete,
    }


def missing_scientific_evidence(db: Session, experiment: Experiment) -> list[str]:
    status = scientific_evidence_status(db, experiment)
    return [name for name in EVIDENCE_REQUIREMENTS if not status[name]]


def lock_scientific_evidence(db: Session, experiment: Experiment) -> datetime | None:
    """Freeze this run's evidence in PostgreSQL. Returns None if it is incomplete.

    Must be the last statement of the transaction that wrote the evidence: the
    triggers read the stamp inside that same transaction.
    """

    if experiment.scientific_evidence_locked_at is not None:
        return experiment.scientific_evidence_locked_at
    if str(experiment.status).upper() != "COMPLETED":
        return None
    if missing_scientific_evidence(db, experiment):
        return None
    experiment.scientific_evidence_locked_at = func.now()
    db.flush()
    db.refresh(experiment, ["scientific_evidence_locked_at"])
    return experiment.scientific_evidence_locked_at
