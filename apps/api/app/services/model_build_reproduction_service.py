"""Build ModelBuildReproductionSpec from canonical persisted evidence only."""

from __future__ import annotations

import hashlib
import math
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, object_session, selectinload

from app.db.models import (
    Artifact,
    CodeSnapshot,
    DataPreparationDecision,
    Dataset,
    Experiment,
    ExperimentCandidate,
    Feature,
    FeatureLineage,
    FeatureSetVersion,
    ModelEvaluation,
    ModelVersion,
    SplitPlan,
    User,
    Workspace,
)
from app.domain.errors import IdentityError
from app.domain.model_build import (
    ModelBuildReproductionArtifactRead,
    ModelBuildReproductionArtifactsRead,
)
from app.domain.model_build_reproduction import (
    AUTHORIZED_DATASET_PATH_PLACEHOLDER,
    AUTHORIZED_SPLIT_ASSIGNMENT_PATH_PLACEHOLDER,
    DATASET_PATH_ENV,
    GENERATOR_VERSION,
    SPLIT_ASSIGNMENT_PATH_ENV,
    ExperimentCodeDocument,
    ExperimentCodeInput,
    ExperimentCodeRead,
    ModelBuildReproductionSpec,
    ReproductionBranch,
    ReproductionCandidate,
    ReproductionColumn,
    ReproductionDataset,
    ReproductionFeature,
    ReproductionFeatureTransform,
    ReproductionFinalHoldout,
    ReproductionFinalRefit,
    ReproductionHoldoutPlan,
    ReproductionLeakageExclusion,
    ReproductionMetricPlan,
    ReproductionPreprocessingStep,
    ReproductionSplitAssignment,
    ReproductionTask,
    ReproductionValidationPlan,
    ReproductionWinner,
)
from app.domain.data_plane import REPRODUCTION_NOTEBOOK_TYPE, REPRODUCTION_SCRIPT_TYPE
from app.engine.modeling.validation_planner import (
    GROUP_KFOLD,
    KFOLD,
    STRATIFIED_GROUP_KFOLD,
    STRATIFIED_KFOLD,
    TIME_SERIES_SPLIT,
)
from app.engine.validation.splits import SOURCE_ROW_COLUMN
from app.services.artifact_service import read_artifact_bytes, store_artifact
from app.services.authorization_service import can_read_workspace
from app.services.model_build_codegen import render_stage_code, standalone_cv_supported
from app.services.model_build_notebook import (
    reproduction_filenames,
    reproduction_notebook_bytes,
    reproduction_script_bytes,
)
from app.services.scientific_lineage_service import content_digest

_SECRET_FRAGMENTS = (
    "api_key",
    "authorization",
    "credential",
    "llm_prompt",
    "location",
    "object_key",
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

REPRODUCTION_RUN_LOAD = (
    joinedload(Experiment.dataset).selectinload(Dataset.columns),
    joinedload(Experiment.task),
    joinedload(Experiment.workflow_run),
    joinedload(Experiment.scientific_plan),
    selectinload(Experiment.stage_runs),
    selectinload(Experiment.data_quality_findings),
    selectinload(Experiment.data_preparation_decisions).joinedload(
        DataPreparationDecision.dataset_column
    ),
    selectinload(Experiment.preprocessing_steps),
    selectinload(Experiment.model_selection_decisions),
    selectinload(Experiment.candidates).options(
        selectinload(ExperimentCandidate.hyperparameters),
        selectinload(ExperimentCandidate.cv_fold_runs),
        selectinload(ExperimentCandidate.evaluations).selectinload(ModelEvaluation.metrics),
        joinedload(ExperimentCandidate.feature_set_version)
        .selectinload(FeatureSetVersion.features)
        .options(
            selectinload(Feature.transformations),
            selectinload(Feature.lineage).joinedload(FeatureLineage.source_dataset_column),
        ),
    ),
    selectinload(Experiment.code_snapshots).joinedload(CodeSnapshot.runtime_environment),
    joinedload(Experiment.model_version).options(
        joinedload(ModelVersion.feature_set_version)
        .selectinload(FeatureSetVersion.features)
        .options(
            selectinload(Feature.transformations),
            selectinload(Feature.lineage).joinedload(FeatureLineage.source_dataset_column),
        ),
        joinedload(ModelVersion.code_snapshot).joinedload(CodeSnapshot.runtime_environment),
        joinedload(ModelVersion.runtime_environment),
        selectinload(ModelVersion.evaluations).selectinload(ModelEvaluation.metrics),
    ),
)


def _secret_key(name: str) -> bool:
    lowered = name.lower()
    return any(fragment in lowered for fragment in _SECRET_FRAGMENTS)


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _safe_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not _secret_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        if value and all(isinstance(item, str) for item in value):
            return [str(item)[:512] for item in value]
        if value and all(isinstance(item, (bool, int, float)) or item is None for item in value):
            return list(value)
        return {"value_count": len(value)}
    return str(value)[:512]


def _safe_params(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): _safe_value(value)
        for key, value in sorted(payload.items(), key=lambda pair: str(pair[0]))
        if not _secret_key(str(key))
    }


