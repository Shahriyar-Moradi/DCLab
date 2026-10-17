"""Usage records derived from existing ledgers (append-only).

Revision ID: 0081_usage_records
Revises: 0080_monitoring_windows
Create Date: 2026-10-15

P8.4-A. ``usage_records``: one measured quantity of a workspace for one UTC day (compute seconds,
storage bytes, AI tokens and cost, agent and assistant runs), computed by a worker tick from
``ml_jobs``, ``artifacts``, ``llm_invocations`` and ``agent_runs``. A recompute that changes a
number appends a row with a new ``source_ref``; ``UNIQUE (workspace_id, kind, period_day,
project_id, source_ref) NULLS NOT DISTINCT`` makes a repeated recompute a no-op. Append-only
through the existing ``prevent_canonical_row_mutation()`` trigger function; tenant-keyed by the
composite (workspace, project) FK; ``created_at`` uses ``clock_timestamp()``. Reads take the
newest row per (kind, day) by ``created_at`` (ticks of one workspace are serialized by an advisory
lock), so a correction wins by being newer, never by how its ``source_ref`` sorts.

Also ``ix_ml_jobs_workspace_completed_at`` (partial, ``completed_at IS NOT NULL``): the admission
check sums a workspace's compute time for the month on every new run. Built in the migration's
transaction under ``lock_timeout = 5s`` (not CONCURRENTLY, the 0076 precedent): CREATE INDEX
holds a SHARE lock on ``ml_jobs`` (reads continue, writes wait) until commit; the table is small.

Additive: one new table and one index. Downgrade drops both. The records are derived: after a
downgrade/upgrade (or more than a day of worker downtime) only yesterday and today are
recomputed; older days are not backfilled.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0081_usage_records"
down_revision: Union[str, Sequence[str], None] = "0080_monitoring_windows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Identical literals in app/domain/usage.py and app/db/integrity.py.
_CK_KIND = (
    "kind IN ('compute_seconds', 'storage_bytes', 'llm_tokens', 'llm_cost_micros', 'agent_runs', "
    "'assistant_runs')"
)
_CK_QUANTITY = "quantity >= 0"
_CK_SOURCE_REF = "char_length(source_ref) BETWEEN 1 AND 128 AND source_ref ~ '^[a-z0-9][a-z0-9:._-]*$'"
_CK_PERIOD_DAY = "period_day >= DATE '2020-01-01' AND period_day <= (created_at AT TIME ZONE 'UTC')::date + 1"
# Identical literal in app/db/models.py (MlJob indexes).
_ML_JOBS_COMPLETED = "completed_at IS NOT NULL"
_TRIGGER = (
    "CREATE TRIGGER usage_records_append_only BEFORE UPDATE OR DELETE ON usage_records "
    "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()"
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "usage_records",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("period_day", sa.Date(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("source_ref", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                  nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_usage_records_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "kind", "period_day", "project_id", "source_ref",
            name="uq_usage_records_source", postgresql_nulls_not_distinct=True,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
            name="fk_usage_records_workspace_project",
        ),
        sa.CheckConstraint(_CK_KIND, name="ck_usage_records_kind"),
        sa.CheckConstraint(_CK_QUANTITY, name="ck_usage_records_quantity"),
        sa.CheckConstraint(_CK_SOURCE_REF, name="ck_usage_records_source_ref"),
        sa.CheckConstraint(_CK_PERIOD_DAY, name="ck_usage_records_period_day"),
    )
    op.create_index(
        "ix_usage_records_workspace_kind_day", "usage_records",
        ["workspace_id", "kind", "period_day", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_usage_records_project_id", "usage_records", ["workspace_id", "project_id"],
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )
    op.execute(sa.text(_TRIGGER))
    op.create_index("ix_ml_jobs_workspace_completed_at", "ml_jobs", ["workspace_id", "completed_at"],
                    postgresql_where=sa.text(_ML_JOBS_COMPLETED))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index("ix_ml_jobs_workspace_completed_at", table_name="ml_jobs")
    op.execute(sa.text("DROP TRIGGER IF EXISTS usage_records_append_only ON usage_records"))
    op.drop_index("ix_usage_records_project_id", table_name="usage_records")
    op.drop_index("ix_usage_records_workspace_kind_day", table_name="usage_records")
    op.drop_table("usage_records")
