"""Durable worker-queue vocabularies. Execution lives in a worker, not the API request.

PostgreSQL accepts any slug ``job_type`` / ``handler_key`` matching the format
checks. Shipped handlers are registered in application code. Adding a worker
capability must not require a new closed CHECK enum. This module does not
define MCP jobs.
"""

from __future__ import annotations

from app.domain.data_plane import sql_in_clause

# Shipped types. Not a closed database enum — see CK_ML_JOB_TYPE.
ML_JOB_TYPES = ("auto_train", "auth_cleanup")
JOB_TYPE_AUTO_TRAIN = "auto_train"
JOB_TYPE_AUTH_CLEANUP = "auth_cleanup"

HANDLER_LABS_AUTO_TRAIN = "labs.auto_train"
HANDLER_VERSION_LABS_AUTO_TRAIN = "1"
HANDLER_AUTH_SESSION_CLEANUP = "auth.session_cleanup"
HANDLER_VERSION_AUTH_SESSION_CLEANUP = "1"

ML_JOB_STATUSES = (
    "queued",
    "running",
    "completed",
    "failed",
)

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"

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
