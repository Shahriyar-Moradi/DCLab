"""Batch predictions (P4.9-A): score a scoring dataset with a model version.

The API records intent (``batch_predictions`` row + ``ExecutionRequest`` +
``models.batch_predict`` ``ml_jobs`` row); only the worker loads the model.
``model_release_id`` stays NULL until Phase 7 adds releases. Public reads never
carry object keys, buckets or providers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.data_plane import sql_in_clause

PREDICTION_QUEUED = "queued"
PREDICTION_RUNNING = "running"
PREDICTION_COMPLETED = "completed"
PREDICTION_FAILED = "failed"
PREDICTION_STATUSES = (PREDICTION_QUEUED, PREDICTION_RUNNING, PREDICTION_COMPLETED, PREDICTION_FAILED)
TERMINAL_PREDICTION_STATUSES = frozenset({PREDICTION_COMPLETED, PREDICTION_FAILED})
OUTPUT_FORMATS = ("csv", "parquet")
CONTRACT_CHECK_MAX_BYTES = 65536

# Stable failure codes (``error_code``); ``error_message`` is generic public text.
FEATURE_CONTRACT_FAILED = "feature_contract_failed"
UNSUPPORTED_TRANSFORM = "unsupported_transform"
UNSUPPORTED_MODEL = "unsupported_model"
SCORING_FAILED = "scoring_failed"

# DB CHECK backstops (models.py; Alembic 0069 inlines the same literal SQL).
CK_BATCH_PREDICTIONS_STATUS = sql_in_clause("status", PREDICTION_STATUSES)
CK_BATCH_PREDICTIONS_OUTPUT_FORMAT = sql_in_clause("output_format", OUTPUT_FORMATS)
CK_BATCH_PREDICTIONS_ROWS = "(rows_in IS NULL OR rows_in >= 0) AND (rows_out IS NULL OR rows_out >= 0)"
CK_BATCH_PREDICTIONS_COMPLETED = (
    "status <> 'completed' OR (output_artifact_id IS NOT NULL AND rows_in IS NOT NULL "
    "AND rows_out = rows_in AND completed_at IS NOT NULL)"
)
CK_BATCH_PREDICTIONS_FAILED = "status <> 'failed' OR (error_code IS NOT NULL AND completed_at IS NOT NULL)"
CK_BATCH_PREDICTIONS_THRESHOLD = (
    "decision_threshold IS NULL OR (decision_threshold >= 0 AND decision_threshold <= 1)"
)
CK_BATCH_PREDICTIONS_CONTRACT_OBJECT = "contract_check IS NULL OR jsonb_typeof(contract_check) = 'object'"
CK_BATCH_PREDICTIONS_CONTRACT_BOUNDED = (
    f"contract_check IS NULL OR octet_length(CAST(contract_check AS TEXT)) <= {CONTRACT_CHECK_MAX_BYTES}"
)
CK_BATCH_PREDICTIONS_ERROR_CODE = "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{0,63}$'"


class BatchPredictionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID = Field(description="A dataset of the model's project uploaded with POST /v1/datasets.")
    output_format: Literal["csv", "parquet"] = Field("csv", description="File format of the predictions.")


class BatchPredictionOutput(BaseModel):
    artifact_id: UUID
    content_digest: str
    size_bytes: int
    mime_type: str | None = None
    download_path: str = Field(description="Authorized download of the file (same credentials as this read).")


class BatchPredictionRead(BaseModel):
    """One scoring run. ``contract_check`` names required/missing/ignored columns
    (the caller's own schema); ``error_message`` is generic text, never internals."""

    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    model_version_id: UUID
    model_release_id: UUID | None = None
    input_dataset_id: UUID
    execution_request_id: UUID | None
    status: Literal["queued", "running", "completed", "failed"]
    output_format: Literal["csv", "parquet"]
    rows_in: int | None
    rows_out: int | None
    decision_threshold: float | None
    contract_check: dict[str, Any] | None
    output: BatchPredictionOutput | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
