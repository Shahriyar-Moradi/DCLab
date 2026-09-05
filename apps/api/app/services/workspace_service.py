"""Create customer workspaces and manage membership within entitlement limits."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    BusinessProfile,
    PlatformRole,
    User,
    UserRole,
    Workspace,
    WorkspaceKind,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.domain.errors import IdentityError
from app.domain.workspace_identity import WorkspaceRead
from app.services.authorization_service import (
    can_manage_workspace_members,
    platform_role_for,
)
from app.services.auth_service import create_user
from app.services.workspace_entitlement_service import (
    assert_can_add_member,
    max_members_for,
    seed_default_entitlements,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")

_USER_ROLE_FOR_MEMBERSHIP = {
    WorkspaceRole.WORKSPACE_OWNER: UserRole.WORKSPACE_OWNER,
    WorkspaceRole.WORKSPACE_ADMIN: UserRole.WORKSPACE_ADMIN,
    WorkspaceRole.ML_ENGINEER: UserRole.ML_ENGINEER,
    WorkspaceRole.VIEWER: UserRole.VIEWER,
    WorkspaceRole.BUSINESS_ADMIN: UserRole.BUSINESS_ADMIN,
    WorkspaceRole.BUSINESS_DEVELOPER: UserRole.BUSINESS_DEVELOPER,
}


def slugify(value: str, *, fallback: str = "workspace", max_length: int = 48) -> str:
    slug = _SLUG_RE.sub("-", value.lower()).strip("-")
    slug = slug[:max_length].strip("-")
    return slug or fallback


def _unique_workspace_slug(db: Session, name: str, requested: str | None) -> str:
    base = slugify(requested or name)
    candidate = base
    while db.scalar(select(Workspace.id).where(Workspace.slug == candidate)) is not None:
        suffix = uuid4().hex[:8]
        candidate = f"{base[: 64 - len(suffix) - 1]}-{suffix}"
    return candidate[:64]


def _require_workspace_write_actor(db: Session, actor: User) -> None:
    platform_role = platform_role_for(db, actor)
    if platform_role is PlatformRole.DCLAB_DEVELOPER:
        raise IdentityError(
            "platform write access requires dclab_admin",
            status_code=403,
        )


def _to_read(db: Session, workspace: Workspace) -> WorkspaceRead:
    return WorkspaceRead(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        kind=workspace.kind,
        created_at=workspace.created_at,
        max_members=max_members_for(db, workspace.id),
    )


def _create_workspace(
    db: Session,
    *,
    owner: User,
    name: str,
    kind: WorkspaceKind,
    slug: str | None,
) -> Workspace:
    _require_workspace_write_actor(db, owner)
    workspace = Workspace(
        name=name.strip(),
        slug=_unique_workspace_slug(db, name, slug),
        kind=kind.value,
    )
    db.add(workspace)
    db.flush()
    if kind is WorkspaceKind.BUSINESS:
        db.add(
            BusinessProfile(
                workspace_id=workspace.id,
                legal_name=workspace.name,
                profile_data={},
            )
        )
    db.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=owner.id,
            role=WorkspaceRole.WORKSPACE_OWNER.value,
        )
    )
    if owner.workspace_id is None and platform_role_for(db, owner) is None:
        owner.workspace_id = workspace.id
    seed_default_entitlements(db, workspace)
    db.flush()
    return workspace


def create_personal_workspace(
    db: Session,
    *,
    owner: User,
    name: str,
    slug: str | None = None,
) -> Workspace:
    return _create_workspace(
        db, owner=owner, name=name, kind=WorkspaceKind.PERSONAL, slug=slug
    )


def create_business_workspace(
    db: Session,
    *,
    owner: User,
    name: str,
    slug: str | None = None,
) -> Workspace:
    return _create_workspace(
        db, owner=owner, name=name, kind=WorkspaceKind.BUSINESS, slug=slug
    )


def workspace_to_read(db: Session, workspace: Workspace) -> WorkspaceRead:
    return _to_read(db, workspace)


def _parse_member_role(raw: str) -> WorkspaceRole:
    try:
        return WorkspaceRole(raw)
    except ValueError as exc:
        raise IdentityError(f"unknown workspace role '{raw}'") from exc


def add_workspace_member(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    email: str,
    password: str,
    role: str,
    full_name: str = "",
) -> WorkspaceMembership:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise IdentityError("workspace not found", status_code=404)
    if not can_manage_workspace_members(db, actor, workspace_id):
        raise IdentityError(
            "membership administration requires workspace_owner, workspace_admin, or dclab_admin",
            status_code=403,
        )
    membership_role = _parse_member_role(role)
    assert_can_add_member(db, workspace_id)
    normalized_email = email.strip().lower()
    existing = db.query(User).filter(User.email == normalized_email).one_or_none()
    if existing is not None:
        if platform_role_for(db, existing) is not None:
            raise IdentityError("platform members cannot be added as workspace customers")
        already = db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == existing.id,
            )
        )
        if already is not None:
            raise IdentityError("user is already a member of this workspace", status_code=409)
        membership = WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=existing.id,
            role=membership_role.value,
        )
        db.add(membership)
        db.flush()
        return membership

    user_role = _USER_ROLE_FOR_MEMBERSHIP[membership_role]
    user = create_user(
        db,
        email=normalized_email,
        password=password,
        role=user_role,
        full_name=full_name,
        workspace_id=workspace_id,
    )
    membership = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    if membership is None:
        raise IdentityError("failed to create workspace membership")
    return membership
