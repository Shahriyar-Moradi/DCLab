"""add prediction evidence json

Revision ID: 0002_prediction_evidence
Revises: 0001_init
Create Date: 2026-08-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_prediction_evidence"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("predictions", sa.Column("evidence", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("predictions", "evidence")
