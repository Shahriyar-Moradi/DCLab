"""Shared technical explorer reads for DCLab Admin and customer ML engineers.

Visibility is authorization only. These queries reuse Wave 1/2 tables and
eager-load graphs so the frontend does not assemble joins itself.
"""

from __future__ import annotations

from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db.models import (
    Artifact,
    CVFoldRun,
    CodeSnapshot,
    Dataset,
    Experiment,
    ExperimentCandidate,
    Feature,
    FeatureLineage,
    FeatureSet,
    FeatureSetVersion,
    LlmInvocation,
    MlRunVerification,
    MlWorkflow,
    ModelEvaluation,
    ModelVersion,
    Pipeline,
    Project,
    User,
    WorkflowRun,
    WorkflowRunInput,
    WorkflowVersion,
    Workspace,
)
from app.domain.errors import IdentityError
from app.domain.observability import LlmInvocationRead
from app.domain.reproducibility import (
    ArtifactRead,
    CodeSnapshotRead,
    RuntimeEnvironmentRead,
)
from app.domain.technical_explorer import (
    CandidateSummary,
    CodeRuntimeSection,
    CVFoldRead,
    DatasetListItem,
    DatasetVersionSummary,
    DataQualityFindingRead,
    DevelopmentPlanSection,
    EvaluationMetricRead,
    FeatureEngineeringSection,
    FeatureLineageRead,
    FeatureRead,
    FeatureTransformationRead,
    FinalModelRead,
    HyperparameterRead,
    IdentitySection,
    ModelCandidateDetailRead,
    ModelEvaluationRead,
    ModelVersionDetailRead,
    ModelVersionListItem,
    PipelineRunDetailRead,
    PipelineRunListItem,
    PipelineSummary,
    PipelineVersionSummary,
    PreparationDecisionRead,
    PreprocessingStepRead,
    ProblemSpecSummary,
    ProjectDetailRead,
    ProjectListItem,
    ProjectSummary,
    SplitValidationSection,
    StageTimelineItem,
    VerificationAttemptRead,
    VerificationSection,
    WinnerDecisionRead,
    WorkflowDetailRead,
    WorkflowListItem,
    WorkflowVersionSummary,
    WorkspaceListItem,
)
from app.services.authorization_service import can_read_platform, can_read_workspace
from app.services.reproducibility_service import (
    artifacts_for_model_version,
    artifacts_for_pipeline_run,
)
from app.services.workspace_capability_service import (
    OPENAI_PIPELINE_AUDIT,
    SEMANTIC_LLM_AUDIT,
    capability_matrix,
)

LIST_LIMIT = 100
LIST_LIMIT_MAX = 200


def _limit(limit: int | None) -> int:
    value = LIST_LIMIT if limit is None else int(limit)
    return max(1, min(value, LIST_LIMIT_MAX))


def resolve_explorer_scope(
    db: Session, user: User, workspace_id: UUID | None
) -> UUID | None:
    """Return a workspace filter, or None for platform-wide reads."""

    if workspace_id is None:
        if not can_read_platform(db, user):
            raise IdentityError("not found", status_code=404)
        return None
    if db.get(Workspace, workspace_id) is None:
        raise IdentityError("not found", status_code=404)
    if not can_read_workspace(db, user, workspace_id):
        raise IdentityError("not found", status_code=404)
    return workspace_id


def _count_map(db: Session, column, ids: Iterable[UUID]) -> dict[UUID, int]:
    wanted = [item for item in ids if item is not None]
    if not wanted:
        return {}
    rows = db.execute(
        select(column, func.count()).where(column.in_(wanted)).group_by(column)
    ).all()
    return {row[0]: int(row[1]) for row in rows if row[0] is not None}


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _artifact_read(row: Artifact) -> ArtifactRead:
    return ArtifactRead.model_validate(row)


def _metric_reads(evaluation: ModelEvaluation) -> list[EvaluationMetricRead]:
    return [
        EvaluationMetricRead.model_validate(row)
        for row in sorted(evaluation.metrics, key=lambda item: item.metric_name)
    ]


def _evaluation_read(evaluation: ModelEvaluation) -> ModelEvaluationRead:
    return ModelEvaluationRead(
        id=evaluation.id,
        evaluation_type=evaluation.evaluation_type,
        evaluation_scope=evaluation.evaluation_scope,
        status=evaluation.status,
        summary=dict(evaluation.summary or {}),
        metrics=_metric_reads(evaluation),
        created_at=evaluation.created_at,
    )


