"""DataAccess vocabularies. Executable access to a DataSource; not a connector catalog."""

from __future__ import annotations

from app.domain.data_plane import sql_in_clause

# Architecture modes. Only local/upload copy is operational in this revision.
DATA_ACCESS_TYPES = (
    "upload",
    "object_storage",
    "database",
    "api",
    "customer_runner",
)

ACCESS_TYPE_UPLOAD = "upload"

DATA_ACCESS_EXECUTION_MODES = (
    "copy",
    "query",
    "pushdown",
    "customer_runner",
)

EXECUTION_MODE_COPY = "copy"
EXECUTION_MODE_QUERY = "query"
EXECUTION_MODE_PUSHDOWN = "pushdown"
EXECUTION_MODE_CUSTOMER_RUNNER = "customer_runner"

DATA_ACCESS_STATUSES = (
    "active",
    "disabled",
    "error",
)

ACCESS_ACTIVE = "active"

LOCATOR_MAX_BYTES = 16384
POLICY_MAX_BYTES = 16384
CREDENTIAL_REFERENCE_MAX_LENGTH = 512

# Top-level JSON keys rejected by PostgreSQL. Nested secrets are service-layer only.
FORBIDDEN_DATA_ACCESS_JSON_KEYS = (
    "password",
    "secret",
    "token",
    "api_key",
    "access_key",
    "credentials",
    "authorization",
    "private_key",
    "connection_string",
    "dsn",
)

# Opaque secret-manager pointer. No URI userinfo, no raw connection strings.
# PostgreSQL POSIX `{m,n}` repetition is capped; length is enforced separately.
OPAQUE_CREDENTIAL_REGEX = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"

CK_DATA_ACCESSES_TYPE = sql_in_clause("access_type", DATA_ACCESS_TYPES)
CK_DATA_ACCESSES_EXECUTION_MODE = sql_in_clause(
    "execution_mode", DATA_ACCESS_EXECUTION_MODES
)
CK_DATA_ACCESSES_STATUS = sql_in_clause("status", DATA_ACCESS_STATUSES)
CK_DATA_ACCESSES_UPLOAD_IS_COPY = (
    "access_type <> 'upload' OR execution_mode = 'copy'"
)

_FORBIDDEN_SQL_ARRAY = ", ".join(f"'{key}'" for key in FORBIDDEN_DATA_ACCESS_JSON_KEYS)

CK_DATA_ACCESSES_LOCATOR_OBJECT = "jsonb_typeof(resource_locator) = 'object'"
CK_DATA_ACCESSES_LOCATOR_BOUNDED = (
    f"octet_length(CAST(resource_locator AS TEXT)) <= {LOCATOR_MAX_BYTES}"
)
CK_DATA_ACCESSES_LOCATOR_NO_SECRETS = (
    f"NOT jsonb_exists_any(resource_locator, ARRAY[{_FORBIDDEN_SQL_ARRAY}]::text[])"
)
CK_DATA_ACCESSES_PRIVACY_OBJECT = "jsonb_typeof(privacy_policy) = 'object'"
CK_DATA_ACCESSES_PRIVACY_BOUNDED = (
    f"octet_length(CAST(privacy_policy AS TEXT)) <= {POLICY_MAX_BYTES}"
)
CK_DATA_ACCESSES_PRIVACY_NO_SECRETS = (
    f"NOT jsonb_exists_any(privacy_policy, ARRAY[{_FORBIDDEN_SQL_ARRAY}]::text[])"
)
CK_DATA_ACCESSES_RETENTION_OBJECT = "jsonb_typeof(retention_policy) = 'object'"
CK_DATA_ACCESSES_RETENTION_BOUNDED = (
    f"octet_length(CAST(retention_policy AS TEXT)) <= {POLICY_MAX_BYTES}"
)
CK_DATA_ACCESSES_RETENTION_NO_SECRETS = (
    f"NOT jsonb_exists_any(retention_policy, ARRAY[{_FORBIDDEN_SQL_ARRAY}]::text[])"
)
CK_DATA_ACCESSES_CREDENTIAL_OPAQUE = (
    "credential_reference IS NULL OR ("
    f"char_length(credential_reference) BETWEEN 1 AND {CREDENTIAL_REFERENCE_MAX_LENGTH} "
    f"AND credential_reference ~ '{OPAQUE_CREDENTIAL_REGEX}' "
    "AND position('://' in credential_reference) = 0)"
)
