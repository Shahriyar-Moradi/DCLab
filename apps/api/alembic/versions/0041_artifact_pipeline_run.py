"""nullable pipeline_run_id on artifacts for indexed run lookup

Revision ID: 0041_artifact_pipeline_run
Revises: 0040_ml_jobs
Create Date: 2026-09-05

Artifact.pipeline_run_id is the queryable association. extra_metadata JSON
keeps the same key as compatibility metadata and is copied onto the column
when the PipelineRun is in the same workspace.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_artifact_pipeline_run"
down_revision: Union[str, Sequence[str], None] = "0040_ml_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "artifacts",
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_artifacts_pipeline_run_id", "artifacts", ["pipeline_run_id"]
    )
    op.create_index(
        "ix_artifacts_workspace_pipeline_run_id",
        "artifacts",
        ["workspace_id", "pipeline_run_id"],
    )
    op.create_foreign_key(
        "fk_artifacts_pipeline_run_id",
        "artifacts",
        "experiments",
        ["pipeline_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_artifacts_workspace_pipeline_run",
        "artifacts",
        "experiments",
        ["workspace_id", "pipeline_run_id"],
        ["workspace_id", "id"],
        ondelete="SET NULL",
    )
    op.execute(
        sa.text(
            """
            UPDATE artifacts AS artifact
            SET pipeline_run_id = experiment.id
            FROM experiments AS experiment
            WHERE artifact.pipeline_run_id IS NULL
              AND artifact.workspace_id = experiment.workspace_id
              AND artifact.metadata->>'pipeline_run_id' = experiment.id::text
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_artifacts_workspace_pipeline_run", "artifacts", type_="foreignkey"
    )
    op.drop_constraint("fk_artifacts_pipeline_run_id", "artifacts", type_="foreignkey")
    op.drop_index(
        "ix_artifacts_workspace_pipeline_run_id", table_name="artifacts"
    )
    op.drop_index("ix_artifacts_pipeline_run_id", table_name="artifacts")
    op.drop_column("artifacts", "pipeline_run_id")
