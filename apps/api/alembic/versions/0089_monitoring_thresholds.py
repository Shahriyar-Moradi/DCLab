"""Editable monitoring thresholds: per-project versions and the values each window used.

Revision ID: 0089_monitoring_thresholds
Revises: 0088_improve_runs
Create Date: 2026-10-17

D5b (ADR 0012 D4). Expand-only: one new table and one nullable column; nothing existing changes.

- ``monitoring_thresholds``: append-only versions (``prevent_canonical_row_mutation()``) of a
  project's PSI watch / alert lines and precision / recall floors (null floor = off), within fixed
  bounds (``ck_monitoring_thresholds_bounds``). The newest ``version`` per project is in force; no row
  means the defaults (0.10 / 0.20, no floors). Each row names its ``monitoring_thresholds_changed``
  History entry (composite tenant FK to ``project_decision_records``) and the person who saved it.
- ``monitoring_windows.thresholds_used``: the values a window revision was computed with (bounded
  jsonb object). Null for revisions computed before this migration (they used the defaults). Adding a
  nullable column without a default rewrites no row and fires no trigger on the append-only table.

Locking: ``add_column`` takes a brief ACCESS EXCLUSIVE lock (catalog-only, every row NULL); the CHECK is
added NOT VALID then validated (the repo convention, 0069 / 0082), all under ``lock_timeout``.

Literals are identical in ``app/domain/monitoring.py`` and ``app/db/integrity.py``.

Downgrade drops the trigger, the table and the column: the thresholds fall back to the defaults and
the values stored on windows are gone (the History entries stay; a record's
``details.monitoring_thresholds_id`` then names nothing). A future workspace purge must delete these
rows through the retention guard, like the other append-only logs. Rolling the application back does
not need this downgrade: older code ignores the nullable column and the unused table.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0089_monitoring_thresholds"
down_revision: Union[str, Sequence[str], None] = "0088_improve_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CK_BOUNDS = (
    "psi_watch >= 0.05 AND psi_watch <= 0.5 "
    "AND psi_alert > psi_watch AND psi_alert <= 1.0 "
    "AND (precision_floor IS NULL OR (precision_floor >= 0.01 AND precision_floor <= 0.99)) "
    "AND (recall_floor IS NULL OR (recall_floor >= 0.01 AND recall_floor <= 0.99))"
)
_CK_VERSION = "version >= 1"
_CK_USED = (
    "thresholds_used IS NULL OR (jsonb_typeof(thresholds_used) = 'object' "
    "AND octet_length(CAST(thresholds_used AS TEXT)) <= 1024)"
)
_APPEND_ONLY = (
    "CREATE TRIGGER monitoring_thresholds_append_only BEFORE UPDATE OR DELETE ON monitoring_thresholds "
    "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()"
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "monitoring_thresholds",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("psi_watch", sa.Float(), nullable=False),
        sa.Column("psi_alert", sa.Float(), nullable=False),
        sa.Column("precision_floor", sa.Float(), nullable=True),
        sa.Column("recall_floor", sa.Float(), nullable=True),
        sa.Column("decision_record_id", uuid, nullable=False),
        sa.Column("created_by_user_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                  nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_monitoring_thresholds_workspace_id"),
        sa.UniqueConstraint("workspace_id", "project_id", "version", name="uq_monitoring_thresholds_version"),
        sa.ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
                                name="fk_monitoring_thresholds_workspace_project"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "decision_record_id"],
            ["project_decision_records.workspace_id", "project_decision_records.project_id",
             "project_decision_records.id"],
            name="fk_monitoring_thresholds_workspace_project_record"),
        sa.CheckConstraint(_CK_BOUNDS, name="ck_monitoring_thresholds_bounds"),
        sa.CheckConstraint(_CK_VERSION, name="ck_monitoring_thresholds_version"),
    )
    op.create_index("ix_monitoring_thresholds_created_by_user_id", "monitoring_thresholds", ["created_by_user_id"])
    op.create_index("ix_monitoring_thresholds_workspace_record", "monitoring_thresholds",
                    ["workspace_id", "decision_record_id"])
    op.execute(sa.text(_APPEND_ONLY))
    op.add_column("monitoring_windows", sa.Column("thresholds_used", postgresql.JSONB(), nullable=True))
    op.execute(sa.text(f"ALTER TABLE monitoring_windows ADD CONSTRAINT ck_monitoring_windows_thresholds_used "
                       f"CHECK ({_CK_USED}) NOT VALID"))
    op.execute(sa.text("ALTER TABLE monitoring_windows VALIDATE CONSTRAINT ck_monitoring_windows_thresholds_used"))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint("ck_monitoring_windows_thresholds_used", "monitoring_windows", type_="check")
    op.drop_column("monitoring_windows", "thresholds_used")
    op.execute(sa.text("DROP TRIGGER IF EXISTS monitoring_thresholds_append_only ON monitoring_thresholds"))
    op.drop_table("monitoring_thresholds")
