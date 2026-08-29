"""add lab_decision_records — missing-value decision audit trail

One row per feature column on a Labs custom-box upload, covering rule-engine
decisions as well as LLM-assisted ones. Lets an admin answer why a column was
handled a given way, regardless of which path made the call.

Revision ID: 0012_lab_decision_records
Revises: 0011_client_lab_upload_pipeline
Create Date: 2026-08-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_lab_decision_records"
down_revision: Union[str, None] = "0011_client_lab_upload_pipeline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lab_decision_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "upload_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("client_lab_uploads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("column", sa.String(256), nullable=False),
        sa.Column("evidence_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("raw_llm_output", postgresql.JSONB(), nullable=True),
        sa.Column("validator_verdict", sa.String(1024), nullable=False),
        sa.Column("final_decision", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("source IN ('rule', 'llm', 'fallback')", name="ck_lab_decision_records_source"),
    )
    op.create_index("ix_lab_decision_records_upload_id", "lab_decision_records", ["upload_id"])


def downgrade() -> None:
    op.drop_index("ix_lab_decision_records_upload_id", table_name="lab_decision_records")
    op.drop_table("lab_decision_records")
