"""Batch predictions (P4.9-A): score a scoring dataset with a model version.

The API records intent (``batch_predictions`` row + ``ExecutionRequest`` +
``models.batch_predict`` ``ml_jobs`` row); only the worker loads the model.
``model_release_id`` is the approval of the model in use when the scoring was
queued with it (P7.2-A); scoring with a model that is not in use stays allowed and
leaves it NULL (no approval is claimed). Public reads never carry object keys,
buckets or providers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.data_plane import sql_in_clause
from app.domain.model_releases import ModelReleaseSummary

# Stable failure codes (``error_code``); ``error_message`` is generic public text. The
# scoring codes and the contract-check bound are owned by the engine, which raises them.
from app.engine.serving.codes import (
    CONTRACT_CHECK_MAX_BYTES,
    FEATURE_CONTRACT_FAILED,
    SCORING_FAILED,
    UNSUPPORTED_MODEL,
    UNSUPPORTED_TRANSFORM,
)

PREDICTION_QUEUED = "queued"
PREDICTION_RUNNING = "running"
PREDICTION_COMPLETED = "completed"
PREDICTION_FAILED = "failed"
PREDICTION_STATUSES = (PREDICTION_QUEUED, PREDICTION_RUNNING, PREDICTION_COMPLETED, PREDICTION_FAILED)
TERMINAL_PREDICTION_STATUSES = frozenset({PREDICTION_COMPLETED, PREDICTION_FAILED})
OUTPUT_FORMATS = ("csv", "parquet")

DATASET_UNAVAILABLE = "dataset_unavailable"
RELEASE_MISMATCH = "release_mismatch"  # the approval no longer matches the model's package
# A weekly scheduled scoring whose person lost the right to score before it ran.
SCHEDULE_OWNER_NOT_ALLOWED = "schedule_owner_not_allowed"

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


REASON_METHODS = ("tree_shap", "linear_contributions", "occlusion")  # app.engine.explain.METHODS
REASONS_TURNED_OFF = "per-row reasons are turned off on this server"
REASONS_NOT_RECORDED = "not recorded: this scoring ran before per-row reasons were added"


class BatchPredictionReasons(BaseModel):
    """Per-row reasons in the predictions file (P7.7-A): columns ``reason_1``..``reason_3``
    name, per row, the input columns that moved THIS MODEL's score most, with the direction
    and the reference it is measured against. They describe what the model used, not causes
    in the world. For multiclass, a reason measures the predicted class's lead over the
    average of the other classes (a log-ratio; occlusion uses the class's probability): a
    column can raise that lead while lowering the class's probability if it raises a rival
    class more. Only the file holds the reasons; this record holds no row values."""

    available: bool = Field(description="True when the file's reason columns are filled for the explained rows.")
    method: Literal["tree_shap", "linear_contributions", "occlusion"] | None = Field(
        default=None,
        description="tree_shap: the boosting model's own per-row contributions, compared with the model's "
        "average row; linear_contributions: coefficient x value compared with the training average; occlusion: "
        "each column set to its training median / most common value in turn (approximate).",
    )
    method_label: str | None = Field(default=None, description="The method in plain words.")
    additive: bool = Field(
        default=False,
        description="True when each row's contributions add up to the model's raw output within a checked "
        "numerical tolerance; false for occlusion.",
    )
    top_k: int = Field(default=3, description="Reasons per row at most (fewer when fewer columns moved the score).")
    rows_explained: int = Field(default=0, description="Rows with reasons: the first rows of the file, up to a cap.")
    note: str | None = Field(default=None, description="Plain note, e.g. when only the first rows were explained.")
    unavailable_reason: str | None = Field(
        default=None, description="Why there are no reasons (the scores are not affected)."
    )


class BatchPredictionRead(BaseModel):
    """One scoring run. ``contract_check`` names required/missing/ignored columns
    (the caller's own schema); ``error_message`` is generic text, never internals."""

    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    model_version_id: UUID
    model_release_id: UUID | None = Field(
        default=None, description="The approval of the model in use this scoring ran with; null when not in use."
    )
    release: ModelReleaseSummary | None = Field(
        default=None, description="That approval (`is_current`: still the model in use now)."
    )
    input_dataset_id: UUID
    execution_request_id: UUID | None
    status: Literal["queued", "running", "completed", "failed"]
    output_format: Literal["csv", "parquet"]
    rows_in: int | None
    rows_out: int | None
    decision_threshold: float | None
    contract_check: dict[str, Any] | None
    output: BatchPredictionOutput | None
    reasons: BatchPredictionReasons | None = Field(
        default=None,
        description="Per-row reasons in the file (which inputs moved the model's score, not causes in the world); "
        "null until the scoring completes.",
    )
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    scheduled: bool = Field(
        default=False, description="Queued by the project's weekly schedule; false when a person scored it by hand."
    )
    scheduled_for: datetime | None = Field(default=None, description="The weekly slot (UTC) it was queued for.")
