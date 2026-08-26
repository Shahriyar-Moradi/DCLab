"""add workspaces table and workspace_id fk to opportunities/predictions/decisions

Revision ID: 0005_workspaces
Revises: 0004_experiment_platform
Create Date: 2026-08-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_workspaces"
down_revision: Union[str, None] = "0004_experiment_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match app.db.models.DEFAULT_WORKSPACE_ID / DEFAULT_WORKSPACE_SLUG exactly —
# this is the workspace every pre-existing row is backfilled into, and the one the
# API falls back to when a request omits X-Workspace-Id.
DEFAULT_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_WORKSPACE_SLUG = "default"

SCOPED_TABLES = ("opportunities", "predictions", "decisions")


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_workspaces_slug", "workspaces", ["slug"], unique=True)

    # Seed the default workspace first so the nullable-then-backfilled columns
    # below always have a valid row to point at, whether the DB is empty or
    # already has data.
    op.execute(
        "INSERT INTO workspaces (id, slug, name, created_at) "
        f"VALUES ('{DEFAULT_WORKSPACE_ID}', '{DEFAULT_WORKSPACE_SLUG}', 'Default', now())"
    )

    for table in SCOPED_TABLES:
        op.add_column(table, sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.execute(f"UPDATE {table} SET workspace_id = '{DEFAULT_WORKSPACE_ID}' WHERE workspace_id IS NULL")
        op.alter_column(table, "workspace_id", nullable=False, server_default=DEFAULT_WORKSPACE_ID)
        op.create_foreign_key(
            f"fk_{table}_workspace_id_workspaces", table, "workspaces", ["workspace_id"], ["id"]
        )
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])


def downgrade() -> None:
    for table in reversed(SCOPED_TABLES):
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)
        op.drop_constraint(f"fk_{table}_workspace_id_workspaces", table, type_="foreignkey")
        op.drop_column(table, "workspace_id")
    op.drop_index("ix_workspaces_slug", table_name="workspaces")
    op.drop_table("workspaces")