def _fold_metrics(
    fold: CVFoldRun, evaluations: list[ModelEvaluation]
) -> list[EvaluationMetricRead]:
    for row in evaluations:
        if row.evaluation_scope != "cv_fold":
            continue
        if int((row.summary or {}).get("fold_number") or 0) == fold.fold_number:
            return _metric_reads(row)
    return []


def _cv_folds(
    candidate: ExperimentCandidate,
) -> list[CVFoldRead]:
    evaluations = list(candidate.evaluations)
    return [
        CVFoldRead(
            id=fold.id,
            fold_number=fold.fold_number,
            train_row_count=fold.train_row_count,
            validation_row_count=fold.validation_row_count,
            status=fold.status,
            duration_ms=fold.duration_ms,
            metrics=_fold_metrics(fold, evaluations),
        )
        for fold in sorted(candidate.cv_fold_runs, key=lambda row: row.fold_number)
    ]


def _candidate_summary(candidate: ExperimentCandidate) -> CandidateSummary:
    return CandidateSummary(
        id=candidate.id,
        candidate_key=candidate.candidate_key,
        status=candidate.status,
        model_family=candidate.model_family,
        algorithm=candidate.algorithm,
        implementation_class=candidate.implementation_class,
        search_stage=candidate.search_stage,
        trial_number=candidate.trial_number,
        hyperparameters=[
            HyperparameterRead.model_validate(row)
            for row in sorted(
                candidate.hyperparameters, key=lambda item: item.parameter_name
            )
        ],
        cv_folds=_cv_folds(candidate),
        evaluations=[
            _evaluation_read(row)
            for row in sorted(
                candidate.evaluations,
                key=lambda item: (item.evaluation_scope, str(item.created_at), str(item.id)),
            )
        ],
    )


def _pipeline_run_list_item(
    experiment: Experiment, *, candidate_count: int
) -> PipelineRunListItem:
    workflow_run = experiment.workflow_run
    workflow = workflow_run.workflow if workflow_run is not None else None
    project = experiment.project
    return PipelineRunListItem(
        id=experiment.id,
        workspace_id=experiment.workspace_id,
        project_id=experiment.project_id,
        project_name=project.name if project is not None else None,
        workflow_id=workflow.id if workflow is not None else (
            workflow_run.workflow_id if workflow_run is not None else None
        ),
        workflow_run_id=experiment.workflow_run_id,
        pipeline_id=experiment.pipeline_id,
        pipeline_name=experiment.pipeline_name,
        status=experiment.status,
        candidate_count=candidate_count,
        started_at=experiment.started_at,
        ended_at=experiment.ended_at,
        created_at=experiment.created_at,
    )


def _workflow_list_item(
    workflow: MlWorkflow,
    *,
    version_count: int,
    run_count: int,
    pipeline_count: int,
) -> WorkflowListItem:
    return WorkflowListItem(
        id=workflow.id,
        workspace_id=workflow.workspace_id,
        project_id=workflow.project_id,
        name=workflow.name,
        slug=workflow.slug,
        status=workflow.status,
        version_count=version_count,
        run_count=run_count,
        pipeline_count=pipeline_count,
        created_at=workflow.created_at,
    )


def _model_version_list_item(row: ModelVersion) -> ModelVersionListItem:
    return ModelVersionListItem(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        version=row.version,
        pipeline_run_id=row.pipeline_run_id,
        selected_candidate_id=row.selected_candidate_id,
        dataset_id=row.dataset_id,
        created_at=row.created_at,
    )


def _dataset_list_item(row: Dataset) -> DatasetListItem:
    return DatasetListItem(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        dataset_asset_id=row.dataset_asset_id,
        name=row.name,
        version=row.version,
        content_digest=row.content_digest,
        row_count=row.row_count,
        column_count=row.column_count,
        created_at=row.created_at,
    )


def _visible_llm(
    db: Session, user: User, workspace_id: UUID, rows: list[LlmInvocation]
) -> list[LlmInvocationRead]:
    capabilities = capability_matrix(db, user, workspace_id)
    visible = []
    for row in rows:
        if row.purpose.startswith("semantic_") and not capabilities[SEMANTIC_LLM_AUDIT]:
            continue
        if row.purpose.startswith("pipeline_audit_") and not capabilities[OPENAI_PIPELINE_AUDIT]:
            continue
        visible.append(LlmInvocationRead.model_validate(row))
    return visible


