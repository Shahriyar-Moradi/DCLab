"""Persist queryable candidate, hyperparameter, CV, evaluation, and selection rows.

experiment_candidates remains the physical candidate table. payload JSONB stays
as compatibility evidence and is not authoritative for normalized fields.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    CVFoldRun,
    EvaluationMetric,
    Experiment,
    ExperimentCandidate,
    ModelEvaluation,
    ModelHyperparameter,
    ModelSelectionDecision,
    ModelVersion,
)
from app.engine.models.registry import applied_hyperparameters, implementation_for_family
from app.engine.search.fingerprint import candidate_fingerprint
from app.services.scientific_lineage_service import latest_pipeline_run_feature_set_version


def _now() -> datetime:
    return datetime.now(UTC)


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, str)):
        return value
    if hasattr(value, "item") and callable(value.item):
        try:
            return value.item()
        except Exception:  # noqa: BLE001
            pass
    return json.loads(json.dumps(value, default=str))


def _scalar_metrics(payload: Any) -> dict[str, float]:
    if not isinstance(payload, dict):
        return {}
    metrics: dict[str, float] = {}
    for name, raw in payload.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        metrics[str(name)] = float(raw)
    return metrics


def _fingerprint_for(experiment: Experiment, row: dict[str, Any], candidate_key: str) -> str:
    fingerprint = str(row.get("fingerprint") or "").strip()
    if fingerprint:
        return fingerprint[:40]
    return candidate_fingerprint(
        {
            "pipeline_run_id": str(experiment.id),
            "candidate_id": candidate_key,
            "model_family": row.get("model_family"),
        }
    )


def _search_stage(experiment: Experiment) -> str | None:
    config = experiment.config if isinstance(experiment.config, dict) else {}
    strategy = str(config.get("strategy") or "").strip()
    return strategy or None


def _delete_run_candidate_modeling(db: Session, pipeline_run_id: UUID) -> None:
    candidate_ids = list(
        db.scalars(
            select(ExperimentCandidate.id).where(
                ExperimentCandidate.experiment_id == pipeline_run_id
            )
        )
    )
    db.query(ModelSelectionDecision).filter(
        ModelSelectionDecision.pipeline_run_id == pipeline_run_id
    ).delete(synchronize_session=False)
    if candidate_ids:
        evaluation_ids = list(
            db.scalars(
                select(ModelEvaluation.id).where(
                    ModelEvaluation.candidate_id.in_(candidate_ids)
                )
            )
        )
        if evaluation_ids:
            db.query(EvaluationMetric).filter(
                EvaluationMetric.model_evaluation_id.in_(evaluation_ids)
            ).delete(synchronize_session=False)
        db.query(ModelEvaluation).filter(
            ModelEvaluation.candidate_id.in_(candidate_ids)
        ).delete(synchronize_session=False)
        db.query(CVFoldRun).filter(CVFoldRun.candidate_id.in_(candidate_ids)).delete(
            synchronize_session=False
        )
        db.query(ModelHyperparameter).filter(
            ModelHyperparameter.candidate_id.in_(candidate_ids)
        ).delete(synchronize_session=False)
    db.query(ExperimentCandidate).filter(
        ExperimentCandidate.experiment_id == pipeline_run_id
    ).delete(synchronize_session=False)
    db.flush()


def _add_metrics(db: Session, evaluation: ModelEvaluation, metrics: dict[str, float]) -> None:
    for name, value in metrics.items():
        db.add(
            EvaluationMetric(
                model_evaluation_id=evaluation.id,
                metric_name=name,
                metric_value=value,
            )
        )


def persist_candidate_modeling(
    db: Session, experiment: Experiment, result: dict[str, Any]
) -> dict[str, ExperimentCandidate]:
    """Replace queryable candidate modeling facts for this PipelineRun.

    A persisted winner lock is immutable: do not delete or rewrite candidate
    rows after ``ModelSelectionDecision`` exists (PostgreSQL also rejects that).
    """

    existing_selection = db.scalar(
        select(ModelSelectionDecision).where(
            ModelSelectionDecision.pipeline_run_id == experiment.id
        )
    )
    if existing_selection is not None:
        return {
            row.candidate_key: row
            for row in db.scalars(
                select(ExperimentCandidate).where(
                    ExperimentCandidate.experiment_id == experiment.id
                )
            )
        }

    _delete_run_candidate_modeling(db, experiment.id)
    feature_set_version = latest_pipeline_run_feature_set_version(db, experiment)
    feature_set_version_id = feature_set_version.id if feature_set_version is not None else None
    search_stage = _search_stage(experiment)
    rows = [row for row in list(result.get("candidates") or []) if isinstance(row, dict)]
    by_key: dict[str, ExperimentCandidate] = {}
    used_fingerprints: set[str] = set()

    for row in rows:
        candidate_key = str(row.get("candidate_id") or row.get("candidate") or "")
        if not candidate_key:
            continue
        fingerprint = _fingerprint_for(experiment, row, candidate_key)
        if fingerprint in used_fingerprints:
            fingerprint = candidate_fingerprint(
                {
                    "pipeline_run_id": str(experiment.id),
                    "candidate_id": candidate_key,
                    "fingerprint": fingerprint,
                }
            )
        used_fingerprints.add(fingerprint)
        family = str(row.get("model_family") or "")
        library, implementation_class, library_version = implementation_for_family(family)
        original_hp = dict(row.get("hyperparameters") or {})
        seed = int(row.get("random_seed") or experiment.seed or 42)
        applied = applied_hyperparameters(family, seed=seed, hyperparameters=original_hp)
        if not applied:
            applied = {"estimator": family or candidate_key}
        folds = [item for item in list(row.get("folds") or []) if isinstance(item, dict)]
        started_at = _dt(folds[0].get("started_at")) if folds else None
        completed_at = _dt(folds[-1].get("ended_at")) if folds else None
        duration_ms = row.get("fit_duration_ms")
        if duration_ms is None and row.get("train_seconds") is not None:
            duration_ms = float(row["train_seconds"]) * 1000.0
        candidate = ExperimentCandidate(
            workspace_id=experiment.workspace_id,
            project_id=experiment.project_id,
            experiment_id=experiment.id,
            candidate_key=candidate_key,
            fingerprint=fingerprint,
            status=str(row.get("status") or "generated"),
            payload=_json_safe(row),
            model_family=family,
            algorithm=family,
            implementation_library=library if family else None,
            implementation_class=implementation_class if family else None,
            library_version=library_version,
            search_stage=search_stage,
            trial_number=None,
            feature_set_version_id=feature_set_version_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=float(duration_ms) if duration_ms is not None else None,
        )
        db.add(candidate)
        db.flush()
        by_key[candidate_key] = candidate
        for name, value in applied.items():
            db.add(
                ModelHyperparameter(
                    candidate_id=candidate.id,
                    parameter_name=str(name),
                    value_json=_json_safe(value),
                    source="planner" if name in original_hp else "default",
                )
            )
        if str(row.get("status") or "").lower() != "trained":
            continue
        for fold in folds:
            fold_number = int(fold.get("fold_number") or fold.get("fold") or 0)
            if fold_number < 1:
                continue
            db.add(
                CVFoldRun(
                    workspace_id=experiment.workspace_id,
                    project_id=experiment.project_id,
                    candidate_id=candidate.id,
                    fold_number=fold_number,
                    train_row_count=int(
                        fold.get("train_row_count") or fold.get("train_count") or 0
                    ),
                    validation_row_count=int(
                        fold.get("validation_row_count") or fold.get("validation_count") or 0
                    ),
                    train_group_count=(
                        int(fold["train_group_count"])
                        if fold.get("train_group_count") is not None
                        else None
                    ),
                    validation_group_count=(
                        int(fold["validation_group_count"])
                        if fold.get("validation_group_count") is not None
                        else None
                    ),
                    train_time_start=_dt(fold.get("train_time_min")),
                    train_time_end=_dt(fold.get("train_time_max")),
                    validation_time_start=_dt(fold.get("validation_time_min")),
                    validation_time_end=_dt(fold.get("validation_time_max")),
                    status="completed",
                    started_at=_dt(fold.get("started_at")),
                    completed_at=_dt(fold.get("ended_at")),
                    duration_ms=(
                        float(fold["fit_duration_ms"])
                        if fold.get("fit_duration_ms") is not None
                        else None
                    ),
                )
            )
            fold_metrics = _scalar_metrics(fold.get("metrics"))
            if fold_metrics:
                evaluation = ModelEvaluation(
                    workspace_id=experiment.workspace_id,
                    project_id=experiment.project_id,
                    candidate_id=candidate.id,
                    evaluation_type="cross_validation",
                    evaluation_scope="cv_fold",
                    dataset_id=experiment.dataset_id,
                    status="completed",
                    summary={
                        "fold_number": fold_number,
                        "train_row_count": fold.get("train_row_count") or fold.get("train_count"),
                        "validation_row_count": fold.get("validation_row_count")
                        or fold.get("validation_count"),
                    },
                )
                db.add(evaluation)
                db.flush()
                _add_metrics(db, evaluation, fold_metrics)
        cv_mean = _scalar_metrics(row.get("cv_mean") or row.get("metrics"))
        if cv_mean:
            aggregate = ModelEvaluation(
                workspace_id=experiment.workspace_id,
                project_id=experiment.project_id,
                candidate_id=candidate.id,
                evaluation_type="cross_validation",
                evaluation_scope="cv_aggregate",
                dataset_id=experiment.dataset_id,
                status="completed",
                summary={
                    "n_folds": row.get("actual_folds") or row.get("n_folds") or len(folds),
                    "cv_strategy": row.get("cv_strategy"),
                },
            )
            db.add(aggregate)
            db.flush()
            _add_metrics(db, aggregate, cv_mean)
        robustness = _scalar_metrics(row.get("robustness") or row.get("cv_score"))
        if robustness:
            robust_eval = ModelEvaluation(
                workspace_id=experiment.workspace_id,
                project_id=experiment.project_id,
                candidate_id=candidate.id,
                evaluation_type="robustness",
                evaluation_scope="robustness",
                dataset_id=experiment.dataset_id,
                status="completed",
                summary={"source": "cv_fold_scores"},
            )
            db.add(robust_eval)
            db.flush()
            _add_metrics(db, robust_eval, robustness)

    _persist_selection_and_holdout(db, experiment, result, by_key)
    db.flush()
    return by_key


def _persist_selection_and_holdout(
    db: Session,
    experiment: Experiment,
    result: dict[str, Any],
    by_key: dict[str, ExperimentCandidate],
) -> None:
    selection = result.get("selection") if isinstance(result.get("selection"), dict) else {}
    selected_key = str(
        selection.get("selected_candidate_id") or selection.get("candidate_id") or ""
    )
    winner = by_key.get(selected_key)
    if winner is None or str(winner.status).lower() != "trained":
        return
    trained = [
        candidate
        for candidate in by_key.values()
        if str(candidate.status).lower() == "trained" and candidate.id != winner.id
    ]
    runner_up = None
    if trained:
        runner_up = max(
            trained,
            key=lambda row: float((row.payload or {}).get("score") or 0.0),
        )
    locked_at = _dt(selection.get("locked_at")) or _now()
    selected_score = selection.get("cv_score")
    if selected_score is None:
        selected_score = (winner.payload or {}).get("score")
    db.add(
        ModelSelectionDecision(
            workspace_id=experiment.workspace_id,
            project_id=experiment.project_id,
            pipeline_run_id=experiment.id,
            selected_candidate_id=winner.id,
            selection_metric=str(
                selection.get("selection_metric") or "score"
            ),
            selected_score=float(selected_score or 0.0),
            selection_policy=str(
                selection.get("selection_policy") or "maximum eligible primary CV score"
            ),
            runner_up_candidate_id=runner_up.id if runner_up is not None else None,
            reason="Winner locked from eligible CV scores before final holdout evaluation.",
            evidence={
                "selection_source": selection.get("selection_source") or "cross_validation",
                "eligible_candidate_ids": list(selection.get("eligible_candidate_ids") or []),
                "locked": bool(selection.get("locked")),
            },
            locked_at=locked_at,
        )
    )
    holdout = (
        result.get("final_test_evaluation")
        if isinstance(result.get("final_test_evaluation"), dict)
        else {}
    )
    holdout_key = str(holdout.get("candidate_id") or selected_key)
    if holdout_key != winner.candidate_key:
        return
    holdout_metrics = _scalar_metrics(holdout.get("metrics") or winner.payload.get("test_metrics"))
    if not holdout_metrics:
        return
    evaluation = ModelEvaluation(
        workspace_id=experiment.workspace_id,
        project_id=experiment.project_id,
        candidate_id=winner.id,
        evaluation_type="final_holdout",
        evaluation_scope="final_holdout",
        dataset_id=experiment.dataset_id,
        status="completed",
        summary={
            "candidate_id": winner.candidate_key,
            "evaluation_count": holdout.get("evaluation_count"),
            "test_row_count": holdout.get("test_row_count"),
            "started_at": holdout.get("started_at") or holdout.get("test_evaluation_started_at"),
            "ended_at": holdout.get("ended_at") or holdout.get("test_evaluation_completed_at"),
            "duration_ms": holdout.get("duration_ms")
            or holdout.get("test_evaluation_duration_ms"),
        },
    )
    db.add(evaluation)
    db.flush()
    _add_metrics(db, evaluation, holdout_metrics)


def link_candidates_to_feature_set_version(db: Session, experiment: Experiment) -> None:
    version = latest_pipeline_run_feature_set_version(db, experiment)
    if version is None:
        return
    db.query(ExperimentCandidate).filter(
        ExperimentCandidate.experiment_id == experiment.id,
        ExperimentCandidate.feature_set_version_id.is_(None),
    ).update(
        {"feature_set_version_id": version.id},
        synchronize_session=False,
    )
    db.flush()


def link_holdout_evaluation_to_model_version(
    db: Session, experiment: Experiment, model_version: ModelVersion
) -> None:
    evaluation = db.scalar(
        select(ModelEvaluation)
        .where(
            ModelEvaluation.candidate_id == model_version.selected_candidate_id,
            ModelEvaluation.evaluation_scope == "final_holdout",
        )
        .limit(1)
    )
    if evaluation is None:
        return
    evaluation.model_version_id = model_version.id
    db.flush()
