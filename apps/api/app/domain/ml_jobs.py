"""Durable worker-queue vocabularies. Execution lives in a worker, not the API request.

PostgreSQL accepts any slug ``job_type`` / ``handler_key`` matching the format
checks. Shipped handlers are registered in application code. Adding a worker
capability must not require a new closed CHECK enum. This module does not
define MCP jobs.
"""

from __future__ import annotations

from app.domain.data_plane import sql_in_clause

# Shipped types. Not a closed database enum — see CK_ML_JOB_TYPE.
ML_JOB_TYPES = ("auto_train", "auth_cleanup", "batch_predict", "agent_run", "monitoring", "improve")
JOB_TYPE_AUTO_TRAIN = "auto_train"
JOB_TYPE_AUTH_CLEANUP = "auth_cleanup"
JOB_TYPE_BATCH_PREDICT = "batch_predict"
JOB_TYPE_AGENT_RUN = "agent_run"
JOB_TYPE_MONITORING = "monitoring"
# P5.4-A2 (ADR 0007 §8): one step of an improve loop; never counted by ensure_run_capacity.
JOB_TYPE_IMPROVE = "improve"

HANDLER_LABS_AUTO_TRAIN = "labs.auto_train"
HANDLER_VERSION_LABS_AUTO_TRAIN = "1"
HANDLER_AUTH_SESSION_CLEANUP = "auth.session_cleanup"
HANDLER_VERSION_AUTH_SESSION_CLEANUP = "1"
# P4.9-A: score a scoring dataset with a model version (the only place a model loads).
HANDLER_MODELS_BATCH_PREDICT = "models.batch_predict"
HANDLER_VERSION_MODELS_BATCH_PREDICT = "1"
# P6.10-A: run one agent through the harness (payload: {"agent_run_id"} only).
HANDLER_AGENTS_RUN = "agents.run"
HANDLER_VERSION_AGENTS_RUN = "1"
# P7.4-A: compute one monitoring window (payload: project, model version, window start). Reads
# stored files only; never loads a model.
HANDLER_MONITORING_COMPUTE_WINDOW = "monitoring.compute_window"
HANDLER_VERSION_MONITORING_COMPUTE_WINDOW = "1"
# P5.4-A2: one step of an improve loop (payload: {"improve_run_id"} only; acts only when it is the
# run's continuation job).
HANDLER_LABS_IMPROVE = "labs.improve"
HANDLER_VERSION_LABS_IMPROVE = "1"

ML_JOB_STATUSES = (
    "queued",
    "running",
    "completed",
    "failed",
    # P3.1-B2 (Alembic 0066): terminal; a queued job is cancelled at once, a
    # running one when the worker reaches its next checkpoint
    # (``cancel_requested_at`` set, see ``ml_job_service``).
    "cancelled",
)

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 300.0
DEFAULT_PRIORITY = 0
PAYLOAD_MAX_BYTES = 16384
PRIORITY_MIN = -1000
PRIORITY_MAX = 1000

FORBIDDEN_JOB_PAYLOAD_KEYS = (
    "password",
    "secret",
    "token",
    "api_key",
    "access_key",
    "credentials",
    "authorization",
    "rows",
    "records",
    "dataset_rows",
    "csv",
    "file_bytes",
    "contents",
)

CK_ML_JOB_TYPE = "job_type ~ '^[a-z][a-z0-9_]{0,31}$'"
CK_ML_JOB_HANDLER_KEY = "handler_key ~ '^[a-z][a-z0-9_.]{0,63}$'"
CK_ML_JOB_HANDLER_VERSION = (
    "handler_version IS NULL OR handler_version ~ '^[a-zA-Z0-9._-]{1,32}$'"
)
CK_ML_JOB_STATUS = sql_in_clause("status", ML_JOB_STATUSES)
CK_ML_JOB_PRIORITY = (
    f"priority >= {PRIORITY_MIN} AND priority <= {PRIORITY_MAX}"
)
CK_ML_JOB_AUTO_TRAIN_UPLOAD = "job_type <> 'auto_train' OR upload_id IS NOT NULL"

