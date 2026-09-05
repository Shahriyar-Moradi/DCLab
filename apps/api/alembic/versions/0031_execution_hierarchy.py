"""workflow versions, pipeline definitions, pipeline versions, stage-run state

Revision ID: 0031_execution_hierarchy
Revises: 0030_object_storage_lineage
Create Date: 2026-09-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.domain.execution_plane import (
    CK_PIPELINE_STAGE_RUNS_STATUS,
    CK_PIPELINE_VERSION_POSITIVE,
    CK_PIPELINES_STATUS,
    CK_WORKFLOW_RUNS_INITIATED_BY,
    CK_WORKFLOW_VERSION_POSITIVE,
)

revision: str = "0031_execution_hierarchy"
down_revision: Union[str, Sequence[str], None] = "0030_object_storage_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_problem_specs_workspace_id", "problem_specs", ["workspace_id", "id"]
    )
    op.create_unique_constraint(
        "uq_ml_workflows_workspace_id", "ml_workflows", ["workspace_id", "id"]
    )
    op.create_unique_constraint(
        "uq_workflow_runs_workspace_id", "workflow_runs", ["workspace_id", "id"]
    )
    op.create_unique_constraint(
        "uq_experiments_workspace_id", "experiments", ["workspace_id", "id"]
    )

    op.add_column(
        "ml_workflows",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_ml_workflows_project_id", "ml_workflows", ["project_id"])
    op.create_foreign_key(
        "fk_ml_workflows_workspace_project",
        "ml_workflows",
        "projects",
        ["workspace_id", "project_id"],
        ["workspace_id", "id"],
    )

    op.create_table(
        "workflow_versions",
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
            "workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ml_workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
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
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workflow_id", "version", name="uq_workflow_versions_workflow_version"
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_workflow_versions_workspace_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_workflow_versions_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["ml_workflows.workspace_id", "ml_workflows.id"],
            name="fk_workflow_versions_workspace_workflow",
        ),
        sa.CheckConstraint(
            CK_WORKFLOW_VERSION_POSITIVE, name="ck_workflow_versions_version_positive"
        ),
    )
    op.create_index(
        "ix_workflow_versions_workspace_id", "workflow_versions", ["workspace_id"]
    )
    op.create_index(
        "ix_workflow_versions_project_id", "workflow_versions", ["project_id"]
    )
    op.create_index(
        "ix_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"]
    )

    op.create_table(
        "pipelines",
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
            "workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ml_workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="active",
            nullable=False,
        ),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("workflow_id", "slug", name="uq_pipelines_workflow_slug"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_pipelines_workspace_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_pipelines_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["ml_workflows.workspace_id", "ml_workflows.id"],
            name="fk_pipelines_workspace_workflow",
        ),
        sa.CheckConstraint(CK_PIPELINES_STATUS, name="ck_pipelines_status_valid"),
    )
    op.create_index("ix_pipelines_workspace_id", "pipelines", ["workspace_id"])
    op.create_index("ix_pipelines_project_id", "pipelines", ["project_id"])
    op.create_index("ix_pipelines_workflow_id", "pipelines", ["workflow_id"])

    op.create_table(
        "pipeline_versions",
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
            "pipeline_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("graph_definition", postgresql.JSONB(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "pipeline_id", "version", name="uq_pipeline_versions_pipeline_version"
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_pipeline_versions_workspace_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_pipeline_versions_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "pipeline_id"],
            ["pipelines.workspace_id", "pipelines.id"],
            name="fk_pipeline_versions_workspace_pipeline",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_version_id"],
            ["workflow_versions.workspace_id", "workflow_versions.id"],
            name="fk_pipeline_versions_workspace_workflow_version",
        ),
        sa.CheckConstraint(
            CK_PIPELINE_VERSION_POSITIVE, name="ck_pipeline_versions_version_positive"
        ),
    )
    op.create_index(
        "ix_pipeline_versions_workspace_id", "pipeline_versions", ["workspace_id"]
    )
    op.create_index(
        "ix_pipeline_versions_project_id", "pipeline_versions", ["project_id"]
    )
    op.create_index(
        "ix_pipeline_versions_pipeline_id", "pipeline_versions", ["pipeline_id"]
    )
    op.create_index(
        "ix_pipeline_versions_workflow_version_id",
        "pipeline_versions",
        ["workflow_version_id"],
    )

    op.add_column(
        "workflow_runs",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "workflow_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "problem_spec_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("problem_specs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("initiated_by_type", sa.String(length=32), nullable=True),
    )
    op.create_index("ix_workflow_runs_project_id", "workflow_runs", ["project_id"])
    op.create_index(
        "ix_workflow_runs_workflow_version_id", "workflow_runs", ["workflow_version_id"]
    )
    op.create_index(
        "ix_workflow_runs_problem_spec_id", "workflow_runs", ["problem_spec_id"]
    )
    op.create_check_constraint(
        "ck_workflow_runs_initiated_by_type",
        "workflow_runs",
        CK_WORKFLOW_RUNS_INITIATED_BY,
    )
    op.create_foreign_key(
        "fk_workflow_runs_workspace_project",
        "workflow_runs",
        "projects",
        ["workspace_id", "project_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_workflow_runs_workspace_workflow",
        "workflow_runs",
        "ml_workflows",
        ["workspace_id", "workflow_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_workflow_runs_workspace_workflow_version",
        "workflow_runs",
        "workflow_versions",
        ["workspace_id", "workflow_version_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_workflow_runs_workspace_problem_spec",
        "workflow_runs",
        "problem_specs",
        ["workspace_id", "problem_spec_id"],
        ["workspace_id", "id"],
    )

    op.add_column(
        "experiments",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "experiments",
        sa.Column(
            "pipeline_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipelines.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "experiments",
        sa.Column(
            "pipeline_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipeline_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("experiments", sa.Column("run_number", sa.Integer(), nullable=True))
    op.create_index("ix_experiments_project_id", "experiments", ["project_id"])
    op.create_index("ix_experiments_pipeline_id", "experiments", ["pipeline_id"])
    op.create_index(
        "ix_experiments_pipeline_version_id", "experiments", ["pipeline_version_id"]
    )
    op.create_foreign_key(
        "fk_experiments_workspace_project",
        "experiments",
        "projects",
        ["workspace_id", "project_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_experiments_workspace_pipeline",
        "experiments",
        "pipelines",
        ["workspace_id", "pipeline_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_experiments_workspace_pipeline_version",
        "experiments",
        "pipeline_versions",
        ["workspace_id", "pipeline_version_id"],
        ["workspace_id", "id"],
    )

    op.create_table(
        "pipeline_stage_runs",
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
        sa.Column("stage_key", sa.String(length=80), nullable=False),
        sa.Column("stage_type", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("input_summary", postgresql.JSONB(), nullable=False),
        sa.Column("output_summary", postgresql.JSONB(), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "pipeline_run_id", "sequence", name="uq_pipeline_stage_runs_run_sequence"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_pipeline_stage_runs_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_pipeline_stage_runs_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            CK_PIPELINE_STAGE_RUNS_STATUS, name="ck_pipeline_stage_runs_status_valid"
        ),
    )
    op.create_index(
        "ix_pipeline_stage_runs_workspace_id", "pipeline_stage_runs", ["workspace_id"]
    )
    op.create_index(
        "ix_pipeline_stage_runs_project_id", "pipeline_stage_runs", ["project_id"]
    )
    op.create_index(
        "ix_pipeline_stage_runs_pipeline_run_id",
        "pipeline_stage_runs",
        ["pipeline_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pipeline_stage_runs_pipeline_run_id", table_name="pipeline_stage_runs"
    )
    op.drop_index("ix_pipeline_stage_runs_project_id", table_name="pipeline_stage_runs")
    op.drop_index(
        "ix_pipeline_stage_runs_workspace_id", table_name="pipeline_stage_runs"
    )
    op.drop_table("pipeline_stage_runs")
    op.drop_constraint(
        "fk_experiments_workspace_pipeline_version", "experiments", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_experiments_workspace_pipeline", "experiments", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_experiments_workspace_project", "experiments", type_="foreignkey"
    )
    op.drop_index("ix_experiments_pipeline_version_id", table_name="experiments")
    op.drop_index("ix_experiments_pipeline_id", table_name="experiments")
    op.drop_index("ix_experiments_project_id", table_name="experiments")
    op.drop_column("experiments", "run_number")
    op.drop_column("experiments", "pipeline_version_id")
    op.drop_column("experiments", "pipeline_id")
    op.drop_column("experiments", "project_id")
    op.drop_constraint(
        "fk_workflow_runs_workspace_problem_spec", "workflow_runs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_workflow_runs_workspace_workflow_version",
        "workflow_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_workflow_runs_workspace_workflow", "workflow_runs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_workflow_runs_workspace_project", "workflow_runs", type_="foreignkey"
    )
    op.drop_constraint(
        "ck_workflow_runs_initiated_by_type", "workflow_runs", type_="check"
    )
    op.drop_index("ix_workflow_runs_problem_spec_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_workflow_version_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_project_id", table_name="workflow_runs")
    op.drop_column("workflow_runs", "initiated_by_type")
    op.drop_column("workflow_runs", "problem_spec_id")
    op.drop_column("workflow_runs", "workflow_version_id")
    op.drop_column("workflow_runs", "project_id")
    op.drop_index(
        "ix_pipeline_versions_workflow_version_id", table_name="pipeline_versions"
    )
    op.drop_index("ix_pipeline_versions_pipeline_id", table_name="pipeline_versions")
    op.drop_index("ix_pipeline_versions_project_id", table_name="pipeline_versions")
    op.drop_index("ix_pipeline_versions_workspace_id", table_name="pipeline_versions")
    op.drop_table("pipeline_versions")
    op.drop_index("ix_pipelines_workflow_id", table_name="pipelines")
    op.drop_index("ix_pipelines_project_id", table_name="pipelines")
    op.drop_index("ix_pipelines_workspace_id", table_name="pipelines")
    op.drop_table("pipelines")
    op.drop_index("ix_workflow_versions_workflow_id", table_name="workflow_versions")
    op.drop_index("ix_workflow_versions_project_id", table_name="workflow_versions")
    op.drop_index("ix_workflow_versions_workspace_id", table_name="workflow_versions")
    op.drop_table("workflow_versions")
    op.drop_constraint(
        "fk_ml_workflows_workspace_project", "ml_workflows", type_="foreignkey"
    )
    op.drop_index("ix_ml_workflows_project_id", table_name="ml_workflows")
    op.drop_column("ml_workflows", "project_id")
    op.drop_constraint("uq_experiments_workspace_id", "experiments", type_="unique")
    op.drop_constraint("uq_workflow_runs_workspace_id", "workflow_runs", type_="unique")
    op.drop_constraint("uq_ml_workflows_workspace_id", "ml_workflows", type_="unique")
    op.drop_constraint("uq_problem_specs_workspace_id", "problem_specs", type_="unique")
