"""separate DataAccess from DataSource

Revision ID: 0049_data_access
Revises: 0048_execution_requests
Create Date: 2026-09-09

DataSource stays the logical source. DataAccess is an authorized, executable
way to reach it. resource_locator is non-secret; credential_reference is an
opaque secret-manager pointer only. This revision does not add Salesforce,
Airbyte, MCP, Snowflake, or OAuth connectors. Dataset remains the immutable
DatasetVersion — no dataset_snapshots table.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from alembic_frozen.rev_0049_data_access import (
    CK_DATA_ACCESSES_CREDENTIAL_OPAQUE,
    CK_DATA_ACCESSES_EXECUTION_MODE,
    CK_DATA_ACCESSES_LOCATOR_BOUNDED,
    CK_DATA_ACCESSES_LOCATOR_NO_SECRETS,
    CK_DATA_ACCESSES_LOCATOR_OBJECT,
    CK_DATA_ACCESSES_PRIVACY_BOUNDED,
    CK_DATA_ACCESSES_PRIVACY_NO_SECRETS,
    CK_DATA_ACCESSES_PRIVACY_OBJECT,
    CK_DATA_ACCESSES_RETENTION_BOUNDED,
    CK_DATA_ACCESSES_RETENTION_NO_SECRETS,
    CK_DATA_ACCESSES_RETENTION_OBJECT,
    CK_DATA_ACCESSES_STATUS,
    CK_DATA_ACCESSES_TYPE,
    CK_DATA_ACCESSES_UPLOAD_IS_COPY,
)

revision: str = "0049_data_access"
down_revision: Union[str, Sequence[str], None] = "0048_execution_requests"
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


def _add_set_null_source_access_fk() -> None:
    op.execute(
        sa.text(
            'ALTER TABLE "ingestion_runs" '
            'ADD CONSTRAINT "fk_ingestion_runs_data_source_data_access" '
            'FOREIGN KEY ("data_source_id", "data_access_id") '
            'REFERENCES "data_accesses" ("data_source_id", "id") '
            'ON DELETE SET NULL ("data_access_id")'
        )
    )


def upgrade() -> None:
    op.create_table(
        "data_accesses",
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
        sa.Column(
            "data_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("access_type", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("execution_mode", sa.String(length=32), nullable=False),
        sa.Column(
            "resource_locator",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("credential_reference", sa.String(length=512), nullable=True),
        sa.Column("data_region", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "privacy_policy",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "retention_policy",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_data_accesses_workspace_id"),
        sa.UniqueConstraint(
            "data_source_id", "id", name="uq_data_accesses_data_source_id"
        ),
        sa.CheckConstraint(CK_DATA_ACCESSES_TYPE, name="ck_data_accesses_access_type"),
        sa.CheckConstraint(
            CK_DATA_ACCESSES_EXECUTION_MODE, name="ck_data_accesses_execution_mode"
        ),
        sa.CheckConstraint(CK_DATA_ACCESSES_STATUS, name="ck_data_accesses_status"),
        sa.CheckConstraint(
            CK_DATA_ACCESSES_UPLOAD_IS_COPY, name="ck_data_accesses_upload_is_copy"
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESSES_LOCATOR_OBJECT, name="ck_data_accesses_locator_object"
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESSES_LOCATOR_BOUNDED, name="ck_data_accesses_locator_bounded"
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESSES_LOCATOR_NO_SECRETS,
            name="ck_data_accesses_locator_no_secrets",
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESSES_PRIVACY_OBJECT, name="ck_data_accesses_privacy_object"
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESSES_PRIVACY_BOUNDED, name="ck_data_accesses_privacy_bounded"
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESSES_PRIVACY_NO_SECRETS,
            name="ck_data_accesses_privacy_no_secrets",
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESSES_RETENTION_OBJECT, name="ck_data_accesses_retention_object"
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESSES_RETENTION_BOUNDED,
            name="ck_data_accesses_retention_bounded",
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESSES_RETENTION_NO_SECRETS,
            name="ck_data_accesses_retention_no_secrets",
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESSES_CREDENTIAL_OPAQUE,
            name="ck_data_accesses_credential_opaque",
        ),
    )
    _add_set_null_fk(
        "data_accesses",
        "fk_data_accesses_workspace_project",
        "project_id",
        "projects",
    )
    op.create_foreign_key(
        "fk_data_accesses_workspace_data_source",
        "data_accesses",
        "data_sources",
        ["workspace_id", "data_source_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_data_accesses_workspace_status_created_at",
        "data_accesses",
        ["workspace_id", "status", "created_at"],
    )
    op.create_index(
        "ix_data_accesses_data_source_id",
        "data_accesses",
        ["data_source_id"],
    )

    op.add_column(
        "ingestion_runs",
        sa.Column(
            "data_access_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_accesses.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "ingestion_runs",
        sa.Column(
            "execution_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    _add_set_null_fk(
        "ingestion_runs",
        "fk_ingestion_runs_workspace_data_access",
        "data_access_id",
        "data_accesses",
    )
    _add_set_null_fk(
        "ingestion_runs",
        "fk_ingestion_runs_workspace_execution_request",
        "execution_request_id",
        "execution_requests",
    )
    _add_set_null_source_access_fk()
    op.create_index(
        "ix_ingestion_runs_data_access_id",
        "ingestion_runs",
        ["data_access_id"],
    )
    op.create_index(
        "ix_ingestion_runs_execution_request_id",
        "ingestion_runs",
        ["execution_request_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_runs_execution_request_id", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_data_access_id", table_name="ingestion_runs")
    op.drop_constraint(
        "fk_ingestion_runs_data_source_data_access",
        "ingestion_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_ingestion_runs_workspace_execution_request",
        "ingestion_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_ingestion_runs_workspace_data_access",
        "ingestion_runs",
        type_="foreignkey",
    )
    op.drop_column("ingestion_runs", "execution_request_id")
    op.drop_column("ingestion_runs", "data_access_id")
    op.drop_index("ix_data_accesses_data_source_id", table_name="data_accesses")
    op.drop_index(
        "ix_data_accesses_workspace_status_created_at", table_name="data_accesses"
    )
    op.drop_table("data_accesses")
