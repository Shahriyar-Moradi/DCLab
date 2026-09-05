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


def persist_scientific_lineage_from_result(
    db: Session, experiment: Experiment, result: dict[str, Any]
) -> None:
    """Persist runner output that was actually produced. Does not invent configs."""

    quality = result.get("quality") if isinstance(result.get("quality"), dict) else None
    development = (
        result.get("model_development_plan")
        if isinstance(result.get("model_development_plan"), dict)
        else {}
    )
    excluded = [
        row
        for row in list(development.get("excluded_features") or [])
        if isinstance(row, dict)
    ]
    feature_report = (
        result.get("feature_engineering")
        if isinstance(result.get("feature_engineering"), dict)
        else {}
    )
    actions = [
        row
        for row in list(
            feature_report.get("feature_engineering_actions")
            or feature_report.get("transformations")
            or []
        )
        if isinstance(row, dict)
    ]
    preprocessing = (
        result.get("preprocessing") if isinstance(result.get("preprocessing"), dict) else {}
    )
    numerical = [str(name) for name in list(preprocessing.get("numeric_columns") or [])]
    categorical = [str(name) for name in list(preprocessing.get("categorical_columns") or [])]
    modeled = list(dict.fromkeys([*numerical, *categorical]))
    raw_scope = str(preprocessing.get("fit_scope") or "")
    if "all_data" in raw_scope:
        raise ValueError("learned preprocessors cannot be recorded as all_data")
    persist_scientific_lineage(
        db,
        experiment,
        ScientificRunEvidence(
            quality=quality,
            leakage_exclusions=excluded,
            feature_actions=actions,
            numerical_cols=numerical,
            categorical_cols=categorical,
            modeled_features=modeled,
            dropped_columns=[str(name) for name in list(feature_report.get("removed_features") or [])],
            fit_scope="fold_train" if (numerical or categorical) else "non_learned",
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
