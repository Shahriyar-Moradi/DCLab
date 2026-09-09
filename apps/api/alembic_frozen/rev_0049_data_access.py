"""Frozen 0049 DataAccess check-constraint vocabularies.

Copied from ``app.domain.data_access`` at 0049. Do not import the live module.
"""

from __future__ import annotations

from alembic_frozen.clauses import sql_in_clause

DATA_ACCESS_TYPES = (
    "upload",
    "object_storage",
    "database",
    "api",
    "customer_runner",
)

DATA_ACCESS_EXECUTION_MODES = (
    "copy",
    "query",
    "pushdown",
    "customer_runner",
)

DATA_ACCESS_STATUSES = (
    "active",
    "disabled",
    "error",
)

LOCATOR_MAX_BYTES = 16384
POLICY_MAX_BYTES = 16384
CREDENTIAL_REFERENCE_MAX_LENGTH = 512

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