def _feature_engineering(feature_set: FeatureSet | None) -> FeatureEngineeringSection:
    if feature_set is None or not feature_set.versions:
        return FeatureEngineeringSection()
    version = max(feature_set.versions, key=lambda row: row.version)
    features = []
    for feature in sorted(version.features, key=lambda row: row.name):
        features.append(
            FeatureRead(
                id=feature.id,
                name=feature.name,
                feature_type=feature.feature_type,
                output_dtype=feature.output_dtype,
                definition=feature.definition,
                status=feature.status,
                transformations=[
                    FeatureTransformationRead.model_validate(row)
                    for row in sorted(feature.transformations, key=lambda item: item.sequence)
                ],
                lineage=[
                    FeatureLineageRead(
                        source_dataset_column_id=row.source_dataset_column_id,
                        source_column_name=(
                            row.source_dataset_column.name
                            if row.source_dataset_column is not None
                            else None
                        ),
                        lineage_relationship=row.relationship,
                    )
                    for row in feature.lineage
                ],
            )
        )
    return FeatureEngineeringSection(
        feature_set_id=feature_set.id,
        feature_set_version_id=version.id,
        feature_set_version=version.version,
        content_digest=version.content_digest,
        features=features,
    )


def _candidate_algorithms(plan: dict[str, Any]) -> list[Any]:
    algorithms = (
        plan.get("recommended_model_family_hints")
        or plan.get("candidate_algorithms")
        or plan.get("families")
        or plan.get("model_families")
        or []
    )
    return list(algorithms) if isinstance(algorithms, list) else []


def _development_plan(experiment: Experiment, result: dict[str, Any]) -> DevelopmentPlanSection:
    row = experiment.scientific_plan
    task = _as_dict(result.get("task"))
    if row is not None:
        full = _as_dict(row.full_plan)
        plan = _as_dict(full.get("model_development_plan"))
        metric = _as_dict(full.get("metric_plan") or plan.get("metric_plan"))
        return DevelopmentPlanSection(
            task_type=row.task_type or plan.get("task_type") or task.get("task_type"),
            primary_metric=(
                row.primary_metric
                or metric.get("primary_metric")
                or plan.get("primary_metric")
                or task.get("evaluation_metric")
            ),
            excluded_features=list(plan.get("excluded_features") or []),
            candidate_algorithms=_candidate_algorithms(plan),
            plan_version=plan.get("plan_version") or plan.get("version"),
        )
    plan = _as_dict(result.get("model_development_plan"))
    metric = _as_dict(plan.get("metric_plan") or result.get("metric_plan"))
    profile = _as_dict(plan.get("problem_profile"))
    return DevelopmentPlanSection(
        task_type=plan.get("task_type") or profile.get("task_type") or task.get("task_type"),
        primary_metric=(
            plan.get("primary_metric")
            or metric.get("primary_metric")
            or task.get("evaluation_metric")
        ),
        excluded_features=list(plan.get("excluded_features") or []),
        candidate_algorithms=_candidate_algorithms(plan),
        plan_version=plan.get("plan_version") or plan.get("version"),
    )


def _split_validation(experiment: Experiment, result: dict[str, Any]) -> SplitValidationSection:
    row = experiment.scientific_plan
    if row is not None:
        full = _as_dict(row.full_plan)
        holdout = _as_dict(full.get("holdout_plan") or result.get("holdout_plan"))
        validation = _as_dict(full.get("validation_plan") or result.get("validation_plan"))
        split = _as_dict(full.get("split") or result.get("split"))
        holdout = {
            **holdout,
            "strategy": row.holdout_strategy,
            "test_size": row.holdout_test_size,
            "group_column": row.group_column,
            "time_column": row.time_column,
        }
        validation = {
            **validation,
            "strategy": row.validation_strategy,
            "requested_folds": row.requested_folds,
            "actual_folds": row.actual_folds,
            "group_column": row.group_column,
            "time_column": row.time_column,
        }
        return SplitValidationSection(
            split=split,
            holdout_plan=holdout,
            validation_plan=validation,
        )
    return SplitValidationSection(
        split=_as_dict(result.get("split")),
        holdout_plan=_as_dict(result.get("holdout_plan")),
        validation_plan=_as_dict(result.get("validation_plan")),
    )


def _verification_section(
    result: dict[str, Any], attempts: list[MlRunVerification]
) -> VerificationSection:
    payload = _as_dict(result.get("deterministic_verification"))
    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    failures = [row for row in checks if isinstance(row, dict) and row.get("status") == "FAIL"]
    warnings = [row for row in checks if isinstance(row, dict) and row.get("status") == "WARN"]
    return VerificationSection(
        overall_status=payload.get("overall_status"),
        failure_count=len(failures),
        warning_count=len(warnings),
        check_count=len(checks),
        attempts=[
            VerificationAttemptRead(
                id=row.id,
                audit_mode=row.audit_mode,
                deterministic_status=row.deterministic_status,
                llm_status=row.llm_status,
                started_at=row.started_at,
                completed_at=row.completed_at,
            )
            for row in attempts
        ],
    )


