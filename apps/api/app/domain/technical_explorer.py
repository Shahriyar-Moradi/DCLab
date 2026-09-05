"""Read models for the shared Admin / Customer technical explorer.

Both surfaces use the same query services. Authorization changes visibility,
not schema. Scientific tables are not duplicated here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.observability import LlmInvocationRead
from app.domain.reproducibility import (
    ArtifactRead,
    CodeSnapshotRead,
    RuntimeEnvironmentRead,
)


class WorkspaceListItem(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: str
    project_count: int
    pipeline_run_count: int
    created_at: datetime


class ProjectListItem(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    slug: str
    status: str
    pipeline_run_count: int
    workflow_count: int
    dataset_count: int
    created_at: datetime


class DatasetListItem(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    dataset_asset_id: UUID
    name: str
    version: str
    content_digest: str | None
    row_count: int
    column_count: int
    created_at: datetime


class ProblemSpecSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    task_type: str
    target_column: str | None
    primary_metric: str | None
    business_objective: str
    status: str


class WorkflowListItem(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    name: str
    slug: str
    status: str
    version_count: int
    run_count: int
    pipeline_count: int
    created_at: datetime


class WorkflowVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    content_digest: str
    locked_at: datetime | None
    created_at: datetime


class PipelineSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    purpose: str
    status: str


class PipelineVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    content_digest: str | None = None
    locked_at: datetime | None = None


class PipelineRunListItem(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    project_name: str | None
    workflow_id: UUID | None
    workflow_run_id: UUID | None
    pipeline_id: UUID | None
    pipeline_name: str
    status: str
    candidate_count: int
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime


class ModelVersionListItem(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    version: str
    pipeline_run_id: UUID
    selected_candidate_id: UUID
    dataset_id: UUID
    created_at: datetime


class IdentitySection(BaseModel):
    pipeline_run_id: UUID
    workspace_id: UUID
    status: str
    pipeline_name: str
    pipeline_index: int
    pipeline_purpose: str
    failure_reason: str | None
    seed: int
    git_commit: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime


class ProjectSummary(BaseModel):
    id: UUID | None
    workspace_id: UUID
    name: str | None
    slug: str | None
    status: str | None


class DatasetVersionSummary(BaseModel):
    id: UUID
    dataset_asset_id: UUID
    name: str
    version: str
    content_digest: str | None
    row_count: int
    column_count: int
    input_role: str | None = None


class StageTimelineItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    stage_key: str
    stage_type: str
    sequence: int
    name: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: float | None
    failure_code: str | None
    failure_reason: str | None


class DataQualityFindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    finding_type: str
    severity: str
    dataset_id: UUID
    dataset_column_id: UUID | None
    evidence: dict[str, Any]
    created_at: datetime


class PreparationDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    decision_type: str
    strategy: str
    parameter_value: dict[str, Any]
    reason: str
    evidence: dict[str, Any]
    decision_source: str
    dataset_column_id: UUID | None
    created_at: datetime


class FeatureTransformationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    transformation_type: str
    transformer_class: str | None
    parameters: dict[str, Any]
    fit_required: bool


class FeatureLineageRead(BaseModel):
    source_dataset_column_id: UUID
    source_column_name: str | None
    lineage_relationship: str


class FeatureRead(BaseModel):
    id: UUID
    name: str
    feature_type: str
    output_dtype: str
    definition: str
    status: str
    transformations: list[FeatureTransformationRead] = Field(default_factory=list)
    lineage: list[FeatureLineageRead] = Field(default_factory=list)


class FeatureEngineeringSection(BaseModel):
    feature_set_id: UUID | None = None
    feature_set_version_id: UUID | None = None
    feature_set_version: int | None = None
    content_digest: str | None = None
    features: list[FeatureRead] = Field(default_factory=list)


class PreprocessingStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    column_scope: str
    transformer_type: str
    transformer_class: str
    parameters: dict[str, Any]
    fit_scope: str


class HyperparameterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    parameter_name: str
    value_json: Any
    source: str


class EvaluationMetricRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    metric_name: str
    metric_value: float
    threshold: float | None


class ModelEvaluationRead(BaseModel):
    id: UUID
    evaluation_type: str
    evaluation_scope: str
    status: str
    summary: dict[str, Any]
    metrics: list[EvaluationMetricRead] = Field(default_factory=list)
    created_at: datetime


class CVFoldRead(BaseModel):
    id: UUID
    fold_number: int
    train_row_count: int
    validation_row_count: int
    status: str
    duration_ms: float | None
    metrics: list[EvaluationMetricRead] = Field(default_factory=list)


class CandidateSummary(BaseModel):
    id: UUID
    candidate_key: str
    status: str
    model_family: str
    algorithm: str
    implementation_class: str | None
    search_stage: str | None
    trial_number: int | None
    hyperparameters: list[HyperparameterRead] = Field(default_factory=list)
    cv_folds: list[CVFoldRead] = Field(default_factory=list)
    evaluations: list[ModelEvaluationRead] = Field(default_factory=list)


class WinnerDecisionRead(BaseModel):
    id: UUID
    selected_candidate_id: UUID
    runner_up_candidate_id: UUID | None
    selection_metric: str
    selected_score: float
    selection_policy: str
    reason: str
    evidence: dict[str, Any]
    locked_at: datetime


class FinalModelRead(BaseModel):
    model_version_id: UUID | None
    version: str | None
    selected_candidate_id: UUID | None
    dataset_id: UUID | None
    feature_set_version_id: UUID | None
    model_artifact_id: UUID | None
    preprocessor_artifact_id: UUID | None
    artifact_uri: str | None
    content_digest: str | None
    metrics: dict[str, Any] = Field(default_factory=dict)


class CodeRuntimeSection(BaseModel):
    runtime_environment: RuntimeEnvironmentRead | None = None
    code_snapshot: CodeSnapshotRead | None = None


class VerificationAttemptRead(BaseModel):
    id: UUID
    audit_mode: str
    deterministic_status: str
    llm_status: str
    started_at: datetime
    completed_at: datetime | None


class VerificationSection(BaseModel):
    overall_status: str | None = None
    failure_count: int = 0
    warning_count: int = 0
    check_count: int = 0
    attempts: list[VerificationAttemptRead] = Field(default_factory=list)


class DevelopmentPlanSection(BaseModel):
    task_type: str | None = None
    primary_metric: str | None = None
    excluded_features: list[Any] = Field(default_factory=list)
    candidate_algorithms: list[Any] = Field(default_factory=list)
    plan_version: Any = None


class SplitValidationSection(BaseModel):
    split: dict[str, Any] = Field(default_factory=dict)
    holdout_plan: dict[str, Any] = Field(default_factory=dict)
    validation_plan: dict[str, Any] = Field(default_factory=dict)


class ProjectDetailRead(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    slug: str
    description: str
    status: str
    created_at: datetime
    problem_specs: list[ProblemSpecSummary] = Field(default_factory=list)
    datasets: list[DatasetListItem] = Field(default_factory=list)
    workflows: list[WorkflowListItem] = Field(default_factory=list)
    pipeline_runs: list[PipelineRunListItem] = Field(default_factory=list)
    model_versions: list[ModelVersionListItem] = Field(default_factory=list)


class WorkflowDetailRead(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    name: str
    slug: str
    description: str
    business_objective: str
    status: str
    created_at: datetime
    versions: list[WorkflowVersionSummary] = Field(default_factory=list)
    pipelines: list[PipelineSummary] = Field(default_factory=list)
    runs: list[PipelineRunListItem] = Field(default_factory=list)


class PipelineRunDetailRead(BaseModel):
    identity: IdentitySection
    project: ProjectSummary
    problem: ProblemSpecSummary | None
    datasets: list[DatasetVersionSummary] = Field(default_factory=list)
    workflow: WorkflowListItem | None = None
    workflow_version: WorkflowVersionSummary | None = None
    pipeline: PipelineSummary | None = None
    pipeline_version: PipelineVersionSummary | None = None
    stage_timeline: list[StageTimelineItem] = Field(default_factory=list)
    data_quality: list[DataQualityFindingRead] = Field(default_factory=list)
    preparation_decisions: list[PreparationDecisionRead] = Field(default_factory=list)
    feature_engineering: FeatureEngineeringSection = Field(
        default_factory=FeatureEngineeringSection
    )
    preprocessing: list[PreprocessingStepRead] = Field(default_factory=list)
    development_plan: DevelopmentPlanSection = Field(default_factory=DevelopmentPlanSection)
    split_validation: SplitValidationSection = Field(default_factory=SplitValidationSection)
    model_candidates: list[CandidateSummary] = Field(default_factory=list)
    winner_decision: WinnerDecisionRead | None = None
    final_model: FinalModelRead | None = None
    artifacts: list[ArtifactRead] = Field(default_factory=list)
    code_runtime: CodeRuntimeSection = Field(default_factory=CodeRuntimeSection)
    verification: VerificationSection = Field(default_factory=VerificationSection)
    llm_invocations: list[LlmInvocationRead] = Field(default_factory=list)


class ModelCandidateDetailRead(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    pipeline_run_id: UUID
    candidate_key: str
    fingerprint: str
    status: str
    model_family: str
    algorithm: str
    implementation_library: str | None
    implementation_class: str | None
    library_version: str | None
    search_stage: str | None
    trial_number: int | None
    feature_set_version_id: UUID | None
    hyperparameters: list[HyperparameterRead] = Field(default_factory=list)
    cv_folds: list[CVFoldRead] = Field(default_factory=list)
    evaluations: list[ModelEvaluationRead] = Field(default_factory=list)
    selected: bool = False


class ModelVersionDetailRead(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    version: str
    workflow_id: UUID
    workflow_version_id: UUID | None
    workflow_run_id: UUID
    pipeline_id: UUID | None
    pipeline_version_id: UUID | None
    pipeline_run_id: UUID
    selected_candidate_id: UUID
    dataset_id: UUID
    feature_set_version_id: UUID | None
    artifact_uri: str | None
    content_digest: str
    metrics: dict[str, Any]
    created_at: datetime
    candidate: CandidateSummary | None = None
    artifacts: list[ArtifactRead] = Field(default_factory=list)
    code_runtime: CodeRuntimeSection = Field(default_factory=CodeRuntimeSection)
    evaluations: list[ModelEvaluationRead] = Field(default_factory=list)
