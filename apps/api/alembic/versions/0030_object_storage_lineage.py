"""artifact registry, data sources, ingestion runs, dataset columns

Revision ID: 0030_object_storage_lineage
Revises: 0029_workspace_identity
Create Date: 2026-09-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.domain.data_plane import (
    CK_ARTIFACTS_PROVIDER,
    CK_ARTIFACTS_TYPE,
    CK_DATA_SOURCES_STATUS,
    CK_DATA_SOURCES_TYPE,
    CK_INGESTION_RUNS_STATUS,
)

revision: str = "0030_object_storage_lineage"
down_revision: Union[str, Sequence[str], None] = "0029_workspace_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_dataset_assets_workspace_id",
        "dataset_assets",
        ["workspace_id", "id"],
    )
    op.create_unique_constraint(
        "uq_datasets_workspace_id",
        "datasets",
        ["workspace_id", "id"],
    )

    op.create_table(
        "artifacts",
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
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("bucket", sa.String(length=256), nullable=True),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=256), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id", "object_key", name="uq_artifacts_workspace_object_key"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_artifacts_workspace_project",
        ),
        sa.CheckConstraint(CK_ARTIFACTS_TYPE, name="ck_artifacts_type_valid"),
        sa.CheckConstraint(CK_ARTIFACTS_PROVIDER, name="ck_artifacts_provider_valid"),
    )
    op.create_index(
        "ix_artifacts_workspace_created_at",
        "artifacts",
        ["workspace_id", "created_at"],
    )
    op.create_index("ix_artifacts_workspace_id", "artifacts", ["workspace_id"])
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])

    op.create_table(
        "data_sources",
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
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("credential_reference", sa.String(length=512), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="active",
            nullable=False,
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
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_data_sources_workspace_project",
        ),
        sa.CheckConstraint(CK_DATA_SOURCES_TYPE, name="ck_data_sources_type_valid"),
        sa.CheckConstraint(CK_DATA_SOURCES_STATUS, name="ck_data_sources_status_valid"),
    )
    op.create_index("ix_data_sources_workspace_id", "data_sources", ["workspace_id"])
    op.create_index("ix_data_sources_project_id", "data_sources", ["project_id"])

    op.create_table(
        "ingestion_runs",
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
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "data_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows_read", sa.Integer(), nullable=False),
        sa.Column("rows_written", sa.Integer(), nullable=False),
        sa.Column("bytes_read", sa.BigInteger(), nullable=False),
        sa.Column("schema_digest", sa.String(length=64), nullable=True),
        sa.Column("content_digest", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_ingestion_runs_workspace_project",
        ),
        sa.CheckConstraint(CK_INGESTION_RUNS_STATUS, name="ck_ingestion_runs_status_valid"),
    )
    op.create_index("ix_ingestion_runs_workspace_id", "ingestion_runs", ["workspace_id"])
    op.create_index("ix_ingestion_runs_project_id", "ingestion_runs", ["project_id"])
    op.create_index(
        "ix_ingestion_runs_data_source_id", "ingestion_runs", ["data_source_id"]
    )

    op.add_column(
        "dataset_assets",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_dataset_assets_project_id", "dataset_assets", ["project_id"])
    op.create_foreign_key(
        "fk_dataset_assets_workspace_project",
        "dataset_assets",
        "projects",
        ["workspace_id", "project_id"],
        ["workspace_id", "id"],
    )

    op.add_column(
        "datasets",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "datasets",
        sa.Column(
            "ingestion_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "datasets",
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("datasets", sa.Column("schema_digest", sa.String(length=64), nullable=True))
    op.add_column("datasets", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.create_index("ix_datasets_project_id", "datasets", ["project_id"])
    op.create_index("ix_datasets_ingestion_run_id", "datasets", ["ingestion_run_id"])
    op.create_index("ix_datasets_artifact_id", "datasets", ["artifact_id"])
    op.create_foreign_key(
        "fk_datasets_workspace_project",
        "datasets",
        "projects",
        ["workspace_id", "project_id"],
        ["workspace_id", "id"],
    )

    op.create_table(
        "dataset_columns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal_position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("physical_dtype", sa.String(length=64), nullable=False),
        sa.Column("semantic_type", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("nullable", sa.Boolean(), nullable=False),
        sa.Column("missing_count", sa.Integer(), nullable=False),
        sa.Column("missing_fraction", sa.Float(), nullable=False),
        sa.Column("unique_count", sa.Integer(), nullable=False),
        sa.Column("cardinality", sa.Integer(), nullable=True),
        sa.Column("min_value", postgresql.JSONB(), nullable=True),
        sa.Column("max_value", postgresql.JSONB(), nullable=True),
        sa.Column("mean_value", sa.Float(), nullable=True),
        sa.Column("median_value", sa.Float(), nullable=True),
        sa.Column("stats", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("dataset_id", "name", name="uq_dataset_columns_dataset_name"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.id"],
            name="fk_dataset_columns_workspace_dataset",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_dataset_columns_workspace_id", "dataset_columns", ["workspace_id"]
    )
    op.create_index("ix_dataset_columns_dataset_id", "dataset_columns", ["dataset_id"])

    op.add_column(
        "client_lab_uploads",
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "client_lab_uploads",
        sa.Column(
            "data_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "client_lab_uploads",
        sa.Column(
            "ingestion_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("client_lab_uploads", "ingestion_run_id")
    op.drop_column("client_lab_uploads", "data_source_id")
    op.drop_column("client_lab_uploads", "artifact_id")
    op.drop_index("ix_dataset_columns_dataset_id", table_name="dataset_columns")
    op.drop_index("ix_dataset_columns_workspace_id", table_name="dataset_columns")
    op.drop_table("dataset_columns")
    op.drop_constraint("fk_datasets_workspace_project", "datasets", type_="foreignkey")
    op.drop_index("ix_datasets_artifact_id", table_name="datasets")
    op.drop_index("ix_datasets_ingestion_run_id", table_name="datasets")
    op.drop_index("ix_datasets_project_id", table_name="datasets")
    op.drop_column("datasets", "size_bytes")
    op.drop_column("datasets", "schema_digest")
    op.drop_column("datasets", "artifact_id")
    op.drop_column("datasets", "ingestion_run_id")
    op.drop_column("datasets", "project_id")
    op.drop_constraint(
        "fk_dataset_assets_workspace_project", "dataset_assets", type_="foreignkey"
    )
    op.drop_index("ix_dataset_assets_project_id", table_name="dataset_assets")
    op.drop_column("dataset_assets", "project_id")
    op.drop_index("ix_ingestion_runs_data_source_id", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_project_id", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_workspace_id", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
    op.drop_index("ix_data_sources_project_id", table_name="data_sources")
    op.drop_index("ix_data_sources_workspace_id", table_name="data_sources")
    op.drop_table("data_sources")
    op.drop_index("ix_artifacts_project_id", table_name="artifacts")
    op.drop_index("ix_artifacts_workspace_id", table_name="artifacts")
    op.drop_index("ix_artifacts_workspace_created_at", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_constraint("uq_datasets_workspace_id", "datasets", type_="unique")
    op.drop_constraint(
        "uq_dataset_assets_workspace_id", "dataset_assets", type_="unique"
    )
