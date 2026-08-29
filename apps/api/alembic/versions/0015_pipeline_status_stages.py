"""widen client_lab_uploads.pipeline_status for fine-grained run stages

Revision ID: 0015_pipeline_status_stages
Revises: 0014_client_lab_upload_dataset
Create Date: 2026-08-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_pipeline_status_stages"
down_revision: Union[str, None] = "0014_client_lab_upload_dataset"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "client_lab_uploads",
        "pipeline_status",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
        existing_server_default="not_applicable",
    )


def downgrade() -> None:
    op.alter_column(
        "client_lab_uploads",
        "pipeline_status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
        existing_server_default="not_applicable",
    )
