"""Cooperative run cancellation for durable ml_jobs.

Revision ID: 0066_run_cancellation
Revises: 0065_idempotency_keys
Create Date: 2026-10-02

P3.1-B2 (``POST /v1/experiments/{id}/cancel``). Expand-only:

- ``ml_jobs.cancel_requested_at timestamptz NULL``: set once on a running job;
  the worker stops at its next stage/candidate checkpoint. No backfill (every
  existing row is "never requested").
- ``ml_jobs.cancel_requested_by_user_id uuid NULL`` → ``users.id`` ON DELETE SET
  NULL: who asked (audit; also in the terminal event payload).
- ``ck_ml_jobs_status_valid`` widens to a superset that adds the terminal
  ``cancelled`` status (added ``NOT VALID`` then validated; every existing row
  already satisfies the superset).

Locking: Alembic runs the upgrade in ONE transaction, so DROP/ADD CONSTRAINT
hold ``ml_jobs``' ACCESS EXCLUSIVE lock until commit (NOT VALID does not avoid
it). ``ml_jobs`` is small, so the hold is short, but the ALTER could queue behind
a long transaction that read ``ml_jobs`` (an old worker) and stall claims and
heartbeats: ``lock_timeout`` is 5s (the migration fails instead of stalling;
retry). Drain or stop the workers before migrating.

Downgrade refuses while any job is ``cancelled`` (repair forward: those rows have
no pre-0066 representation), then restores the old CHECK and drops the column.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0066_run_cancellation"
down_revision: Union[str, Sequence[str], None] = "0065_idempotency_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_STATUS = "status IN ('queued', 'running', 'completed', 'failed')"
_NEW_STATUS = "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')"

_DOWNGRADE_PRECHECK_SQL = """
DO $$
DECLARE
    cancelled bigint;
BEGIN
    SELECT count(*) INTO cancelled FROM ml_jobs WHERE status = 'cancelled';
    IF cancelled > 0 THEN
        RAISE EXCEPTION
            '0066 downgrade refused: % ml_jobs are cancelled; repair forward', cancelled;
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "ml_jobs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ml_jobs",
        sa.Column(
            "cancel_requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute("ALTER TABLE ml_jobs DROP CONSTRAINT ck_ml_jobs_status_valid")
    op.execute(
        f"ALTER TABLE ml_jobs ADD CONSTRAINT ck_ml_jobs_status_valid CHECK ({_NEW_STATUS}) NOT VALID"
    )
    op.execute("ALTER TABLE ml_jobs VALIDATE CONSTRAINT ck_ml_jobs_status_valid")


def downgrade() -> None:
    op.execute(_DOWNGRADE_PRECHECK_SQL)
    op.execute("ALTER TABLE ml_jobs DROP CONSTRAINT ck_ml_jobs_status_valid")
    op.execute(
        f"ALTER TABLE ml_jobs ADD CONSTRAINT ck_ml_jobs_status_valid CHECK ({_OLD_STATUS})"
    )
    op.drop_column("ml_jobs", "cancel_requested_by_user_id")
    op.drop_column("ml_jobs", "cancel_requested_at")
