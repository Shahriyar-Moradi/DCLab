"""add experiment_test_predictions — holdout scores for a Labs experiment

The opportunity `predictions` table stays as-is (conversion probability per
opportunity). These rows are one per held-out test record on an experiment.

Revision ID: 0017_experiment_test_predictions
Revises: 0016_upload_run_id_client_status
Create Date: 2026-08-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_experiment_test_predictions"
down_revision: Union[str, Sequence[str], None] = "0016_upload_run_id_client_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "experiment_test_predictions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.String(length=512), nullable=False),
        sa.Column("predicted_value", postgresql.JSONB(), nullable=False),
        sa.Column("probability", sa.Float(), nullable=True),
        sa.Column("y_true", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "experiment_id",
            "row_index",
            name="uq_experiment_test_predictions_experiment_row",
        ),
    )
    op.create_index(
        "ix_experiment_test_predictions_experiment_id",
        "experiment_test_predictions",
        ["experiment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiment_test_predictions_experiment_id",
        table_name="experiment_test_predictions",
    )
    op.drop_table("experiment_test_predictions")
