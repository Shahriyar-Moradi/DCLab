"""data quality, preparation decisions, features, preprocessing

Revision ID: 0032_scientific_lineage
Revises: 0031_execution_hierarchy
Create Date: 2026-09-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.domain.scientific_plane import (
    CK_DATA_QUALITY_FINDING_TYPE,
    CK_DATA_QUALITY_SEVERITY,
    CK_FEATURE_LINEAGE_RELATIONSHIP,
    CK_FEATURE_SET_VERSION_POSITIVE,
    CK_FEATURE_STATUS,
    CK_PREPARATION_DECISION_SOURCE,
    CK_PREPROCESSING_FIT_SCOPE,
)

revision: str = "0032_scientific_lineage"
down_revision: Union[str, Sequence[str], None] = "0031_execution_hierarchy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_pipeline_stage_runs_workspace_id",
        "pipeline_stage_runs",
        ["workspace_id", "id"],
    )

    op.create_table(
        "data_quality_findings",
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
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pipeline_stage_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipeline_stage_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasets.id"),
            nullable=False,
        ),
        sa.Column(
            "dataset_column_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_columns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("finding_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_data_quality_findings_workspace_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_data_quality_findings_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_data_quality_findings_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.id"],
            name="fk_data_quality_findings_workspace_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "pipeline_stage_run_id"],
            ["pipeline_stage_runs.workspace_id", "pipeline_stage_runs.id"],
            name="fk_data_quality_findings_workspace_pipeline_stage_run",
        ),
        sa.CheckConstraint(
            CK_DATA_QUALITY_FINDING_TYPE, name="ck_data_quality_findings_type_valid"
        ),
        sa.CheckConstraint(
            CK_DATA_QUALITY_SEVERITY, name="ck_data_quality_findings_severity_valid"
        ),
    )
    op.create_index(
        "ix_data_quality_findings_workspace_id", "data_quality_findings", ["workspace_id"]
    )
    op.create_index(
        "ix_data_quality_findings_project_id", "data_quality_findings", ["project_id"]
    )
    op.create_index(
        "ix_data_quality_findings_pipeline_run_id",
        "data_quality_findings",
        ["pipeline_run_id"],
    )
    op.create_index(
        "ix_data_quality_findings_dataset_id", "data_quality_findings", ["dataset_id"]
    )
    op.create_index(
        "ix_data_quality_findings_dataset_column_id",
        "data_quality_findings",
        ["dataset_column_id"],
    )

    op.create_table(
        "data_preparation_decisions",
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
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pipeline_stage_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipeline_stage_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasets.id"),
            nullable=False,
        ),
        sa.Column(
            "dataset_column_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_columns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision_type", sa.String(length=64), nullable=False),
        sa.Column("strategy", sa.String(length=64), nullable=False),
        sa.Column("parameter_value", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.String(length=2048), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("decision_source", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_data_preparation_decisions_workspace_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_data_preparation_decisions_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_data_preparation_decisions_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.id"],
            name="fk_data_preparation_decisions_workspace_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "pipeline_stage_run_id"],
            ["pipeline_stage_runs.workspace_id", "pipeline_stage_runs.id"],
            name="fk_data_preparation_decisions_workspace_pipeline_stage_run",
        ),
        sa.CheckConstraint(
            CK_PREPARATION_DECISION_SOURCE,
            name="ck_data_preparation_decisions_source_valid",
        ),
    )
    op.create_index(
        "ix_data_preparation_decisions_workspace_id",
        "data_preparation_decisions",
        ["workspace_id"],
    )
    op.create_index(
        "ix_data_preparation_decisions_project_id",
        "data_preparation_decisions",
        ["project_id"],
    )
    op.create_index(
        "ix_data_preparation_decisions_pipeline_run_id",
        "data_preparation_decisions",
        ["pipeline_run_id"],
    )
    op.create_index(
        "ix_data_preparation_decisions_dataset_id",
        "data_preparation_decisions",
        ["dataset_id"],
    )
    op.create_index(
        "ix_data_preparation_decisions_dataset_column_id",
        "data_preparation_decisions",
        ["dataset_column_id"],
    )

    op.create_table(
        "feature_sets",
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
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.String(length=2048), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("workspace_id", "name", name="uq_feature_sets_workspace_name"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_feature_sets_workspace_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_feature_sets_workspace_project",
        ),
    )
    op.create_index("ix_feature_sets_workspace_id", "feature_sets", ["workspace_id"])
    op.create_index("ix_feature_sets_project_id", "feature_sets", ["project_id"])

    op.create_table(
        "feature_set_versions",
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
            "feature_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("feature_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "feature_set_id", "version", name="uq_feature_set_versions_set_version"
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_feature_set_versions_workspace_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_feature_set_versions_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "feature_set_id"],
            ["feature_sets.workspace_id", "feature_sets.id"],
            name="fk_feature_set_versions_workspace_feature_set",
        ),
        sa.CheckConstraint(
            CK_FEATURE_SET_VERSION_POSITIVE, name="ck_feature_set_versions_version_positive"
        ),
    )
    op.create_index(
        "ix_feature_set_versions_workspace_id", "feature_set_versions", ["workspace_id"]
    )
    op.create_index(
        "ix_feature_set_versions_project_id", "feature_set_versions", ["project_id"]
    )
    op.create_index(
        "ix_feature_set_versions_feature_set_id",
        "feature_set_versions",
        ["feature_set_id"],
    )

    op.create_table(
        "features",
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
            "feature_set_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("feature_set_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("feature_type", sa.String(length=64), nullable=False),
        sa.Column("output_dtype", sa.String(length=64), nullable=False),
        sa.Column("definition", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "feature_set_version_id", "name", name="uq_features_version_name"
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_features_workspace_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_features_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "feature_set_version_id"],
            ["feature_set_versions.workspace_id", "feature_set_versions.id"],
            name="fk_features_workspace_feature_set_version",
        ),
        sa.CheckConstraint(CK_FEATURE_STATUS, name="ck_features_status_valid"),
    )
    op.create_index("ix_features_workspace_id", "features", ["workspace_id"])
    op.create_index("ix_features_project_id", "features", ["project_id"])
    op.create_index(
        "ix_features_feature_set_version_id", "features", ["feature_set_version_id"]
    )

    op.create_table(
        "feature_transformations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "feature_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("features.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("transformation_type", sa.String(length=64), nullable=False),
        sa.Column("transformer_class", sa.String(length=256), nullable=True),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("fit_required", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "feature_id",
            "sequence",
            name="uq_feature_transformations_feature_sequence",
        ),
    )
    op.create_index(
        "ix_feature_transformations_feature_id", "feature_transformations", ["feature_id"]
    )

    op.create_table(
        "feature_lineage",
        sa.Column(
            "feature_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("features.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "source_dataset_column_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_columns.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "relationship",
            sa.String(length=32),
            server_default="source",
            nullable=False,
        ),
        sa.CheckConstraint(
            CK_FEATURE_LINEAGE_RELATIONSHIP, name="ck_feature_lineage_relationship_valid"
        ),
    )
    op.create_index("ix_feature_lineage_feature_id", "feature_lineage", ["feature_id"])
    op.create_index(
        "ix_feature_lineage_source_dataset_column_id",
        "feature_lineage",
        ["source_dataset_column_id"],
    )

    op.create_table(
        "preprocessing_steps",
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
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pipeline_stage_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipeline_stage_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("column_scope", sa.String(length=2048), nullable=False),
        sa.Column("transformer_type", sa.String(length=64), nullable=False),
        sa.Column("transformer_class", sa.String(length=256), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("fit_scope", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "pipeline_run_id", "sequence", name="uq_preprocessing_steps_run_sequence"
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_preprocessing_steps_workspace_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_preprocessing_steps_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_preprocessing_steps_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "pipeline_stage_run_id"],
            ["pipeline_stage_runs.workspace_id", "pipeline_stage_runs.id"],
            name="fk_preprocessing_steps_workspace_pipeline_stage_run",
        ),
        sa.CheckConstraint(
            CK_PREPROCESSING_FIT_SCOPE, name="ck_preprocessing_steps_fit_scope_valid"
        ),
    )
    op.create_index(
        "ix_preprocessing_steps_workspace_id", "preprocessing_steps", ["workspace_id"]
    )
    op.create_index(
        "ix_preprocessing_steps_project_id", "preprocessing_steps", ["project_id"]
    )
    op.create_index(
        "ix_preprocessing_steps_pipeline_run_id",
        "preprocessing_steps",
        ["pipeline_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_preprocessing_steps_pipeline_run_id", table_name="preprocessing_steps")
    op.drop_index("ix_preprocessing_steps_project_id", table_name="preprocessing_steps")
    op.drop_index("ix_preprocessing_steps_workspace_id", table_name="preprocessing_steps")
    op.drop_table("preprocessing_steps")
    op.drop_index(
        "ix_feature_lineage_source_dataset_column_id", table_name="feature_lineage"
    )
    op.drop_index("ix_feature_lineage_feature_id", table_name="feature_lineage")
    op.drop_table("feature_lineage")
    op.drop_index(
        "ix_feature_transformations_feature_id", table_name="feature_transformations"
    )
    op.drop_table("feature_transformations")
    op.drop_index("ix_features_feature_set_version_id", table_name="features")
    op.drop_index("ix_features_project_id", table_name="features")
    op.drop_index("ix_features_workspace_id", table_name="features")
    op.drop_table("features")
    op.drop_index(
        "ix_feature_set_versions_feature_set_id", table_name="feature_set_versions"
    )
    op.drop_index("ix_feature_set_versions_project_id", table_name="feature_set_versions")
    op.drop_index(
        "ix_feature_set_versions_workspace_id", table_name="feature_set_versions"
    )
    op.drop_table("feature_set_versions")
    op.drop_index("ix_feature_sets_project_id", table_name="feature_sets")
    op.drop_index("ix_feature_sets_workspace_id", table_name="feature_sets")
    op.drop_table("feature_sets")
    op.drop_index(
        "ix_data_preparation_decisions_dataset_column_id",
        table_name="data_preparation_decisions",
    )
    op.drop_index(
        "ix_data_preparation_decisions_dataset_id",
        table_name="data_preparation_decisions",
    )
    op.drop_index(
        "ix_data_preparation_decisions_pipeline_run_id",
        table_name="data_preparation_decisions",
    )
    op.drop_index(
        "ix_data_preparation_decisions_project_id",
        table_name="data_preparation_decisions",
    )
    op.drop_index(
        "ix_data_preparation_decisions_workspace_id",
        table_name="data_preparation_decisions",
    )
    op.drop_table("data_preparation_decisions")
    op.drop_index(
        "ix_data_quality_findings_dataset_column_id", table_name="data_quality_findings"
    )
    op.drop_index("ix_data_quality_findings_dataset_id", table_name="data_quality_findings")
    op.drop_index(
        "ix_data_quality_findings_pipeline_run_id", table_name="data_quality_findings"
    )
    op.drop_index("ix_data_quality_findings_project_id", table_name="data_quality_findings")
    op.drop_index(
        "ix_data_quality_findings_workspace_id", table_name="data_quality_findings"
    )
    op.drop_table("data_quality_findings")
    op.drop_constraint(
        "uq_pipeline_stage_runs_workspace_id", "pipeline_stage_runs", type_="unique"
    )
