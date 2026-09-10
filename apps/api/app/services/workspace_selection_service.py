"""Active-workspace selector payloads. Membership checks stay in authorization_service."""

from __future__ import annotations

from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuthSession, User, UserRole, Workspace
from app.domain.application_api import PrincipalRead, PrincipalWorkspaceRead
from app.services.authorization_service import (
    AuthorizationError,
    active_workspace_memberships,
    default_workspace_id,
    platform_role_for,
    workspace_is_selectable,
)
from app.services.request_ids import request_id_of


def _membership_role_map(db: Session, user: User) -> dict[UUID, str]:
    return {
        row.workspace_id: row.role for row in active_workspace_memberships(db, user)
    }


def list_selectable_workspaces(
    db: Session, user: User
) -> list[PrincipalWorkspaceRead]:
    roles = _membership_role_map(db, user)
    if platform_role_for(db, user) is not None:
        rows = list(db.scalars(select(Workspace).order_by(Workspace.name, Workspace.id)))
        return [
            PrincipalWorkspaceRead(
                id=row.id,
                slug=row.slug,
                name=row.name,
                kind=row.kind,
                role=roles.get(row.id),
            )
            for row in rows
        ]

    memberships = active_workspace_memberships(db, user)
    if memberships:
        workspace_ids = [row.workspace_id for row in memberships]
        workspaces = {
            row.id: row
            for row in db.scalars(select(Workspace).where(Workspace.id.in_(workspace_ids)))
        }
        ordered = sorted(
            (workspaces[membership.workspace_id] for membership in memberships if membership.workspace_id in workspaces),
            key=lambda row: (row.name, str(row.id)),
        )
        return [
            PrincipalWorkspaceRead(
                id=row.id,
                slug=row.slug,
                name=row.name,
                kind=row.kind,
                role=roles.get(row.id),
            )
            for row in ordered
        ]

    if user.role == UserRole.CLIENT_USER.value and user.workspace_id is not None:
        home = db.get(Workspace, user.workspace_id)
        if home is not None:
            return [
                PrincipalWorkspaceRead(
                    id=home.id,
                    slug=home.slug,
                    name=home.name,
                    kind=home.kind,
                    role="business_admin",
                )
            ]
    return []


def session_selected_workspace_id(request: Request) -> UUID | None:
    row = getattr(request.state, "auth_session", None)
    if not isinstance(row, AuthSession):
        return None
    return row.selected_workspace_id


def requested_workspace_header(request: Request) -> UUID | None:
    return getattr(request.state, "requested_workspace_id", None)


def effective_active_workspace_id(
    db: Session,
    user: User,
    *,
    requested: UUID | None,
    session_selected: UUID | None,
) -> UUID | None:
    for candidate in (requested, session_selected, default_workspace_id(db, user)):
        if candidate is not None and workspace_is_selectable(db, user, candidate):
            return candidate
    return None


def persist_session_workspace(
    db: Session,
    row: AuthSession,
    user: User,
    workspace_id: UUID | None,
) -> None:
    if workspace_id is None:
        row.selected_workspace_id = None
        return
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise AuthorizationError("workspace not found", status_code=404)
    if not workspace_is_selectable(db, user, workspace_id):
        raise AuthorizationError("not authorized for this workspace")
    row.selected_workspace_id = workspace_id


def principal_read(db: Session, user: User, request: Request) -> PrincipalRead:
    requested = requested_workspace_header(request)
    active = effective_active_workspace_id(
        db,
        user,
        requested=requested,
        session_selected=session_selected_workspace_id(request),
    )
    return PrincipalRead(
        id=user.id,
        email=user.email,
        role=user.role,
        full_name=user.full_name,
        workspace_id=user.workspace_id,
        active_workspace_id=active,
        workspaces=list_selectable_workspaces(db, user),
        request_id=request_id_of(request),
    )
