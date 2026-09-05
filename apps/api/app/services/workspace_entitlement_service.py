"""Centralized workspace entitlement storage and enforcement.

Authorization roles stay in ``authorization_service``. Numeric/product limits
live here so application code does not hard-code plan caps.

``max_members`` is the cap on **all** ``workspace_memberships`` rows for the
workspace: owner, workspace_admin, ml_engineer, viewer, and legacy
business_* roles. It is **not** "N ML-engineer seats plus admins".

A business default of 5 therefore means 5 total members (for example owner +
workspace_admin + 3 ml_engineers). The sixth membership is rejected.
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
)
from app.domain.errors import IdentityError

MAX_MEMBERS = "max_members"
SOURCE_SYSTEM_DEFAULT = "system_default"

_DEFAULT_MAX_MEMBERS = {
    WorkspaceKind.PERSONAL.value: 1,
    WorkspaceKind.BUSINESS.value: 5,
}


def default_max_members_for_kind(kind: str) -> int:
    try:
        return _DEFAULT_MAX_MEMBERS[kind]
    except KeyError as exc:
        raise IdentityError(f"unknown workspace kind '{kind}'") from exc


def get_entitlement(
    db: Session, workspace_id: UUID, entitlement_key: str
) -> WorkspaceEntitlement | None:
    return db.scalar(
        select(WorkspaceEntitlement).where(
            WorkspaceEntitlement.workspace_id == workspace_id,
            WorkspaceEntitlement.entitlement_key == entitlement_key,
        )
    )


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
    set_entitlement(
        db,
        workspace.id,
        MAX_MEMBERS,
        default_max_members_for_kind(workspace.kind),
        source=SOURCE_SYSTEM_DEFAULT,
    )


def max_members_for(db: Session, workspace_id: UUID) -> int:
    row = get_entitlement(db, workspace_id, MAX_MEMBERS)
    if row is not None:
        return int(row.value_json)
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise IdentityError("workspace not found", status_code=404)
    return default_max_members_for_kind(workspace.kind)


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


def assert_can_add_member(db: Session, workspace_id: UUID) -> None:
    """Reject when the next membership would exceed ``max_members`` total seats."""

    limit = max_members_for(db, workspace_id)
    current = member_count(db, workspace_id)
    if current >= limit:
        raise IdentityError(
            "workspace member limit reached: max_members counts all memberships "
            f"(owner, admins, engineers, viewers); {current} of {limit} seats are used",
            status_code=409,
        )