def _code_runtime(
    model_version: ModelVersion | None, snapshots: list[CodeSnapshot]
) -> CodeRuntimeSection:
    runtime = None
    snapshot = None
    if model_version is not None:
        runtime = model_version.runtime_environment
        snapshot = model_version.code_snapshot
    if snapshot is None and snapshots:
        snapshot = sorted(snapshots, key=lambda row: row.created_at)[-1]
        if runtime is None:
            runtime = snapshot.runtime_environment
    return CodeRuntimeSection(
        runtime_environment=(
            RuntimeEnvironmentRead.model_validate(runtime) if runtime is not None else None
        ),
        code_snapshot=(
            CodeSnapshotRead.model_validate(snapshot) if snapshot is not None else None
        ),
    )


def _run_artifacts(db: Session, experiment: Experiment) -> list[Artifact]:
    rows = artifacts_for_pipeline_run(db, experiment)
    model_version = experiment.model_version
    extra = artifacts_for_model_version(db, model_version) if model_version is not None else []
    by_id = {row.id: row for row in [*rows, *extra]}
    return [by_id[key] for key in by_id]


_CANDIDATE_LOAD = (
    selectinload(ExperimentCandidate.hyperparameters),
    selectinload(ExperimentCandidate.cv_fold_runs),
    selectinload(ExperimentCandidate.evaluations).selectinload(ModelEvaluation.metrics),
)

_PIPELINE_RUN_LOAD = (
    joinedload(Experiment.project),
    joinedload(Experiment.dataset),
    joinedload(Experiment.pipeline),
    joinedload(Experiment.pipeline_version),
    joinedload(Experiment.workflow_run).options(
        joinedload(WorkflowRun.workflow),
        joinedload(WorkflowRun.workflow_version),
        joinedload(WorkflowRun.problem_spec),
        selectinload(WorkflowRun.inputs).joinedload(WorkflowRunInput.dataset),
    ),
    selectinload(Experiment.stage_runs),
    selectinload(Experiment.data_quality_findings),
    selectinload(Experiment.data_preparation_decisions),
    selectinload(Experiment.preprocessing_steps),
    selectinload(Experiment.candidates).options(*_CANDIDATE_LOAD),
    selectinload(Experiment.model_selection_decisions),
    joinedload(Experiment.scientific_plan),
    selectinload(Experiment.code_snapshots).joinedload(CodeSnapshot.runtime_environment),
    selectinload(Experiment.llm_invocations),
    joinedload(Experiment.model_version).options(
        joinedload(ModelVersion.runtime_environment),
        joinedload(ModelVersion.code_snapshot).joinedload(CodeSnapshot.runtime_environment),
        joinedload(ModelVersion.model_artifact),
        selectinload(ModelVersion.evaluations).selectinload(ModelEvaluation.metrics),
    ),
)

_FEATURE_SET_LOAD = (
    selectinload(FeatureSet.versions)
    .selectinload(FeatureSetVersion.features)
    .options(
        selectinload(Feature.transformations),
        selectinload(Feature.lineage).joinedload(FeatureLineage.source_dataset_column),
    ),
)


