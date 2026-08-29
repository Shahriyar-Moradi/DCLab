"""record original rule-engine action and optional fill_value on lab_decision_records

Revision ID: 0013_lab_decision_rule_and_fill
Revises: 0012_lab_decision_records
Create Date: 2026-08-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_lab_decision_rule_and_fill"
down_revision: Union[str, None] = "0012_lab_decision_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "lab_decision_records",
        sa.Column("rule_decision", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "lab_decision_records",
        sa.Column("fill_value", postgresql.JSONB(), nullable=True),
    )
    op.execute("UPDATE lab_decision_records SET rule_decision = final_decision WHERE rule_decision = ''")


def downgrade() -> None:
    op.drop_column("lab_decision_records", "fill_value")
    op.drop_column("lab_decision_records", "rule_decision")
