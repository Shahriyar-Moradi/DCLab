"""Centralized membership-based authorization and tenant resolution.

Membership rows are authoritative when present. Legacy ``users.role`` fallback
exists only for pre-migration ``dclab_admin`` and ``client_user`` accounts.

Customer workspace roles are canonicalized at the permission check, not by
rewriting stored membership rows:

* ``business_admin`` → ``workspace_admin``
* ``business_developer`` → ``viewer`` (read-only; never ``ml_engineer``)
* ``client_user`` keeps the existing no-membership fallback path
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    PlatformMembership,
    PlatformRole,
    User,
    UserRole,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)


class AuthorizationError(Exception):
    def __init__(self, message: str, *, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class WorkspaceAccess:
    workspace_id: UUID
    platform_role: PlatformRole | None
    workspace_role: WorkspaceRole | None


_LEGACY_WORKSPACE_ROLE = {
    WorkspaceRole.BUSINESS_ADMIN: WorkspaceRole.WORKSPACE_ADMIN,
    WorkspaceRole.BUSINESS_DEVELOPER: WorkspaceRole.VIEWER,
}

_ML_WRITE_ROLES = frozenset(
    {
        WorkspaceRole.WORKSPACE_OWNER,
        WorkspaceRole.WORKSPACE_ADMIN,
        WorkspaceRole.ML_ENGINEER,
    }
)

_MEMBER_ADMIN_ROLES = frozenset(
    {
        WorkspaceRole.WORKSPACE_OWNER,
        WorkspaceRole.WORKSPACE_ADMIN,
    }
)

BUSINESS_PLANE_USER_ROLES = frozenset(
    {
        UserRole.BUSINESS_ADMIN.value,
        UserRole.BUSINESS_DEVELOPER.value,
        UserRole.WORKSPACE_OWNER.value,
        UserRole.WORKSPACE_ADMIN.value,
        UserRole.ML_ENGINEER.value,
        UserRole.VIEWER.value,
    }
)


def canonical_workspace_role(role: WorkspaceRole | None) -> WorkspaceRole | None:
    """Translate stored membership roles onto the canonical customer model."""

    if role is None:
        return None
    return _LEGACY_WORKSPACE_ROLE.get(role, role)


def platform_role_for(db: Session, user: User) -> PlatformRole | None:
    membership = db.scalar(
        select(PlatformMembership).where(PlatformMembership.user_id == user.id)
    )
    if membership is not None:
        return PlatformRole(membership.role)
    if user.role == UserRole.DCLAB_ADMIN.value:
        return PlatformRole.DCLAB_ADMIN
    return None


def _explicit_workspace_memberships(db: Session, user: User) -> list[WorkspaceMembership]:
    return list(
        db.scalars(
            select(WorkspaceMembership)
            .where(WorkspaceMembership.user_id == user.id)
            .order_by(WorkspaceMembership.created_at, WorkspaceMembership.id)
        )
    )


def workspace_role_for(db: Session, user: User, workspace_id: UUID) -> WorkspaceRole | None:
    memberships = _explicit_workspace_memberships(db, user)
    if memberships:
        membership = next(
            (row for row in memberships if row.workspace_id == workspace_id), None
        )
        return WorkspaceRole(membership.role) if membership is not None else None
    if user.role == UserRole.CLIENT_USER.value and user.workspace_id == workspace_id:
        return WorkspaceRole.BUSINESS_ADMIN
    return None


def can_read_platform(db: Session, user: User) -> bool:
    return platform_role_for(db, user) is not None


def can_write_platform(db: Session, user: User) -> bool:
    return platform_role_for(db, user) is PlatformRole.DCLAB_ADMIN


def can_read_workspace(db: Session, user: User, workspace_id: UUID) -> bool:
    if platform_role_for(db, user) is not None:
        return True
    return workspace_role_for(db, user, workspace_id) is not None


def can_write_workspace(db: Session, user: User, workspace_id: UUID) -> bool:
    platform_role = platform_role_for(db, user)
    if platform_role is not None:
        return platform_role is PlatformRole.DCLAB_ADMIN
    return canonical_workspace_role(workspace_role_for(db, user, workspace_id)) in _ML_WRITE_ROLES


def can_perform_ml_write(db: Session, user: User, workspace_id: UUID) -> bool:
    """Customer ML writes: projects, datasets, workflows, runs, experiments."""

    return can_write_workspace(db, user, workspace_id)


def can_manage_workspace_members(db: Session, user: User, workspace_id: UUID) -> bool:
    platform_role = platform_role_for(db, user)
    if platform_role is not None:
        return platform_role is PlatformRole.DCLAB_ADMIN
    return (
        canonical_workspace_role(workspace_role_for(db, user, workspace_id))
        in _MEMBER_ADMIN_ROLES
    )


def resolve_workspace_access(
    db: Session,
    user: User,
    requested_workspace_id: UUID | None,
) -> WorkspaceAccess:
    """Resolve a workspace only after checking server-side membership.

    A supplied header is a selector, never proof of access. Platform members may
    select any existing workspace. Business members may select only a workspace
    represented by an authoritative membership row.
    """

    platform_role = platform_role_for(db, user)
    if platform_role is not None:
        workspace_id = requested_workspace_id or DEFAULT_WORKSPACE_ID
        if db.get(Workspace, workspace_id) is None:
            raise AuthorizationError("workspace not found", status_code=404)
        return WorkspaceAccess(workspace_id, platform_role, None)

    memberships = _explicit_workspace_memberships(db, user)
    if memberships:
        by_workspace = {row.workspace_id: row for row in memberships}
        if requested_workspace_id is not None:
            membership = by_workspace.get(requested_workspace_id)
            if membership is None:
                raise AuthorizationError("not authorized for this workspace")
        elif user.workspace_id in by_workspace:
            membership = by_workspace[user.workspace_id]
        elif len(memberships) == 1:
            membership = memberships[0]
        else:
            raise AuthorizationError(
                "select an authorized workspace with X-Workspace-Id",
                status_code=400,
            )
        return WorkspaceAccess(
            membership.workspace_id,
            None,
            WorkspaceRole(membership.role),
        )

    # Compatibility-only path for old accounts not yet backfilled. An explicit
    # membership anywhere disables this fallback and therefore prevents mixed
    # legacy/new authority.
    if user.role == UserRole.CLIENT_USER.value and user.workspace_id is not None:
        if (
            requested_workspace_id is not None
            and requested_workspace_id != user.workspace_id
        ):
            raise AuthorizationError("not authorized for this workspace")
        return WorkspaceAccess(
            user.workspace_id,
            None,
            WorkspaceRole.BUSINESS_ADMIN,
        )

    raise AuthorizationError("not authorized for a business workspace")
