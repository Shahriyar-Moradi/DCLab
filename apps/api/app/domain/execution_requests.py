"""Protocol-neutral execution requests. Transport and workers live elsewhere."""

from __future__ import annotations

from app.domain.data_plane import sql_in_clause

# Stable surfaces. mcp/cli are reserved vocabulary only — no transports here.
EXECUTION_SOURCE_SURFACES = (
    "api",
    "studio",
    "system",
    "legacy_labs",
    "mcp",
    "cli",
)

SOURCE_API = "api"
SOURCE_STUDIO = "studio"
SOURCE_SYSTEM = "system"
SOURCE_LEGACY_LABS = "legacy_labs"

EXECUTION_OPERATIONS = ("model_build",)

OPERATION_MODEL_BUILD = "model_build"

EXECUTION_REQUEST_STATUSES = (
    "accepted",
    "running",
    "completed",
    "failed",
)

REQUEST_ACCEPTED = "accepted"
REQUEST_RUNNING = "running"
REQUEST_COMPLETED = "completed"
REQUEST_FAILED = "failed"

REQUEST_SPEC_MAX_BYTES = 16384
RESULT_SUMMARY_MAX_BYTES = 16384

FORBIDDEN_REQUEST_PAYLOAD_KEYS = (
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

ALLOWED_REQUEST_SPEC_KEYS = frozenset(
    {
        "upload_id",
        "dataset_id",
        "artifact_id",
        "filename",
        "category",
        "kind",
        "record_count",
        "target_column",
        "fields_noticed",
        "column_count",
    }
)

CK_EXECUTION_REQUEST_OPERATION = sql_in_clause("operation", EXECUTION_OPERATIONS)
CK_EXECUTION_REQUEST_SOURCE = sql_in_clause("source_surface", EXECUTION_SOURCE_SURFACES)
CK_EXECUTION_REQUEST_STATUS = sql_in_clause("status", EXECUTION_REQUEST_STATUSES)

_FORBIDDEN_SQL_ARRAY = ", ".join(f"'{key}'" for key in FORBIDDEN_REQUEST_PAYLOAD_KEYS)

CK_EXECUTION_REQUEST_SPEC_OBJECT = "jsonb_typeof(request_spec) = 'object'"
CK_EXECUTION_REQUEST_SPEC_BOUNDED = (
    f"octet_length(CAST(request_spec AS TEXT)) <= {REQUEST_SPEC_MAX_BYTES}"
)
CK_EXECUTION_REQUEST_SPEC_NO_SECRETS = (
    f"NOT jsonb_exists_any(request_spec, ARRAY[{_FORBIDDEN_SQL_ARRAY}]::text[])"
)
CK_EXECUTION_REQUEST_RESULT_OBJECT = (
    "result_summary IS NULL OR jsonb_typeof(result_summary) = 'object'"
)
CK_EXECUTION_REQUEST_RESULT_BOUNDED = (
    "result_summary IS NULL OR "
    f"octet_length(CAST(result_summary AS TEXT)) <= {RESULT_SUMMARY_MAX_BYTES}"
)
CK_EXECUTION_REQUEST_RESULT_NO_SECRETS = (
    "result_summary IS NULL OR "
    f"NOT jsonb_exists_any(result_summary, ARRAY[{_FORBIDDEN_SQL_ARRAY}]::text[])"
)
CK_EXECUTION_REQUEST_PARENT_NOT_SELF = (
    "parent_request_id IS NULL OR parent_request_id <> id"
)
