"""one scientific plan record per pipeline run

Revision ID: 0039_scientific_plans
Revises: 0038_runtime_env_lock_scope
Create Date: 2026-09-05

Queryable holdout/validation/metric facts live on pipeline_scientific_plans.
experiments.result JSON remains compatibility evidence beside the row.

The backfill mapper is frozen in this file. It must not import live service
modules; later changes to scientific persistence must not alter 0039 output.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039_scientific_plans"
down_revision: Union[str, Sequence[str], None] = "0038_runtime_env_lock_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen 0039 backfill mapper. Copied from scientific_lineage_service at the
# revision that created this table. Do not import the live service, and do not
# update this copy when that service later changes.


def _content_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _as_plan_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        return dict(payload) if isinstance(payload, dict) else {}
    if isinstance(value, dict):
        return dict(value)
    return {}


def _json_ready(payload: Any) -> Any:
    return json.loads(json.dumps(payload, default=str))


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_str(value: Any) -> str | None:
    return _optional_str(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def scientific_plan_columns_from_payloads(
    *,
    holdout_plan: Any,
    development_plan: Any,
    split: Any = None,
    validation_plan: Any = None,
    metric_plan: Any = None,
) -> dict[str, Any] | None:
    """Map HoldoutPlan / ModelDevelopmentPlan JSON into queryable columns.

    Frozen as of 0039. Byte-identical to the live mapper at that revision.
    """

    holdout = _as_plan_dict(holdout_plan)
    development = _as_plan_dict(development_plan)
    if not holdout or not development:
        return None
    nested_validation = _as_plan_dict(development.get("validation_plan"))
    nested_metric = _as_plan_dict(development.get("metric_plan"))
    validation = nested_validation or _as_plan_dict(validation_plan)
    metric = nested_metric or _as_plan_dict(metric_plan)
    profile = _as_plan_dict(development.get("problem_profile"))
    split_payload = _as_plan_dict(split)
    task_type = _required_str(profile.get("task_type"))
    holdout_strategy = _required_str(holdout.get("strategy"))
    validation_strategy = _required_str(validation.get("strategy"))
    primary_metric = _required_str(metric.get("primary_metric"))
    if not task_type or not holdout_strategy or not validation_strategy or not primary_metric:
        return None
    try:
        holdout_test_size = float(holdout.get("test_size"))
    except (TypeError, ValueError):
        return None
    requested = _optional_int(validation.get("requested_folds"))
    requested_folds = 5 if requested is None else requested
    development_payload = dict(development)
    if validation and not nested_validation:
        development_payload["validation_plan"] = validation
    if metric and not nested_metric:
        development_payload["metric_plan"] = metric
    allowed = list(development.get("allowed_features") or [])
    excluded = list(development.get("excluded_features") or [])
    group_column = (
        _optional_str(holdout.get("group_column"))
        or _optional_str(development.get("group_column"))
        or _optional_str(validation.get("group_column"))
    )
    time_column = (
        _optional_str(holdout.get("time_column"))
        or _optional_str(development.get("time_column"))
        or _optional_str(validation.get("time_column"))
    )
    full_plan = _json_ready(
        {
            "holdout_plan": holdout,
            "model_development_plan": development_payload,
            "validation_plan": validation,
            "metric_plan": metric,
            "split": split_payload,
        }
    )
    return {
        "task_type": task_type,
        "holdout_strategy": holdout_strategy,
        "holdout_test_size": holdout_test_size,
        "validation_strategy": validation_strategy,
        "requested_folds": requested_folds,
        "actual_folds": _optional_int(validation.get("actual_folds")),
        "primary_metric": primary_metric,
        "group_column": group_column,
        "time_column": time_column,
        "allowed_feature_count": len(allowed),
        "excluded_feature_count": len(excluded),
        "holdout_plan_digest": _content_digest(_json_ready(holdout)),
        "model_development_plan_digest": _content_digest(_json_ready(development_payload)),
        "full_plan": full_plan,
    }


def _backfill_scientific_plans() -> None:
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
