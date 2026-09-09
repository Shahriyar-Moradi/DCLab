"""Frozen 0048 execution-request check-constraint vocabularies.

Copied from ``app.domain.execution_requests`` at 0048. Do not import the live module.
"""

from __future__ import annotations

from alembic_frozen.clauses import sql_in_clause

EXECUTION_SOURCE_SURFACES = (
    "api",
    "studio",
    "system",
    "legacy_labs",
    "mcp",
    "cli",
)

EXECUTION_OPERATIONS = ("model_build",)

EXECUTION_REQUEST_STATUSES = (
    "accepted",
    "running",
    "completed",
    "failed",
)

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
