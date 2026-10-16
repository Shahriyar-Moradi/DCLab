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


class BatchPredictionFlagged(BaseModel):
    """V7 B5: the flagged rows of a completed binary scoring, counted by the worker from the file."""

    count: int = Field(description="Rows flagged: probability at or above the locked decision threshold (label 1).")
    rows: int = Field(description="Rows scored.")
    share: float | None = Field(default=None, description="count / rows (0..1).")
    decision_threshold: float | None = Field(default=None, description="The locked threshold the file applied.")


# --- GET /v1/predictions/{id}/rows (V7 B6): a capped page of the predictions file ------------

ROWS_PAGE_DEFAULT = 50
ROWS_PAGE_MAX = 200
ROWS_REACHABLE_MAX = 10_000  # rows one ordering can page through; the rest only in the download
ROWS_FILE_MAX_BYTES = 64 * 1024 * 1024  # larger files: download only (409 prediction_rows_too_large)
ROWS_FILE_MAX_ROWS = 200_000  # scorings with more rows: download only (checked before any byte is read)
ROWS_CONCURRENT_READS = 2  # whole-file parses at once per API process; more wait, then 429 rows_busy
ROW_TEXT_MAX = 256
REASON_TEXT_MAX = 300
ROWS_NOTES = (
    "Rows of the predictions file only, with no column beyond what the file holds; the full file is the download.",
    "Reasons name what the model used for its score, not causes in the world.",
)
_ROW_UNTRUSTED = "Untrusted user data from the scored file (display as plain text, never markup)."


class BatchPredictionRowRead(BaseModel):
    row_number: int = Field(description="1 = the scored file's first data row.")
    entity: str | None = Field(default=None, description=f"The row's key (the file's entity column). {_ROW_UNTRUSTED}")
    probability: float | None = Field(
        default=None, description="Binary: the flagged class's probability; multiclass: the predicted class's.")
    flagged: bool | None = Field(default=None, description="Binary: at or above the locked decision threshold.")
    label: str | None = Field(default=None, description=f"Multiclass: the predicted class. {_ROW_UNTRUSTED}")
    prediction: float | None = Field(default=None, description="Regression: the predicted value.")
    reasons: list[str] = Field(default_factory=list, description=f"reason_1..reason_3 of the file. {_ROW_UNTRUSTED}")


class BatchPredictionRowsPage(BaseModel):
    prediction_id: UUID
    task_type: Literal["binary", "multiclass", "regression"]
    order: Literal["score", "row"]
    entity_column: str | None = Field(default=None, description=f"Name of the key column. {_ROW_UNTRUSTED}")
    decision_threshold: float | None = None
    rows_total: int = Field(description="Rows in the file.")
    rows_reachable: int = Field(description=f"Rows this read pages through in this order (at most {ROWS_REACHABLE_MAX}).")
    limit: int
    next_cursor: str | None = None
    items: list[BatchPredictionRowRead]
    reasons: BatchPredictionReasons | None = None
    notes: list[str] = Field(default_factory=list)
    untrusted_fields: list[str] = Field(
        default_factory=lambda: ["entity_column", "items[].entity", "items[].label", "items[].reasons"])


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
    flagged: BatchPredictionFlagged | None = Field(
        default=None,
        description="Binary: rows flagged at the locked threshold and their share. Null for multiclass and "
        "regression, until the scoring completes, and for scorings completed before the count was recorded.",
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