class ProjectDetailQuery:
    def list(
        self,
        db: Session,
        user: User,
        *,
        workspace_id: UUID | None,
        limit: int | None = None,
    ) -> list[ProjectListItem]:
        scope = resolve_explorer_scope(db, user, workspace_id)
        stmt = select(Project).order_by(Project.created_at.desc(), Project.id).limit(
            _limit(limit)
        )
        if scope is not None:
            stmt = stmt.where(Project.workspace_id == scope)
        projects = list(db.scalars(stmt))
        ids = [row.id for row in projects]
        runs = _count_map(db, Experiment.project_id, ids)
        workflows = _count_map(db, MlWorkflow.project_id, ids)
        datasets = _count_map(db, Dataset.project_id, ids)
        return [
            ProjectListItem(
                id=row.id,
                workspace_id=row.workspace_id,
                name=row.name,
                slug=row.slug,
                status=row.status,
                pipeline_run_count=runs.get(row.id, 0),
                workflow_count=workflows.get(row.id, 0),
                dataset_count=datasets.get(row.id, 0),
                created_at=row.created_at,
            )
            for row in projects
        ]

    def get(
        self,
        db: Session,
        user: User,
        project_id: UUID,
        *,
        workspace_id: UUID | None,
    ) -> ProjectDetailRead | None:
        scope = resolve_explorer_scope(db, user, workspace_id)
        stmt = (
            select(Project)
            .options(
                selectinload(Project.problem_specs),
                selectinload(Project.datasets),
                selectinload(Project.ml_workflows),
                selectinload(Project.pipeline_runs).options(
                    joinedload(Experiment.project),
                    joinedload(Experiment.workflow_run).joinedload(WorkflowRun.workflow),
                ),
            )
            .where(Project.id == project_id)
        )
        if scope is not None:
            stmt = stmt.where(Project.workspace_id == scope)
        project = db.scalar(stmt)
        if project is None:
            return None
        model_versions = list(
            db.scalars(
                select(ModelVersion)
                .where(ModelVersion.project_id == project.id)
                .order_by(ModelVersion.created_at.desc(), ModelVersion.id)
            )
        )
        run_ids = [row.id for row in project.pipeline_runs]
        candidate_counts = _count_map(db, ExperimentCandidate.experiment_id, run_ids)
        workflow_ids = [row.id for row in project.ml_workflows]
        version_counts = _count_map(db, WorkflowVersion.workflow_id, workflow_ids)
        run_counts = _count_map(db, WorkflowRun.workflow_id, workflow_ids)
        pipeline_counts = _count_map(db, Pipeline.workflow_id, workflow_ids)
        return ProjectDetailRead(
            id=project.id,
            workspace_id=project.workspace_id,
            name=project.name,
            slug=project.slug,
            description=project.description,
            status=project.status,
            created_at=project.created_at,
            problem_specs=[
                ProblemSpecSummary.model_validate(row)
                for row in sorted(project.problem_specs, key=lambda item: item.version)
            ],
            datasets=[_dataset_list_item(row) for row in project.datasets],
            workflows=[
                _workflow_list_item(
                    row,
                    version_count=version_counts.get(row.id, 0),
                    run_count=run_counts.get(row.id, 0),
                    pipeline_count=pipeline_counts.get(row.id, 0),
                )
                for row in project.ml_workflows
            ],
            pipeline_runs=[
                _pipeline_run_list_item(
                    row, candidate_count=candidate_counts.get(row.id, 0)
                )
                for row in sorted(
                    project.pipeline_runs, key=lambda item: item.created_at, reverse=True
                )
            ],
            model_versions=[
                _model_version_list_item(row)
                for row in model_versions
            ],
        )


class WorkflowDetailQuery:
    def list(
        self,
        db: Session,
        user: User,
        *,
        workspace_id: UUID | None,
        limit: int | None = None,
    ) -> list[WorkflowListItem]:
        scope = resolve_explorer_scope(db, user, workspace_id)
        stmt = select(MlWorkflow).order_by(MlWorkflow.created_at.desc(), MlWorkflow.id).limit(
            _limit(limit)
        )
        if scope is not None:
            stmt = stmt.where(MlWorkflow.workspace_id == scope)
        workflows = list(db.scalars(stmt))
        ids = [row.id for row in workflows]
        version_counts = _count_map(db, WorkflowVersion.workflow_id, ids)
        run_counts = _count_map(db, WorkflowRun.workflow_id, ids)
        pipeline_counts = _count_map(db, Pipeline.workflow_id, ids)
        return [
            _workflow_list_item(
                row,
                version_count=version_counts.get(row.id, 0),
                run_count=run_counts.get(row.id, 0),
                pipeline_count=pipeline_counts.get(row.id, 0),
            )
            for row in workflows
        ]

    def get(
        self,
        db: Session,
        user: User,
        workflow_id: UUID,
        *,
        workspace_id: UUID | None,
    ) -> WorkflowDetailRead | None:
        scope = resolve_explorer_scope(db, user, workspace_id)
        stmt = (
            select(MlWorkflow)
            .options(
                selectinload(MlWorkflow.versions),
                selectinload(MlWorkflow.pipelines),
                selectinload(MlWorkflow.runs).selectinload(WorkflowRun.pipeline_runs).options(
                    joinedload(Experiment.project),
                    joinedload(Experiment.workflow_run).joinedload(WorkflowRun.workflow),
                ),
            )
            .where(MlWorkflow.id == workflow_id)
        )
        if scope is not None:
            stmt = stmt.where(MlWorkflow.workspace_id == scope)
        workflow = db.scalar(stmt)
        if workflow is None:
            return None
        experiments = [
            experiment
            for run in workflow.runs
            for experiment in run.pipeline_runs
        ]
        candidate_counts = _count_map(
            db, ExperimentCandidate.experiment_id, [row.id for row in experiments]
        )
        return WorkflowDetailRead(
            id=workflow.id,
            workspace_id=workflow.workspace_id,
            project_id=workflow.project_id,
            name=workflow.name,
            slug=workflow.slug,
            description=workflow.description,
            business_objective=workflow.business_objective,
            status=workflow.status,
            created_at=workflow.created_at,
            versions=[
                WorkflowVersionSummary.model_validate(row)
                for row in sorted(workflow.versions, key=lambda item: item.version)
            ],
            pipelines=[PipelineSummary.model_validate(row) for row in workflow.pipelines],
            runs=[
                _pipeline_run_list_item(
                    row, candidate_count=candidate_counts.get(row.id, 0)
                )
                for row in sorted(experiments, key=lambda item: item.created_at, reverse=True)
            ],
        )


