"""simulation_runs workspace/project lineage (honest empty backfill)

Revision ID: 0058_simulation_workspace
Revises: 0057_session_workspace
Create Date: 2026-09-10

Adds nullable workspace_id / project_id so new simulation runs are tenant
owned. Does not backfill existing rows: there is no ownership signal
(ADR 0004). Does not change PipelineRun/Experiment or ML behavior.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0058_simulation_workspace"
down_revision: Union[str, Sequence[str], None] = "0057_session_workspace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "simulation_runs",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "simulation_runs",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_simulation_runs_workspace_id",
        "simulation_runs",
        ["workspace_id"],
    )
    op.create_index(
        "ix_simulation_runs_workspace_use_case_created",
        "simulation_runs",
        ["workspace_id", "use_case", "created_at"],
    )
    op.create_unique_constraint(
        "uq_simulation_runs_workspace_id",
        "simulation_runs",
        ["workspace_id", "id"],
    )
    op.create_check_constraint(
        "ck_simulation_runs_project_requires_workspace",
        "simulation_runs",
        "project_id IS NULL OR workspace_id IS NOT NULL",
    )
    op.create_foreign_key(
        "fk_simulation_runs_workspace_id",
        "simulation_runs",
        "workspaces",
        ["workspace_id"],
        ["id"],
    )
    op.execute(
        sa.text(
            'ALTER TABLE "simulation_runs" '
            'ADD CONSTRAINT "fk_simulation_runs_workspace_project" '
            'FOREIGN KEY ("workspace_id", "project_id") '
            'REFERENCES "projects" ("workspace_id", "id") '
            'ON DELETE SET NULL ("project_id")'
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            'ALTER TABLE "simulation_runs" '
            'DROP CONSTRAINT IF EXISTS "fk_simulation_runs_workspace_project"'
        )
    )
    op.drop_constraint(
        "fk_simulation_runs_workspace_id",
        "simulation_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_simulation_runs_project_requires_workspace",
        "simulation_runs",
        type_="check",
    )
    op.drop_constraint(
        "uq_simulation_runs_workspace_id",
        "simulation_runs",
        type_="unique",
    )
    op.drop_index(
        "ix_simulation_runs_workspace_use_case_created",
        table_name="simulation_runs",
    )
    op.drop_index("ix_simulation_runs_workspace_id", table_name="simulation_runs")
    op.drop_column("simulation_runs", "project_id")
    op.drop_column("simulation_runs", "workspace_id")
