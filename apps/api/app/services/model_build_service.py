"""Build the safe model-build timeline from persisted scientific evidence.

This module intentionally does not expose arbitrary JSON columns.  Canonical
tables are preferred, and the few old-run fallbacks are copied through narrow
field allowlists.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    Experiment,
    Feature,
    FeatureSetVersion,
    MlRunVerification,
    ModelEvaluation,
    PipelineStageRun,
    User,
)
from app.domain.model_build import (
    ModelBuildEvidenceReference,
    ModelBuildStageRead,
    PipelineModelBuildRead,
)
from app.domain.model_build_reproduction import ModelBuildGeneratedCode
from app.domain.data_plane import REPRODUCTION_NOTEBOOK_TYPE, REPRODUCTION_SCRIPT_TYPE
from app.services.model_build_reproduction_service import (
    build_model_build_reproduction,
    load_model_build_experiment,
    reproduction_artifact_read,
)


@dataclass(frozen=True)
class _StageSpec:
    key: str
    title: str
    aliases: tuple[str, ...]


_STAGES = (
    _StageSpec("ingestion", "Ingestion", ("ingestion", "file_ingestion", "ingesting")),
    _StageSpec("profiling_eda", "Profiling / EDA", ("profiling_eda", "profiling", "analyzing")),
    _StageSpec("target_task", "Target + task", ("target_task", "target_task_resolution")),
    _StageSpec("structural_cleaning", "Structural cleaning", ("structural_cleaning", "cleaning")),
    _StageSpec("final_holdout_plan", "Final holdout plan", ("final_holdout_plan", "holdout_plan")),
    _StageSpec("holdout_lock", "Holdout lock", ("holdout_lock", "splitting")),
    _StageSpec(
        "problem_profile",
        "Train-only ProblemProfile",
        ("problem_profile", "train_only_decisions"),
    ),
    _StageSpec("validation_plan", "ValidationPlan", ("validation_plan",)),
    _StageSpec("metric_plan", "MetricPlan", ("metric_plan",)),
    _StageSpec("leakage_audit", "Leakage audit", ("leakage_audit",)),
    _StageSpec("missing_value_decisions", "Missing-value decisions", ("missing_value_decisions",)),
    _StageSpec(
        "feature_engineering",
        "Feature engineering",
        ("feature_engineering", "column_roles"),
    ),
    _StageSpec(
        "preprocessing",
        "Preprocessing",
        ("preprocessing", "preprocessing_configuration", "preprocessing_setup"),
    ),
    _StageSpec(
        "candidate_generation",
        "Candidate generation",
        ("candidate_generation", "candidate_training", "model_development_plan"),
    ),
    _StageSpec("cv_training", "CV training", ("cv_training", "cross_validation")),
    _StageSpec(
        "candidate_comparison",
        "Candidate comparison",
        ("candidate_comparison", "model_selection"),
    ),
    _StageSpec("winner_lock", "Winner lock", ("winner_lock",)),
    _StageSpec("final_refit", "Final refit", ("final_refit", "final_fit", "training")),
    _StageSpec("final_holdout", "Final holdout", ("final_holdout", "final_test", "evaluating")),
    _StageSpec(
        "artifact_reproducibility_persistence",
        "Artifact / reproducibility persistence",
        ("artifact_reproducibility_persistence", "artifact_persistence"),
    ),
    _StageSpec(
        "deterministic_verification",
        "Deterministic verification",
        ("deterministic_verification",),
    ),
)

_SECRET_FRAGMENTS = (
    "api_key",
    "authorization",
    "credential",
    "llm_prompt",
    "password",
    "prompt",
    "raw_customer",
    "raw_row",
    "secret",
    "test_indice",
    "test_prediction",
    "token",
    "y_true",
)


def _ref(entity_type: str, row: Any = None, *, compatibility: bool = False):
    return ModelBuildEvidenceReference(
        entity_type=entity_type,
        id=getattr(row, "id", None),
        source="compatibility" if compatibility else "canonical",
    )


def _safe_scalar(value: Any) -> Any:
    """Keep configuration useful without leaking row-derived collections."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return {"value_count": len(value)}
    if isinstance(value, dict):
        return {
            str(key): _safe_scalar(item)
            for key, item in value.items()
            if not any(fragment in str(key).lower() for fragment in _SECRET_FRAGMENTS)
        }
    return str(value)[:512]


