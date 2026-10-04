"""Transaction-bound permission for retention-guarded deletes (ADR 0009 §2.2).

``prevent_mutation_except_retention()`` honours the ``dclab.*`` retention GUCs only
when ``dclab.retention_xact`` equals the current transaction id. These helpers set
all of them with ``set_config(..., true)`` (``SET LOCAL``) in one statement, so the
permission ends with the transaction and cannot leak on a pooled connection.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text

from app.domain.agent_records import RETENTION_HORIZON_MIN_DAYS

_STAMP = "set_config('dclab.retention_xact', pg_current_xact_id()::text, true)"


def allow_workspace_deletion(connection, workspace_id: UUID) -> None:
    """Let this transaction delete append-only rows of one workspace (workspace/experiment deletion)."""

    connection.execute(
        text(f"SELECT {_STAMP}, set_config('dclab.deleting_workspace', :workspace, true)"),
        {"workspace": str(workspace_id)},
    )


def allow_retention(connection, workspace_id: UUID, *, horizon_days: int) -> None:
    """Let this transaction delete one workspace's append-only rows older than the horizon."""

    if horizon_days < RETENTION_HORIZON_MIN_DAYS:
        raise ValueError(f"retention horizon must be at least {RETENTION_HORIZON_MIN_DAYS} days")
    connection.execute(
        text(
            f"SELECT {_STAMP}, set_config('dclab.retention_horizon', :horizon, true), "
            "set_config('dclab.retention_workspace', :workspace, true)"
        ),
        {"horizon": f"{int(horizon_days)} days", "workspace": str(workspace_id)},
    )
