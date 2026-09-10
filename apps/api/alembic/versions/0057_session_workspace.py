"""session selected_workspace_id selector (not tenant proof)

Revision ID: 0057_session_workspace
Revises: 0056_auth_hardening
Create Date: 2026-09-10

Remembers the browser active-workspace selector on auth_sessions.
Identity-plane: the FK is ON DELETE SET NULL and is never treated as
proof of access (ADR 0003). Does not change PipelineRun/Experiment or
ML behavior.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0057_session_workspace"
down_revision: Union[str, Sequence[str], None] = "0056_auth_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "auth_sessions",
        sa.Column(
            "selected_workspace_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_auth_sessions_selected_workspace_id",
        "auth_sessions",
        "workspaces",
        ["selected_workspace_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_auth_sessions_selected_workspace_id",
        "auth_sessions",
        type_="foreignkey",
    )
    op.drop_column("auth_sessions", "selected_workspace_id")