def _named_hyperparameters(rows: Iterable[Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for row in sorted(rows, key=lambda item: item.parameter_name):
        name = str(row.parameter_name)
        if _secret_key(name) or name == "tuning_plan":
            # The tuned candidate's search plan is evidence, not a constructor value.
            continue
        values[name] = _safe_value(row.value_json)
    return values


def _safe_tree(value: Any, depth: int = 0) -> Any:
    """JSON-safe copy of recorded run configuration; secret-like keys are dropped."""

    if depth > 6:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, dict):
        return {
            str(key): _safe_tree(item, depth + 1)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not _secret_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_tree(item, depth + 1) for item in list(value)[:200]]
    return str(value)[:512]


def _scalar_list(value: Any) -> list[Any] | None:
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(item, (str, int, float, bool)) for item in value):
        return None
    return [item[:512] if isinstance(item, str) else item for item in value]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:512] for item in value if isinstance(item, str)]


def _fold_hyperparameters(payload: dict[str, Any], applied: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-fold constructor values of a nested-tuned candidate (the run's own record)."""

    out: dict[str, dict[str, Any]] = {}
    for fold in payload.get("folds") or []:
        if not isinstance(fold, dict) or not isinstance(fold.get("tuning"), dict):
            continue
        params = fold["tuning"].get("params")
        number = int(fold.get("fold_number") or fold.get("fold") or 0)
        if number < 1 or not isinstance(params, dict) or not params:
            continue
        out[str(number)] = {**applied, **_safe_params(params)}
    return out


def _fold_scores(candidate: ExperimentCandidate, metric_name: str | None) -> dict[str, float]:
    scores: dict[str, float] = {}
    for evaluation in candidate.evaluations:
        if evaluation.evaluation_scope != "cv_fold" or not metric_name:
            continue
        number = (evaluation.summary or {}).get("fold_number")
        metrics = _evaluation_metrics(evaluation)
        if isinstance(number, int) and metric_name in metrics:
            scores[str(number)] = metrics[metric_name]
    return dict(sorted(scores.items(), key=lambda pair: int(pair[0])))


def _split_assignment(experiment: Experiment, result: dict[str, Any]) -> ReproductionSplitAssignment | None:
    db = object_session(experiment)
    if experiment.split_plan_id is None or db is None:
        return None
    plan = db.get(SplitPlan, experiment.split_plan_id)
    if plan is None or plan.workspace_id != experiment.workspace_id:
        return None
    split = result.get("split") if isinstance(result.get("split"), dict) else {}
    mode = (
        "split_plan_assignment"
        if split.get("partitioned_by") == "split_plan_assignment"
        else "holdout_plan_resplit"
    )
    return ReproductionSplitAssignment(
        split_plan_id=plan.id,
        version=plan.version,
        assignment_digest=plan.assignment_digest,
        row_count=plan.row_count,
        train_row_count=plan.train_row_count,
        holdout_row_count=plan.holdout_row_count,
        source_row_column=SOURCE_ROW_COLUMN,
        partitioned_by=mode,
    )


def _branch(experiment: Experiment, result: dict[str, Any]) -> ReproductionBranch | None:
    if experiment.change_set is None:
        return None
    config = experiment.config if isinstance(experiment.config, dict) else {}
    overrides = config.get("branch_overrides") if isinstance(config.get("branch_overrides"), dict) else {}
    recorded = result.get("branch") if isinstance(result.get("branch"), dict) else {}
    objective = config.get("objective")
    return ReproductionBranch(
        parent_pipeline_run_id=experiment.parent_pipeline_run_id,
        change_set=_safe_tree(experiment.change_set),
        change_set_digest=str(overrides.get("change_set_digest") or "")[:128] or None,
        effective_overrides=_safe_tree(overrides),
        objective=_safe_tree(objective) if isinstance(objective, dict) else None,
        applied_changes=[
            _safe_tree(row) for row in recorded.get("applied_changes") or [] if isinstance(row, dict)
        ],
    )


def _holdout_random_state(result: dict[str, Any]) -> int | None:
    plan = result.get("holdout_plan") if isinstance(result.get("holdout_plan"), dict) else {}
    value = plan.get("random_state")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _decision_threshold(result: dict[str, Any]) -> float | None:
    record = result.get("decision_threshold")
    value = record.get("value") if isinstance(record, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


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


def _column_name(decision: DataPreparationDecision) -> str | None:
    if decision.dataset_column is not None and decision.dataset_column.name:
        return str(decision.dataset_column.name)
    return None


def _splitter_shuffle(strategy: str | None) -> bool | None:
    if strategy in {STRATIFIED_KFOLD, KFOLD, STRATIFIED_GROUP_KFOLD}:
        return True
    if strategy == GROUP_KFOLD:
        return False
    if strategy == TIME_SERIES_SPLIT:
        return False
    return None


def _splitter_random_state(strategy: str | None, seed: int) -> int | None:
    if strategy in {STRATIFIED_KFOLD, KFOLD, STRATIFIED_GROUP_KFOLD}:
        return seed
    return None


def _evaluation_metrics(row: ModelEvaluation) -> dict[str, float]:
    return {
        metric.metric_name: metric.metric_value
        for metric in sorted(row.metrics, key=lambda item: item.metric_name)
    }


def _aggregate_score(
    evaluations: list[ModelEvaluation],
    candidate_id: UUID | None,
    metric_name: str | None,
) -> float | None:
    if candidate_id is None:
        return None
    for row in evaluations:
        if row.candidate_id != candidate_id or row.evaluation_scope != "cv_aggregate":
            continue
        metrics = _evaluation_metrics(row)
        if metric_name and metric_name in metrics:
            return metrics[metric_name]
        if len(metrics) == 1:
            return next(iter(metrics.values()))
    return None


def _holdout_evaluations(experiment: Experiment) -> list[ModelEvaluation]:
    rows: dict[UUID, ModelEvaluation] = {}
    for candidate in experiment.candidates:
        for evaluation in candidate.evaluations:
            if evaluation.evaluation_scope == "final_holdout":
                rows[evaluation.id] = evaluation
    if experiment.model_version is not None:
        for evaluation in experiment.model_version.evaluations:
            if evaluation.evaluation_scope == "final_holdout":
                rows[evaluation.id] = evaluation
    return sorted(rows.values(), key=lambda item: (item.created_at, str(item.id)))


def reproduction_spec_from_experiment(experiment: Experiment) -> ModelBuildReproductionSpec:
    """Assemble the IR. Never reads Dataset.location.

    From ``experiment.result`` only typed, recorded facts are read: the holdout
    partition mode, multiclass label coding, the locked decision threshold and a
    branch's applied-change record. Candidate columns and per-fold tuned values
    come from each candidate's persisted payload.
    """

    result = experiment.result if isinstance(experiment.result, dict) else {}
    dataset = experiment.dataset
    plan = experiment.scientific_plan
    columns = sorted(dataset.columns, key=lambda row: (row.ordinal_position, row.name))
    features = [
        feature
        for version in _feature_versions(experiment)
        for feature in version.features
    ]
    features = sorted(features, key=lambda item: (item.name, str(item.id)))
    # The runner's record order (its CV tie-break), else creation order.
    record_order = {
        str(row.get("candidate_id")): index
        for index, row in enumerate(result.get("candidates") or [])
        if isinstance(row, dict)
    }
    candidates = sorted(
        experiment.candidates,
        key=lambda row: (record_order.get(row.candidate_key, len(record_order)), row.created_at, str(row.id)),
    )
    selection = sorted(experiment.model_selection_decisions, key=lambda row: row.locked_at)
    winner_row = selection[-1] if selection else None
    winner_id = winner_row.selected_candidate_id if winner_row is not None else None
    runner_up_id = winner_row.runner_up_candidate_id if winner_row is not None else None
    selected = next((row for row in candidates if winner_id is not None and row.id == winner_id), None)
    runner_up = next(
        (row for row in candidates if runner_up_id is not None and row.id == runner_up_id),
        None,
    )
    aggregates = [
        evaluation
        for candidate in candidates
        for evaluation in candidate.evaluations
        if evaluation.evaluation_scope == "cv_aggregate"
    ]
    primary_metric = plan.primary_metric if plan is not None else (
        winner_row.selection_metric if winner_row is not None else None
    )
    leakage_decisions = [
        row
        for row in experiment.data_preparation_decisions
        if row.decision_type == "leakage"
    ]
    leakage_names: dict[str, ReproductionLeakageExclusion] = {}
    for decision in leakage_decisions:
        name = _column_name(decision)
        if not name:
            continue
        leakage_names[name] = ReproductionLeakageExclusion(
            column=name,
            strategy=decision.strategy,
            reason=(decision.reason or None),
        )
    for feature in features:
        if feature.status == "excluded" and feature.name not in leakage_names:
            leakage_names[feature.name] = ReproductionLeakageExclusion(
                column=feature.name,
                strategy="exclude",
                reason=feature.definition or None,
            )
    dropped = sorted(
        {
            *(feature.name for feature in features if feature.status == "dropped"),
            *(
                name
                for decision in experiment.data_preparation_decisions
                if decision.strategy == "drop_column"
                for name in (_column_name(decision),)
                if name
            ),
        }
    )
    datetime_columns = sorted(
        {
            feature.name
            for feature in features
            if feature.feature_type == "datetime"
            or any(item.transformation_type == "datetime_extract" for item in feature.transformations)
        }
    )
    modeled = [feature.name for feature in features if feature.status == "modeled"]
    recipe = [
        ReproductionFeature(
            name=feature.name,
            feature_type=feature.feature_type,
            status=feature.status,
            origin=_feature_origin(feature),
            transformations=[
                ReproductionFeatureTransform(
                    sequence=item.sequence,
                    transformation_type=item.transformation_type,
                    transformer_class=item.transformer_class,
                    parameters=_safe_params(item.parameters),
                )
                for item in sorted(feature.transformations, key=lambda row: row.sequence)
            ],
            sources=sorted(
                {
                    item.source_dataset_column.name
                    for item in feature.lineage
                    if item.source_dataset_column is not None
                }
            ),
        )
        for feature in features
    ]
    preprocessing = [
        ReproductionPreprocessingStep(
            sequence=row.sequence,
            column_scope=row.column_scope,
            transformer_type=row.transformer_type,
            transformer_class=row.transformer_class,
            fit_scope=row.fit_scope,
            parameters=_safe_params(row.parameters),
        )
        for row in sorted(experiment.preprocessing_steps, key=lambda item: item.sequence)
    ]
    candidate_reads = []
    for row in candidates:
        payload = row.payload if isinstance(row.payload, dict) else {}
        applied = _named_hyperparameters(row.hyperparameters)
        candidate_reads.append(ReproductionCandidate(
            id=row.id,
            fingerprint=row.fingerprint,
            model_family=row.model_family,
            algorithm=row.algorithm,
            implementation_library=row.implementation_library,
            implementation_class=row.implementation_class,
            library_version=row.library_version,
            hyperparameters=applied,
            is_winner=winner_id is not None and row.id == winner_id,
            is_runner_up=runner_up_id is not None and row.id == runner_up_id,
            cv_score=_aggregate_score(aggregates, row.id, primary_metric),
            status=str(row.status or "")[:32] or None,
            numerical_columns=_string_list(payload.get("numerical_cols")),
            categorical_columns=_string_list(payload.get("categorical_cols")),
            fold_hyperparameters=_fold_hyperparameters(payload, applied),
            cv_fold_scores=_fold_scores(row, primary_metric),
        ))
    target = None
    task_type = None
    if experiment.task is not None:
        task_type = experiment.task.task_type
    if experiment.workflow_run is not None:
        target = experiment.workflow_run.resolved_target
        task_type = experiment.workflow_run.task_type or task_type
    if plan is not None:
        task_type = plan.task_type
    holdout = ReproductionHoldoutPlan(
        strategy=plan.holdout_strategy if plan is not None else None,
        test_size=plan.holdout_test_size if plan is not None else None,
        group_column=plan.group_column if plan is not None else None,
        time_column=plan.time_column if plan is not None else None,
        plan_digest=plan.holdout_plan_digest if plan is not None else None,
        random_state=_holdout_random_state(result),
    )
    validation_strategy = plan.validation_strategy if plan is not None else None
    seed = int(experiment.seed)
    validation = ReproductionValidationPlan(
        strategy=validation_strategy,
        requested_folds=plan.requested_folds if plan is not None else None,
        actual_folds=plan.actual_folds if plan is not None else None,
        group_column=plan.group_column if plan is not None else None,
        time_column=plan.time_column if plan is not None else None,
        shuffle=_splitter_shuffle(validation_strategy),
        random_state=_splitter_random_state(validation_strategy, seed),
    )
    winner = ReproductionWinner(
        candidate_id=winner_id,
        fingerprint=selected.fingerprint if selected is not None else None,
        model_family=selected.model_family if selected is not None else None,
        algorithm=selected.algorithm if selected is not None else None,
        implementation_library=selected.implementation_library if selected is not None else None,
        implementation_class=selected.implementation_class if selected is not None else None,
        library_version=selected.library_version if selected is not None else None,
        hyperparameters=_named_hyperparameters(selected.hyperparameters) if selected is not None else {},
        selection_metric=winner_row.selection_metric if winner_row is not None else None,
        selected_score=winner_row.selected_score if winner_row is not None else None,
        selection_policy=winner_row.selection_policy if winner_row is not None else None,
        reason=winner_row.reason if winner_row is not None else None,
        runner_up_candidate_id=runner_up_id,
        runner_up_fingerprint=runner_up.fingerprint if runner_up is not None else None,
    )
    version = experiment.model_version
    holdout_rows = _holdout_evaluations(experiment)
    holdout_metrics: dict[str, float] = {}
    holdout_model_version_id = None
    holdout_candidate_id = None
    if holdout_rows:
        latest = holdout_rows[-1]
        holdout_metrics = _evaluation_metrics(latest)
        holdout_model_version_id = latest.model_version_id
        holdout_candidate_id = latest.candidate_id
    spec = ModelBuildReproductionSpec(
        generator_version=GENERATOR_VERSION,
        spec_digest="",
        workspace_id=experiment.workspace_id,
        pipeline_run_id=experiment.id,
        dataset=ReproductionDataset(
            dataset_id=dataset.id,
            source_type=dataset.source_type,
            version=dataset.version,
            content_digest=dataset.content_digest,
            schema_digest=dataset.schema_digest,
            row_count=dataset.row_count,
            column_count=dataset.column_count,
            columns=[
                ReproductionColumn(
                    name=row.name,
                    ordinal_position=row.ordinal_position,
                    physical_dtype=row.physical_dtype,
                    semantic_type=row.semantic_type,
                    role=row.role,
                )
                for row in columns
            ],
        ),
        task=ReproductionTask(
            task_type=task_type,
            target_column=target,
            seed=seed,
            class_labels=_scalar_list(result.get("class_labels")) if task_type == "multiclass" else None,
        ),
        holdout_plan=holdout,
        validation_plan=validation,
        metric_plan=ReproductionMetricPlan(primary_metric=primary_metric),
        leakage_exclusions=sorted(leakage_names.values(), key=lambda item: item.column),
        dropped_columns=dropped,
        datetime_columns=datetime_columns,
        modeled_features=modeled,
        feature_recipe=recipe,
        preprocessing=preprocessing,
        candidates=candidate_reads,
        winner=winner,
        final_refit=ReproductionFinalRefit(
            model_version_id=version.id if version is not None else None,
            version=version.version if version is not None else None,
            content_digest=version.content_digest if version is not None else None,
            selected_candidate_id=version.selected_candidate_id if version is not None else None,
            selected_fingerprint=selected.fingerprint if selected is not None else None,
        ),
        final_holdout=ReproductionFinalHoldout(
            metrics=holdout_metrics,
            model_version_id=holdout_model_version_id,
            candidate_id=holdout_candidate_id,
        ),
        split_assignment=_split_assignment(experiment, result),
        branch=_branch(experiment, result),
        decision_threshold=_decision_threshold(result),
    )
    spec.spec_digest = content_digest(
        spec.model_dump(mode="json", exclude={"spec_digest", "stage_code"})
    )
    return spec


def build_model_build_reproduction(experiment: Experiment) -> ModelBuildReproductionSpec:
    spec = reproduction_spec_from_experiment(experiment)
    spec.stage_code = render_stage_code(spec)
    return spec


def load_model_build_experiment(
    db: Session,
    user: User,
    workspace_id: UUID,
    pipeline_run_id: UUID,
) -> Experiment | None:
    if db.get(Workspace, workspace_id) is None or not can_read_workspace(
        db, user, workspace_id
    ):
        # Keep tenant existence and run existence indistinguishable, matching the
        # workspace explorer's established cross-workspace policy.
        raise IdentityError("not found", status_code=404)
    return db.scalar(
        select(Experiment)
        .options(*REPRODUCTION_RUN_LOAD)
        .where(
            Experiment.id == pipeline_run_id,
            Experiment.workspace_id == workspace_id,
        )
    )


def get_pipeline_model_build_reproduction(
    db: Session,
    user: User,
    workspace_id: UUID,
    pipeline_run_id: UUID,
) -> ModelBuildReproductionSpec | None:
    experiment = load_model_build_experiment(db, user, workspace_id, pipeline_run_id)
    if experiment is None:
        return None
    return build_model_build_reproduction(experiment)


def _reload_experiment(db: Session, experiment: Experiment) -> Experiment:
    loaded = db.scalar(
        select(Experiment)
        .options(*REPRODUCTION_RUN_LOAD)
        .where(
            Experiment.id == experiment.id,
            Experiment.workspace_id == experiment.workspace_id,
        )
    )
    if loaded is None:
        raise IdentityError("not found", status_code=404)
    return loaded


def _artifact_filename(row: Artifact) -> str:
    meta = row.extra_metadata if isinstance(row.extra_metadata, dict) else {}
    named = str(meta.get("filename") or "").strip()
    if named:
        return named
    return (row.object_key or "").rsplit("/", 1)[-1] or "reproduction"


def reproduction_artifact_read(row: Artifact) -> ModelBuildReproductionArtifactRead | None:
    meta = row.extra_metadata if isinstance(row.extra_metadata, dict) else {}
    if row.pipeline_run_id is None:
        return None
    return ModelBuildReproductionArtifactRead(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        pipeline_run_id=row.pipeline_run_id,
        artifact_type=row.artifact_type,
        filename=_artifact_filename(row),
        content_digest=row.content_digest,
        mime_type=row.mime_type,
        size_bytes=int(row.size_bytes),
        generator_version=str(meta.get("generator_version") or "") or None,
        spec_digest=str(meta.get("spec_digest") or "") or None,
        role=str(meta.get("role") or "") or None,
    )


def find_reproduction_artifact(
    db: Session, experiment: Experiment, artifact_type: str
) -> Artifact | None:
    return db.scalar(
        select(Artifact)
        .where(
            Artifact.workspace_id == experiment.workspace_id,
            Artifact.pipeline_run_id == experiment.id,
            Artifact.artifact_type == artifact_type,
        )
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        .limit(1)
    )


def _store_reproduction_bytes(
    db: Session,
    experiment: Experiment,
    *,
    artifact_type: str,
    filename: str,
    data: bytes,
    mime_type: str,
    role: str,
    spec: ModelBuildReproductionSpec,
) -> Artifact:
    existing = find_reproduction_artifact(db, experiment, artifact_type)
    if existing is not None:
        return existing
    return store_artifact(
        db,
        workspace_id=experiment.workspace_id,
        project_id=experiment.project_id,
        pipeline_run_id=experiment.id,
        artifact_type=artifact_type,
        filename=filename,
        data=data,
        mime_type=mime_type,
        extra_metadata={
            "pipeline_run_id": str(experiment.id),
            "role": role,
            "generator_version": spec.generator_version,
            "spec_digest": spec.spec_digest,
            "filename": filename,
        },
    )


def persist_model_build_reproduction_artifacts(
    db: Session, experiment: Experiment
) -> tuple[Artifact, Artifact]:
    """Store the notebook and script after a successful completed model build."""

    loaded = _reload_experiment(db, experiment)
    if str(loaded.status or "").upper() != "COMPLETED":
        raise RuntimeError(
            "reproduction artifacts are generated only after a completed model build"
        )
    spec = build_model_build_reproduction(loaded)
    notebook_name, script_name = reproduction_filenames(loaded.id)
    notebook = _store_reproduction_bytes(
        db,
        loaded,
        artifact_type=REPRODUCTION_NOTEBOOK_TYPE,
        filename=notebook_name,
        data=reproduction_notebook_bytes(spec),
        mime_type="application/x-ipynb+json",
        role="reproduction_notebook",
        spec=spec,
    )
    script = _store_reproduction_bytes(
        db,
        loaded,
        artifact_type=REPRODUCTION_SCRIPT_TYPE,
        filename=script_name,
        data=reproduction_script_bytes(spec),
        mime_type="text/x-python",
        role="reproduction_script",
        spec=spec,
    )
    return notebook, script


def list_model_build_reproduction_artifacts(
    db: Session,
    user: User,
    workspace_id: UUID,
    pipeline_run_id: UUID,
) -> ModelBuildReproductionArtifactsRead | None:
    experiment = load_model_build_experiment(db, user, workspace_id, pipeline_run_id)
    if experiment is None:
        return None
    notebook = find_reproduction_artifact(db, experiment, REPRODUCTION_NOTEBOOK_TYPE)
    script = find_reproduction_artifact(db, experiment, REPRODUCTION_SCRIPT_TYPE)
    return ModelBuildReproductionArtifactsRead(
        workspace_id=experiment.workspace_id,
        pipeline_run_id=experiment.id,
        generator_version=GENERATOR_VERSION,
        spec_digest=(
            (notebook.extra_metadata or {}).get("spec_digest")
            if notebook is not None and isinstance(notebook.extra_metadata, dict)
            else None
        ),
        notebook=reproduction_artifact_read(notebook) if notebook is not None else None,
        script=reproduction_artifact_read(script) if script is not None else None,
    )


def download_model_build_reproduction_artifact(
    db: Session,
    user: User,
    workspace_id: UUID,
    pipeline_run_id: UUID,
    artifact_type: str,
) -> tuple[Artifact, bytes] | None:
    experiment = load_model_build_experiment(db, user, workspace_id, pipeline_run_id)
    if experiment is None:
        return None
    artifact = find_reproduction_artifact(db, experiment, artifact_type)
    if artifact is None:
        return None
    payload = read_artifact_bytes(
        db, workspace_id=experiment.workspace_id, artifact_id=artifact.id
    )
    return artifact, payload


def get_experiment_code(
    db: Session,
    user: User,
    workspace_id: UUID,
    experiment_id: UUID,
) -> ExperimentCodeRead | None:
    """Standalone reproduction code for one experiment (root or branch), rendered now.

    Same read rule as the model-build reproduction routes; another workspace's
    experiment is indistinguishable from a missing one.
    """

    experiment = load_model_build_experiment(db, user, workspace_id, experiment_id)
    if experiment is None:
        return None
    spec = build_model_build_reproduction(experiment)
    notebook_name, script_name = reproduction_filenames(experiment.id)
    script = reproduction_script_bytes(spec)
    notebook = reproduction_notebook_bytes(spec)
    inputs = [
        ExperimentCodeInput(
            name="dataset",
            placeholder=AUTHORIZED_DATASET_PATH_PLACEHOLDER,
            env_var=DATASET_PATH_ENV,
            artifact_id=experiment.dataset.artifact_id,
            content_digest=spec.dataset.content_digest,
            description="Prepared dataset version this experiment trained on; sha256 must equal content_digest.",
        )
    ]
    split = spec.split_assignment
    if split is not None:
        plan = db.get(SplitPlan, split.split_plan_id)
        inputs.append(
            ExperimentCodeInput(
                name="split_assignment",
                placeholder=AUTHORIZED_SPLIT_ASSIGNMENT_PATH_PLACEHOLDER,
                env_var=SPLIT_ASSIGNMENT_PATH_ENV,
                artifact_id=plan.assignment_artifact_id if plan is not None else None,
                content_digest=split.assignment_digest,
                description=(
                    "Stored SplitPlan map (source_row,partition,fold); the script partitions "
                    "the holdout and outer folds by it and fails closed on a digest mismatch."
                ),
            )
        )
    helpers: list[str] = []
    for row in spec.stage_code:
        for helper in row.helper_requirements:
            if helper not in helpers:
                helpers.append(helper)
    return ExperimentCodeRead(
        experiment_id=experiment.id,
        workspace_id=experiment.workspace_id,
        generator_version=spec.generator_version,
        spec_digest=spec.spec_digest,
        split_plan_id=split.split_plan_id if split is not None else None,
        parent_experiment_id=experiment.parent_pipeline_run_id,
        is_branch=spec.branch is not None,
        standalone_cv=standalone_cv_supported(spec),
        script=ExperimentCodeDocument(
            filename=script_name,
            media_type="text/x-python",
            content_digest=hashlib.sha256(script).hexdigest(),
            source=script.decode("utf-8"),
        ),
        notebook=ExperimentCodeDocument(
            filename=notebook_name,
            media_type="application/x-ipynb+json",
            content_digest=hashlib.sha256(notebook).hexdigest(),
            source=notebook.decode("utf-8"),
        ),
        inputs=inputs,
        helper_requirements=helpers,
    )