class PipelineRunDetailQuery:
    def list(
        self,
        db: Session,
        user: User,
        *,
        workspace_id: UUID | None,
        limit: int | None = None,
    ) -> list[PipelineRunListItem]:
        scope = resolve_explorer_scope(db, user, workspace_id)
        stmt = (
            select(Experiment)
            .options(
                joinedload(Experiment.project),
                joinedload(Experiment.workflow_run).joinedload(WorkflowRun.workflow),
            )
            .order_by(Experiment.created_at.desc(), Experiment.id)
            .limit(_limit(limit))
        )
        if scope is not None:
            stmt = stmt.where(Experiment.workspace_id == scope)
        runs = list(db.scalars(stmt).unique())
        counts = _count_map(db, ExperimentCandidate.experiment_id, [row.id for row in runs])
        return [
            _pipeline_run_list_item(row, candidate_count=counts.get(row.id, 0))
            for row in runs
        ]

    def get(
        self,
        db: Session,
        user: User,
        pipeline_run_id: UUID,
        *,
        workspace_id: UUID | None,
    ) -> PipelineRunDetailRead | None:
        scope = resolve_explorer_scope(db, user, workspace_id)
        stmt = (
            select(Experiment)
            .options(*_PIPELINE_RUN_LOAD)
            .where(Experiment.id == pipeline_run_id)
        )
        if scope is not None:
            stmt = stmt.where(Experiment.workspace_id == scope)
        experiment = db.scalar(stmt)
        if experiment is None:
            return None
        feature_set = db.scalar(
            select(FeatureSet)
            .options(*_FEATURE_SET_LOAD)
            .where(
                FeatureSet.workspace_id == experiment.workspace_id,
                FeatureSet.name == f"pipeline-run-{experiment.id}",
            )
        )
        attempts = list(
            db.scalars(
                select(MlRunVerification)
                .where(MlRunVerification.experiment_id == experiment.id)
                .order_by(MlRunVerification.created_at.desc(), MlRunVerification.id)
            )
        )
        artifacts = _run_artifacts(db, experiment)
        result = _as_dict(experiment.result)
        workflow_run = experiment.workflow_run
        workflow = workflow_run.workflow if workflow_run is not None else None
        winner = next(iter(experiment.model_selection_decisions), None)
        model_version = experiment.model_version
        datasets = []
        if experiment.dataset is not None:
            datasets.append(
                DatasetVersionSummary(
                    id=experiment.dataset.id,
                    dataset_asset_id=experiment.dataset.dataset_asset_id,
                    name=experiment.dataset.name,
                    version=experiment.dataset.version,
                    content_digest=experiment.dataset.content_digest,
                    row_count=experiment.dataset.row_count,
                    column_count=experiment.dataset.column_count,
                    input_role="training",
                )
            )
        if workflow_run is not None:
            for item in workflow_run.inputs:
                if item.dataset is None:
                    continue
                if any(row.id == item.dataset.id for row in datasets):
                    continue
                datasets.append(
                    DatasetVersionSummary(
                        id=item.dataset.id,
                        dataset_asset_id=item.dataset.dataset_asset_id,
                        name=item.dataset.name,
                        version=item.dataset.version,
                        content_digest=item.dataset.content_digest,
                        row_count=item.dataset.row_count,
                        column_count=item.dataset.column_count,
                        input_role=item.input_role,
                    )
                )
        problem = None
        if workflow_run is not None and workflow_run.problem_spec is not None:
            problem = ProblemSpecSummary.model_validate(workflow_run.problem_spec)
        project = experiment.project
        return PipelineRunDetailRead(
            identity=IdentitySection(
                pipeline_run_id=experiment.id,
                workspace_id=experiment.workspace_id,
                status=experiment.status,
                pipeline_name=experiment.pipeline_name,
                pipeline_index=experiment.pipeline_index,
                pipeline_purpose=experiment.pipeline_purpose,
                failure_reason=experiment.failure_reason,
                seed=experiment.seed,
                git_commit=experiment.git_commit,
                started_at=experiment.started_at,
                ended_at=experiment.ended_at,
                created_at=experiment.created_at,
            ),
            project=ProjectSummary(
                id=project.id if project is not None else experiment.project_id,
                workspace_id=experiment.workspace_id,
                name=project.name if project is not None else None,
                slug=project.slug if project is not None else None,
                status=project.status if project is not None else None,
            ),
            problem=problem,
            datasets=datasets,
            workflow=(
                _workflow_list_item(workflow, version_count=0, run_count=0, pipeline_count=0)
                if workflow is not None
                else None
            ),
            workflow_version=(
                WorkflowVersionSummary.model_validate(workflow_run.workflow_version)
                if workflow_run is not None and workflow_run.workflow_version is not None
                else None
            ),
            pipeline=(
                PipelineSummary.model_validate(experiment.pipeline)
                if experiment.pipeline is not None
                else None
            ),
            pipeline_version=(
                PipelineVersionSummary.model_validate(experiment.pipeline_version)
                if experiment.pipeline_version is not None
                else None
            ),
            stage_timeline=[
                StageTimelineItem.model_validate(row)
                for row in sorted(experiment.stage_runs, key=lambda item: item.sequence)
            ],
            data_quality=[
                DataQualityFindingRead.model_validate(row)
                for row in experiment.data_quality_findings
            ],
            preparation_decisions=[
                PreparationDecisionRead.model_validate(row)
                for row in experiment.data_preparation_decisions
            ],
            feature_engineering=_feature_engineering(feature_set),
            preprocessing=[
                PreprocessingStepRead.model_validate(row)
                for row in sorted(experiment.preprocessing_steps, key=lambda item: item.sequence)
            ],
            development_plan=_development_plan(experiment, result),
            split_validation=_split_validation(experiment, result),
            model_candidates=[
                _candidate_summary(row)
                for row in sorted(experiment.candidates, key=lambda item: item.candidate_key)
            ],
            winner_decision=(
                WinnerDecisionRead(
                    id=winner.id,
                    selected_candidate_id=winner.selected_candidate_id,
                    runner_up_candidate_id=winner.runner_up_candidate_id,
                    selection_metric=winner.selection_metric,
                    selected_score=winner.selected_score,
                    selection_policy=winner.selection_policy,
                    reason=winner.reason,
                    evidence=dict(winner.evidence or {}),
                    locked_at=winner.locked_at,
                )
                if winner is not None
                else None
            ),
            final_model=(
                FinalModelRead(
                    model_version_id=model_version.id,
                    version=model_version.version,
                    selected_candidate_id=model_version.selected_candidate_id,
                    dataset_id=model_version.dataset_id,
                    feature_set_version_id=model_version.feature_set_version_id,
                    model_artifact_id=model_version.model_artifact_id,
                    preprocessor_artifact_id=model_version.preprocessor_artifact_id,
                    artifact_uri=model_version.artifact_uri,
                    content_digest=model_version.content_digest,
                    metrics=dict(model_version.metrics or {}),
                )
                if model_version is not None
                else None
            ),
            artifacts=[_artifact_read(row) for row in artifacts],
            code_runtime=_code_runtime(model_version, list(experiment.code_snapshots)),
            verification=_verification_section(result, attempts),
            llm_invocations=_visible_llm(
                db, user, experiment.workspace_id, list(experiment.llm_invocations)
            ),
        )


