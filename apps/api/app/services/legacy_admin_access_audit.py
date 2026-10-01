"""Safe audit signal for authorized internal legacy-surface access.

This records an authorized attempt, not a claim that the requested record
exists. Authentication, capability denial, and workspace-selector denial are
audited by the shared dependencies before this function is reached.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from fastapi import Request

from app.services.request_ids import request_id_of

logger = logging.getLogger("dclab.legacy_admin_access")

_ACTIONS = frozenset(
    {
        "simulation_create",
        "simulation_list",
        "simulation_detail",
        "simulation_decision",
        "model_registry_list",
        "trial_audit_detail",
        "monitoring_list",
    }
)


def record_legacy_admin_access(
    request: Request,
    *,
    action: str,
    actor_id: UUID,
    workspace_id: UUID,
) -> None:
    if action not in _ACTIONS:
        raise ValueError("unknown legacy admin audit action")
    event = {
        "event": "legacy_admin_access",
        "action": action,
        "capabilities": [
            "platform_write" if action == "simulation_create" else "platform_read",
            "workspace_read",
        ],
        "outcome": "authorized_attempt",
        "request_id": request_id_of(request),
        "actor_id": str(actor_id),
        "workspace_id": str(workspace_id),
    }
    logger.info("legacy_admin_audit %s", json.dumps(event, sort_keys=True))
