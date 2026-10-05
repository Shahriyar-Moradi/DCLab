"""Agent run release marker and terminal-status guard.

Revision ID: 0073_agent_run_release
Revises: 0072_ai_governance
Create Date: 2026-10-05

P6.10-A2, ADR 0009 §2.1, §4 (budget release), §5.1 step 9. Expand-only:

- ``agent_runs.budget_released_at timestamptz NULL``: the durable, write-once marker
  that the run's budget hold was released. The gateway stamps it in the same
  compare-and-set that moves the run from a live to a terminal status (the only
  live -> terminal transition), so a second release from any process finds it set.
  ``ck_agent_runs_released_terminal``: a marker only on a terminal row. Runs that
  ended before this revision keep NULL (their terminal status was the marker).
- ``guard_agent_run_lifecycle()`` (BEFORE UPDATE OF status, budget_released_at):
  a terminal status is final (no change, in particular never back to a live
  status) and the marker is write-once.

Locking: ADD COLUMN without default is metadata-only; the CHECK validates under
ACCESS EXCLUSIVE on ``agent_runs`` (every row is NULL, a short scan); ``lock_timeout``
5s fails fast. No existing row is rewritten.

Downgrade drops the trigger, function, CHECK and column; the pre-0073 code treats a
terminal status as the release marker, which every released run still has.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0073_agent_run_release"
down_revision: Union[str, Sequence[str], None] = "0072_ai_governance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TERMINAL = "'completed', 'failed', 'rejected_by_validator', 'over_budget', 'timed_out', 'cancelled', 'closed'"

# Identical literal SQL in app/db/integrity.py (GUARD_AGENT_RUN_LIFECYCLE_SQL).
GUARD_AGENT_RUN_LIFECYCLE_SQL = f"""
CREATE OR REPLACE FUNCTION guard_agent_run_lifecycle()
RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ({_TERMINAL}) AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION 'agent_runs: status % is final', OLD.status;
    END IF;
    IF OLD.budget_released_at IS NOT NULL
        AND NEW.budget_released_at IS DISTINCT FROM OLD.budget_released_at THEN
        RAISE EXCEPTION 'agent_runs.budget_released_at is write-once';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

AGENT_RUN_LIFECYCLE_TRIGGER_SQL = (
    "CREATE TRIGGER agent_runs_lifecycle BEFORE UPDATE OF status, budget_released_at ON agent_runs "
    "FOR EACH ROW EXECUTE FUNCTION guard_agent_run_lifecycle()"
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("agent_runs", sa.Column("budget_released_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_agent_runs_released_terminal",
        "agent_runs",
        f"budget_released_at IS NULL OR status IN ({_TERMINAL})",
    )
    op.execute(GUARD_AGENT_RUN_LIFECYCLE_SQL)
    op.execute(AGENT_RUN_LIFECYCLE_TRIGGER_SQL)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TRIGGER IF EXISTS agent_runs_lifecycle ON agent_runs")
    op.execute("DROP FUNCTION IF EXISTS guard_agent_run_lifecycle()")
    op.drop_constraint("ck_agent_runs_released_terminal", "agent_runs", type_="check")
    op.drop_column("agent_runs", "budget_released_at")