class ModelCandidateDetailQuery:
    def get(
        self,
        db: Session,
        user: User,
        candidate_id: UUID,
        *,
        workspace_id: UUID | None,
    ) -> ModelCandidateDetailRead | None:
        scope = resolve_explorer_scope(db, user, workspace_id)
        stmt = (
            select(ExperimentCandidate)
            .options(
                *_CANDIDATE_LOAD,
                joinedload(ExperimentCandidate.model_version),
            )
            .where(ExperimentCandidate.id == candidate_id)
        )
        if scope is not None:
            stmt = stmt.where(ExperimentCandidate.workspace_id == scope)
        candidate = db.scalar(stmt)
        if candidate is None:
            return None
        summary = _candidate_summary(candidate)
        return ModelCandidateDetailRead(
            id=candidate.id,
            workspace_id=candidate.workspace_id,
            project_id=candidate.project_id,
            pipeline_run_id=candidate.experiment_id,
            candidate_key=candidate.candidate_key,
            fingerprint=candidate.fingerprint,
            status=candidate.status,
            model_family=candidate.model_family,
            algorithm=candidate.algorithm,
            implementation_library=candidate.implementation_library,
            implementation_class=candidate.implementation_class,
            library_version=candidate.library_version,
            search_stage=candidate.search_stage,
            trial_number=candidate.trial_number,
            feature_set_version_id=candidate.feature_set_version_id,
            hyperparameters=summary.hyperparameters,
            cv_folds=summary.cv_folds,
            evaluations=summary.evaluations,
            selected=candidate.model_version is not None,
        )


