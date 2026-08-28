"""add auto-train pipeline columns to client_lab_uploads

Simple-case auto-train (see docs/LABS_DATA_UNDERSTANDING.md): admin-only
status/log for the automatic EDA -> ColumnTransformer -> RandomForest/XGBoost
job that runs behind a Labs custom-box upload. Never exposed to `/app`.

Revision ID: 0011_client_lab_upload_pipeline
Revises: 0010_client_lab_uploads
Create Date: 2026-08-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_client_lab_upload_pipeline"
down_revision: Union[str, None] = "0010_client_lab_uploads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "client_lab_uploads",
        sa.Column("pipeline_status", sa.String(16), nullable=False, server_default="not_applicable"),
    )
    op.add_column(
        "client_lab_uploads",
        sa.Column("pipeline_log", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "client_lab_uploads",
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_client_lab_uploads_pipeline_status", "client_lab_uploads", ["pipeline_status"])


def downgrade() -> None:
    op.drop_index("ix_client_lab_uploads_pipeline_status", table_name="client_lab_uploads")
    op.drop_column("client_lab_uploads", "experiment_id")
    op.drop_column("client_lab_uploads", "pipeline_log")
    op.drop_column("client_lab_uploads", "pipeline_status")
