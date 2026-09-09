"""Column policy vocabularies and data-access audit events. Not a PII classifier."""

from __future__ import annotations

from app.domain.data_plane import sql_in_clause

# Queryable labels only. Nothing here classifies existing columns.
SENSITIVITY_CLASSES = (
    "public",
    "internal",
    "identifier",
    "pii",
    "sensitive",
    "restricted",
)

CLASSIFICATION_SOURCES = (
    "manual",
    "system",
    "policy",
    "import",
)

DATA_USE_POLICIES = (
    "allow",
    "deny",
    "aggregate_only",
    "metadata_only",
)

DATA_ACCESS_EVENT_ACTOR_TYPES = (
    "user",
    "system",
    "worker",
    "service",
)

DATA_ACCESS_EVENT_PURPOSES = (
    "ingest",
    "model_build",
    "scoring",
    "exploration",
    "audit",
    "other",
)

DATA_ACCESS_EVENT_OPERATIONS = (
    "read",
    "copy",
    "query",
    "ingest",
    "inspect",
)

DATA_ACCESS_EVENT_STATUSES = (
    "started",
    "completed",
    "failed",
)

ACTOR_USER = "user"
PURPOSE_INGEST = "ingest"
OPERATION_COPY = "copy"
EVENT_COMPLETED = "completed"
EVENT_STARTED = "started"
EVENT_FAILED = "failed"

SUMMARY_MAX_BYTES = 16384

FORBIDDEN_AUDIT_JSON_KEYS = (
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
    "values",
    "samples",
)

ALLOWED_RESOURCE_SUMMARY_KEYS = frozenset(
    {
        "data_source_id",
        "data_access_id",
        "dataset_id",
        "artifact_id",
        "object_key",
        "filename",
        "provider",
        "access_type",
        "execution_mode",
    }
)

ALLOWED_COLUMN_SUMMARY_KEYS = frozenset(
    {
        "column_names",
        "column_count",
    }
)

CK_DATASET_COLUMNS_SENSITIVITY = (
    "sensitivity_class IS NULL OR "
    + sql_in_clause("sensitivity_class", SENSITIVITY_CLASSES)
)
CK_DATASET_COLUMNS_CLASSIFICATION_SOURCE = (
    "classification_source IS NULL OR "
    + sql_in_clause("classification_source", CLASSIFICATION_SOURCES)
)
CK_DATASET_COLUMNS_MODEL_USE = (
    "model_use_policy IS NULL OR "
    + sql_in_clause("model_use_policy", DATA_USE_POLICIES)
)
CK_DATASET_COLUMNS_LLM_EXPOSURE = (
    "llm_exposure_policy IS NULL OR "
    + sql_in_clause("llm_exposure_policy", DATA_USE_POLICIES)
)

CK_DATA_ACCESS_EVENTS_ACTOR = sql_in_clause("actor_type", DATA_ACCESS_EVENT_ACTOR_TYPES)
CK_DATA_ACCESS_EVENTS_PURPOSE = sql_in_clause("purpose", DATA_ACCESS_EVENT_PURPOSES)
CK_DATA_ACCESS_EVENTS_OPERATION = sql_in_clause(
    "operation", DATA_ACCESS_EVENT_OPERATIONS
)
CK_DATA_ACCESS_EVENTS_STATUS = sql_in_clause("status", DATA_ACCESS_EVENT_STATUSES)
CK_DATA_ACCESS_EVENTS_COMPLETED = (
    "status = 'started' OR completed_at IS NOT NULL"
)
CK_DATA_ACCESS_EVENTS_FAILURE = (
    "status <> 'failed' OR failure_code IS NOT NULL"
)

_FORBIDDEN_SQL_ARRAY = ", ".join(f"'{key}'" for key in FORBIDDEN_AUDIT_JSON_KEYS)

CK_DATA_ACCESS_EVENTS_RESOURCE_OBJECT = "jsonb_typeof(resource_summary) = 'object'"
CK_DATA_ACCESS_EVENTS_RESOURCE_BOUNDED = (
    f"octet_length(CAST(resource_summary AS TEXT)) <= {SUMMARY_MAX_BYTES}"
)
CK_DATA_ACCESS_EVENTS_RESOURCE_NO_ROWS = (
    f"NOT jsonb_exists_any(resource_summary, ARRAY[{_FORBIDDEN_SQL_ARRAY}]::text[])"
)
CK_DATA_ACCESS_EVENTS_COLUMN_OBJECT = "jsonb_typeof(column_summary) = 'object'"
CK_DATA_ACCESS_EVENTS_COLUMN_BOUNDED = (
    f"octet_length(CAST(column_summary AS TEXT)) <= {SUMMARY_MAX_BYTES}"
)
CK_DATA_ACCESS_EVENTS_COLUMN_NO_ROWS = (
    f"NOT jsonb_exists_any(column_summary, ARRAY[{_FORBIDDEN_SQL_ARRAY}]::text[])"
)

PREVENT_DATA_ACCESS_EVENT_MUTATION_SQL = """
CREATE OR REPLACE FUNCTION prevent_data_access_event_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'data_access_events is append-only';
END;
$$ LANGUAGE plpgsql
"""

DATA_ACCESS_EVENTS_APPEND_ONLY_TRIGGER_SQL = """
CREATE TRIGGER data_access_events_append_only
BEFORE UPDATE OR DELETE ON data_access_events
FOR EACH ROW EXECUTE FUNCTION prevent_data_access_event_mutation()
"""
