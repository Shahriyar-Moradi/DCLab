"""hashed recovery tokens, membership suspension, email_verified_at

Revision ID: 0056_auth_hardening
Revises: 0055_auth_sessions
Create Date: 2026-09-10

CSRF/CSP/throttle live in application code (ADR 0002). This revision is
additive identity-plane state only. Does not change PipelineRun/Experiment
or ML behavior.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0056_auth_hardening"
down_revision: Union[str, Sequence[str], None] = "0055_auth_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workspace_memberships",
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "auth_recovery_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_auth_recovery_tokens_token_hash"),
        sa.CheckConstraint(
            "purpose IN ('password_reset', 'email_verification')",
            name="ck_auth_recovery_tokens_purpose",
        ),
    )
    op.create_index(
        "ix_auth_recovery_tokens_user_id", "auth_recovery_tokens", ["user_id"]
    )
    op.create_index(
        "ix_auth_recovery_tokens_expires_at", "auth_recovery_tokens", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_auth_recovery_tokens_expires_at", table_name="auth_recovery_tokens"
    )
    op.drop_index("ix_auth_recovery_tokens_user_id", table_name="auth_recovery_tokens")
    op.drop_table("auth_recovery_tokens")
    op.drop_column("workspace_memberships", "suspended_at")
    op.drop_column("users", "email_verified_at")
