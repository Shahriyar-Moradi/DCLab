"""generalize the durable ml_jobs worker queue

Revision ID: 0051_ml_job_queue
Revises: 0050_privacy_audit
Create Date: 2026-09-09

Evolve ml_jobs into an extensible handler queue. auto_train / upload_id stay
compatible. job_type is a slug, not a closed auto_train enum. This revision
does not add MCP jobs or extra worker job kinds.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from alembic_frozen.rev_0051_ml_job_queue import (
    CK_ML_JOB_AUTO_TRAIN_UPLOAD,
    CK_ML_JOB_HANDLER_KEY,
    CK_ML_JOB_HANDLER_VERSION,
    CK_ML_JOB_PAYLOAD_BOUNDED,
    CK_ML_JOB_PAYLOAD_NO_SECRETS,
    CK_ML_JOB_PAYLOAD_OBJECT,
    CK_ML_JOB_PRIORITY,
    CK_ML_JOB_TYPE,
    HANDLER_LABS_AUTO_TRAIN,
)

revision: str = "0051_ml_job_queue"
down_revision: Union[str, Sequence[str], None] = "0050_privacy_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_set_null_fk(table: str, name: str, child_id: str, parent: str) -> None:
    op.execute(
        sa.text(
            f'ALTER TABLE "{table}" '
            f'ADD CONSTRAINT "{name}" '
            f'FOREIGN KEY ("workspace_id", "{child_id}") '
            f'REFERENCES "{parent}" ("workspace_id", "id") '
            f'ON DELETE SET NULL ("{child_id}")'
        )
    )


def upgrade() -> None:
    op.add_column(
        "ml_jobs",
        sa.Column(
            "execution_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "ml_jobs",
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "ml_jobs",
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "ml_jobs",
        sa.Column("handler_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ml_jobs",
        sa.Column("handler_version", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "ml_jobs",
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "ml_jobs",
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ml_jobs",
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "ml_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ml_jobs",
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.execute(
        sa.text(
            "UPDATE ml_jobs SET handler_key = :handler_key "
            "WHERE handler_key IS NULL"
        ).bindparams(handler_key=HANDLER_LABS_AUTO_TRAIN)
    )
    op.execute(sa.text("UPDATE ml_jobs SET available_at = queued_at WHERE available_at IS NULL"))
    op.alter_column("ml_jobs", "handler_key", nullable=False)
    op.alter_column(
        "ml_jobs",
        "available_at",
        nullable=False,
        server_default=sa.text("now()"),
    )

    op.drop_constraint("ck_ml_jobs_type_valid", "ml_jobs", type_="check")
    op.create_check_constraint("ck_ml_jobs_type_valid", "ml_jobs", CK_ML_JOB_TYPE)
    op.create_check_constraint(
        "ck_ml_jobs_handler_key_valid", "ml_jobs", CK_ML_JOB_HANDLER_KEY
    )
    op.create_check_constraint(
        "ck_ml_jobs_handler_version_valid", "ml_jobs", CK_ML_JOB_HANDLER_VERSION
    )
    op.create_check_constraint("ck_ml_jobs_priority_range", "ml_jobs", CK_ML_JOB_PRIORITY)
    op.create_check_constraint(
        "ck_ml_jobs_auto_train_upload", "ml_jobs", CK_ML_JOB_AUTO_TRAIN_UPLOAD
    )
    op.create_check_constraint(
        "ck_ml_jobs_payload_object", "ml_jobs", CK_ML_JOB_PAYLOAD_OBJECT
    )
    op.create_check_constraint(
        "ck_ml_jobs_payload_bounded", "ml_jobs", CK_ML_JOB_PAYLOAD_BOUNDED
    )
    op.create_check_constraint(
        "ck_ml_jobs_payload_no_secrets", "ml_jobs", CK_ML_JOB_PAYLOAD_NO_SECRETS
    )

    op.drop_constraint("uq_ml_jobs_type_target", "ml_jobs", type_="unique")

    _add_set_null_fk(
        "ml_jobs",
        "fk_ml_jobs_workspace_execution_request",
        "execution_request_id",
        "execution_requests",
    )
    _add_set_null_fk(
        "ml_jobs",
        "fk_ml_jobs_workspace_workflow_run",
        "workflow_run_id",
        "workflow_runs",
    )
    _add_set_null_fk(
        "ml_jobs",
        "fk_ml_jobs_workspace_pipeline_run",
        "pipeline_run_id",
        "experiments",
    )

    op.create_index("ix_ml_jobs_execution_request_id", "ml_jobs", ["execution_request_id"])
    op.create_index("ix_ml_jobs_workflow_run_id", "ml_jobs", ["workflow_run_id"])
    op.create_index("ix_ml_jobs_pipeline_run_id", "ml_jobs", ["pipeline_run_id"])
    op.create_index(
        "ix_ml_jobs_queued_claim",
        "ml_jobs",
        ["priority", "available_at", "queued_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_ml_jobs_running_lease",
        "ml_jobs",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("ix_ml_jobs_running_lease", table_name="ml_jobs")
    op.drop_index("ix_ml_jobs_queued_claim", table_name="ml_jobs")
    op.drop_index("ix_ml_jobs_pipeline_run_id", table_name="ml_jobs")
    op.drop_index("ix_ml_jobs_workflow_run_id", table_name="ml_jobs")
    op.drop_index("ix_ml_jobs_execution_request_id", table_name="ml_jobs")
    op.drop_constraint(
        "fk_ml_jobs_workspace_pipeline_run", "ml_jobs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_ml_jobs_workspace_workflow_run", "ml_jobs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_ml_jobs_workspace_execution_request", "ml_jobs", type_="foreignkey"
    )
    op.create_unique_constraint(
        "uq_ml_jobs_type_target", "ml_jobs", ["job_type", "target_id"]
    )
    op.drop_constraint("ck_ml_jobs_payload_no_secrets", "ml_jobs", type_="check")
    op.drop_constraint("ck_ml_jobs_payload_bounded", "ml_jobs", type_="check")
    op.drop_constraint("ck_ml_jobs_payload_object", "ml_jobs", type_="check")
    op.drop_constraint("ck_ml_jobs_auto_train_upload", "ml_jobs", type_="check")
    op.drop_constraint("ck_ml_jobs_priority_range", "ml_jobs", type_="check")
    op.drop_constraint("ck_ml_jobs_handler_version_valid", "ml_jobs", type_="check")
    op.drop_constraint("ck_ml_jobs_handler_key_valid", "ml_jobs", type_="check")
    op.drop_constraint("ck_ml_jobs_type_valid", "ml_jobs", type_="check")
    op.create_check_constraint(
        "ck_ml_jobs_type_valid",
        "ml_jobs",
        "job_type IN ('auto_train')",
    )
    op.drop_column("ml_jobs", "payload")
    op.drop_column("ml_jobs", "lease_expires_at")
    op.drop_column("ml_jobs", "claimed_by")
    op.drop_column("ml_jobs", "available_at")
    op.drop_column("ml_jobs", "priority")
    op.drop_column("ml_jobs", "handler_version")
    op.drop_column("ml_jobs", "handler_key")
    op.drop_column("ml_jobs", "pipeline_run_id")
    op.drop_column("ml_jobs", "workflow_run_id")
    op.drop_column("ml_jobs", "execution_request_id")
