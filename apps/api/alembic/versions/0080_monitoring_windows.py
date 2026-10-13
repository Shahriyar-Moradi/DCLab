"""Monitoring windows and linked outcome files (append-only).

Revision ID: 0080_monitoring_windows
Revises: 0079_scoring_schedules
Create Date: 2026-10-12

P7.4-A. ``monitoring_windows``: one row per computed revision of a weekly (Monday 00:00 UTC)
window of the model in use: input drift (PSI per original feature), score drift, outcomes
when linked, a status and a plain summary built from numbers. A recompute inserts the next
``revision``; reads use the highest. ``monitoring_outcome_sets``: a person's link of an
uploaded file of real outcomes (key column, outcome column). Both are append-only through the
existing ``prevent_canonical_row_mutation()`` trigger function, tenant-keyed by composite FKs
(project, model version, release pinned to the same version; project dataset), with CHECKs on
statuses, the week shape and bounded jsonb. ``computed_at`` / ``created_at`` use
``clock_timestamp()``.

Additive: two new tables. Downgrade drops both (the computed windows and the links are gone;
scorings, datasets and releases stay).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0080_monitoring_windows"
down_revision: Union[str, Sequence[str], None] = "0079_scoring_schedules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Identical literals in app/domain/monitoring.py and app/db/integrity.py.
_CK_STATUS = "status IN ('ok', 'watch', 'alert', 'not_enough_data')"
_CK_SUMMARY_CODE = "summary_code ~ '^[a-z][a-z0-9_]{0,63}$'"
_CK_SUMMARY = "char_length(btrim(summary)) > 0 AND char_length(summary) <= 1000"
_CK_COUNTS = "revision >= 1 AND n_rows >= 0 AND n_batch_predictions >= 1"
_CK_WEEK = (
    "window_end = window_start + interval '168 hours' "
    "AND EXTRACT(ISODOW FROM window_start AT TIME ZONE 'UTC') = 1 "
    "AND date_trunc('day', window_start AT TIME ZONE 'UTC') = window_start AT TIME ZONE 'UTC'"
)
_CK_JSON = " AND ".join(
    f"jsonb_typeof({column}) = 'object' AND octet_length(CAST({column} AS TEXT)) <= 32768"
    for column in ("input_drift", "prediction_drift", "outcomes")
)
_CK_COLUMNS = (
    "char_length(key_column) BETWEEN 1 AND 256 "
    "AND char_length(outcome_column) BETWEEN 1 AND 256 AND key_column <> outcome_column"
)
_TRIGGERS = tuple(
    f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
    "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()"
    for table in ("monitoring_windows", "monitoring_outcome_sets")
)


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    clock = sa.text("clock_timestamp()")
    op.create_table(
        "monitoring_windows",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("model_version_id", uuid, nullable=False),
        sa.Column("model_release_id", uuid, nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("n_rows", sa.Integer(), nullable=False),
        sa.Column("n_batch_predictions", sa.Integer(), nullable=False),
        sa.Column("input_drift", jsonb, nullable=False),
        sa.Column("prediction_drift", jsonb, nullable=False),
        sa.Column("outcomes", jsonb, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("summary_code", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.String(length=1000), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=clock, nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_monitoring_windows_workspace_id"),
        sa.UniqueConstraint(
            "project_id", "model_version_id", "window_start", "revision", name="uq_monitoring_windows_revision"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
            name="fk_monitoring_windows_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "model_version_id"],
            ["model_versions.workspace_id", "model_versions.project_id", "model_versions.id"],
            name="fk_monitoring_windows_workspace_project_model_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "model_version_id", "model_release_id"],
            ["model_releases.workspace_id", "model_releases.model_version_id", "model_releases.id"],
            name="fk_monitoring_windows_workspace_model_release",
        ),
        sa.CheckConstraint(_CK_STATUS, name="ck_monitoring_windows_status"),
        sa.CheckConstraint(_CK_SUMMARY_CODE, name="ck_monitoring_windows_summary_code"),
        sa.CheckConstraint(_CK_SUMMARY, name="ck_monitoring_windows_summary"),
        sa.CheckConstraint(_CK_COUNTS, name="ck_monitoring_windows_counts"),
        sa.CheckConstraint(_CK_WEEK, name="ck_monitoring_windows_week"),
        sa.CheckConstraint(_CK_JSON, name="ck_monitoring_windows_json"),
    )
    op.create_index(
        "ix_monitoring_windows_latest", "monitoring_windows",
        ["workspace_id", "project_id", "model_version_id", sa.text("window_start DESC"), sa.text("revision DESC")],
    )
    op.create_index(
        "ix_monitoring_windows_workspace_model_release", "monitoring_windows", ["workspace_id", "model_release_id"],
        postgresql_where=sa.text("model_release_id IS NOT NULL"),
    )
    op.create_table(
        "monitoring_outcome_sets",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("dataset_id", uuid, nullable=False),
        sa.Column("key_column", sa.String(length=256), nullable=False),
        sa.Column("outcome_column", sa.String(length=256), nullable=False),
        sa.Column("linked_by_user_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=clock, nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_monitoring_outcome_sets_workspace_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
            name="fk_monitoring_outcome_sets_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.project_id", "datasets.id"],
            name="fk_monitoring_outcome_sets_workspace_project_dataset",
        ),
        sa.CheckConstraint(_CK_COLUMNS, name="ck_monitoring_outcome_sets_columns"),
    )
    op.create_index(
        "ix_monitoring_outcome_sets_project_created_at", "monitoring_outcome_sets",
        ["workspace_id", "project_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_monitoring_outcome_sets_dataset", "monitoring_outcome_sets", ["workspace_id", "dataset_id"])
    op.create_index("ix_monitoring_outcome_sets_linked_by_user_id", "monitoring_outcome_sets", ["linked_by_user_id"])
    for statement in _TRIGGERS:
        op.execute(sa.text(statement))


def downgrade() -> None:
    for table in ("monitoring_outcome_sets", "monitoring_windows"):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}"))
    op.drop_index("ix_monitoring_outcome_sets_linked_by_user_id", table_name="monitoring_outcome_sets")
    op.drop_index("ix_monitoring_outcome_sets_dataset", table_name="monitoring_outcome_sets")
    op.drop_index("ix_monitoring_outcome_sets_project_created_at", table_name="monitoring_outcome_sets")
    op.drop_table("monitoring_outcome_sets")
    op.drop_index("ix_monitoring_windows_workspace_model_release", table_name="monitoring_windows")
    op.drop_index("ix_monitoring_windows_latest", table_name="monitoring_windows")
    op.drop_table("monitoring_windows")
