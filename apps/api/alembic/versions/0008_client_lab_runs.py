"""add client_lab_runs table

Step 5 — Client Labs. Stores one row per bounded, client-triggered trial run.
`insights` holds only already-translated `ClientFacingInsight` payloads.

Revision ID: 0008_client_lab_runs
Revises: 0007_decision_incremental_value
Create Date: 2026-08-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_client_lab_runs"
down_revision: Union[str, None] = "0007_decision_incremental_value"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.create_table(
        "client_lab_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            server_default=DEFAULT_WORKSPACE_ID,
        ),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("use_case", sa.String(64), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("data_source", sa.String(16), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_reason", sa.String(512), nullable=True),
        sa.Column("insights", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_client_lab_runs_workspace_id", "client_lab_runs", ["workspace_id"])
    op.create_index("ix_client_lab_runs_use_case", "client_lab_runs", ["use_case"])
    op.create_index("ix_client_lab_runs_status", "client_lab_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_client_lab_runs_status", table_name="client_lab_runs")
    op.drop_index("ix_client_lab_runs_use_case", table_name="client_lab_runs")
    op.drop_index("ix_client_lab_runs_workspace_id", table_name="client_lab_runs")
    op.drop_table("client_lab_runs")
