"""Deterministic model-build reproduction IR and generated Python sections.

The spec is built only from canonical persisted tables. Generated code is a
pure function of this IR plus a versioned template set.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

GENERATOR_VERSION = "dclab.model_build_reproduction.v2"
AUTHORIZED_DATASET_PATH_PLACEHOLDER = "<authorized-local-dataset-path>"
AUTHORIZED_SPLIT_ASSIGNMENT_PATH_PLACEHOLDER = "<authorized-local-split-assignment-path>"
# Generated code reads local copies from these variables, else the placeholders.
DATASET_PATH_ENV = "DCLAB_DATASET_PATH"
SPLIT_ASSIGNMENT_PATH_ENV = "DCLAB_SPLIT_ASSIGNMENT_PATH"


class ModelBuildGeneratedCode(BaseModel):
    generator_version: str
    spec_digest: str
    source: str
    digest: str
    helper_requirements: list[str] = Field(default_factory=list)
    code_generation_support_status: Literal["supported", "not_available", "not_applicable"]


class ModelBuildStageCode(ModelBuildGeneratedCode):
    key: str
    sequence: int
    title: str


class ReproductionColumn(BaseModel):
    name: str
    ordinal_position: int
    physical_dtype: str
    semantic_type: str | None = None
    role: str | None = None


class ReproductionDataset(BaseModel):
    dataset_id: UUID
    source_type: str
    version: str
    content_digest: str | None = None
    schema_digest: str | None = None
    row_count: int | None = None
    column_count: int | None = None
    columns: list[ReproductionColumn] = Field(default_factory=list)


class ReproductionTask(BaseModel):
    task_type: str | None = None
    target_column: str | None = None
    seed: int
    # Multiclass label code i is class_labels[i] (the runner's coding).
    class_labels: list[Any] | None = None


class ReproductionHoldoutPlan(BaseModel):
    strategy: str | None = None
    test_size: float | None = None
    group_column: str | None = None
    time_column: str | None = None
    plan_digest: str | None = None
    # The HoldoutPlan's own seed (splits use ``random_state or 42``).
    random_state: int | None = None


class ReproductionValidationPlan(BaseModel):
    strategy: str | None = None
    requested_folds: int | None = None
    actual_folds: int | None = None
    group_column: str | None = None
    time_column: str | None = None
    shuffle: bool | None = None
    random_state: int | None = None


class ReproductionMetricPlan(BaseModel):
    primary_metric: str | None = None


class ReproductionLeakageExclusion(BaseModel):
    column: str
    strategy: str
    reason: str | None = None


class ReproductionFeatureTransform(BaseModel):
    sequence: int
    transformation_type: str
    transformer_class: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class ReproductionFeature(BaseModel):
    name: str
    feature_type: str
    status: str
    origin: str
    transformations: list[ReproductionFeatureTransform] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class ReproductionPreprocessingStep(BaseModel):
    sequence: int
    column_scope: str
    transformer_type: str
    transformer_class: str
    fit_scope: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ReproductionCandidate(BaseModel):
    id: UUID
    fingerprint: str
    model_family: str
    algorithm: str
    implementation_library: str | None = None
    implementation_class: str | None = None
    library_version: str | None = None
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    is_winner: bool = False
    is_runner_up: bool = False
    cv_score: float | None = None
    status: str | None = None
    numerical_columns: list[str] = Field(default_factory=list)
    categorical_columns: list[str] = Field(default_factory=list)
    # Nested-tuned candidates: constructor values each outer fold trained with.
    fold_hyperparameters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Persisted primary-metric value per outer fold (cv_fold evaluations).
    cv_fold_scores: dict[str, float] = Field(default_factory=dict)


class ReproductionWinner(BaseModel):
    candidate_id: UUID | None = None
    fingerprint: str | None = None
    model_family: str | None = None
    algorithm: str | None = None
    implementation_library: str | None = None
    implementation_class: str | None = None
    library_version: str | None = None
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    selection_metric: str | None = None
    selected_score: float | None = None
    selection_policy: str | None = None
    reason: str | None = None
    runner_up_candidate_id: UUID | None = None
    runner_up_fingerprint: str | None = None


class ReproductionFinalRefit(BaseModel):
    model_version_id: UUID | None = None
    version: str | None = None
    content_digest: str | None = None
    selected_candidate_id: UUID | None = None
    selected_fingerprint: str | None = None


class ReproductionFinalHoldout(BaseModel):
    metrics: dict[str, float] = Field(default_factory=dict)
    model_version_id: UUID | None = None
    candidate_id: UUID | None = None


class ReproductionSplitAssignment(BaseModel):
    """The stored SplitPlan map this run was partitioned by (ADR 0006 §3)."""

    split_plan_id: UUID
    version: int | None = None
    assignment_digest: str
    row_count: int
    train_row_count: int
    holdout_row_count: int
    source_row_column: str
    # split_plan_assignment: holdout taken from the map, pool in dataset order;
    # holdout_plan_resplit: the run re-split with its HoldoutPlan (verified equal
    # to the map) and the pool keeps that split's row order.
    partitioned_by: Literal["split_plan_assignment", "holdout_plan_resplit"]


class ReproductionBranch(BaseModel):
    """What a branch run recorded about its change set; never re-derived from ancestors."""

    parent_pipeline_run_id: UUID | None = None
    change_set: dict[str, Any] = Field(default_factory=dict)
    change_set_digest: str | None = None
    effective_overrides: dict[str, Any] = Field(default_factory=dict)
    objective: dict[str, Any] | None = None
    applied_changes: list[dict[str, Any]] = Field(default_factory=list)


class ModelBuildReproductionSpec(BaseModel):
    """Canonical scientific recipe for one pipeline run, plus generated stage code."""

    generator_version: str
    spec_digest: str
    workspace_id: UUID
    pipeline_run_id: UUID
    dataset: ReproductionDataset
    task: ReproductionTask
    holdout_plan: ReproductionHoldoutPlan
    validation_plan: ReproductionValidationPlan
    metric_plan: ReproductionMetricPlan
    leakage_exclusions: list[ReproductionLeakageExclusion] = Field(default_factory=list)
    dropped_columns: list[str] = Field(default_factory=list)
    datetime_columns: list[str] = Field(default_factory=list)
    modeled_features: list[str] = Field(default_factory=list)
    feature_recipe: list[ReproductionFeature] = Field(default_factory=list)
    preprocessing: list[ReproductionPreprocessingStep] = Field(default_factory=list)
    candidates: list[ReproductionCandidate] = Field(default_factory=list)
    winner: ReproductionWinner
    final_refit: ReproductionFinalRefit
    final_holdout: ReproductionFinalHoldout
    split_assignment: ReproductionSplitAssignment | None = None
    branch: ReproductionBranch | None = None
    decision_threshold: float | None = None
    stage_code: list[ModelBuildStageCode] = Field(default_factory=list)


class ExperimentCodeInput(BaseModel):
    """A local file the generated code needs; fetched by artifact id, never by storage key."""

    name: Literal["dataset", "split_assignment"]
    placeholder: str
    env_var: str
    artifact_id: UUID | None = None
    content_digest: str | None = None
    description: str


class ExperimentCodeDocument(BaseModel):
    filename: str
    media_type: str
    content_digest: str
    source: str


class ExperimentCodeRead(BaseModel):
    """GET /v1/experiments/{id}/code: standalone reproduction script and notebook."""

    experiment_id: UUID
    workspace_id: UUID
    generator_version: str
    spec_digest: str
    split_plan_id: UUID | None = None
    parent_experiment_id: UUID | None = None
    is_branch: bool = False
    standalone_cv: bool
    script: ExperimentCodeDocument
    notebook: ExperimentCodeDocument
    inputs: list[ExperimentCodeInput] = Field(default_factory=list)
    helper_requirements: list[str] = Field(default_factory=list)
