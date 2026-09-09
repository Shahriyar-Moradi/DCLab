"""Frozen 0051 ml_jobs queue-generalization check constraints.

Copied from ``app.domain.ml_jobs`` at 0051. Do not import the live module.
"""

from __future__ import annotations

from alembic_frozen.clauses import sql_in_clause

ML_JOB_STATUSES = (
    "queued",
    "running",
    "completed",
    "failed",
)

PAYLOAD_MAX_BYTES = 16384
PRIORITY_MIN = -1000
PRIORITY_MAX = 1000
HANDLER_LABS_AUTO_TRAIN = "labs.auto_train"

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
