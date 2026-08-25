"""add simulation_runs

Revision ID: 0003_simulation_runs
Revises: 0002_prediction_evidence
Create Date: 2026-08-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_simulation_runs"
down_revision: Union[str, None] = "0002_prediction_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "simulation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("use_case", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("fusion", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_simulation_runs_use_case", "simulation_runs", ["use_case"])


def downgrade() -> None:
    op.drop_index("ix_simulation_runs_use_case", table_name="simulation_runs")
    op.drop_table("simulation_runs")
