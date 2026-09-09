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
"""

from __future__ import annotations

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
    consumes_ml_engineer_seat,
    stored_roles_consuming_technical_seats,
)

MAX_MEMBERS = "max_members"
MAX_ML_ENGINEER_SEATS = "max_ml_engineer_seats"
SOURCE_SYSTEM_DEFAULT = "system_default"

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
    return row


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
