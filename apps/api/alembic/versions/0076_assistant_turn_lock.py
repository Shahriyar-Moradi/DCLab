"""One live turn per assistant thread; turns of different threads never collide.

Revision ID: 0076_assistant_turn_lock
Revises: 0075_semantic_review_proposals
Create Date: 2026-10-05

P6.3-B2 (ADR 0009 §2.1, §7.2). An assistant thread is an ``agent_runs`` row of kind
``assistant``; each turn is a child ``lead`` run (``parent_run_id`` = the thread). The 0071
partial unique ``uq_agent_runs_active_subject`` (one active run per agent and subject,
NULLS NOT DISTINCT) would let only one turn run per project at a time across all threads
and users, so its predicate now also skips thread turns (``kind = 'lead'`` with a parent);
the new partial unique ``uq_agent_runs_live_turn`` allows one queued/running turn per
thread (``409 turn_in_progress``). Index-only: no column, constraint or trigger changes.

Locking: both indexes are rebuilt in one transaction under ``lock_timeout = 5s`` (not
CONCURRENTLY): DROP INDEX takes an ACCESS EXCLUSIVE lock on ``agent_runs`` (reads and writes
wait) until the transaction commits; the table is small today, so the block is short.
Downgrade locks the table and refuses while a thread turn is live (the old index could
collide), then restores the 0071 index.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0076_assistant_turn_lock"
down_revision: Union[str, Sequence[str], None] = "0075_semantic_review_proposals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE_COLUMNS = ["workspace_id", "agent_key", "subject_kind", "project_id", "experiment_id", "dataset_id",
                   "problem_spec_id", "split_plan_id", "model_version_id"]
_ACTIVE_0071 = "status IN ('queued', 'running') AND kind <> 'assistant'"
# Identical literals in app/db/models.py (AgentRun indexes).
_ACTIVE = _ACTIVE_0071 + " AND (kind <> 'lead' OR parent_run_id IS NULL)"
_LIVE_TURN = "kind = 'lead' AND parent_run_id IS NOT NULL AND status IN ('queued', 'running')"


def _active_subject(where: str) -> None:
    op.create_index("uq_agent_runs_active_subject", "agent_runs", _ACTIVE_COLUMNS, unique=True,
                    postgresql_nulls_not_distinct=True, postgresql_where=sa.text(where))


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index("uq_agent_runs_active_subject", table_name="agent_runs")
    _active_subject(_ACTIVE)
    op.create_index("uq_agent_runs_live_turn", "agent_runs", ["workspace_id", "parent_run_id"], unique=True,
                    postgresql_where=sa.text(_LIVE_TURN))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE agent_runs IN SHARE ROW EXCLUSIVE MODE")
    if op.get_bind().execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM agent_runs WHERE {_LIVE_TURN})")).scalar():
        raise RuntimeError("0076 downgrade refused: an assistant turn is live; wait until it ends")
    op.drop_index("uq_agent_runs_live_turn", table_name="agent_runs")
    op.drop_index("uq_agent_runs_active_subject", table_name="agent_runs")
    _active_subject(_ACTIVE_0071)
