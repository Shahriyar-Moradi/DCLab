"""Bounded workspace access counters and safe, structured audit signals.

Counters are process-local diagnostics; production aggregation consumes the
structured audit log. Neither metric dimensions nor log records contain a
request path, email, token, or unvalidated selector value.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from uuid import UUID

from fastapi import Request

from app.services.request_ids import request_id_of

logger = logging.getLogger("dclab.workspace_access")

FAMILIES = frozenset({"denial", "selection"})
REASONS = frozenset(
    {
        "missing_selector",
        "malformed_selector",
        "unavailable_selection",
        "unauthorized_selector",
        "path_mismatch",
        "capability_denied",
        "selected",
        "cleared",
        "selection_denied",
        "other",
    }
)

_lock = threading.Lock()
_counts: dict[tuple[str, str], int] = defaultdict(int)


def reset_workspace_access_metrics() -> None:
    with _lock:
        _counts.clear()


def workspace_access_metric_counts() -> dict[str, int]:
    with _lock:
        return {
            f"{family}.{reason}": count
            for (family, reason), count in _counts.items()
        }


def record_workspace_access_event(
    request: Request,
    family: str,
    reason: str,
    *,
    actor_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> None:
    """Record a fixed-cardinality reason and one correlation-safe audit event."""

    bounded_family = family if family in FAMILIES else "denial"
    bounded_reason = reason if reason in REASONS else "other"
    with _lock:
        _counts[(bounded_family, bounded_reason)] += 1
    event = {
        "event": "workspace_access",
        "family": bounded_family,
        "reason": bounded_reason,
        "request_id": request_id_of(request),
        "actor_id": str(actor_id) if actor_id is not None else None,
        "workspace_id": str(workspace_id) if workspace_id is not None else None,
    }
    logger.info("workspace_audit %s", json.dumps(event, sort_keys=True))
