"""canonical visualization metadata

Revision ID: 0052_visualizations
Revises: 0051_ml_job_queue
Create Date: 2026-09-09

Declarative chart spec in JSONB; large series and optional PNG/SVG live in
Artifact/ObjectStorage. Identity/spec is immutable. This revision does not
generate charts or add a visualization UI.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from alembic_frozen.rev_0052_visualizations import (
    CK_VISUALIZATIONS_DIGEST,
    CK_VISUALIZATIONS_RENDERER,
    CK_VISUALIZATIONS_SPEC_BOUNDED,
    CK_VISUALIZATIONS_SPEC_NO_BULK,
    CK_VISUALIZATIONS_SPEC_OBJECT,
    CK_VISUALIZATIONS_SPEC_VERSION,
    CK_VISUALIZATIONS_TYPE,
    PREVENT_VISUALIZATION_MUTATION_SQL,
    VISUALIZATIONS_IMMUTABLE_TRIGGER_SQL,
)

revision: str = "0052_visualizations"
down_revision: Union[str, Sequence[str], None] = "0051_ml_job_queue"
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
        "visualizations",
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
            sa.ForeignKey("experiments.id"),
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
            "model_evaluation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_evaluations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("visualization_type", sa.String(length=64), nullable=False),
        sa.Column("spec_version", sa.String(length=32), nullable=False),
        sa.Column("renderer_hint", sa.String(length=32), nullable=True),
        sa.Column(
            "spec",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "data_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=True,
        ),
        sa.Column(
            "image_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=True,
        ),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_visualizations_workspace_id"),
        sa.CheckConstraint(CK_VISUALIZATIONS_TYPE, name="ck_visualizations_type"),
        sa.CheckConstraint(
            CK_VISUALIZATIONS_SPEC_VERSION, name="ck_visualizations_spec_version"
        ),
        sa.CheckConstraint(CK_VISUALIZATIONS_RENDERER, name="ck_visualizations_renderer"),
        sa.CheckConstraint(CK_VISUALIZATIONS_DIGEST, name="ck_visualizations_digest"),
        sa.CheckConstraint(
            CK_VISUALIZATIONS_SPEC_OBJECT, name="ck_visualizations_spec_object"
        ),
        sa.CheckConstraint(
            CK_VISUALIZATIONS_SPEC_BOUNDED, name="ck_visualizations_spec_bounded"
        ),
        sa.CheckConstraint(
            CK_VISUALIZATIONS_SPEC_NO_BULK, name="ck_visualizations_spec_no_bulk"
        ),
    )
    op.create_foreign_key(
        "fk_visualizations_workspace_pipeline_run",
        "visualizations",
        "experiments",
        ["workspace_id", "pipeline_run_id"],
        ["workspace_id", "id"],
    )
    _add_set_null_fk(
        "visualizations",
        "fk_visualizations_workspace_project",
        "project_id",
        "projects",
    )
    _add_set_null_fk(
        "visualizations",
        "fk_visualizations_workspace_stage",
        "pipeline_stage_run_id",
        "pipeline_stage_runs",
    )
    _add_set_null_fk(
        "visualizations",
        "fk_visualizations_workspace_candidate",
        "candidate_id",
        "experiment_candidates",
    )
    _add_set_null_fk(
        "visualizations",
        "fk_visualizations_workspace_evaluation",
        "model_evaluation_id",
        "model_evaluations",
    )
    op.create_foreign_key(
        "fk_visualizations_workspace_data_artifact",
        "visualizations",
        "artifacts",
        ["workspace_id", "data_artifact_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_visualizations_workspace_image_artifact",
        "visualizations",
        "artifacts",
        ["workspace_id", "image_artifact_id"],
        ["workspace_id", "id"],
    )
    op.create_index(
        "ix_visualizations_workspace_created_at",
        "visualizations",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_visualizations_pipeline_run_id",
        "visualizations",
        ["pipeline_run_id"],
    )
    op.create_index(
        "ix_visualizations_workspace_type",
        "visualizations",
        ["workspace_id", "visualization_type"],
    )
    op.create_index(
        "ix_visualizations_candidate_id",
        "visualizations",
        ["candidate_id"],
    )
    op.execute(sa.text(PREVENT_VISUALIZATION_MUTATION_SQL))
    op.execute(sa.text(VISUALIZATIONS_IMMUTABLE_TRIGGER_SQL))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS visualizations_immutable ON visualizations")
    op.execute("DROP FUNCTION IF EXISTS prevent_visualization_mutation()")
    op.drop_index("ix_visualizations_candidate_id", table_name="visualizations")
    op.drop_index("ix_visualizations_workspace_type", table_name="visualizations")
    op.drop_index("ix_visualizations_pipeline_run_id", table_name="visualizations")
    op.drop_index("ix_visualizations_workspace_created_at", table_name="visualizations")
    op.drop_table("visualizations")
