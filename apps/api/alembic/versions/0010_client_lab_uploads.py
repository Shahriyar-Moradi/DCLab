"""add client_lab_uploads table

Open ingest for Client Labs: store any usual data file without a required schema.
Structuring messy files is not in this revision.

Revision ID: 0010_client_lab_uploads
Revises: 0009_client_lab_run_audits
Create Date: 2026-08-27

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_client_lab_uploads"
down_revision: Union[str, None] = "0009_client_lab_run_audits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.create_table(
        "client_lab_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            server_default=DEFAULT_WORKSPACE_ID,
        ),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("stored_path", sa.String(1024), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fields_noticed", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("has_named_fields", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_client_lab_uploads_workspace_id", "client_lab_uploads", ["workspace_id"])
    op.create_index("ix_client_lab_uploads_category", "client_lab_uploads", ["category"])


def downgrade() -> None:
    op.drop_index("ix_client_lab_uploads_category", table_name="client_lab_uploads")
    op.drop_index("ix_client_lab_uploads_workspace_id", table_name="client_lab_uploads")
    op.drop_table("client_lab_uploads")
