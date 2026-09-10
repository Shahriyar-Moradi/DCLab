"""Frozen 0054 needs_input check-constraint vocabularies.

Copied from live domain modules at 0054. Do not import the live modules.
"""

from __future__ import annotations

from alembic_frozen.clauses import sql_in_clause

EXECUTION_REQUEST_STATUSES = (
    "accepted",
    "running",
    "completed",
    "failed",
    "needs_input",
)

CLIENT_LAB_UPLOAD_CLIENT_STATUSES = (
    "queued",
    "processing",
    "completed",
    "failed",
    "needs_input",
)

CK_EXECUTION_REQUEST_STATUS = sql_in_clause("status", EXECUTION_REQUEST_STATUSES)
CK_CLIENT_LAB_UPLOADS_CLIENT_STATUS = sql_in_clause(
    "client_status", CLIENT_LAB_UPLOAD_CLIENT_STATUSES
)
