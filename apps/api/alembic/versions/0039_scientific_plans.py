"""one scientific plan record per pipeline run

Revision ID: 0039_scientific_plans
Revises: 0038_runtime_env_lock_scope
Create Date: 2026-09-05

Queryable holdout/validation/metric facts live on pipeline_scientific_plans.
experiments.result JSON remains compatibility evidence beside the row.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039_scientific_plans"
down_revision: Union[str, Sequence[str], None] = "0038_runtime_env_lock_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill_scientific_plans() -> None:
    from app.services.scientific_lineage_service import scientific_plan_columns_from_payloads

    connection = op.get_bind()
    existing = {
        row[0]
        for row in connection.execute(
            sa.text("SELECT pipeline_run_id FROM pipeline_scientific_plans")
        )
    }
    rows = connection.execute(
        sa.text(
            "SELECT id, workspace_id, project_id, result FROM experiments WHERE result IS NOT NULL"
        )
    ).mappings()
    now = datetime.now(UTC)
    insert_sql = sa.text(
        """
        INSERT INTO pipeline_scientific_plans (
            id, workspace_id, project_id, pipeline_run_id,
            task_type, holdout_strategy, holdout_test_size,
            validation_strategy, requested_folds, actual_folds,
            primary_metric, group_column, time_column,
            allowed_feature_count, excluded_feature_count,
            holdout_plan_digest, model_development_plan_digest,
            full_plan, locked_at, created_at
        ) VALUES (
            :id, :workspace_id, :project_id, :pipeline_run_id,
            :task_type, :holdout_strategy, :holdout_test_size,
            :validation_strategy, :requested_folds, :actual_folds,
            :primary_metric, :group_column, :time_column,
            :allowed_feature_count, :excluded_feature_count,
            :holdout_plan_digest, :model_development_plan_digest,
            CAST(:full_plan AS jsonb), :locked_at, :created_at
        )
        """
    )
    for row in rows:
        pipeline_run_id = row["id"]
        if pipeline_run_id in existing:
            continue
        result = row["result"]
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                continue
        if not isinstance(result, dict):
            continue
        values = scientific_plan_columns_from_payloads(
            holdout_plan=result.get("holdout_plan"),
            development_plan=result.get("model_development_plan"),
            split=result.get("split"),
            validation_plan=result.get("validation_plan"),
            metric_plan=result.get("metric_plan"),
        )
        if values is None:
            continue
        full_plan = values.pop("full_plan")
        connection.execute(
            insert_sql,
            {
                "id": uuid.uuid4(),
                "workspace_id": row["workspace_id"],
                "project_id": row["project_id"],
                "pipeline_run_id": pipeline_run_id,
                "full_plan": json.dumps(full_plan, default=str),
                "locked_at": now,
                "created_at": now,
                **values,
            },
        )
        existing.add(pipeline_run_id)


def upgrade() -> None:
    op.create_table(
        "pipeline_scientific_plans",
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
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("holdout_strategy", sa.String(length=64), nullable=False),
        sa.Column("holdout_test_size", sa.Float(), nullable=False),
        sa.Column("validation_strategy", sa.String(length=64), nullable=False),
        sa.Column("requested_folds", sa.Integer(), nullable=False),
        sa.Column("actual_folds", sa.Integer(), nullable=True),
        sa.Column("primary_metric", sa.String(length=64), nullable=False),
        sa.Column("group_column", sa.String(length=256), nullable=True),
        sa.Column("time_column", sa.String(length=256), nullable=True),
        sa.Column("allowed_feature_count", sa.Integer(), nullable=False),
        sa.Column("excluded_feature_count", sa.Integer(), nullable=False),
        sa.Column("holdout_plan_digest", sa.String(length=64), nullable=False),
        sa.Column("model_development_plan_digest", sa.String(length=64), nullable=False),
        sa.Column("full_plan", postgresql.JSONB(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "pipeline_run_id", name="uq_pipeline_scientific_plans_pipeline_run"
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_pipeline_scientific_plans_workspace_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_pipeline_scientific_plans_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_pipeline_scientific_plans_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_pipeline_scientific_plans_workspace_id",
        "pipeline_scientific_plans",
        ["workspace_id"],
    )
    op.create_index(
        "ix_pipeline_scientific_plans_project_id",
        "pipeline_scientific_plans",
        ["project_id"],
    )
    op.create_index(
        "ix_pipeline_scientific_plans_pipeline_run_id",
        "pipeline_scientific_plans",
        ["pipeline_run_id"],
    )
    _backfill_scientific_plans()


def downgrade() -> None:
    op.drop_index(
        "ix_pipeline_scientific_plans_pipeline_run_id",
        table_name="pipeline_scientific_plans",
    )
    op.drop_index(
        "ix_pipeline_scientific_plans_project_id",
        table_name="pipeline_scientific_plans",
    )
    op.drop_index(
        "ix_pipeline_scientific_plans_workspace_id",
        table_name="pipeline_scientific_plans",
    )
    op.drop_table("pipeline_scientific_plans")
