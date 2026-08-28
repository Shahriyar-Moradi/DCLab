"""add client_lab_run_audits table

Step 7 — admin-only, full raw ML output of a Client Labs trial run, one-to-one
with the client-facing (translated-only) client_lab_runs row it audits.

Revision ID: 0009_client_lab_run_audits
Revises: 0008_client_lab_runs
Create Date: 2026-08-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_client_lab_run_audits"
down_revision: Union[str, None] = "0008_client_lab_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "client_lab_run_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "client_lab_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("client_lab_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("use_case", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_client_lab_run_audits_client_lab_run_id", "client_lab_run_audits", ["client_lab_run_id"])
    op.create_index("ix_client_lab_run_audits_use_case", "client_lab_run_audits", ["use_case"])


def downgrade() -> None:
    op.drop_index("ix_client_lab_run_audits_use_case", table_name="client_lab_run_audits")
    op.drop_index("ix_client_lab_run_audits_client_lab_run_id", table_name="client_lab_run_audits")
    op.drop_table("client_lab_run_audits")
