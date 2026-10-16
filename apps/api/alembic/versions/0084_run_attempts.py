"""Run attempts: one row per worker claim of an ml_jobs row and how it ended.

Revision ID: 0084_run_attempts
Revises: 0083_notifications
Create Date: 2026-10-16

D2a. Expand-only: one new table, no change to existing ones.

- ``ml_job_attempts``: attempt number (``ml_jobs.attempts`` at the claim), started / ended
  timestamps, a closed ``outcome`` (running, completed, failed, retried, cancelled) and a closed
  ``failure_class`` (out_of_memory, timeout, worker_lost, data_error, internal_error, cancelled).
  No free text: an exception message is never stored here. Composite tenant FK to the job
  (CASCADE, like the job's own cascade from its workspace / upload); ``UNIQUE (ml_job_id,
  attempt)``. A finished attempt is final: ``ml_job_attempts_final`` refuses an UPDATE of a row
  whose outcome is no longer ``running`` (DELETE stays possible for the cascades).

No backfill: jobs that ran before this revision read an empty attempts list.

Locking: new table only (the FK takes a brief SHARE ROW EXCLUSIVE lock on ``ml_jobs``);
``lock_timeout`` 5s. Downgrade drops the table (attempt history is lost; nothing references it).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0084_run_attempts"
down_revision: Union[str, Sequence[str], None] = "0083_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Identical literals in app/domain/ml_jobs.py (and the trigger in app/db/integrity.py).
_CK_NUMBER = "attempt >= 1"
_CK_OUTCOME = "outcome IN ('running', 'completed', 'failed', 'retried', 'cancelled')"
_CK_FAILURE_CLASS = (
    "failure_class IS NULL OR failure_class IN ('out_of_memory', 'timeout', 'worker_lost', 'data_error', "
    "'internal_error', 'cancelled')"
)
_CK_SHAPE = (
    "(outcome IN ('running', 'completed') AND failure_class IS NULL"
    " OR outcome IN ('failed', 'retried') AND failure_class IS NOT NULL AND failure_class <> 'cancelled'"
    " OR outcome = 'cancelled' AND failure_class = 'cancelled')"
    " AND (outcome = 'running') = (ended_at IS NULL)"
    " AND (ended_at IS NULL OR ended_at >= started_at)"
)
_TRIGGER = (
    "CREATE TRIGGER ml_job_attempts_final BEFORE UPDATE ON ml_job_attempts "
    "FOR EACH ROW WHEN (OLD.outcome <> 'running') EXECUTE FUNCTION prevent_canonical_row_mutation()"
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "ml_job_attempts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ml_job_id", uuid, nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("failure_class", sa.String(length=32), nullable=True),
        sa.UniqueConstraint("ml_job_id", "attempt", name="uq_ml_job_attempts_job_attempt"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "ml_job_id"],
            ["ml_jobs.workspace_id", "ml_jobs.id"],
            name="fk_ml_job_attempts_workspace_job",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_CK_NUMBER, name="ck_ml_job_attempts_number"),
        sa.CheckConstraint(_CK_OUTCOME, name="ck_ml_job_attempts_outcome"),
        sa.CheckConstraint(_CK_FAILURE_CLASS, name="ck_ml_job_attempts_failure_class"),
        sa.CheckConstraint(_CK_SHAPE, name="ck_ml_job_attempts_shape"),
    )
    op.create_index("ix_ml_job_attempts_workspace_id", "ml_job_attempts", ["workspace_id"])
    op.execute(sa.text(_TRIGGER))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(sa.text("DROP TRIGGER IF EXISTS ml_job_attempts_final ON ml_job_attempts"))
    op.drop_table("ml_job_attempts")
