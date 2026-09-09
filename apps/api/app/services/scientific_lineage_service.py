"""Persist scientific lineage from real execution. JSONB payloads stay as compatibility."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    DataPreparationDecision,
    DataQualityFinding,
    Dataset,
    DatasetColumn,
    Experiment,
    Feature,
    FeatureLineage,
    FeatureSet,
    FeatureSetVersion,
    FeatureTransformation,
    LabDecisionRecord,
    PipelineScientificPlan,
    PipelineStageRun,
    PreprocessingStep,
    Project,
)
from app.domain.scientific_plane import (
    LEDGER_SOURCE_TO_DECISION_SOURCE,
    MISSING_ACTION_TO_STRATEGY,
    PREPROCESSING_FIT_SCOPES,
    QUALITY_CODE_TO_FINDING,
)
from app.engine.lab.auto_prepare import MissingValuePlan


def _now() -> datetime:
    return datetime.now(UTC)


def content_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _as_plan_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        return dict(payload) if isinstance(payload, dict) else {}
    if isinstance(value, dict):
        return dict(value)
    return {}


def _json_ready(payload: Any) -> Any:
    return json.loads(json.dumps(payload, default=str))


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_str(value: Any) -> str | None:
    return _optional_str(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def scientific_plan_columns_from_payloads(
    *,
    holdout_plan: Any,
    development_plan: Any,
    split: Any = None,
    validation_plan: Any = None,
    metric_plan: Any = None,
) -> dict[str, Any] | None:
    """Map authoritative HoldoutPlan / ModelDevelopmentPlan into queryable columns."""

    holdout = _as_plan_dict(holdout_plan)
    development = _as_plan_dict(development_plan)
    if not holdout or not development:
        return None
    nested_validation = _as_plan_dict(development.get("validation_plan"))
    nested_metric = _as_plan_dict(development.get("metric_plan"))
    validation = nested_validation or _as_plan_dict(validation_plan)
    metric = nested_metric or _as_plan_dict(metric_plan)
    profile = _as_plan_dict(development.get("problem_profile"))
    split_payload = _as_plan_dict(split)
    task_type = _required_str(profile.get("task_type"))
    holdout_strategy = _required_str(holdout.get("strategy"))
    validation_strategy = _required_str(validation.get("strategy"))
    primary_metric = _required_str(metric.get("primary_metric"))
    if not task_type or not holdout_strategy or not validation_strategy or not primary_metric:
        return None
    try:
        holdout_test_size = float(holdout.get("test_size"))
    except (TypeError, ValueError):
        return None
    requested = _optional_int(validation.get("requested_folds"))
    requested_folds = 5 if requested is None else requested
    development_payload = dict(development)
    if validation and not nested_validation:
        development_payload["validation_plan"] = validation
    if metric and not nested_metric:
        development_payload["metric_plan"] = metric
    allowed = list(development.get("allowed_features") or [])
    excluded = list(development.get("excluded_features") or [])
    group_column = (
        _optional_str(holdout.get("group_column"))
        or _optional_str(development.get("group_column"))
        or _optional_str(validation.get("group_column"))
    )
    time_column = (
        _optional_str(holdout.get("time_column"))
        or _optional_str(development.get("time_column"))
        or _optional_str(validation.get("time_column"))
    )
    full_plan = _json_ready(
        {
            "holdout_plan": holdout,
            "model_development_plan": development_payload,
            "validation_plan": validation,
            "metric_plan": metric,
            "split": split_payload,
        }
    )
    return {
        "task_type": task_type,
        "holdout_strategy": holdout_strategy,
        "holdout_test_size": holdout_test_size,
        "validation_strategy": validation_strategy,
        "requested_folds": requested_folds,
        "actual_folds": _optional_int(validation.get("actual_folds")),
        "primary_metric": primary_metric,
        "group_column": group_column,
        "time_column": time_column,
        "allowed_feature_count": len(allowed),
        "excluded_feature_count": len(excluded),
        "holdout_plan_digest": content_digest(_json_ready(holdout)),
        "model_development_plan_digest": content_digest(_json_ready(development_payload)),
        "full_plan": full_plan,
    }


def persist_scientific_plan(
    db: Session,
    experiment: Experiment,
    *,
    holdout_plan: Any,
    development_plan: Any,
    split: Any = None,
    validation_plan: Any = None,
    metric_plan: Any = None,
) -> PipelineScientificPlan | None:
    """Insert-once scientific plan for this PipelineRun. Second persist is a no-op."""

    existing = db.scalar(
        select(PipelineScientificPlan).where(
            PipelineScientificPlan.pipeline_run_id == experiment.id
        )
    )
    if existing is not None:
        return existing
    values = scientific_plan_columns_from_payloads(
        holdout_plan=holdout_plan,
        development_plan=development_plan,
        split=split,
        validation_plan=validation_plan,
        metric_plan=metric_plan,
    )
    if values is None:
        return None
    row = PipelineScientificPlan(
        workspace_id=experiment.workspace_id,
        project_id=experiment.project_id,
        pipeline_run_id=experiment.id,
        locked_at=_now(),
        **values,
    )
    db.add(row)
    db.flush()
    return row


def persist_scientific_plan_from_result(
    db: Session, experiment: Experiment, result: dict[str, Any]
) -> PipelineScientificPlan | None:
    return persist_scientific_plan(
        db,
        experiment,
        holdout_plan=result.get("holdout_plan"),
        development_plan=result.get("model_development_plan"),
        split=result.get("split"),
        validation_plan=result.get("validation_plan"),
        metric_plan=result.get("metric_plan"),
    )


@dataclass
class ScientificRunEvidence:
    quality: dict[str, Any] | None = None
    missing_plan: MissingValuePlan | None = None
    leakage_exclusions: list[dict[str, Any]] = field(default_factory=list)
    feature_actions: list[dict[str, Any]] = field(default_factory=list)
    numerical_cols: list[str] = field(default_factory=list)
    categorical_cols: list[str] = field(default_factory=list)
    modeled_features: list[str] = field(default_factory=list)
    dropped_columns: list[str] = field(default_factory=list)
    source_dataset_id: UUID | None = None
    lab_decision_sources: dict[str, str] = field(default_factory=dict)
    fit_scope: str = "fold_train"


def _column_map(db: Session, dataset_id: UUID | None) -> dict[str, DatasetColumn]:
    if dataset_id is None:
        return {}
    rows = list(
        db.scalars(select(DatasetColumn).where(DatasetColumn.dataset_id == dataset_id))
    )
    return {row.name: row for row in rows}


def _merge_column_maps(*maps: dict[str, DatasetColumn]) -> dict[str, DatasetColumn]:
    merged: dict[str, DatasetColumn] = {}
    for item in maps:
        merged.update(item)
    return merged


def _stage_id(db: Session, pipeline_run_id: UUID, keys: tuple[str, ...]) -> UUID | None:
    row = db.scalar(
        select(PipelineStageRun)
        .where(
            PipelineStageRun.pipeline_run_id == pipeline_run_id,
            PipelineStageRun.stage_key.in_(keys),
        )
        .order_by(PipelineStageRun.sequence)
        .limit(1)
    )
    return row.id if row is not None else None


def _severity_for_quality(code: str, issue: dict[str, Any]) -> str:
    if code in {"insufficient_samples", "target_imbalance"}:
        return "error"
    rate = issue.get("rate")
    if code == "missing_values" and isinstance(rate, (int, float)) and rate >= 0.5:
        return "error"
    return "warning"


def _finding_type_for_leakage(item: dict[str, Any]) -> str:
    availability = item.get("availability") if isinstance(item.get("availability"), dict) else {}
    status = str(availability.get("status") or "")
    if status == "known_after_prediction":
        return "prediction_time_leakage"
    return "target_leakage"


def _decision_source(raw: str | None) -> str:
    key = str(raw or "deterministic").strip().lower()
    return LEDGER_SOURCE_TO_DECISION_SOURCE.get(key, "deterministic")


def replace_run_scientific_rows(db: Session, pipeline_run_id: UUID) -> None:
    db.query(DataQualityFinding).filter(
        DataQualityFinding.pipeline_run_id == pipeline_run_id
    ).delete(synchronize_session=False)
    db.query(DataPreparationDecision).filter(
        DataPreparationDecision.pipeline_run_id == pipeline_run_id
    ).delete(synchronize_session=False)
    db.query(PreprocessingStep).filter(
        PreprocessingStep.pipeline_run_id == pipeline_run_id
    ).delete(synchronize_session=False)
    db.flush()


def latest_pipeline_run_feature_set_version(
    db: Session, experiment: Experiment
) -> FeatureSetVersion | None:
    name = f"pipeline-run-{experiment.id}"
    feature_set = db.scalar(
        select(FeatureSet).where(
            FeatureSet.workspace_id == experiment.workspace_id,
            FeatureSet.name == name,
        )
    )
    if feature_set is None:
        return None
    return db.scalar(
        select(FeatureSetVersion)
        .where(FeatureSetVersion.feature_set_id == feature_set.id)
        .order_by(FeatureSetVersion.version.desc())
        .limit(1)
    )


def persist_feature_lineage(
    db: Session,
    feature: Feature,
    source_columns: list[DatasetColumn],
    *,
    relationship: str = "source",
) -> list[FeatureLineage]:
    rows: list[FeatureLineage] = []
    for column in source_columns:
        if column.workspace_id != feature.workspace_id:
            continue
        existing = db.get(FeatureLineage, (feature.id, column.id))
        if existing is not None:
            rows.append(existing)
            continue
        row = FeatureLineage(
            feature_id=feature.id,
            source_dataset_column_id=column.id,
            relationship=relationship,
        )
        db.add(row)
        rows.append(row)
    if rows:
        db.flush()
    return rows


def _get_or_create_run_feature_set(
    db: Session, experiment: Experiment, project: Project
) -> FeatureSet:
    name = f"pipeline-run-{experiment.id}"
    existing = db.scalar(
        select(FeatureSet).where(
            FeatureSet.workspace_id == experiment.workspace_id,
            FeatureSet.name == name,
        )
    )
    if existing is not None:
        if existing.project_id != project.id:
            raise ValueError("feature set belongs to another project")
        return existing
    row = FeatureSet(
        workspace_id=experiment.workspace_id,
        project_id=project.id,
        name=name,
        description="Features observed during this pipeline run.",
    )
    db.add(row)
    db.flush()
    return row


def _open_feature_set_version(
    db: Session, feature_set: FeatureSet
) -> FeatureSetVersion:
    current = db.scalar(
        select(func.max(FeatureSetVersion.version)).where(
            FeatureSetVersion.feature_set_id == feature_set.id
        )
    )
    row = FeatureSetVersion(
        workspace_id=feature_set.workspace_id,
        project_id=feature_set.project_id,
        feature_set_id=feature_set.id,
        version=int(current or 0) + 1,
        content_digest=content_digest({"features": []}),
        locked_at=None,
    )
    db.add(row)
    db.flush()
    return row


def _learned_fit_scope(requested: str | None) -> str:
    scope = str(requested or "fold_train").strip()
    if scope == "all_data":
        raise ValueError("learned preprocessors cannot be recorded as all_data")
    if scope not in PREPROCESSING_FIT_SCOPES:
        raise ValueError(f"unsupported preprocessing fit_scope {scope!r}")
    return scope


def persist_scientific_lineage(
    db: Session,
    experiment: Experiment,
    evidence: ScientificRunEvidence,
) -> None:
    """Replace queryable scientific facts for this PipelineRun from real execution."""

    if experiment.dataset_id is None:
        return
    if experiment.project_id is None:
        dataset = db.get(Dataset, experiment.dataset_id)
        if dataset is not None and dataset.project_id is not None:
            experiment.project_id = dataset.project_id
    replace_run_scientific_rows(db, experiment.id)
    run_columns = _column_map(db, experiment.dataset_id)
    source_columns = _merge_column_maps(
        run_columns, _column_map(db, evidence.source_dataset_id)
    )
    quality_stage = _stage_id(
        db, experiment.id, ("profiling_eda", "profiling", "analyzing")
    )
    missing_stage = _stage_id(
        db,
        experiment.id,
        ("missing_value_decisions", "train_only_decisions", "structural_cleaning"),
    )
    feature_stage = _stage_id(db, experiment.id, ("feature_engineering",))
    prep_stage = _stage_id(
        db, experiment.id, ("preprocessing_setup", "preprocessing", "preprocessing_configuration")
    )
    project_id = experiment.project_id

    _persist_quality_findings(
        db,
        experiment,
        evidence,
        source_columns,
        quality_stage,
        project_id,
    )
    _persist_missing_decisions(
        db,
        experiment,
        evidence,
        source_columns,
        missing_stage,
        project_id,
    )
    _persist_leakage(
        db,
        experiment,
        evidence,
        source_columns,
        missing_stage,
        project_id,
    )
    _persist_preprocessing(
        db,
        experiment,
        evidence,
        prep_stage,
        project_id,
    )
    if project_id is not None:
        project = db.get(Project, project_id)
        if project is not None and project.workspace_id == experiment.workspace_id:
            _persist_features(
                db,
                experiment,
                project,
                evidence,
                source_columns,
                feature_stage,
            )
    db.flush()


def _dict_rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in list(value or []) if isinstance(row, dict)]


def _fit_scope_from_evidence(raw: str, *, has_learned_steps: bool) -> str:
    scope = str(raw or "").strip()
    if "all_data" in scope:
        raise ValueError("learned preprocessors cannot be recorded as all_data")
    if scope in PREPROCESSING_FIT_SCOPES:
        return scope
    return "fold_train" if has_learned_steps else "non_learned"


def persist_scientific_lineage_from_result(
    db: Session,
    experiment: Experiment,
    result: dict[str, Any],
    *,
    missing_plan: MissingValuePlan | None = None,
    lab_decision_sources: dict[str, str] | None = None,
    source_dataset_id: UUID | None = None,
) -> None:
    """Persist runner output that was actually produced. Does not invent configs."""

    payload = (
        result.get("scientific_evidence")
        if isinstance(result.get("scientific_evidence"), dict)
        else {}
    )
    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else None
    if quality is None:
        quality = result.get("quality") if isinstance(result.get("quality"), dict) else None
    development = (
        result.get("model_development_plan")
        if isinstance(result.get("model_development_plan"), dict)
        else {}
    )
    if "leakage_exclusions" in payload:
        excluded = _dict_rows(payload.get("leakage_exclusions"))
    else:
        excluded = _dict_rows(development.get("excluded_features"))
    feature_report = (
        result.get("feature_engineering")
        if isinstance(result.get("feature_engineering"), dict)
        else {}
    )
    if "feature_actions" in payload:
        actions = _dict_rows(payload.get("feature_actions"))
    else:
        actions = _dict_rows(
            feature_report.get("feature_engineering_actions")
            or feature_report.get("transformations")
        )
    preprocessing = (
        result.get("preprocessing") if isinstance(result.get("preprocessing"), dict) else {}
    )
    numerical = [
        str(name)
        for name in list(
            payload.get("numerical_columns") or preprocessing.get("numeric_columns") or []
        )
    ]
    categorical = [
        str(name)
        for name in list(
            payload.get("categorical_columns") or preprocessing.get("categorical_columns") or []
        )
    ]
    modeled = [
        str(name)
        for name in list(payload.get("modeled_features") or [])
        if str(name)
    ] or list(dict.fromkeys([*numerical, *categorical]))
    dropped = [
        str(name)
        for name in list(
            payload.get("dropped_columns") or feature_report.get("removed_features") or []
        )
    ]
    plan = missing_plan or MissingValuePlan.from_dict(payload.get("missing_value_plan"))
    raw_scope = str(
        payload.get("preprocessing_fit_scope") or preprocessing.get("fit_scope") or ""
    )
    persist_scientific_plan_from_result(db, experiment, result)
    persist_scientific_lineage(
        db,
        experiment,
        ScientificRunEvidence(
            quality=quality,
            missing_plan=plan,
            leakage_exclusions=excluded,
            feature_actions=actions,
            numerical_cols=numerical,
            categorical_cols=categorical,
            modeled_features=modeled,
            dropped_columns=dropped,
            source_dataset_id=source_dataset_id,
            lab_decision_sources=lab_decision_sources or {},
            fit_scope=_fit_scope_from_evidence(
                raw_scope, has_learned_steps=bool(numerical or categorical)
            ),
        ),
    )


def _persist_quality_findings(
    db: Session,
    experiment: Experiment,
    evidence: ScientificRunEvidence,
    columns: dict[str, DatasetColumn],
    stage_id: UUID | None,
    project_id: UUID | None,
) -> None:
    quality = evidence.quality or {}
    for issue in list(quality.get("issues") or []):
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "")
        finding_type = QUALITY_CODE_TO_FINDING.get(code)
        if finding_type is None:
            continue
        column_name = issue.get("column")
        column = columns.get(str(column_name)) if column_name is not None else None
        db.add(
            DataQualityFinding(
                workspace_id=experiment.workspace_id,
                project_id=project_id,
                pipeline_run_id=experiment.id,
                pipeline_stage_run_id=stage_id,
                dataset_id=experiment.dataset_id,
                dataset_column_id=column.id if column is not None else None,
                finding_type=finding_type,
                severity=_severity_for_quality(code, issue),
                evidence=dict(issue),
            )
        )


def _persist_missing_decisions(
    db: Session,
    experiment: Experiment,
    evidence: ScientificRunEvidence,
    columns: dict[str, DatasetColumn],
    stage_id: UUID | None,
    project_id: UUID | None,
) -> None:
    plan = evidence.missing_plan
    if plan is None:
        return
    dropped = set(plan.dropped_columns)
    for decision in plan.column_decisions:
        strategy = MISSING_ACTION_TO_STRATEGY.get(decision.action)
        if strategy is None:
            continue
        column = columns.get(decision.column)
        source = _decision_source(evidence.lab_decision_sources.get(decision.column))
        reason = {
            "median_imputation": "Numeric column with missing values; median imputation was selected.",
            "most_frequent": "Non-numeric column with missing values; most-frequent imputation was selected.",
            "drop_column": "Column exceeded the missingness drop threshold and was removed.",
            "drop_row": "Incomplete rows were dropped.",
            "domain_fill": "A domain fill value was applied.",
            "keep": "Column had no missing-value intervention; values were kept as ingested.",
        }.get(strategy, decision.action)
        db.add(
            DataPreparationDecision(
                workspace_id=experiment.workspace_id,
                project_id=project_id,
                pipeline_run_id=experiment.id,
                pipeline_stage_run_id=stage_id,
                dataset_id=experiment.dataset_id,
                dataset_column_id=column.id if column is not None else None,
                decision_type="missing_value",
                strategy=strategy,
                parameter_value={
                    "action": decision.action,
                    "fill_value": decision.fill_value,
                    "missing_count": decision.missing_count,
                    "missing_fraction": decision.missing_fraction,
                },
                reason=reason,
                evidence={
                    "column": decision.column,
                    "dropped": decision.column in dropped,
                },
                decision_source=source,
            )
        )


def _persist_leakage(
    db: Session,
    experiment: Experiment,
    evidence: ScientificRunEvidence,
    columns: dict[str, DatasetColumn],
    stage_id: UUID | None,
    project_id: UUID | None,
) -> None:
    for item in evidence.leakage_exclusions:
        name = str(item.get("column") or "")
        if not name:
            continue
        column = columns.get(name)
        finding_type = _finding_type_for_leakage(item)
        severity = "critical" if item.get("risk") == "CRITICAL" else "error"
        db.add(
            DataQualityFinding(
                workspace_id=experiment.workspace_id,
                project_id=project_id,
                pipeline_run_id=experiment.id,
                pipeline_stage_run_id=stage_id,
                dataset_id=experiment.dataset_id,
                dataset_column_id=column.id if column is not None else None,
                finding_type=finding_type,
                severity=severity,
                evidence=dict(item),
            )
        )
        db.add(
            DataPreparationDecision(
                workspace_id=experiment.workspace_id,
                project_id=project_id,
                pipeline_run_id=experiment.id,
                pipeline_stage_run_id=stage_id,
                dataset_id=experiment.dataset_id,
                dataset_column_id=column.id if column is not None else None,
                decision_type="leakage",
                strategy="exclude",
                parameter_value={"risk": item.get("risk"), "action": item.get("action") or "exclude"},
                reason=str(item.get("reason") or "; ".join(item.get("reasons") or []) or "Leakage exclusion."),
                evidence=dict(item),
                decision_source="deterministic",
            )
        )


def _persist_preprocessing(
    db: Session,
    experiment: Experiment,
    evidence: ScientificRunEvidence,
    stage_id: UUID | None,
    project_id: UUID | None,
) -> None:
    steps: list[tuple[str, str, str, dict[str, Any]]] = []
    if evidence.numerical_cols:
        steps.append(
            (
                "numerical",
                "impute",
                "sklearn.impute.SimpleImputer",
                {"strategy": "median", "columns": list(evidence.numerical_cols)},
            )
        )
        steps.append(
            (
                "numerical",
                "scale",
                "sklearn.preprocessing.StandardScaler",
                {"columns": list(evidence.numerical_cols)},
            )
        )
    if evidence.categorical_cols:
        steps.append(
            (
                "categorical",
                "impute",
                "sklearn.impute.SimpleImputer",
                {"strategy": "most_frequent", "columns": list(evidence.categorical_cols)},
            )
        )
        steps.append(
            (
                "categorical",
                "encode",
                "sklearn.preprocessing.OneHotEncoder",
                {
                    "drop": "first",
                    "handle_unknown": "ignore",
                    "columns": list(evidence.categorical_cols),
                },
            )
        )
    fit_scope = _learned_fit_scope(evidence.fit_scope)
    for sequence, (scope, transformer_type, transformer_class, parameters) in enumerate(
        steps, start=1
    ):
        db.add(
            PreprocessingStep(
                workspace_id=experiment.workspace_id,
                project_id=project_id,
                pipeline_run_id=experiment.id,
                pipeline_stage_run_id=stage_id,
                sequence=sequence,
                column_scope=scope,
                transformer_type=transformer_type,
                transformer_class=transformer_class,
                parameters=parameters,
                fit_scope=fit_scope,
            )
        )


def _datetime_outputs(actions: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for action in actions:
        step = str(action.get("step") or "")
        transformation = str(action.get("transformation") or "")
        if step != "datetime_to_unix_seconds" and transformation != "datetime_to_epoch":
            continue
        for name in action.get("output_columns") or action.get("columns") or []:
            names.add(str(name))
    return names


def _persist_features(
    db: Session,
    experiment: Experiment,
    project: Project,
    evidence: ScientificRunEvidence,
    columns: dict[str, DatasetColumn],
    stage_id: UUID | None,
) -> FeatureSetVersion:
    feature_set = _get_or_create_run_feature_set(db, experiment, project)
    version = _open_feature_set_version(db, feature_set)
    datetime_cols = _datetime_outputs(evidence.feature_actions)
    excluded = {
        str(item.get("column"))
        for item in evidence.leakage_exclusions
        if item.get("column")
    }
    dropped = set(evidence.dropped_columns)
    if evidence.missing_plan is not None:
        dropped.update(evidence.missing_plan.dropped_columns)
    modeled = [name for name in evidence.modeled_features if name not in excluded and name not in dropped]
    seen: set[str] = set()
    digest_payload: list[dict[str, Any]] = []

    def _add_feature(name: str, status: str, feature_type: str, definition: str) -> Feature | None:
        if name in seen:
            return None
        seen.add(name)
        row = Feature(
            workspace_id=experiment.workspace_id,
            project_id=project.id,
            feature_set_version_id=version.id,
            name=name,
            feature_type=feature_type,
            output_dtype="float64" if feature_type in {"numeric", "datetime"} else "object",
            definition=definition,
            status=status,
        )
        db.add(row)
        db.flush()
        digest_payload.append({"name": name, "status": status, "feature_type": feature_type})
        column = columns.get(name)
        if column is not None:
            persist_feature_lineage(db, row, [column])
        return row

    for name in modeled:
        feature_type = "numeric" if name in evidence.numerical_cols else "categorical"
        if name in datetime_cols:
            feature_type = "datetime"
        feature = _add_feature(
            name,
            "modeled",
            feature_type,
            "Unix-seconds datetime" if name in datetime_cols else f"Modeled {feature_type} column.",
        )
        if feature is not None and name in datetime_cols:
            db.add(
                FeatureTransformation(
                    feature_id=feature.id,
                    sequence=1,
                    transformation_type="datetime_extract",
                    transformer_class=None,
                    parameters={"unit": "seconds", "epoch": "unix"},
                    fit_required=False,
                )
            )
            db.add(
                DataPreparationDecision(
                    workspace_id=experiment.workspace_id,
                    project_id=project.id,
                    pipeline_run_id=experiment.id,
                    pipeline_stage_run_id=stage_id,
                    dataset_id=experiment.dataset_id,
                    dataset_column_id=(columns[name].id if name in columns else None),
                    decision_type="type_conversion",
                    strategy="datetime",
                    parameter_value={"unit": "seconds", "epoch": "unix"},
                    reason="Datetime values were converted to unix seconds for the tabular pipeline.",
                    evidence={"column": name},
                    decision_source="deterministic",
                )
            )
    for name in sorted(excluded):
        _add_feature(name, "excluded", "numeric", "Excluded by the leakage auditor.")
    for name in sorted(dropped):
        _add_feature(name, "dropped", "numeric", "Dropped during data preparation.")

    version.content_digest = content_digest({"features": digest_payload})
    version.locked_at = _now()
    db.flush()
    return version


def lab_decision_sources_for_upload(db: Session, upload_id: UUID) -> dict[str, str]:
    rows = list(
        db.scalars(select(LabDecisionRecord).where(LabDecisionRecord.upload_id == upload_id))
    )
    return {row.column: row.source for row in rows}


def create_derived_feature_with_sources(
    db: Session,
    *,
    experiment: Experiment,
    name: str,
    source_column_names: list[str],
    transformation_type: str,
    definition: str,
    parameters: dict[str, Any] | None = None,
) -> Feature:
    """Record a derived feature against an existing locked-or-open run feature set.

    Used when a real transform with multiple source columns is persisted.
    """

    if experiment.project_id is None:
        raise ValueError("pipeline run is missing a project")
    project = db.get(Project, experiment.project_id)
    if project is None:
        raise ValueError("project not found")
    feature_set = _get_or_create_run_feature_set(db, experiment, project)
    latest = db.scalar(
        select(FeatureSetVersion)
        .where(FeatureSetVersion.feature_set_id == feature_set.id)
        .order_by(FeatureSetVersion.version.desc())
        .limit(1)
    )
    if latest is None or latest.locked_at is not None:
        latest = _open_feature_set_version(db, feature_set)
    feature = Feature(
        workspace_id=experiment.workspace_id,
        project_id=project.id,
        feature_set_version_id=latest.id,
        name=name,
        feature_type="derived",
        output_dtype="float64",
        definition=definition,
        status="modeled",
    )
    db.add(feature)
    db.flush()
    db.add(
        FeatureTransformation(
            feature_id=feature.id,
            sequence=1,
            transformation_type=transformation_type,
            transformer_class=None,
            parameters=dict(parameters or {}),
            fit_required=False,
        )
    )
    columns = _column_map(db, experiment.dataset_id)
    persist_feature_lineage(
        db,
        feature,
        [columns[name] for name in source_column_names if name in columns],
    )
    if latest.locked_at is None:
        latest.content_digest = content_digest({"feature": name, "sources": source_column_names})
        latest.locked_at = _now()
    db.flush()
    return feature
