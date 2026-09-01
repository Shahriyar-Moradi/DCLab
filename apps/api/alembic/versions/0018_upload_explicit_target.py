"""add optional explicit target for arbitrary uploads

Revision ID: 0018_upload_explicit_target
Revises: 0017_experiment_test_predictions
Create Date: 2026-09-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_upload_explicit_target"
down_revision: Union[str, Sequence[str], None] = "0017_experiment_test_predictions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "client_lab_uploads",
        sa.Column("explicit_target_column", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("client_lab_uploads", "explicit_target_column")
