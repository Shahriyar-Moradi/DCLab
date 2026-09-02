"""persist multiple ML run verification attempts

Revision ID: 0020_ml_verifications
Revises: 0019_prediction_source_rows
Create Date: 2026-09-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_ml_verifications"
down_revision: Union[str, Sequence[str], None] = "0019_prediction_source_rows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ml_run_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("client_lab_uploads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("audit_mode", sa.String(16), nullable=False),
        sa.Column("deterministic_status", sa.String(32), nullable=False),
        sa.Column("deterministic_checks", postgresql.JSONB(), nullable=False),
        sa.Column("deterministic_schema_version", sa.Integer(), nullable=False),
        sa.Column("llm_provider", sa.String(32), nullable=False),
        sa.Column("llm_model", sa.String(128), nullable=False),
        sa.Column("llm_status", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("redaction_summary", postgresql.JSONB(), nullable=False),
        sa.Column("llm_report", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.String(128), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("audit_mode IN ('routine', 'deep')", name="ck_ml_run_verifications_audit_mode"),
    )
    op.create_index("ix_ml_run_verifications_run_id", "ml_run_verifications", ["run_id"])
    op.create_index("ix_ml_run_verifications_experiment_id", "ml_run_verifications", ["experiment_id"])
    op.create_index("ix_ml_run_verifications_input_digest", "ml_run_verifications", ["input_digest"])
    op.create_index("ix_ml_run_verifications_created_at", "ml_run_verifications", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ml_run_verifications_created_at", table_name="ml_run_verifications")
    op.drop_index("ix_ml_run_verifications_input_digest", table_name="ml_run_verifications")
    op.drop_index("ix_ml_run_verifications_experiment_id", table_name="ml_run_verifications")
    op.drop_index("ix_ml_run_verifications_run_id", table_name="ml_run_verifications")
    op.drop_table("ml_run_verifications")