class ModelVersionDetailQuery:
    def list(
        self,
        db: Session,
        user: User,
        *,
        workspace_id: UUID | None,
        limit: int | None = None,
    ) -> list[ModelVersionListItem]:
        scope = resolve_explorer_scope(db, user, workspace_id)
        stmt = (
            select(ModelVersion)
            .order_by(ModelVersion.created_at.desc(), ModelVersion.id)
            .limit(_limit(limit))
        )
        if scope is not None:
            stmt = stmt.where(ModelVersion.workspace_id == scope)
        return [_model_version_list_item(row) for row in db.scalars(stmt)]

    def get(
        self,
        db: Session,
        user: User,
        model_version_id: UUID,
        *,
        workspace_id: UUID | None,
    ) -> ModelVersionDetailRead | None:
        scope = resolve_explorer_scope(db, user, workspace_id)
        stmt = (
            select(ModelVersion)
            .options(
                joinedload(ModelVersion.runtime_environment),
                joinedload(ModelVersion.code_snapshot).joinedload(
                    CodeSnapshot.runtime_environment
                ),
                selectinload(ModelVersion.evaluations).selectinload(ModelEvaluation.metrics),
                joinedload(ModelVersion.selected_candidate).options(*_CANDIDATE_LOAD),
            )
            .where(ModelVersion.id == model_version_id)
        )
        if scope is not None:
            stmt = stmt.where(ModelVersion.workspace_id == scope)
        row = db.scalar(stmt)
        if row is None:
            return None
        artifacts = artifacts_for_model_version(db, row)
        candidate = row.selected_candidate
        return ModelVersionDetailRead(
            id=row.id,
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            version=row.version,
            workflow_id=row.workflow_id,
            workflow_version_id=row.workflow_version_id,
            workflow_run_id=row.workflow_run_id,
            pipeline_id=row.pipeline_id,
            pipeline_version_id=row.pipeline_version_id,
            pipeline_run_id=row.pipeline_run_id,
            selected_candidate_id=row.selected_candidate_id,
            dataset_id=row.dataset_id,
            feature_set_version_id=row.feature_set_version_id,
            artifact_uri=row.artifact_uri,
            content_digest=row.content_digest,
            metrics=dict(row.metrics or {}),
            created_at=row.created_at,
            candidate=_candidate_summary(candidate) if candidate is not None else None,
            artifacts=[_artifact_read(item) for item in artifacts],
            code_runtime=_code_runtime(row, []),
            evaluations=[_evaluation_read(item) for item in row.evaluations],
        )


def list_workspaces(
    db: Session, user: User, *, limit: int | None = None
) -> list[WorkspaceListItem]:
    resolve_explorer_scope(db, user, None)
    workspaces = list(
        db.scalars(
            select(Workspace)
            .order_by(Workspace.name, Workspace.id)
            .limit(_limit(limit))
        )
    )
    ids = [row.id for row in workspaces]
    projects = _count_map(db, Project.workspace_id, ids)
    runs = _count_map(db, Experiment.workspace_id, ids)
    return [
        WorkspaceListItem(
            id=row.id,
            slug=row.slug,
            name=row.name,
            kind=row.kind,
            project_count=projects.get(row.id, 0),
            pipeline_run_count=runs.get(row.id, 0),
            created_at=row.created_at,
        )
        for row in workspaces
    ]


def list_datasets(
    db: Session,
    user: User,
    *,
    workspace_id: UUID | None,
    limit: int | None = None,
) -> list[DatasetListItem]:
    scope = resolve_explorer_scope(db, user, workspace_id)
    stmt = select(Dataset).order_by(Dataset.created_at.desc(), Dataset.id).limit(
        _limit(limit)
    )
    if scope is not None:
        stmt = stmt.where(Dataset.workspace_id == scope)
    return [_dataset_list_item(row) for row in db.scalars(stmt)]


project_detail_query = ProjectDetailQuery()
workflow_detail_query = WorkflowDetailQuery()
pipeline_run_detail_query = PipelineRunDetailQuery()
model_candidate_detail_query = ModelCandidateDetailQuery()
model_version_detail_query = ModelVersionDetailQuery()
