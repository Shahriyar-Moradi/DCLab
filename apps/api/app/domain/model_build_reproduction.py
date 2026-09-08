"""Deterministic model-build reproduction IR and generated Python sections.

The spec is built only from canonical persisted tables. Generated code is a
pure function of this IR plus a versioned template set.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

GENERATOR_VERSION = "dclab.model_build_reproduction.v1"
AUTHORIZED_DATASET_PATH_PLACEHOLDER = "<authorized-local-dataset-path>"


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


class ReproductionHoldoutPlan(BaseModel):
    strategy: str | None = None
    test_size: float | None = None
    group_column: str | None = None
    time_column: str | None = None
    plan_digest: str | None = None


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
    stage_code: list[ModelBuildStageCode] = Field(default_factory=list)
