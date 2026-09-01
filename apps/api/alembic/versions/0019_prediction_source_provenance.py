"""add source-row provenance to experiment test predictions

Revision ID: 0019_prediction_source_rows
Revises: 0018_upload_explicit_target
Create Date: 2026-09-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_prediction_source_rows"
down_revision: Union[str, Sequence[str], None] = "0018_upload_explicit_target"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "experiment_test_predictions",
        sa.Column("source_row_index", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_experiment_test_predictions_source_row_index",
        "experiment_test_predictions",
        ["source_row_index"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiment_test_predictions_source_row_index",
        table_name="experiment_test_predictions",
    )
    op.drop_column("experiment_test_predictions", "source_row_index")
