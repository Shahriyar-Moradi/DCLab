"""Weekly scheduled scoring: one schedule per project and its append-only run log.

Revision ID: 0079_scoring_schedules
Revises: 0078_model_releases
Create Date: 2026-10-12

``scoring_schedules`` is mutable config a person saves (weekday 0-6 = Monday-Sunday and
hour 0-23, both UTC; enabled; who saved it; the last and next weekly slot), one per project
(``uq_scoring_schedules_project``), composite tenant FK to projects, a partial index for the
worker's due scan. ``scoring_schedule_runs`` records each handled weekly slot once
(``UNIQUE (schedule_id, planned_for)``): ``queued`` with its batch prediction, the approval and
the file it scored, or a closed skip/failure outcome. Composite tenant FKs to the schedule,
batch prediction, model version, release (pinned to that version) and dataset; append-only
through the existing ``prevent_canonical_row_mutation()`` function; ``created_at`` is
``clock_timestamp()``.

Additive: two new tables. Downgrade drops the trigger and both tables: the schedules and their
run history are gone for good (the batch predictions, their files and the History entries of
schedule changes stay). Scheduled predictions' execution requests keep
``request_spec.scoring_schedule_run_id`` (a JSON id, no FK) and their reads fall back to
``scheduled: false``.

``scoring_schedule_runs`` is append-only (like ``model_releases``, its trigger refuses every
DELETE): a future purge of workspaces or projects must move it to the retention-aware trigger
and delete through ``app.db.retention_guard``, never a plain DELETE.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0079_scoring_schedules"
down_revision: Union[str, Sequence[str], None] = "0078_model_releases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Identical literals in app/domain/scoring_schedules.py and app/db/integrity.py.
_CK_WEEKDAY = "weekday BETWEEN 0 AND 6"
_CK_HOUR = "hour_utc BETWEEN 0 AND 23"
_CK_OUTCOME = (
    "outcome IN ('queued', 'skipped_no_new_file', 'skipped_no_model_in_use', 'skipped_no_approval', "
    "'skipped_owner_not_allowed', 'skipped_missed', 'failed_to_queue')"
)
_CK_REASON = (
    "reason_code IS NULL OR reason_code IN ('run_limit', 'worker_was_down', 'owner_missing', "
    "'model_not_scoreable', 'dataset_unavailable', 'release_changed', 'internal_error')"
)
_CK_QUEUED = (
    "(outcome = 'queued') = (batch_prediction_id IS NOT NULL) AND (outcome <> 'queued' "
    "OR (model_release_id IS NOT NULL AND input_dataset_id IS NOT NULL))"
)
_CK_RELEASE_VERSION = "model_release_id IS NULL OR model_version_id IS NOT NULL"
_TRIGGER = (
    "CREATE TRIGGER scoring_schedule_runs_append_only BEFORE UPDATE OR DELETE ON scoring_schedule_runs "
    "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()"
)


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    now = sa.text("now()")
    op.create_table(
        "scoring_schedules",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("hour_utc", sa.SmallInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", uuid, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by_user_id", uuid, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_planned_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_planned_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_scoring_schedules_workspace_id"),
        sa.UniqueConstraint("workspace_id", "project_id", "id", name="uq_scoring_schedules_workspace_project_id"),
        sa.UniqueConstraint("workspace_id", "project_id", name="uq_scoring_schedules_project"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
            name="fk_scoring_schedules_workspace_project",
        ),
        sa.CheckConstraint(_CK_WEEKDAY, name="ck_scoring_schedules_weekday"),
        sa.CheckConstraint(_CK_HOUR, name="ck_scoring_schedules_hour_utc"),
    )
    op.create_index(
        "ix_scoring_schedules_due", "scoring_schedules", ["next_planned_for"], postgresql_where=sa.text("enabled")
    )
    op.create_index("ix_scoring_schedules_created_by_user_id", "scoring_schedules", ["created_by_user_id"])
    op.create_index("ix_scoring_schedules_updated_by_user_id", "scoring_schedules", ["updated_by_user_id"])
    op.create_table(
        "scoring_schedule_runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("schedule_id", uuid, nullable=False),
        sa.Column("planned_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("batch_prediction_id", uuid, nullable=True),
        sa.Column("model_version_id", uuid, nullable=True),
        sa.Column("model_release_id", uuid, nullable=True),
        sa.Column("input_dataset_id", uuid, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_scoring_schedule_runs_workspace_id"),
        sa.UniqueConstraint("schedule_id", "planned_for", name="uq_scoring_schedule_runs_slot"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "schedule_id"],
            ["scoring_schedules.workspace_id", "scoring_schedules.project_id", "scoring_schedules.id"],
            name="fk_scoring_schedule_runs_workspace_project_schedule",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "batch_prediction_id"], ["batch_predictions.workspace_id", "batch_predictions.id"],
            name="fk_scoring_schedule_runs_workspace_batch_prediction",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "model_version_id"],
            ["model_versions.workspace_id", "model_versions.project_id", "model_versions.id"],
            name="fk_scoring_schedule_runs_workspace_project_model_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "model_version_id", "model_release_id"],
            ["model_releases.workspace_id", "model_releases.model_version_id", "model_releases.id"],
            name="fk_scoring_schedule_runs_workspace_model_release",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "input_dataset_id"],
            ["datasets.workspace_id", "datasets.project_id", "datasets.id"],
            name="fk_scoring_schedule_runs_workspace_project_input_dataset",
        ),
        sa.CheckConstraint(_CK_OUTCOME, name="ck_scoring_schedule_runs_outcome"),
        sa.CheckConstraint(_CK_REASON, name="ck_scoring_schedule_runs_reason_code"),
        sa.CheckConstraint(_CK_QUEUED, name="ck_scoring_schedule_runs_queued"),
        sa.CheckConstraint(_CK_RELEASE_VERSION, name="ck_scoring_schedule_runs_release_version"),
    )
    op.create_index(
        "uq_scoring_schedule_runs_batch_prediction", "scoring_schedule_runs", ["batch_prediction_id"], unique=True,
        postgresql_where=sa.text("batch_prediction_id IS NOT NULL"),
    )
    op.create_index(
        "ix_scoring_schedule_runs_workspace_input_dataset", "scoring_schedule_runs",
        ["workspace_id", "input_dataset_id"], postgresql_where=sa.text("input_dataset_id IS NOT NULL"),
    )
    op.create_index(
        "ix_scoring_schedule_runs_workspace_model_version", "scoring_schedule_runs",
        ["workspace_id", "model_version_id"], postgresql_where=sa.text("model_version_id IS NOT NULL"),
    )
    op.execute(sa.text(_TRIGGER))


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS scoring_schedule_runs_append_only ON scoring_schedule_runs"))
    op.drop_index("ix_scoring_schedule_runs_workspace_model_version", table_name="scoring_schedule_runs")
    op.drop_index("ix_scoring_schedule_runs_workspace_input_dataset", table_name="scoring_schedule_runs")
    op.drop_index("uq_scoring_schedule_runs_batch_prediction", table_name="scoring_schedule_runs")
    op.drop_table("scoring_schedule_runs")
    op.drop_index("ix_scoring_schedules_updated_by_user_id", table_name="scoring_schedules")
    op.drop_index("ix_scoring_schedules_created_by_user_id", table_name="scoring_schedules")
    op.drop_index("ix_scoring_schedules_due", table_name="scoring_schedules")
    op.drop_table("scoring_schedules")
