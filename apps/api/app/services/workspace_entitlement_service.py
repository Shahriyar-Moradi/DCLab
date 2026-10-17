"""Centralized workspace entitlement storage and enforcement.

Authorization roles stay in ``authorization_service``. Numeric/product limits
live here so application code does not hard-code plan caps.

Two entitlements, two meanings:

* ``max_ml_engineer_seats`` — Business technical seats (canonical ``ml_engineer``,
  including any stored role that translates to it). Owner, admin, viewer, and
  legacy ``business_admin`` / ``business_developer`` do **not** consume these.
  Business default is 5.
* ``max_members`` — optional overall cap on **all** ``workspace_memberships``
  rows. Personal workspaces seed ``1`` (one owner/user). Business workspaces do
  not seed this key; it is only a safety cap when an operator sets it.

Do not reuse ``max_members`` as the ML-engineer seat limit.

Feature flags (D6a): ``ai_assistant_beta`` (value ``true`` / ``false``; absent = off) lets one
workspace use the in-app assistant beta. Only a DCLab platform administrator sets it
(``set_feature_flag``, CLI ``dclab entitlements set``; no HTTP route, tenants cannot): the row
records ``platform_staff:<admin id>`` and every change is logged on ``dclab.audit``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Workspace,
    WorkspaceEntitlement,
    WorkspaceKind,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.domain.errors import IdentityError
from app.services.authorization_service import (
    can_write_platform,
    consumes_ml_engineer_seat,
    stored_roles_consuming_technical_seats,
)

MAX_MEMBERS = "max_members"
MAX_ML_ENGINEER_SEATS = "max_ml_engineer_seats"
SOURCE_SYSTEM_DEFAULT = "system_default"
AI_ASSISTANT_BETA = "ai_assistant_beta"
FEATURE_FLAG_KEYS = (AI_ASSISTANT_BETA,)
audit_log = logging.getLogger("dclab.audit")

_DEFAULT_MAX_MEMBERS = {
    WorkspaceKind.PERSONAL.value: 1,
    WorkspaceKind.BUSINESS.value: None,
}
_DEFAULT_MAX_ML_ENGINEER_SEATS = {
    WorkspaceKind.PERSONAL.value: 0,
    WorkspaceKind.BUSINESS.value: 5,
}


def default_max_members_for_kind(kind: str) -> int | None:
    try:
        return _DEFAULT_MAX_MEMBERS[kind]
    except KeyError as exc:
        raise IdentityError(f"unknown workspace kind '{kind}'") from exc


def default_max_ml_engineer_seats_for_kind(kind: str) -> int:
    try:
        return _DEFAULT_MAX_ML_ENGINEER_SEATS[kind]
    except KeyError as exc:
        raise IdentityError(f"unknown workspace kind '{kind}'") from exc


def get_entitlement(
    db: Session, workspace_id: UUID, entitlement_key: str, *, for_update: bool = False
) -> WorkspaceEntitlement | None:
    stmt = select(WorkspaceEntitlement).where(
        WorkspaceEntitlement.workspace_id == workspace_id,
        WorkspaceEntitlement.entitlement_key == entitlement_key,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def set_entitlement(
    db: Session,
    workspace_id: UUID,
    entitlement_key: str,
    value: Any,
    *,
    source: str,
) -> WorkspaceEntitlement:
    row = get_entitlement(db, workspace_id, entitlement_key)
    if row is None:
        row = WorkspaceEntitlement(
            workspace_id=workspace_id,
            entitlement_key=entitlement_key,
            value_json=value,
            source=source,
        )
        db.add(row)
    else:
        row.value_json = value
        row.source = source
        row.updated_at = datetime.now(UTC)
    db.flush()
    # Local import avoids making entitlement storage depend on the resolver at
    # module import time while still invalidating same-transaction reads.
    from app.services.workspace_capability_service import invalidate_capability_cache

    invalidate_capability_cache(db, workspace_id=workspace_id)
    return row


def feature_enabled(db: Session, workspace_id: UUID, key: str) -> bool:
    """A feature flag is on only when its row holds exactly ``true`` (absent or anything else: off)."""

    row = get_entitlement(db, workspace_id, key)
    return row is not None and row.value_json is True


def assistant_beta_enabled(db: Session, workspace_id: UUID) -> bool:
    return feature_enabled(db, workspace_id, AI_ASSISTANT_BETA)


def set_feature_flag(db: Session, *, actor: Any, workspace_id: UUID, key: str, enabled: bool) -> dict[str, Any]:
    """Platform staff (an active ``dclab_admin``) only; no HTTP route. The row records
    ``platform_staff:<admin id>`` and the change is logged on ``dclab.audit``. The caller commits."""

    if not getattr(actor, "is_active", False) or not can_write_platform(db, actor):
        raise IdentityError("setting a workspace feature flag requires a DCLab platform administrator", status_code=403)
    if key not in FEATURE_FLAG_KEYS:
        raise IdentityError(f"unknown feature flag {key!r}; one of {', '.join(FEATURE_FLAG_KEYS)}", status_code=422)
    if db.get(Workspace, workspace_id) is None:
        raise IdentityError("workspace not found", status_code=404)
    current = get_entitlement(db, workspace_id, key, for_update=True)  # concurrent changes log a true ``before``
    before = current is not None and current.value_json is True
    row = set_entitlement(db, workspace_id, key, bool(enabled), source=f"platform_staff:{actor.id}")
    audit_log.warning(json.dumps({"event": "workspace_feature_flag_set", "workspace_id": str(workspace_id), "key": key,
                               "before": before, "after": bool(enabled), "actor_user_id": str(actor.id)}))
    return {"workspace_id": str(workspace_id), "key": key, "enabled": row.value_json is True, "before": before,
            "source": row.source}


def seed_default_entitlements(db: Session, workspace: Workspace) -> None:
    if workspace.kind == WorkspaceKind.PERSONAL.value:
        set_entitlement(
            db,
            workspace.id,
            MAX_MEMBERS,
            default_max_members_for_kind(workspace.kind),
            source=SOURCE_SYSTEM_DEFAULT,
        )
        return
    set_entitlement(
        db,
        workspace.id,
        MAX_ML_ENGINEER_SEATS,
        default_max_ml_engineer_seats_for_kind(workspace.kind),
        source=SOURCE_SYSTEM_DEFAULT,
    )


def max_members_for(db: Session, workspace_id: UUID) -> int | None:
    row = get_entitlement(db, workspace_id, MAX_MEMBERS)
    if row is not None:
        return int(row.value_json)
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise IdentityError("workspace not found", status_code=404)
    return default_max_members_for_kind(workspace.kind)


def max_ml_engineer_seats_for(db: Session, workspace_id: UUID) -> int:
    row = get_entitlement(db, workspace_id, MAX_ML_ENGINEER_SEATS)
    if row is not None:
        return int(row.value_json)
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise IdentityError("workspace not found", status_code=404)
    return default_max_ml_engineer_seats_for_kind(workspace.kind)


def member_count(db: Session, workspace_id: UUID) -> int:
    """Count every membership row, including owner and admins."""

    return int(
        db.scalar(
            select(func.count(WorkspaceMembership.id)).where(
                WorkspaceMembership.workspace_id == workspace_id
            )
        )
        or 0
    )


def technical_seat_count(db: Session, workspace_id: UUID) -> int:
    """Count memberships that consume ``max_ml_engineer_seats``."""

    roles = stored_roles_consuming_technical_seats()
    if not roles:
        return 0
    return int(
        db.scalar(
            select(func.count(WorkspaceMembership.id)).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.role.in_(roles),
            )
        )
        or 0
    )


def _lock_workspace(db: Session, workspace_id: UUID) -> Workspace:
    workspace = db.scalar(
        select(Workspace).where(Workspace.id == workspace_id).with_for_update()
    )
    if workspace is None:
        raise IdentityError("workspace not found", status_code=404)
    return workspace


def assert_can_add_member(db: Session, workspace_id: UUID, role: WorkspaceRole) -> None:
    """Reject memberships that would exceed overall or technical seat caps.

    Locks the workspace row and the entitlement rows used for the check so two
    concurrent inserts cannot both pass.
    """

    _lock_workspace(db, workspace_id)
    get_entitlement(db, workspace_id, MAX_MEMBERS, for_update=True)
    get_entitlement(db, workspace_id, MAX_ML_ENGINEER_SEATS, for_update=True)

    overall = max_members_for(db, workspace_id)
    if overall is not None and member_count(db, workspace_id) >= overall:
        current = member_count(db, workspace_id)
        raise IdentityError(
            "workspace member limit reached: max_members counts all memberships "
            f"(owner, admins, engineers, viewers); {current} of {overall} seats are used",
            status_code=409,
        )

    if not consumes_ml_engineer_seat(role):
        return
    limit = max_ml_engineer_seats_for(db, workspace_id)
    current_seats = technical_seat_count(db, workspace_id)
    if current_seats >= limit:
        raise IdentityError(
            "workspace ML engineer seat limit reached: max_ml_engineer_seats counts "
            f"technical roles only; {current_seats} of {limit} seats are used",
            status_code=409,
        )
