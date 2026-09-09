"""protocol-neutral execution requests

Revision ID: 0048_execution_requests
Revises: 0047_personal_dev_identity
Create Date: 2026-09-09

Control-plane intent, independent of MCP/CLI/studio transport. MlJob remains
the worker queue. This revision does not add transports or change Labs routes.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from alembic_frozen.rev_0048_execution_requests import (
    CK_EXECUTION_REQUEST_OPERATION,
    CK_EXECUTION_REQUEST_PARENT_NOT_SELF,
    CK_EXECUTION_REQUEST_RESULT_BOUNDED,
    CK_EXECUTION_REQUEST_RESULT_NO_SECRETS,
    CK_EXECUTION_REQUEST_RESULT_OBJECT,
    CK_EXECUTION_REQUEST_SOURCE,
    CK_EXECUTION_REQUEST_SPEC_BOUNDED,
    CK_EXECUTION_REQUEST_SPEC_NO_SECRETS,
    CK_EXECUTION_REQUEST_SPEC_OBJECT,
    CK_EXECUTION_REQUEST_STATUS,
)

revision: str = "0048_execution_requests"
down_revision: Union[str, Sequence[str], None] = "0047_personal_dev_identity"
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
    op.create_table(
        "execution_requests",
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
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("source_surface", sa.String(length=32), nullable=False),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("external_request_id", sa.String(length=128), nullable=True),
        sa.Column(
            "parent_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "request_spec",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result_summary", postgresql.JSONB(), nullable=True),
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_summary", sa.String(length=2048), nullable=True),
        sa.UniqueConstraint("workspace_id", "id", name="uq_execution_requests_workspace_id"),
        sa.CheckConstraint(CK_EXECUTION_REQUEST_OPERATION, name="ck_execution_requests_operation"),
        sa.CheckConstraint(CK_EXECUTION_REQUEST_SOURCE, name="ck_execution_requests_source"),
        sa.CheckConstraint(CK_EXECUTION_REQUEST_STATUS, name="ck_execution_requests_status"),
        sa.CheckConstraint(
            CK_EXECUTION_REQUEST_SPEC_OBJECT, name="ck_execution_requests_spec_object"
        ),
        sa.CheckConstraint(
            CK_EXECUTION_REQUEST_SPEC_BOUNDED, name="ck_execution_requests_spec_bounded"
        ),
        sa.CheckConstraint(
            CK_EXECUTION_REQUEST_SPEC_NO_SECRETS,
            name="ck_execution_requests_spec_no_secrets",
        ),
        sa.CheckConstraint(
            CK_EXECUTION_REQUEST_RESULT_OBJECT, name="ck_execution_requests_result_object"
        ),
        sa.CheckConstraint(
            CK_EXECUTION_REQUEST_RESULT_BOUNDED,
            name="ck_execution_requests_result_bounded",
        ),
        sa.CheckConstraint(
            CK_EXECUTION_REQUEST_RESULT_NO_SECRETS,
            name="ck_execution_requests_result_no_secrets",
        ),
        sa.CheckConstraint(
            CK_EXECUTION_REQUEST_PARENT_NOT_SELF,
            name="ck_execution_requests_parent_not_self",
        ),
    )
    _add_set_null_fk(
        "execution_requests",
        "fk_execution_requests_workspace_project",
        "project_id",
        "projects",
    )
    _add_set_null_fk(
        "execution_requests",
        "fk_execution_requests_workspace_parent",
        "parent_request_id",
        "execution_requests",
    )
    _add_set_null_fk(
        "execution_requests",
        "fk_execution_requests_workspace_workflow_run",
        "workflow_run_id",
        "workflow_runs",
    )
    _add_set_null_fk(
        "execution_requests",
        "fk_execution_requests_workspace_pipeline_run",
        "pipeline_run_id",
        "experiments",
    )
    op.create_index(
        "ix_execution_requests_workspace_status_created_at",
        "execution_requests",
        ["workspace_id", "status", "created_at"],
    )
    op.create_index(
        "uq_execution_requests_workspace_idempotency_key",
        "execution_requests",
        ["workspace_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_execution_requests_workspace_idempotency_key",
        table_name="execution_requests",
    )
    op.drop_index(
        "ix_execution_requests_workspace_status_created_at",
        table_name="execution_requests",
    )
    op.drop_table("execution_requests")
