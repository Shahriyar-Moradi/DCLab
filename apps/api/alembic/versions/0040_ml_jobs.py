"""durable ml job queue

Revision ID: 0040_ml_jobs
Revises: 0039_scientific_plans
Create Date: 2026-09-05

API requests persist a queued ml_jobs row in the same transaction as the
upload. A worker claims with FOR UPDATE SKIP LOCKED.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.domain.ml_jobs import CK_ML_JOB_STATUS, CK_ML_JOB_TYPE

revision: str = "0040_ml_jobs"
down_revision: Union[str, Sequence[str], None] = "0039_scientific_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ml_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "upload_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("client_lab_uploads.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_ml_jobs_workspace_id"),
        sa.UniqueConstraint("job_type", "target_id", name="uq_ml_jobs_type_target"),
        sa.UniqueConstraint("upload_id", name="uq_ml_jobs_upload_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_ml_jobs_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "upload_id"],
            ["client_lab_uploads.workspace_id", "client_lab_uploads.id"],
            name="fk_ml_jobs_workspace_upload",
        ),
        sa.CheckConstraint(CK_ML_JOB_TYPE, name="ck_ml_jobs_type_valid"),
        sa.CheckConstraint(CK_ML_JOB_STATUS, name="ck_ml_jobs_status_valid"),
        sa.CheckConstraint("attempts >= 0", name="ck_ml_jobs_attempts_non_negative"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_ml_jobs_max_attempts_positive"),
    )
    op.create_index("ix_ml_jobs_status_queued_at", "ml_jobs", ["status", "queued_at"])
    op.create_index("ix_ml_jobs_workspace_created_at", "ml_jobs", ["workspace_id", "created_at"])
    op.create_index("ix_ml_jobs_workspace_id", "ml_jobs", ["workspace_id"])
    op.create_index("ix_ml_jobs_project_id", "ml_jobs", ["project_id"])
    op.create_index("ix_ml_jobs_job_type", "ml_jobs", ["job_type"])
    op.create_index("ix_ml_jobs_status", "ml_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_ml_jobs_status", table_name="ml_jobs")
    op.drop_index("ix_ml_jobs_job_type", table_name="ml_jobs")
    op.drop_index("ix_ml_jobs_project_id", table_name="ml_jobs")
    op.drop_index("ix_ml_jobs_workspace_id", table_name="ml_jobs")
    op.drop_index("ix_ml_jobs_workspace_created_at", table_name="ml_jobs")
    op.drop_index("ix_ml_jobs_status_queued_at", table_name="ml_jobs")
    op.drop_table("ml_jobs")
