"""Durable ML job queue vocabularies. Execution lives in a worker, not the API request."""

from __future__ import annotations

from app.domain.data_plane import sql_in_clause

ML_JOB_TYPES = ("auto_train",)

ML_JOB_STATUSES = (
    "queued",
    "running",
    "completed",
    "failed",
)

JOB_TYPE_AUTO_TRAIN = "auto_train"

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 300.0

CK_ML_JOB_TYPE = sql_in_clause("job_type", ML_JOB_TYPES)
CK_ML_JOB_STATUS = sql_in_clause("status", ML_JOB_STATUSES)