_FORBIDDEN_SQL_ARRAY = ", ".join(f"'{key}'" for key in FORBIDDEN_JOB_PAYLOAD_KEYS)

CK_ML_JOB_PAYLOAD_OBJECT = "jsonb_typeof(payload) = 'object'"
CK_ML_JOB_PAYLOAD_BOUNDED = (
    f"octet_length(CAST(payload AS TEXT)) <= {PAYLOAD_MAX_BYTES}"
)
CK_ML_JOB_PAYLOAD_NO_SECRETS = (
    f"NOT jsonb_exists_any(payload, ARRAY[{_FORBIDDEN_SQL_ARRAY}]::text[])"
)

# --- attempts (Alembic 0084) ----------------------------------------------------------------
# One ``ml_job_attempts`` row per claim of a job (``ml_jobs.attempts`` numbers it). A retry is
# the queue's existing automatic re-claim (``fail_or_retry_job`` / abandoned-lease recovery),
# nothing else. Closed codes only: an attempt never stores an exception message.
ATTEMPT_RUNNING = "running"
ATTEMPT_COMPLETED = "completed"
ATTEMPT_FAILED = "failed"  # the job's last attempt failed: the job is failed
ATTEMPT_RETRIED = "retried"  # failed, and the job went back to the queue for another attempt
ATTEMPT_CANCELLED = "cancelled"
ATTEMPT_OUTCOMES = (ATTEMPT_RUNNING, ATTEMPT_COMPLETED, ATTEMPT_FAILED, ATTEMPT_RETRIED, ATTEMPT_CANCELLED)

FAILURE_OUT_OF_MEMORY = "out_of_memory"
FAILURE_TIMEOUT = "timeout"
FAILURE_WORKER_LOST = "worker_lost"  # the worker stopped heartbeating (killed, crashed, lost)
FAILURE_DATA_ERROR = "data_error"  # the run stopped on a problem with the data (a data stage)
FAILURE_INTERNAL_ERROR = "internal_error"
FAILURE_CANCELLED = "cancelled"
ATTEMPT_FAILURE_CLASSES = (
    FAILURE_OUT_OF_MEMORY, FAILURE_TIMEOUT, FAILURE_WORKER_LOST, FAILURE_DATA_ERROR, FAILURE_INTERNAL_ERROR,
    FAILURE_CANCELLED,
)

CK_ML_JOB_ATTEMPTS_NUMBER = "attempt >= 1"
CK_ML_JOB_ATTEMPTS_OUTCOME = sql_in_clause("outcome", ATTEMPT_OUTCOMES)
CK_ML_JOB_ATTEMPTS_FAILURE_CLASS = (
    "failure_class IS NULL OR " + sql_in_clause("failure_class", ATTEMPT_FAILURE_CLASSES)
)
# A running or completed attempt has no class; a failed / retried one has one; a cancelled one
# is ``cancelled``. ``ended_at`` is set exactly when the attempt is over.
CK_ML_JOB_ATTEMPTS_SHAPE = (
    "(outcome IN ('running', 'completed') AND failure_class IS NULL"
    " OR outcome IN ('failed', 'retried') AND failure_class IS NOT NULL AND failure_class <> 'cancelled'"
    " OR outcome = 'cancelled' AND failure_class = 'cancelled')"
    " AND (outcome = 'running') = (ended_at IS NULL)"
    " AND (ended_at IS NULL OR ended_at >= started_at)"
)


def exception_failure_class(exc: BaseException) -> str:
    """The closed class of an exception that ended an attempt (never its message)."""

    if isinstance(exc, MemoryError):
        return FAILURE_OUT_OF_MEMORY
    if isinstance(exc, TimeoutError):
        return FAILURE_TIMEOUT
    return FAILURE_INTERNAL_ERROR
