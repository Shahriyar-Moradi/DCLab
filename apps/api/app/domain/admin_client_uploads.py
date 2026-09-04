"""Admin-only. The full detail behind a Labs custom-box upload: the simple-case
auto-train job (EDA, target choice, missing-value decisions, column roles,
candidate scores) that `apps/api/app/services/auto_train_service.py` runs
behind the client's upload response. See docs/LABS_DATA_UNDERSTANDING.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AdminClientUploadSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    category: str
    original_filename: str
    kind: str
    record_count: int
    has_named_fields: bool
    pipeline_status: str
    experiment_id: UUID | None
    workflow_run_id: UUID | None = None
    created_at: datetime


class AdminLabDecisionRecord(BaseModel):
    """One feature-column missing-value decision from `lab_decision_records`."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    column: str
    source: str
    rule_decision: str
    final_decision: str
    fill_value: Any | None
    validator_verdict: str
    prompt_version: str
    evidence_snapshot: dict[str, Any]
    raw_llm_output: dict[str, Any] | None
    created_at: datetime


class AdminCleaningStep(BaseModel):
    column: str
    problem: str
    action: str
    result: str


class AdminDataAnalysis(BaseModel):
    rows: int | None = None
    columns: int | None = None
    numerical_columns: list[str] = []
    categorical_columns: list[str] = []
    missing_values: int | None = None
    duplicates: int | None = None
    constant_columns: list[str] = []
    high_cardinality_columns: list[str] = []


class AdminFeatureEngineering(BaseModel):
    original_features: list[str] = []
    generated_features: list[str] = []
    removed_features: list[str] = []
    transformations: list[dict[str, Any]] = []


class AdminValidation(BaseModel):
    train_rows: int | None = None
    test_rows: int | None = None
    cv_strategy: str | None = None
    n_folds: int | None = None
    random_state: int | None = None


class AdminModelComparisonRow(BaseModel):
    name: str
    model_family: str
    cv_auc: float | None = None
    test_auc: float | None = None
    cv_metrics: dict[str, Any] = {}
    test_metrics: dict[str, Any] | None = None
    selected: bool = False
    status: str = ""


class AdminFinalModel(BaseModel):
    selected_model: str | None = None
    model_family: str | None = None
    cv_metrics: dict[str, Any] = {}
    test_metrics: dict[str, Any] = {}


class AdminPredictions(BaseModel):
    count: int = 0
    distribution: dict[str, int] = {}
    download_available: bool = False


class AdminProcessingSummary(BaseModel):
    """Completed-work flags for the default admin view — not a live stage tracker."""

    cleaning_completed: bool = False
    feature_engineering_completed: bool = False
    preprocessing_completed: bool = False
    train_test_split: str | None = None
    cross_validation: str | None = None
    training_completed: bool = False
    evaluation_completed: bool = False
    predictions_completed: bool = False


class AdminMlRun(BaseModel):
    """Technical visualization of a completed (or in-progress) open-ingest ML run."""

    run_id: UUID
    dataset: str
    dataset_id: UUID | None = None
    status: str
    target: str | None = None
    task_type: str | None = None
    target_source: str | None = None
    target_reason: str | None = None
    target_confidence: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    analysis: AdminDataAnalysis = Field(default_factory=AdminDataAnalysis)
    processing_summary: AdminProcessingSummary = Field(default_factory=AdminProcessingSummary)
    cleaning: list[AdminCleaningStep] = Field(default_factory=list)
    feature_engineering: AdminFeatureEngineering = Field(default_factory=AdminFeatureEngineering)
    validation: AdminValidation = Field(default_factory=AdminValidation)
    model_comparison: list[AdminModelComparisonRow] = Field(default_factory=list)
    final_model: AdminFinalModel | None = None
    predictions: AdminPredictions = Field(default_factory=AdminPredictions)


class AdminClientUploadDetail(AdminClientUploadSummary):
    stored_path: str
    fields_noticed: list[str]
    pipeline_log: dict[str, Any] | None
    decision_records: list[AdminLabDecisionRecord] = []
    ml_run: AdminMlRun | None = None
