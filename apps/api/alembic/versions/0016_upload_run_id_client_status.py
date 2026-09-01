"""persist explicit run_id and coarse client_status on client_lab_uploads

`run_id` is the stable ML-run identity (currently equal to the upload id).
`client_status` is the four-state client view (queued / processing / completed
/ failed), kept separate from fine-grained `pipeline_status`.

Revision ID: 0016_upload_run_id_client_status
Revises: 0015_pipeline_status_stages
Create Date: 2026-08-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_upload_run_id_client_status"
down_revision: Union[str, None] = "0015_pipeline_status_stages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "client_lab_uploads",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(sa.text("UPDATE client_lab_uploads SET run_id = id WHERE run_id IS NULL"))
    op.alter_column("client_lab_uploads", "run_id", nullable=False)
    op.create_index("ix_client_lab_uploads_run_id", "client_lab_uploads", ["run_id"], unique=True)

    op.add_column(
        "client_lab_uploads",
        sa.Column("client_status", sa.String(length=16), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE client_lab_uploads SET client_status = CASE
                WHEN pipeline_status = 'queued' THEN 'queued'
                WHEN pipeline_status = 'completed' THEN 'completed'
                WHEN pipeline_status IN (
                    'ingesting', 'analyzing', 'cleaning', 'feature_engineering',
                    'preprocessing', 'splitting', 'cross_validation', 'training',
                    'evaluating', 'predicting', 'running'
                ) THEN 'processing'
                ELSE 'failed'
            END
            """
        )
    )
    op.alter_column(
        "client_lab_uploads",
        "client_status",
        nullable=False,
        server_default="queued",
    )
    op.create_index("ix_client_lab_uploads_client_status", "client_lab_uploads", ["client_status"])
    op.create_check_constraint(
        "ck_client_lab_uploads_client_status",
        "client_lab_uploads",
        "client_status IN ('queued', 'processing', 'completed', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_client_lab_uploads_client_status", "client_lab_uploads", type_="check")
    op.drop_index("ix_client_lab_uploads_client_status", table_name="client_lab_uploads")
    op.drop_column("client_lab_uploads", "client_status")
    op.drop_index("ix_client_lab_uploads_run_id", table_name="client_lab_uploads")
    op.drop_column("client_lab_uploads", "run_id")
