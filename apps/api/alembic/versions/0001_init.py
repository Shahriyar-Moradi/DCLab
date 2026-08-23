"""init decision tables

Revision ID: 0001_init
Revises:
Create Date: 2026-08-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_init"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "opportunities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("customer_id", sa.String(128), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="AED"),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("close_date", sa.Date(), nullable=True),
        sa.Column("last_contact_days_ago", sa.Integer(), nullable=True),
        sa.Column("engagement_score", sa.Float(), nullable=True),
        sa.Column("sales_rep_available", sa.Boolean(), nullable=True),
        sa.Column("industry", sa.String(128), nullable=True),
        sa.Column("num_interactions", sa.Integer(), nullable=True),
        sa.Column("converted", sa.Integer(), nullable=True),
    )
    op.create_index("ix_opportunities_external_id", "opportunities", ["external_id"], unique=True)

    op.create_table(
        "predictions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("opportunity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("opportunities.id"), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("conversion_probability", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_predictions_opportunity_id", "predictions", ["opportunity_id"])

    op.create_table(
        "decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("opportunity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("opportunities.id"), nullable=False),
        sa.Column("prediction_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("predictions.id"), nullable=False),
        sa.Column("recommended_action", sa.String(64), nullable=False),
        sa.Column("expected_revenue", sa.Numeric(14, 2), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reasoning", postgresql.JSONB(), nullable=False),
        sa.Column("policy_version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending_review"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_decisions_opportunity_id", "decisions", ["opportunity_id"], unique=True)
    op.create_index("ix_decisions_prediction_id", "decisions", ["prediction_id"])


def downgrade() -> None:
    op.drop_table("decisions")
    op.drop_table("predictions")
    op.drop_table("opportunities")
