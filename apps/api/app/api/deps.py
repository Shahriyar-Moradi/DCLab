from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.models import User, UserRole
from app.db.session import get_db
from app.services.auth_service import AuthError, user_from_token
from app.services.authorization_service import (
    AuthorizationError,
    WorkspaceAccess,
    can_execute_workspace_ml,
    can_read_platform,
    can_write_platform,
    can_write_workspace,
    platform_role_for,
    resolve_workspace_access,
)


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _bearer_token(request)
    try:
        return user_from_token(db, token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_platform_read(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> User:
    if not can_read_platform(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this area is restricted to DCLab platform members",
        )
    return user


def require_platform_admin(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> User:
    if not can_write_platform(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="platform write access requires dclab_admin",
        )
    return user


def require_business_administration(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> User:
    if platform_role_for(db, user) is not None or user.role in {
        UserRole.BUSINESS_ADMIN.value,
        UserRole.BUSINESS_DEVELOPER.value,
    }:
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="this area is restricted to Business administration members",
    )


def _requested_workspace_id(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("X-Workspace-Id")
    if not raw:
        return None
    try:
        return uuid.UUID(raw.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Workspace-Id must be a UUID",
        ) from exc


def _workspace_access(request: Request, db: Session, user: User) -> WorkspaceAccess:
    try:
        access = resolve_workspace_access(db, user, _requested_workspace_id(request))
    except AuthorizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    request.state.workspace_access = access
    return access


def require_workspace_read(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    _workspace_access(request, db, user)
    return user


def require_workspace_admin(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    access = _workspace_access(request, db, user)
    if not can_write_workspace(db, user, access.workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace write access requires business_admin or dclab_admin",
        )
    return user


def require_workspace_ml_execution(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Authorize one shared ML-core mutation in the selected workspace.

    This permission is deliberately distinct from workspace/business administration.
    Business Developers and Personal Developers can build and run ML workloads without
    gaining organization-management authority.
    """
    access = _workspace_access(request, db, user)
    if not can_execute_workspace_ml(db, user, access.workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace ML execution access is not permitted",
        )
    return user


def request_workspace_id(request: Request) -> uuid.UUID:
    access = getattr(request.state, "workspace_access", None)
    if not isinstance(access, WorkspaceAccess):
        raise RuntimeError("workspace authorization dependency was not evaluated")
    return access.workspace_id


def request_workspace_access(request: Request) -> WorkspaceAccess:
    access = getattr(request.state, "workspace_access", None)
    if not isinstance(access, WorkspaceAccess):
        raise RuntimeError("workspace authorization dependency was not evaluated")
    return access


_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def require_admin(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Compatibility name for the method-aware /admin tree guard."""
    if request.method in _READ_METHODS:
        return require_platform_read(user, db)
    return require_platform_admin(user, db)


def require_client(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Compatibility name for the method-aware, tenant-aware /app guard."""
    if request.method in _READ_METHODS:
        return require_workspace_read(request, user, db)
    return require_workspace_admin(request, user, db)
