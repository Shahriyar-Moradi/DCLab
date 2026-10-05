"""A run ends only through the budget release (DB-enforced).

Revision ID: 0074_agent_run_release_guard
Revises: 0073_agent_run_release
Create Date: 2026-10-05

P6.10-A follow-up (security review of A2, note 1), ADR 0009 §4, §5.1 step 9. Replaces
``guard_agent_run_lifecycle()`` (CREATE OR REPLACE; the 0073 trigger keeps calling it):
besides "a terminal status is final" and "the marker is write-once", a move from a live
status (queued, running, waiting_user) to a terminal one must stamp
``budget_released_at`` in the same UPDATE. The gateway's release compare-and-set
(``budget._end_run``, used by ``release`` and ``release_run``) is the only such writer
and already stamps it. INSERTs are not guarded: a row inserted terminal (fixtures,
imports) keeps a NULL marker, and the release code treats a terminal status as released.

Locking: CREATE OR REPLACE FUNCTION only; no table is touched. Downgrade restores the
0073 function body.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0074_agent_run_release_guard"
down_revision: Union[str, Sequence[str], None] = "0073_agent_run_release"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LIVE = "'queued', 'running', 'waiting_user'"
_TERMINAL = "'completed', 'failed', 'rejected_by_validator', 'over_budget', 'timed_out', 'cancelled', 'closed'"

# Identical literal SQL in app/db/integrity.py (GUARD_AGENT_RUN_LIFECYCLE_V2_SQL).
GUARD_AGENT_RUN_LIFECYCLE_V2_SQL = f"""
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
    IF OLD.status IN ({_LIVE}) AND NEW.status IN ({_TERMINAL}) AND NEW.budget_released_at IS NULL THEN
        RAISE EXCEPTION 'agent_runs: a run ends only through the budget release (budget_released_at)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# Verbatim 0073 body, restored by downgrade.
GUARD_AGENT_RUN_LIFECYCLE_0073_SQL = f"""
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


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(GUARD_AGENT_RUN_LIFECYCLE_V2_SQL)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(GUARD_AGENT_RUN_LIFECYCLE_0073_SQL)
