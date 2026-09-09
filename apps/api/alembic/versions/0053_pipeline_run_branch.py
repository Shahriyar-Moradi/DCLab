"""pipeline-run scientific branch lineage

Revision ID: 0053_pipeline_run_branch
Revises: 0052_visualizations
Create Date: 2026-09-09

Nullable parent_pipeline_run_id / branch_key / branch_reason on experiments.
Tenant-aware self FK. Parent delete is NO ACTION so a child execution is not
rewritten and the parent row is not cascaded. This revision does not fork
runs, copy evidence, or implement agent branching behavior.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from alembic_frozen.rev_0053_pipeline_run_branch import (
    CK_EXPERIMENTS_BRANCH_KEY,
    CK_EXPERIMENTS_BRANCH_REASON,
    CK_EXPERIMENTS_BRANCH_REQUIRES_PARENT,
    CK_EXPERIMENTS_PARENT_NOT_SELF,
)

revision: str = "0053_pipeline_run_branch"
down_revision: Union[str, Sequence[str], None] = "0052_visualizations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column(
            "parent_pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "experiments",
        sa.Column("branch_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "experiments",
        sa.Column("branch_reason", sa.String(length=512), nullable=True),
    )
    op.create_foreign_key(
        "fk_experiments_workspace_parent_pipeline_run",
        "experiments",
        "experiments",
        ["workspace_id", "parent_pipeline_run_id"],
        ["workspace_id", "id"],
    )
    op.create_index(
        "ix_experiments_parent_pipeline_run_id",
        "experiments",
        ["parent_pipeline_run_id"],
    )
    op.create_check_constraint(
        "ck_experiments_parent_not_self",
        "experiments",
        CK_EXPERIMENTS_PARENT_NOT_SELF,
    )
    op.create_check_constraint(
        "ck_experiments_branch_key",
        "experiments",
        CK_EXPERIMENTS_BRANCH_KEY,
    )
    op.create_check_constraint(
        "ck_experiments_branch_reason",
        "experiments",
        CK_EXPERIMENTS_BRANCH_REASON,
    )
    op.create_check_constraint(
        "ck_experiments_branch_requires_parent",
        "experiments",
        CK_EXPERIMENTS_BRANCH_REQUIRES_PARENT,
    )


def downgrade() -> None:
    op.drop_constraint("ck_experiments_branch_requires_parent", "experiments", type_="check")
    op.drop_constraint("ck_experiments_branch_reason", "experiments", type_="check")
    op.drop_constraint("ck_experiments_branch_key", "experiments", type_="check")
    op.drop_constraint("ck_experiments_parent_not_self", "experiments", type_="check")
    op.drop_index("ix_experiments_parent_pipeline_run_id", table_name="experiments")
    op.drop_constraint(
        "fk_experiments_workspace_parent_pipeline_run",
        "experiments",
        type_="foreignkey",
    )
    op.drop_column("experiments", "branch_reason")
    op.drop_column("experiments", "branch_key")
    op.drop_column("experiments", "parent_pipeline_run_id")