def _safe_named_values(rows: Iterable[Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for row in sorted(rows, key=lambda item: item.parameter_name):
        name = str(row.parameter_name)
        if any(fragment in name.lower() for fragment in _SECRET_FRAGMENTS):
            continue
        values[name] = _safe_scalar(row.value_json)
    return values


def _counted(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def _stage_row(spec: _StageSpec, rows: list[PipelineStageRun]) -> PipelineStageRun | None:
    by_key = {row.stage_key: row for row in rows}
    for alias in spec.aliases:
        if alias in by_key:
            return by_key[alias]
    return None


def _row_count(row: PipelineStageRun | None, key: str) -> int | None:
    if row is None:
        return None
    for payload in (row.output_summary or {}, row.input_summary or {}):
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _legacy_dict(result: dict[str, Any], *path: str) -> dict[str, Any]:
    current: Any = result
    for key in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return dict(current) if isinstance(current, dict) else {}


def _allow(payload: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: _safe_scalar(payload[key]) for key in keys if key in payload}


def _legacy_plan(result: dict[str, Any], key: str) -> dict[str, Any]:
    direct = _legacy_dict(result, key)
    if direct:
        return direct
    return _legacy_dict(result, "model_development_plan", key)


def _stage_status(
    row: PipelineStageRun | None,
    *,
    has_evidence: bool,
    pipeline_status: str,
) -> str:
    if row is not None:
        status = str(row.status).lower()
        if status in {"complete", "completed", "succeeded", "success"}:
            return "completed"
        if status in {"failed", "error"}:
            return "failed"
        if status in {"running", "started", "in_progress"}:
            return "running"
        if status in {"skipped", "cancelled", "canceled"}:
            return status
        if status not in {"queued", "pending", "created"}:
            return status
    if has_evidence:
        return "completed"
    return "failed" if str(pipeline_status).upper() == "FAILED" and row is not None else "pending"


def _feature_versions(experiment: Experiment) -> list[FeatureSetVersion]:
    rows: dict[UUID, FeatureSetVersion] = {}
    for candidate in experiment.candidates:
        if candidate.feature_set_version is not None:
            rows[candidate.feature_set_version.id] = candidate.feature_set_version
    if (
        experiment.model_version is not None
        and experiment.model_version.feature_set_version is not None
    ):
        version = experiment.model_version.feature_set_version
        rows[version.id] = version
    return sorted(rows.values(), key=lambda item: (item.version, str(item.id)))


def _all_evaluations(experiment: Experiment) -> list[ModelEvaluation]:
    rows: dict[UUID, ModelEvaluation] = {}
    for candidate in experiment.candidates:
        for evaluation in candidate.evaluations:
            rows[evaluation.id] = evaluation
    if experiment.model_version is not None:
        for evaluation in experiment.model_version.evaluations:
            rows[evaluation.id] = evaluation
    return sorted(rows.values(), key=lambda item: (item.created_at, str(item.id)))


def _evaluation_metrics(row: ModelEvaluation) -> dict[str, float]:
    return {
        metric.metric_name: metric.metric_value
        for metric in sorted(row.metrics, key=lambda item: item.metric_name)
    }


def _fold_number(row: ModelEvaluation) -> int | None:
    summary = row.summary if isinstance(row.summary, dict) else {}
    value = summary.get("fold_number")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _metrics_for(evaluations: list[ModelEvaluation], candidate_id: UUID | None) -> dict[str, float]:
    if candidate_id is None:
        return {}
    for row in evaluations:
        if row.candidate_id == candidate_id:
            return _evaluation_metrics(row)
    return {}


def _feature_origin(feature: Feature) -> str:
    if feature.feature_type == "derived":
        return "generated"
    if len(feature.lineage) > 1:
        return "generated"
    extra = [
        item
        for item in feature.transformations
        if item.transformation_type not in {"identity", "passthrough", "datetime_extract"}
    ]
    return "generated" if extra else "original"


def _feature_decision(status: str) -> str:
    return "accepted" if str(status).lower() == "modeled" else "rejected"


def _feature_reads(features: list[Feature]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feature in sorted(features, key=lambda item: (item.name, str(item.id))):
        rows.append(
            {
                "id": str(feature.id),
                "name": feature.name,
                "feature_type": feature.feature_type,
                "status": feature.status,
                "origin": _feature_origin(feature),
                "decision": _feature_decision(feature.status),
                "definition": (feature.definition or "")[:512],
                "transformations": [
                    {
                        "sequence": item.sequence,
                        "transformation_type": item.transformation_type,
                        "transformer_class": item.transformer_class,
                    }
                    for item in sorted(feature.transformations, key=lambda row: row.sequence)
                ],
                "sources": [
                    {
                        "column_name": (
                            item.source_dataset_column.name
                            if item.source_dataset_column is not None
                            else None
                        ),
                        "relationship": item.relationship,
                    }
                    for item in feature.lineage
                ],
            }
        )
    return rows


def _artifact_rows(db: Session, experiment: Experiment) -> list[Artifact]:
    ids: set[UUID] = {row.artifact_id for row in experiment.code_snapshots}
    for snapshot in experiment.code_snapshots:
        if snapshot.dependency_lock_artifact_id is not None:
            ids.add(snapshot.dependency_lock_artifact_id)
    version = experiment.model_version
    if version is not None:
        ids.update(
            value
            for value in (
                version.model_artifact_id,
                version.preprocessor_artifact_id,
                version.feature_manifest_artifact_id,
            )
            if value is not None
        )
    stmt = select(Artifact).where(Artifact.workspace_id == experiment.workspace_id)
    predicates = [Artifact.pipeline_run_id == experiment.id]
    if ids:
        predicates.append(Artifact.id.in_(ids))
    return list(
        db.scalars(
            stmt.where(or_(*predicates)).order_by(Artifact.created_at, Artifact.id)
        )
    )


def get_pipeline_model_build(
    db: Session,
    user: User,
    workspace_id: UUID,
    pipeline_run_id: UUID,
) -> PipelineModelBuildRead | None:
    """Return a tenant-safe, canonical-evidence model build timeline."""

    experiment = load_model_build_experiment(db, user, workspace_id, pipeline_run_id)
    if experiment is None:
        return None
    reproduction = build_model_build_reproduction(experiment)
    generated = {row.key: row for row in reproduction.stage_code}

    stage_rows = sorted(experiment.stage_runs, key=lambda row: (row.sequence, str(row.id)))
    candidates = sorted(experiment.candidates, key=lambda row: (row.created_at, str(row.id)))
    candidate_ids = [row.id for row in candidates]
    folds = sorted(
        (fold for candidate in candidates for fold in candidate.cv_fold_runs),
        key=lambda row: (str(row.candidate_id), row.fold_number),
    )
    evaluations = _all_evaluations(experiment)
    cv_evaluations = [row for row in evaluations if row.evaluation_scope == "cv_fold"]
    aggregate_evaluations = [row for row in evaluations if row.evaluation_scope == "cv_aggregate"]
    holdout_evaluations = [row for row in evaluations if row.evaluation_scope == "final_holdout"]
    selection = sorted(experiment.model_selection_decisions, key=lambda row: row.locked_at)
    plan = experiment.scientific_plan
    feature_versions = _feature_versions(experiment)
    features = [feature for version in feature_versions for feature in version.features]
    transformations = [item for feature in features for item in feature.transformations]
    lineage = [item for feature in features for item in feature.lineage]
    artifacts = _artifact_rows(db, experiment)
    snapshots = sorted(experiment.code_snapshots, key=lambda row: (row.created_at, str(row.id)))
    attempts = list(
        db.scalars(
            select(MlRunVerification)
            .where(MlRunVerification.experiment_id == experiment.id)
            .order_by(MlRunVerification.started_at, MlRunVerification.id)
        )
    )
    result = dict(experiment.result) if isinstance(experiment.result, dict) else {}
    compatibility_used = False
    built: list[ModelBuildStageRead] = []

    def add(
        spec: _StageSpec,
        *,
        evidence: list[ModelBuildEvidenceReference] | None = None,
        configuration: dict[str, Any] | None = None,
        summary: str | None = None,
        reason: str | None = None,
        related_candidates: list[UUID] | None = None,
        related_folds: list[UUID] | None = None,
        compatibility: bool = False,
    ) -> None:
        nonlocal compatibility_used
        row = _stage_row(spec, stage_rows)
        refs = list(evidence or [])
        if row is not None:
            refs.insert(0, _ref("pipeline_stage_run", row))
        if compatibility:
            compatibility_used = True
            refs.append(_ref("experiment_result", compatibility=True))
        # Count rows only describe the absence of evidence; they must not make an
        # unstarted stage appear complete. A persisted stage row or canonical /
        # compatibility reference is what advances the read-model status.
        has_evidence = bool(refs)
        generated_row = generated.get(spec.key)
        built.append(
            ModelBuildStageRead(
                key=spec.key,
                sequence=len(built) + 1,
                title=spec.title,
                status=_stage_status(
                    row, has_evidence=has_evidence, pipeline_status=experiment.status
                ),
                started_at=row.started_at if row is not None else None,
                completed_at=row.completed_at if row is not None else None,
                duration_ms=row.duration_ms if row is not None else None,
                rows_in=_row_count(row, "rows_in"),
                rows_out=_row_count(row, "rows_out"),
                decision_summary=summary,
                reason=(row.failure_reason if row is not None and row.failure_reason else reason),
                configuration=configuration or {},
                evidence_references=refs,
                related_candidate_ids=related_candidates or [],
                related_fold_ids=related_folds or [],
                code_generation_support_status=(
                    generated_row.code_generation_support_status
                    if generated_row is not None
                    else "not_available"
                ),
                generated_code=(
                    ModelBuildGeneratedCode(
                        generator_version=generated_row.generator_version,
                        spec_digest=generated_row.spec_digest,
                        source=generated_row.source,
                        digest=generated_row.digest,
                        helper_requirements=generated_row.helper_requirements,
                        code_generation_support_status=generated_row.code_generation_support_status,
                    )
                    if generated_row is not None
                    else None
                ),
            )
        )

    # 1. Ingestion: Dataset is an immutable physical version; never expose location.
    add(
        _STAGES[0],
        evidence=[_ref("dataset", experiment.dataset)],
        configuration={
            "dataset_id": str(experiment.dataset.id),
            "source_type": experiment.dataset.source_type,
            "version": experiment.dataset.version,
            "content_digest": experiment.dataset.content_digest,
            "row_count": experiment.dataset.row_count,
            "column_count": experiment.dataset.column_count,
        },
        summary="Persisted an immutable dataset version.",
    )

    leakage_findings = [
        row for row in experiment.data_quality_findings if "leakage" in row.finding_type
    ]
    profile_findings = [
        row for row in experiment.data_quality_findings if row not in leakage_findings
    ]
    add(
        _STAGES[1],
        evidence=[_ref("data_quality_finding", row) for row in profile_findings],
        configuration={
            "finding_count": len(profile_findings),
            "finding_types": _counted(row.finding_type for row in profile_findings),
            "severities": _counted(row.severity for row in profile_findings),
        },
        summary=f"Persisted {len(profile_findings)} normalized profiling findings.",
    )

    task_config: dict[str, Any] = {}
    task_refs: list[ModelBuildEvidenceReference] = []
    if experiment.task is not None:
        task_config["task_type"] = experiment.task.task_type
        task_refs.append(_ref("prediction_task", experiment.task))
    if experiment.workflow_run is not None:
        task_config.update(
            {
                "target_column": experiment.workflow_run.resolved_target,
                "task_type": experiment.workflow_run.task_type or task_config.get("task_type"),
            }
        )
        task_refs.append(_ref("workflow_run", experiment.workflow_run))
    if plan is not None:
        task_config["task_type"] = plan.task_type
        task_config["evaluation_metric"] = plan.primary_metric
        task_refs.append(_ref("pipeline_scientific_plan", plan))
    task_compat = False
    legacy_task = _legacy_dict(result, "task")
    if not task_config.get("task_type") and legacy_task.get("task_type") is not None:
        task_config["task_type"] = _safe_scalar(legacy_task["task_type"])
        task_compat = True
    if not task_config.get("target_column"):
        legacy_target = legacy_task.get("target_column", legacy_task.get("target"))
        if legacy_target is not None:
            task_config["target_column"] = _safe_scalar(legacy_target)
            task_compat = True
    if (
        not task_config.get("evaluation_metric")
        and legacy_task.get("evaluation_metric") is not None
    ):
        task_config["evaluation_metric"] = _safe_scalar(
            legacy_task["evaluation_metric"]
        )
        task_compat = True
    add(
        _STAGES[2],
        evidence=task_refs,
        configuration=task_config,
        summary="Resolved the prediction target and task type." if task_config else None,
        compatibility=task_compat,
    )

    structural = [
        row
        for row in experiment.data_preparation_decisions
        if row.decision_type not in {"missing_value", "leakage"}
    ]
    add(
        _STAGES[3],
        evidence=[_ref("data_preparation_decision", row) for row in structural],
        configuration={
            "decision_count": len(structural),
            "decision_types": _counted(row.decision_type for row in structural),
            "strategies": _counted(row.strategy for row in structural),
        },
        summary=f"Applied {len(structural)} normalized structural decisions.",
    )

    holdout_config: dict[str, Any] = {}
    holdout_refs: list[ModelBuildEvidenceReference] = []
    holdout_compat = False
    if plan is not None:
        holdout_config = {
            "strategy": plan.holdout_strategy,
            "test_size": plan.holdout_test_size,
            "group_column": plan.group_column,
            "time_column": plan.time_column,
            "plan_digest": plan.holdout_plan_digest,
        }
        holdout_refs = [_ref("pipeline_scientific_plan", plan)]
    else:
        legacy = _legacy_plan(result, "holdout_plan")
        holdout_config = _allow(
            legacy,
            ("strategy", "test_size", "group_column", "time_column", "plan_version"),
        )
        holdout_compat = bool(holdout_config)
    add(
        _STAGES[4],
        evidence=holdout_refs,
        configuration=holdout_config,
        summary="Persisted the final holdout policy." if holdout_config else None,
        compatibility=holdout_compat,
    )
    add(
        _STAGES[5],
        evidence=holdout_refs,
        configuration=(
            {
                "locked": True,
                "locked_at": plan.locked_at.isoformat(),
                "strategy": plan.holdout_strategy,
                "test_size": plan.holdout_test_size,
                "group_column": plan.group_column,
                "time_column": plan.time_column,
            }
            if plan is not None
            else {}
        ),
        summary="Locked the holdout and scientific plan." if plan is not None else None,
    )

    profile_config: dict[str, Any] = {}
    profile_compat = False
    if plan is not None:
        profile_config = {
            "task_type": plan.task_type,
            "feature_count": plan.allowed_feature_count,
            "excluded_feature_count": plan.excluded_feature_count,
        }
    else:
        legacy = _legacy_plan(result, "problem_profile")
        profile_config = _allow(
            legacy,
            (
                "version",
                "task_type",
                "row_count",
                "n_rows",
                "feature_count",
                "n_features",
                "class_count",
                "imbalance_ratio",
                "has_groups",
                "has_time",
            ),
        )
        profile_compat = bool(profile_config)
    add(
        _STAGES[6],
        evidence=[_ref("pipeline_scientific_plan", plan)] if plan is not None else [],
        configuration=profile_config,
        summary="Persisted a train-only problem profile." if profile_config else None,
        compatibility=profile_compat,
    )

    validation_config: dict[str, Any] = {}
    validation_compat = False
    if plan is not None:
        validation_config = {
            "strategy": plan.validation_strategy,
            "requested_folds": plan.requested_folds,
            "actual_folds": plan.actual_folds,
            "group_column": plan.group_column,
            "time_column": plan.time_column,
        }
    else:
        validation_config = _allow(
            _legacy_plan(result, "validation_plan"),
            (
                "version",
                "strategy",
                "requested_folds",
                "actual_folds",
                "group_column",
                "time_column",
                "shuffle",
                "stratified",
            ),
        )
        validation_compat = bool(validation_config)
    add(
        _STAGES[7],
        evidence=[_ref("pipeline_scientific_plan", plan)] if plan is not None else [],
        configuration=validation_config,
        summary="Persisted the validation strategy." if validation_config else None,
        compatibility=validation_compat,
    )

    metric_config: dict[str, Any] = {}
    metric_compat = False
    if plan is not None:
        metric_config = {"primary_metric": plan.primary_metric}
    else:
        metric_config = _allow(
            _legacy_plan(result, "metric_plan"),
            ("version", "primary_metric", "direction", "task_type"),
        )
        metric_compat = bool(metric_config)
    add(
        _STAGES[8],
        evidence=[_ref("pipeline_scientific_plan", plan)] if plan is not None else [],
        configuration=metric_config,
        summary="Persisted the model-selection metric." if metric_config else None,
        compatibility=metric_compat,
    )

    leakage_decisions = [
        row for row in experiment.data_preparation_decisions if row.decision_type == "leakage"
    ]
    leakage_refs = [
        *[_ref("data_quality_finding", row) for row in leakage_findings],
        *[_ref("data_preparation_decision", row) for row in leakage_decisions],
    ]
    add(
        _STAGES[9],
        evidence=leakage_refs,
        configuration={
            "finding_count": len(leakage_findings),
            "decision_count": len(leakage_decisions),
            "excluded_feature_count": plan.excluded_feature_count if plan is not None else None,
        },
        summary=(
            f"Recorded {len(leakage_findings)} findings and "
            f"{len(leakage_decisions)} leakage decisions."
        ),
        reason=next((row.reason for row in leakage_decisions if row.reason), None),
    )

    missing = [
        row
        for row in experiment.data_preparation_decisions
        if row.decision_type == "missing_value"
    ]
    add(
        _STAGES[10],
        evidence=[_ref("data_preparation_decision", row) for row in missing],
        configuration={
            "decision_count": len(missing),
            "strategies": _counted(row.strategy for row in missing),
            "sources": _counted(row.decision_source for row in missing),
        },
        summary=f"Persisted {len(missing)} missing-value decisions.",
        reason=next((row.reason for row in missing if row.reason), None),
    )

    feature_reads = _feature_reads(features)
    add(
        _STAGES[11],
        evidence=[
            *[_ref("feature_set_version", row) for row in feature_versions],
            *[_ref("feature", row) for row in features],
            *[_ref("feature_transformation", row) for row in transformations],
            *[
                ModelBuildEvidenceReference(
                    entity_type="feature_lineage",
                    id=row.feature_id,
                    source="canonical",
                )
                for row in lineage
            ],
        ],
        configuration={
            "feature_set_version_ids": [str(row.id) for row in feature_versions],
            "feature_count": len(features),
            "feature_types": _counted(row.feature_type for row in features),
            "transformation_count": len(transformations),
            "transformation_types": _counted(row.transformation_type for row in transformations),
            "lineage_edge_count": len(lineage),
            "original_feature_count": sum(1 for row in feature_reads if row["origin"] == "original"),
            "generated_feature_count": sum(1 for row in feature_reads if row["origin"] == "generated"),
            "accepted_count": sum(1 for row in feature_reads if row["decision"] == "accepted"),
            "rejected_count": sum(1 for row in feature_reads if row["decision"] == "rejected"),
            "features": feature_reads,
        },
        summary=f"Persisted {len(features)} features and {len(lineage)} lineage edges.",
    )

    preprocessing = sorted(experiment.preprocessing_steps, key=lambda row: row.sequence)
    add(
        _STAGES[12],
        evidence=[_ref("preprocessing_step", row) for row in preprocessing],
        configuration={
            "steps": [
                {
                    "sequence": row.sequence,
                    "column_scope": row.column_scope,
                    "transformer_type": row.transformer_type,
                    "transformer_class": row.transformer_class,
                    "fit_scope": row.fit_scope,
                    "parameter_names": sorted(
                        key
                        for key in (row.parameters or {})
                        if not any(fragment in key.lower() for fragment in _SECRET_FRAGMENTS)
                    ),
                }
                for row in preprocessing
            ]
        },
        summary=f"Persisted {len(preprocessing)} fitted preprocessing steps.",
    )

    add(
        _STAGES[13],
        evidence=[
            *[_ref("experiment_candidate", row) for row in candidates],
            *[
                _ref("model_hyperparameter", parameter)
                for candidate in candidates
                for parameter in candidate.hyperparameters
            ],
        ],
        configuration={
            "candidates": [
                {
                    "id": str(row.id),
                    "candidate_key": row.candidate_key,
                    "fingerprint": row.fingerprint,
                    "model_family": row.model_family,
                    "algorithm": row.algorithm,
                    "implementation_library": row.implementation_library,
                    "implementation_class": row.implementation_class,
                    "library_version": row.library_version,
                    "search_stage": row.search_stage,
                    "trial_number": row.trial_number,
                    "status": row.status,
                    "hyperparameters": _safe_named_values(row.hyperparameters),
                }
                for row in candidates
            ]
        },
        summary=f"Persisted {len(candidates)} model candidates and their applied hyperparameters.",
        related_candidates=candidate_ids,
    )

    add(
        _STAGES[14],
        evidence=[
            *[_ref("cv_fold_run", row) for row in folds],
            *[_ref("model_evaluation", row) for row in cv_evaluations],
            *[
                _ref("evaluation_metric", metric)
                for row in cv_evaluations
                for metric in row.metrics
            ],
        ],
        configuration={
            "folds": [
                {
                    "id": str(row.id),
                    "candidate_id": str(row.candidate_id),
                    "fold_number": row.fold_number,
                    "train_row_count": row.train_row_count,
                    "validation_row_count": row.validation_row_count,
                    "status": row.status,
                    "metrics": next(
                        (
                            _evaluation_metrics(evaluation)
                            for evaluation in cv_evaluations
                            if evaluation.candidate_id == row.candidate_id
                            and _fold_number(evaluation) == row.fold_number
                        ),
                        {},
                    ),
                }
                for row in folds
            ],
            "fold_metrics": [
                {
                    "evaluation_id": str(row.id),
                    "candidate_id": str(row.candidate_id) if row.candidate_id else None,
                    "fold_number": _fold_number(row),
                    "metrics": _evaluation_metrics(row),
                }
                for row in cv_evaluations
            ],
        },
        summary=f"Persisted {len(folds)} CV fold runs and {len(cv_evaluations)} fold evaluations.",
        related_candidates=candidate_ids,
        related_folds=[row.id for row in folds],
    )

    add(
        _STAGES[15],
        evidence=[
            *[_ref("model_evaluation", row) for row in aggregate_evaluations],
            *[
                _ref("evaluation_metric", metric)
                for row in aggregate_evaluations
                for metric in row.metrics
            ],
        ],
        configuration={
            "candidate_scores": [
                {
                    "candidate_id": str(row.candidate_id) if row.candidate_id else None,
                    "status": row.status,
                    "metrics": {
                        metric.metric_name: metric.metric_value
                        for metric in sorted(row.metrics, key=lambda item: item.metric_name)
                    },
                }
                for row in aggregate_evaluations
            ]
        },
        summary=f"Compared {len(aggregate_evaluations)} canonical candidate evaluations.",
        related_candidates=sorted(
            {row.candidate_id for row in aggregate_evaluations if row.candidate_id}, key=str
        ),
    )

    winner = selection[-1] if selection else None
    selected = next(
        (row for row in candidates if winner is not None and row.id == winner.selected_candidate_id),
        None,
    )
    runner_up = next(
        (
            row
            for row in candidates
            if winner is not None and winner.runner_up_candidate_id is not None
            and row.id == winner.runner_up_candidate_id
        ),
        None,
    )
    selected_metrics = (
        _metrics_for(aggregate_evaluations, winner.selected_candidate_id) if winner is not None else {}
    )
    runner_up_metrics = (
        _metrics_for(aggregate_evaluations, winner.runner_up_candidate_id) if winner is not None else {}
    )
    runner_up_score = None
    if winner is not None:
        metric_value = runner_up_metrics.get(winner.selection_metric)
        runner_up_score = metric_value if isinstance(metric_value, (int, float)) else None
    add(
        _STAGES[16],
        evidence=[_ref("model_selection_decision", winner)] if winner is not None else [],
        configuration=(
            {
                "selected_candidate_id": str(winner.selected_candidate_id),
                "runner_up_candidate_id": (
                    str(winner.runner_up_candidate_id)
                    if winner.runner_up_candidate_id is not None
                    else None
                ),
                "selection_metric": winner.selection_metric,
                "selected_score": winner.selected_score,
                "runner_up_score": runner_up_score,
                "score_delta": (
                    winner.selected_score - runner_up_score
                    if runner_up_score is not None
                    else None
                ),
                "selection_policy": winner.selection_policy,
                "locked_at": winner.locked_at.isoformat(),
                "selected_algorithm": selected.algorithm if selected is not None else None,
                "selected_model_family": selected.model_family if selected is not None else None,
                "runner_up_algorithm": runner_up.algorithm if runner_up is not None else None,
                "runner_up_model_family": runner_up.model_family if runner_up is not None else None,
                "selected_metrics": selected_metrics,
                "runner_up_metrics": runner_up_metrics,
            }
            if winner is not None
            else {}
        ),
        summary="Locked the winning candidate from CV evidence." if winner is not None else None,
        reason=winner.reason if winner is not None else None,
        related_candidates=(
            [winner.selected_candidate_id]
            + ([winner.runner_up_candidate_id] if winner.runner_up_candidate_id else [])
            if winner is not None
            else []
        ),
    )

    version = experiment.model_version
    add(
        _STAGES[17],
        evidence=[_ref("model_version", version)] if version is not None else [],
        configuration=(
            {
                "model_version_id": str(version.id),
                "version": version.version,
                "selected_candidate_id": str(version.selected_candidate_id),
                "content_digest": version.content_digest,
            }
            if version is not None
            else {}
        ),
        summary="Refit and linked the selected candidate to an immutable model version."
        if version is not None
        else None,
        related_candidates=[version.selected_candidate_id] if version is not None else [],
    )

    add(
        _STAGES[18],
        evidence=[
            *[_ref("model_evaluation", row) for row in holdout_evaluations],
            *[
                _ref("evaluation_metric", metric)
                for row in holdout_evaluations
                for metric in row.metrics
            ],
        ],
        configuration={
            "evaluations": [
                {
                    "id": str(row.id),
                    "candidate_id": str(row.candidate_id) if row.candidate_id else None,
                    "model_version_id": (
                        str(row.model_version_id) if row.model_version_id else None
                    ),
                    "status": row.status,
                    "metrics": {
                        metric.metric_name: metric.metric_value
                        for metric in sorted(row.metrics, key=lambda item: item.metric_name)
                    },
                }
                for row in holdout_evaluations
            ]
        },
        summary=(
            f"Persisted {len(holdout_evaluations)} final-holdout evaluation "
            "without test rows."
        ),
        related_candidates=sorted(
            {row.candidate_id for row in holdout_evaluations if row.candidate_id}, key=str
        ),
    )

    runtime_rows = {
        row.runtime_environment.id: row.runtime_environment
        for row in snapshots
        if row.runtime_environment is not None
    }
    if version is not None and version.runtime_environment is not None:
        runtime_rows[version.runtime_environment.id] = version.runtime_environment
    add(
        _STAGES[19],
        evidence=[
            *[_ref("artifact", row) for row in artifacts],
            *[_ref("code_snapshot", row) for row in snapshots],
            *[_ref("runtime_environment", row) for row in runtime_rows.values()],
            *([_ref("model_version", version)] if version is not None else []),
        ],
        configuration={
            "artifacts": [
                {
                    "id": str(row.id),
                    "artifact_type": row.artifact_type,
                    "content_digest": row.content_digest,
                    "size_bytes": row.size_bytes,
                    "mime_type": row.mime_type,
                }
                for row in artifacts
            ],
            "code_snapshots": [
                {
                    "id": str(row.id),
                    "language": row.language,
                    "entrypoint": row.entrypoint,
                    "git_commit": row.git_commit,
                    "code_digest": row.code_digest,
                    "dependency_lock_digest": row.dependency_lock_digest,
                    "runtime_environment_id": str(row.runtime_environment_id),
                }
                for row in snapshots
            ],
            "runtime_environments": [
                {
                    "id": str(row.id),
                    "environment_digest": row.environment_digest,
                    "python_version": row.python_version,
                    "os_name": row.os_name,
                    "architecture": row.architecture,
                }
                for row in sorted(runtime_rows.values(), key=lambda item: str(item.id))
            ],
        },
        summary=f"Persisted {len(artifacts)} artifacts and {len(snapshots)} code snapshots.",
        related_candidates=[version.selected_candidate_id] if version is not None else [],
    )

    verification_config: dict[str, Any] = {}
    verification_refs = [_ref("ml_run_verification", row) for row in attempts]
    verification_compat = False
    if attempts:
        verification_config = {
            "attempts": [
                {
                    "id": str(row.id),
                    "audit_mode": row.audit_mode,
                    "deterministic_status": row.deterministic_status,
                    "schema_version": row.deterministic_schema_version,
                    "started_at": row.started_at.isoformat(),
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                }
                for row in attempts
            ]
        }
    elif _stage_row(_STAGES[20], stage_rows) is None:
        legacy = _legacy_dict(result, "deterministic_verification")
        checks = legacy.get("checks") if isinstance(legacy.get("checks"), list) else []
        verification_config = _allow(
            legacy, ("overall_status", "status", "schema_version", "version")
        )
        if checks:
            verification_config.update(
                {
                    "check_count": len(checks),
                    "failure_count": sum(
                        1
                        for check in checks
                        if isinstance(check, dict) and check.get("status") == "FAIL"
                    ),
                    "warning_count": sum(
                        1
                        for check in checks
                        if isinstance(check, dict) and check.get("status") == "WARN"
                    ),
                }
            )
        verification_compat = bool(verification_config)
    add(
        _STAGES[20],
        evidence=verification_refs,
        configuration=verification_config,
        summary=(
            f"Persisted {len(attempts)} verification attempts."
            if attempts
            else "Read an allowlisted legacy verification summary."
            if verification_compat
            else None
        ),
        compatibility=verification_compat,
    )

    notebook_row = next(
        (
            row
            for row in reversed(artifacts)
            if row.artifact_type == REPRODUCTION_NOTEBOOK_TYPE
        ),
        None,
    )
    script_row = next(
        (
            row
            for row in reversed(artifacts)
            if row.artifact_type == REPRODUCTION_SCRIPT_TYPE
        ),
        None,
    )
    return PipelineModelBuildRead(
        workspace_id=experiment.workspace_id,
        pipeline_run_id=experiment.id,
        pipeline_run_status=experiment.status,
        scientific_evidence_locked_at=experiment.scientific_evidence_locked_at,
        compatibility_fallback_used=compatibility_used,
        generator_version=reproduction.generator_version,
        reproduction_spec_digest=reproduction.spec_digest,
        reproduction_notebook=reproduction_artifact_read(notebook_row) if notebook_row is not None else None,
        reproduction_script=reproduction_artifact_read(script_row) if script_row is not None else None,
        stages=built,
    )
