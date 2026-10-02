"""Service tokens: hashed, scoped, expiring /v1 machine credentials.

Revision ID: 0067_service_tokens
Revises: 0066_run_cancellation
Create Date: 2026-10-02

P3.2-A. Expand-only:

- New table ``service_tokens`` (workspace-scoped; ``UNIQUE (workspace_id, id)``
  is the composite-FK target). Only ``secret_hash`` (HMAC-SHA256 / SHA-256 hex
  of the whole bearer string) is stored, ``UNIQUE``. ``scopes`` is a JSONB array
  CHECKed to be a non-empty, duplicate-free subset of the code-owned scope set.
  ``expires_at`` is required: after ``created_at`` and at most 90 days (2160
  hours, TimeZone-independent) later. ``revoked_by_user_id`` only on a revoked
  row (NULL there = system revocation). Creator FK ``ON DELETE CASCADE`` (a token
  never outlives the human whose authority it uses); ``revoked_by_user_id``
  ``SET NULL``.
- Triggers: ``service_tokens_columns_immutable`` reuses the 0042
  ``prevent_canonical_column_mutation()`` (identity, name, scopes, hash, created
  and expiry are frozen; only ``last_used_at`` and revocation change) and
  ``service_tokens_revocation_final`` reuses ``prevent_canonical_row_mutation()``
  so a revoked token can never be un-revoked and its revoker can only become
  NULL afterwards (the ``SET NULL`` FK action), never another user.
- ``project_decision_records.actor_service_token_id`` gets the composite FK
  ``(workspace_id, actor_service_token_id) → service_tokens(workspace_id, id)``
  (NO ACTION, append-only rows), reserved by 0063; like ``actor_user_id``, it
  makes deleting a token (via its creator or workspace) fail once the token has
  authored a decision record (provenance is kept). Added ``NOT VALID`` then
  validated: the column is NULL in every existing row (agents failed closed
  until now), so validation scans without blocking writes for long.

Locking: the FK takes SHARE ROW EXCLUSIVE on ``project_decision_records`` for
the (short) transaction; ``lock_timeout`` 5s fails fast instead of queueing.

Downgrade refuses while any decision record names a service token (repair
forward: dropping the table would leave unverifiable agent provenance), then
drops the FK and the table (its triggers go with it).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0067_service_tokens"
down_revision: Union[str, Sequence[str], None] = "0066_run_cancellation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCOPES_CHECK = (
    "CASE WHEN jsonb_typeof(scopes) = 'array' THEN jsonb_array_length(scopes) BETWEEN 1 AND 5 "
    "AND scopes <@ '[\"read\", \"projects:write\", \"datasets:write\", \"experiments:write\", "
    "\"decisions:propose\"]'::jsonb AND jsonb_array_length(scopes) = "
    "jsonb_exists(scopes, 'read')::int + jsonb_exists(scopes, 'projects:write')::int "
    "+ jsonb_exists(scopes, 'datasets:write')::int + jsonb_exists(scopes, 'experiments:write')::int "
    "+ jsonb_exists(scopes, 'decisions:propose')::int ELSE false END"
)

_FROZEN_TRIGGER_SQL = """
CREATE TRIGGER service_tokens_columns_immutable
BEFORE UPDATE ON service_tokens
FOR EACH ROW EXECUTE FUNCTION prevent_canonical_column_mutation(
    'id,workspace_id,created_by_user_id,name,scopes,secret_hash,created_at,expires_at'
)
"""
_REVOCATION_FINAL_TRIGGER_SQL = """
CREATE TRIGGER service_tokens_revocation_final
BEFORE UPDATE OF revoked_at, revoked_by_user_id ON service_tokens
FOR EACH ROW
WHEN (OLD.revoked_at IS NOT NULL AND (
    NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
    OR (NEW.revoked_by_user_id IS DISTINCT FROM OLD.revoked_by_user_id AND NEW.revoked_by_user_id IS NOT NULL)
))
EXECUTE FUNCTION prevent_canonical_row_mutation()
"""

_DOWNGRADE_PRECHECK_SQL = """
DO $$
DECLARE
    referencing bigint;
BEGIN
    SELECT count(*) INTO referencing
      FROM project_decision_records WHERE actor_service_token_id IS NOT NULL;
    IF referencing > 0 THEN
        RAISE EXCEPTION
            '0067 downgrade refused: % decision records reference service tokens; repair forward',
            referencing;
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "service_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_service_tokens_workspace_id"),
        sa.UniqueConstraint("secret_hash", name="uq_service_tokens_secret_hash"),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 80", name="ck_service_tokens_name"
        ),
        sa.CheckConstraint(_SCOPES_CHECK, name="ck_service_tokens_scopes"),
        sa.CheckConstraint(
            "secret_hash ~ '^[a-f0-9]{64}$'", name="ck_service_tokens_secret_hash"
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '2160 hours'",
            name="ck_service_tokens_expiry",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at", name="ck_service_tokens_revoked"
        ),
        sa.CheckConstraint(
            "revoked_by_user_id IS NULL OR revoked_at IS NOT NULL",
            name="ck_service_tokens_revoked_by",
        ),
    )
    op.create_index(
        "ix_service_tokens_workspace_created_at",
        "service_tokens",
        ["workspace_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_service_tokens_created_by_user_id", "service_tokens", ["created_by_user_id"]
    )
    op.create_index(
        "ix_service_tokens_revoked_by_user_id",
        "service_tokens",
        ["revoked_by_user_id"],
        postgresql_where=sa.text("revoked_by_user_id IS NOT NULL"),
    )
    op.execute(_FROZEN_TRIGGER_SQL)
    op.execute(_REVOCATION_FINAL_TRIGGER_SQL)
    op.execute(
        "ALTER TABLE project_decision_records ADD CONSTRAINT fk_pdr_actor_service_token "
        "FOREIGN KEY (workspace_id, actor_service_token_id) "
        "REFERENCES service_tokens (workspace_id, id) NOT VALID"
    )
    op.execute(
        "ALTER TABLE project_decision_records VALIDATE CONSTRAINT fk_pdr_actor_service_token"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(_DOWNGRADE_PRECHECK_SQL)
    op.drop_constraint(
        "fk_pdr_actor_service_token", "project_decision_records", type_="foreignkey"
    )
    op.drop_index("ix_service_tokens_revoked_by_user_id", table_name="service_tokens")
    op.drop_index("ix_service_tokens_created_by_user_id", table_name="service_tokens")
    op.drop_index("ix_service_tokens_workspace_created_at", table_name="service_tokens")
    op.drop_table("service_tokens")
