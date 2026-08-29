"""link client_lab_uploads to the Lab dataset created at upload time

Revision ID: 0014_client_lab_upload_dataset
Revises: 0013_lab_decision_rule_and_fill
Create Date: 2026-08-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_client_lab_upload_dataset"
down_revision: Union[str, None] = "0013_lab_decision_rule_and_fill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "client_lab_uploads",
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("client_lab_uploads", "dataset_id")
