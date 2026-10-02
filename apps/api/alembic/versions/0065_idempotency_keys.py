"""Generic /v1 Idempotency-Key store.

Revision ID: 0065_idempotency_keys
Revises: 0064_tenant_llm_attribution
Create Date: 2026-10-02

P3.1-B1. Expand-only: one new table, ``idempotency_keys``, for /v1 commands
whose resource has no idempotency column (``POST /v1/projects``,
``/v1/projects/{id}/problem-specs``, ``/v1/datasets``). The key row is inserted
in the same transaction as the resource it names, so a key never exists
without its resource. ``execution_requests`` keeps its own binding.

- ``UNIQUE (workspace_id, principal_kind, principal_id, operation,
  idempotency_key)``: a key is scoped to one principal and one operation.
- ``principal_id`` has no FK: users today, service tokens from P3.2-A.
- ``resource_id`` is polymorphic (``resource_kind``); no FK, readers re-load it
  workspace-scoped.
- Rows never change (``idempotency_keys_no_update`` trigger, reusing the 0035
  ``prevent_canonical_row_mutation()`` function). DELETE stays possible for the
  workspace cascade and for a later ops expiry job over ``expires_at`` (partial
  index ``ix_idempotency_keys_expires_at``). Lookups and the workspace FK use the
  unique index, which leads with ``workspace_id``.

Downgrade drops the table (and its trigger); stored keys are lost, so retried
requests after a downgrade/upgrade cycle execute again instead of replaying.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0065_idempotency_keys"
down_revision: Union[str, Sequence[str], None] = "0064_tenant_llm_attribution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER idempotency_keys_no_update
BEFORE UPDATE ON idempotency_keys
FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()
"""


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("principal_kind", sa.String(16), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("resource_kind", sa.String(32), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workspace_id",
            "principal_kind",
            "principal_id",
            "operation",
            "idempotency_key",
            name="uq_idempotency_keys_scope",
        ),
        sa.CheckConstraint(
            "principal_kind IN ('user', 'service_token')",
            name="ck_idempotency_keys_principal_kind",
        ),
        sa.CheckConstraint(
            "operation ~ '^(POST|PUT|PATCH|DELETE) /v1/[A-Za-z0-9_{}/.-]{1,52}$'",
            name="ck_idempotency_keys_operation",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9._:-]{1,128}$'", name="ck_idempotency_keys_key"
        ),
        sa.CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$'", name="ck_idempotency_keys_digest"
        ),
        sa.CheckConstraint(
            "resource_kind ~ '^[a-z][a-z0-9_]{0,31}$'", name="ck_idempotency_keys_resource_kind"
        ),
        sa.CheckConstraint(
            "response_status BETWEEN 200 AND 299", name="ck_idempotency_keys_status"
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at", name="ck_idempotency_keys_expiry"
        ),
    )
    op.create_index(
        "ix_idempotency_keys_expires_at",
        "idempotency_keys",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )
    op.execute(_NO_UPDATE_TRIGGER_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS idempotency_keys_no_update ON idempotency_keys")
    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
