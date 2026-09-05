"""runtime environments, code snapshots, model version artifact lineage

Revision ID: 0034_reproducible_code
Revises: 0033_candidate_modeling
Create Date: 2026-09-05

Source packages and lockfiles are stored as Artifact rows. This revision
only adds queryable metadata and FKs; ``model_versions.artifact_uri`` is kept.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.domain.reproducibility import CK_CODE_LANGUAGE

revision: str = "0034_reproducible_code"
down_revision: Union[str, Sequence[str], None] = "0033_candidate_modeling"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_artifacts_workspace_id",
        "artifacts",
        ["workspace_id", "id"],
    )

    op.create_table(
        "runtime_environments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("python_version", sa.String(length=32), nullable=False),
        sa.Column("os_name", sa.String(length=64), nullable=False),
        sa.Column("os_version", sa.String(length=128), nullable=False),
        sa.Column("architecture", sa.String(length=64), nullable=False),
        sa.Column("container_image", sa.String(length=512), nullable=True),
        sa.Column("container_digest", sa.String(length=128), nullable=True),
        sa.Column(
            "dependency_lock_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("hardware", postgresql.JSONB(), nullable=False),
        sa.Column("environment_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("environment_digest", name="uq_runtime_environments_digest"),
    )
    op.create_index(
        "ix_runtime_environments_python_version",
        "runtime_environments",
        ["python_version"],
    )

    op.create_table(
        "code_snapshots",
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
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=False,
        ),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("entrypoint", sa.String(length=256), nullable=False),
        sa.Column("git_commit", sa.String(length=64), nullable=True),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("dependency_lock_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "runtime_environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runtime_environments.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("pipeline_run_id", name="uq_code_snapshots_pipeline_run"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_code_snapshots_workspace_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_code_snapshots_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_code_snapshots_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "pipeline_stage_run_id"],
            ["pipeline_stage_runs.workspace_id", "pipeline_stage_runs.id"],
            name="fk_code_snapshots_workspace_pipeline_stage_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["experiment_candidates.workspace_id", "experiment_candidates.id"],
            name="fk_code_snapshots_workspace_candidate",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "artifact_id"],
            ["artifacts.workspace_id", "artifacts.id"],
            name="fk_code_snapshots_workspace_artifact",
        ),
        sa.CheckConstraint(CK_CODE_LANGUAGE, name="ck_code_snapshots_language_valid"),
    )
    op.create_index("ix_code_snapshots_workspace_id", "code_snapshots", ["workspace_id"])
    op.create_index("ix_code_snapshots_project_id", "code_snapshots", ["project_id"])
    op.create_index("ix_code_snapshots_artifact_id", "code_snapshots", ["artifact_id"])
    op.create_index(
        "ix_code_snapshots_runtime_environment_id",
        "code_snapshots",
        ["runtime_environment_id"],
    )

    op.add_column(
        "model_versions",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "model_versions",
        sa.Column(
            "workflow_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "model_versions",
        sa.Column(
            "pipeline_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipelines.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "model_versions",
        sa.Column(
            "pipeline_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipeline_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "model_versions",
        sa.Column(
            "feature_set_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("feature_set_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "model_versions",
        sa.Column(
            "runtime_environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runtime_environments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "model_versions",
        sa.Column(
            "code_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("code_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "model_versions",
        sa.Column(
            "model_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "model_versions",
        sa.Column(
            "preprocessor_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "model_versions",
        sa.Column(
            "feature_manifest_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE model_versions AS mv SET "
            "project_id = e.project_id, "
            "workflow_version_id = wr.workflow_version_id, "
            "pipeline_id = e.pipeline_id, "
            "pipeline_version_id = e.pipeline_version_id "
            "FROM experiments AS e "
            "JOIN workflow_runs AS wr ON wr.id = e.workflow_run_id "
            "WHERE e.id = mv.pipeline_run_id"
        )
    )
    op.create_unique_constraint(
        "uq_model_versions_workspace_id",
        "model_versions",
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_model_versions_workspace_project",
        "model_versions",
        "projects",
        ["workspace_id", "project_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_model_versions_workspace_workflow_version",
        "model_versions",
        "workflow_versions",
        ["workspace_id", "workflow_version_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_model_versions_workspace_pipeline",
        "model_versions",
        "pipelines",
        ["workspace_id", "pipeline_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_model_versions_workspace_pipeline_version",
        "model_versions",
        "pipeline_versions",
        ["workspace_id", "pipeline_version_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_model_versions_workspace_feature_set_version",
        "model_versions",
        "feature_set_versions",
        ["workspace_id", "feature_set_version_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_model_versions_workspace_code_snapshot",
        "model_versions",
        "code_snapshots",
        ["workspace_id", "code_snapshot_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_model_versions_workspace_model_artifact",
        "model_versions",
        "artifacts",
        ["workspace_id", "model_artifact_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_model_versions_workspace_preprocessor_artifact",
        "model_versions",
        "artifacts",
        ["workspace_id", "preprocessor_artifact_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_model_versions_workspace_feature_manifest_artifact",
        "model_versions",
        "artifacts",
        ["workspace_id", "feature_manifest_artifact_id"],
        ["workspace_id", "id"],
    )
    op.create_index("ix_model_versions_project_id", "model_versions", ["project_id"])
    op.create_index(
        "ix_model_versions_model_artifact_id", "model_versions", ["model_artifact_id"]
    )
    op.create_index(
        "ix_model_versions_runtime_environment_id",
        "model_versions",
        ["runtime_environment_id"],
    )
    op.create_index(
        "ix_model_versions_code_snapshot_id", "model_versions", ["code_snapshot_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_model_versions_code_snapshot_id", table_name="model_versions")
    op.drop_index(
        "ix_model_versions_runtime_environment_id", table_name="model_versions"
    )
    op.drop_index("ix_model_versions_model_artifact_id", table_name="model_versions")
    op.drop_index("ix_model_versions_project_id", table_name="model_versions")
    op.drop_constraint(
        "fk_model_versions_workspace_feature_manifest_artifact",
        "model_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_model_versions_workspace_preprocessor_artifact",
        "model_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_model_versions_workspace_model_artifact",
        "model_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_model_versions_workspace_code_snapshot",
        "model_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_model_versions_workspace_feature_set_version",
        "model_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_model_versions_workspace_pipeline_version",
        "model_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_model_versions_workspace_pipeline",
        "model_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_model_versions_workspace_workflow_version",
        "model_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_model_versions_workspace_project",
        "model_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_model_versions_workspace_id",
        "model_versions",
        type_="unique",
    )
    op.drop_column("model_versions", "feature_manifest_artifact_id")
    op.drop_column("model_versions", "preprocessor_artifact_id")
    op.drop_column("model_versions", "model_artifact_id")
    op.drop_column("model_versions", "code_snapshot_id")
    op.drop_column("model_versions", "runtime_environment_id")
    op.drop_column("model_versions", "feature_set_version_id")
    op.drop_column("model_versions", "pipeline_version_id")
    op.drop_column("model_versions", "pipeline_id")
    op.drop_column("model_versions", "workflow_version_id")
    op.drop_column("model_versions", "project_id")
    op.drop_index(
        "ix_code_snapshots_runtime_environment_id", table_name="code_snapshots"
    )
    op.drop_index("ix_code_snapshots_artifact_id", table_name="code_snapshots")
    op.drop_index("ix_code_snapshots_project_id", table_name="code_snapshots")
    op.drop_index("ix_code_snapshots_workspace_id", table_name="code_snapshots")
    op.drop_table("code_snapshots")
    op.drop_index(
        "ix_runtime_environments_python_version", table_name="runtime_environments"
    )
    op.drop_table("runtime_environments")
    op.drop_constraint("uq_artifacts_workspace_id", "artifacts", type_="unique")
