"""add incremental_value to decisions

Needed so the client-facing translator can rebuild a decision's plain-language
reasoning at read time without ever exposing the raw conversion probability or
model_version that produced it.

Revision ID: 0007_decision_incremental_value
Revises: 0006_users
Create Date: 2026-08-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_decision_incremental_value"
down_revision: Union[str, None] = "0006_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "decisions",
        sa.Column("incremental_value", sa.Numeric(14, 2), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("decisions", "incremental_value")
