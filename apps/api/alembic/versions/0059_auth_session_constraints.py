"""same-user session rotation lineage, cleanup indexes, hash CHECKs

Revision ID: 0059_auth_session_constraints
Revises: 0058_simulation_workspace
Create Date: 2026-09-12

S0-P02C: rotation cannot cross users; expiry/revocation queries are indexed;
token hashes stay unique SHA-256 hex. Forward-repairs any cross-user
``rotated_from_id`` before adding the composite FK. Does not change
PipelineRun/Experiment or ML behavior. Does not introduce Redis.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0059_auth_session_constraints"
down_revision: Union[str, Sequence[str], None] = "0058_simulation_workspace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _drop_legacy_rotated_from_fk() -> None:
    bind = op.get_bind()
    names = bind.execute(
        sa.text(
            """
            SELECT con.conname
            FROM pg_constraint AS con
            JOIN pg_class AS cls ON cls.oid = con.conrelid
            JOIN pg_namespace AS nsp ON nsp.oid = cls.relnamespace
            WHERE nsp.nspname = 'public'
              AND cls.relname = 'auth_sessions'
              AND con.contype = 'f'
              AND pg_get_constraintdef(con.oid) LIKE '%rotated_from_id%'
              AND con.conname <> 'fk_auth_sessions_rotated_from_user'
            """
        )
    ).scalars().all()
    for name in names:
        op.drop_constraint(name, "auth_sessions", type_="foreignkey")


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE auth_sessions AS child
            SET rotated_from_id = NULL
            FROM auth_sessions AS parent
            WHERE child.rotated_from_id = parent.id
              AND child.user_id IS DISTINCT FROM parent.user_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE auth_sessions
            SET rotated_from_id = NULL
            WHERE rotated_from_id = id
            """
        )
    )
    _drop_legacy_rotated_from_fk()
    op.create_unique_constraint(
        "uq_auth_sessions_id_user_id",
        "auth_sessions",
        ["id", "user_id"],
    )
    op.execute(
        sa.text(
            'ALTER TABLE "auth_sessions" '
            'ADD CONSTRAINT "fk_auth_sessions_rotated_from_user" '
            'FOREIGN KEY ("rotated_from_id", "user_id") '
            'REFERENCES "auth_sessions" ("id", "user_id") '
            'ON DELETE SET NULL ("rotated_from_id")'
        )
    )
    op.create_check_constraint(
        "ck_auth_sessions_token_hash_sha256",
        "auth_sessions",
        "token_hash ~ '^[a-f0-9]{64}$'",
    )
    op.create_check_constraint(
        "ck_auth_sessions_rotated_from_not_self",
        "auth_sessions",
        "rotated_from_id IS NULL OR rotated_from_id <> id",
    )
    op.create_index(
        "ix_auth_sessions_absolute_expires_at",
        "auth_sessions",
        ["absolute_expires_at"],
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_auth_sessions_revoked_at "
            "ON auth_sessions (revoked_at) "
            "WHERE revoked_at IS NOT NULL"
        )
    )
    op.create_check_constraint(
        "ck_auth_recovery_tokens_token_hash_sha256",
        "auth_recovery_tokens",
        "token_hash ~ '^[a-f0-9]{64}$'",
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_auth_recovery_tokens_consumed_at "
            "ON auth_recovery_tokens (consumed_at) "
            "WHERE consumed_at IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_auth_recovery_tokens_consumed_at"))
    op.drop_constraint(
        "ck_auth_recovery_tokens_token_hash_sha256",
        "auth_recovery_tokens",
        type_="check",
    )
    op.execute(sa.text("DROP INDEX IF EXISTS ix_auth_sessions_revoked_at"))
    op.drop_index("ix_auth_sessions_absolute_expires_at", table_name="auth_sessions")
    op.drop_constraint(
        "ck_auth_sessions_rotated_from_not_self",
        "auth_sessions",
        type_="check",
    )
    op.drop_constraint(
        "ck_auth_sessions_token_hash_sha256",
        "auth_sessions",
        type_="check",
    )
    op.drop_constraint(
        "fk_auth_sessions_rotated_from_user",
        "auth_sessions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_auth_sessions_id_user_id",
        "auth_sessions",
        type_="unique",
    )
    op.create_foreign_key(
        "auth_sessions_rotated_from_id_fkey",
        "auth_sessions",
        "auth_sessions",
        ["rotated_from_id"],
        ["id"],
        ondelete="SET NULL",
    )
