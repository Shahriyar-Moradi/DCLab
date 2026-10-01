from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AuthSession, User
from app.db.session import get_db
from app.services.auth_service import AuthError, user_from_token
from app.services.session_service import SESSION_HEADER, user_from_session
from app.services.authorization_service import (
    AuthorizationError,
    WorkspaceAccess,
    resolve_workspace_access,
    workspace_is_selectable,
)
from app.services.workspace_capability_service import (
    APPLICATION_ACCESS,
    BUSINESS_ACCESS,
    DEVELOPMENT_ACCESS,
    PLATFORM_READ,
    PLATFORM_WRITE,
    WORKSPACE_EXECUTE_ML,
    WORKSPACE_READ,
    WORKSPACE_WRITE,
    effective_capability_matrix,
)
from app.services.workspace_access_metrics import record_workspace_access_event


def _deny_workspace(
    request: Request,
    status_code: int,
    detail: str,
    reason: str,
    *,
    user: User | None = None,
    workspace_id: uuid.UUID | None = None,
) -> None:
    record_workspace_access_event(
        request,
        "denial",
        reason,
        actor_id=user.id if user is not None else None,
        workspace_id=workspace_id,
    )
    raise HTTPException(status_code=status_code, detail=detail)


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def session_credential(request: Request) -> str | None:
    header = (request.headers.get(SESSION_HEADER) or "").strip()
    if header:
        return header
    cookie = request.cookies.get(get_settings().session_cookie_name)
    if cookie:
        return cookie.strip()
    return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    bearer = _bearer_token(request)
    if bearer:
        try:
            return user_from_token(db, bearer)
        except AuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
    raw = session_credential(request)
    if raw:
        try:
            user, row = user_from_session(db, raw)
        except AuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
            ) from exc
        request.state.auth_session = row
        db.commit()
        return user
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_browser_session(
    request: Request, db: Session = Depends(get_db)
) -> tuple[User, AuthSession]:
    if _bearer_token(request):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="bearer clients must send X-Workspace-Id; session workspace selection is browser-only",
        )
    raw = session_credential(request)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing session",
        )
    try:
        user, row = user_from_session(db, raw)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    request.state.auth_session = row
    db.commit()
    return user, row


def require_platform_read(
    request: Request,
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> User:
    if not effective_capability_matrix(db, user, None)[PLATFORM_READ]:
        _deny_workspace(
            request, status.HTTP_403_FORBIDDEN,
            "this area is restricted to DCLab platform members",
            "capability_denied", user=user,
        )
    return user


def require_platform_admin(
    request: Request,
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> User:
    if not effective_capability_matrix(db, user, None)[PLATFORM_WRITE]:
        _deny_workspace(
            request, status.HTTP_403_FORBIDDEN,
            "platform write access requires dclab_admin",
            "capability_denied", user=user,
        )
    return user


def require_business_administration(
    request: Request,
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> User:
    if effective_capability_matrix(db, user, None)[BUSINESS_ACCESS]:
        return user
    _deny_workspace(
        request, status.HTTP_403_FORBIDDEN,
        "this area is restricted to Business administration members",
        "capability_denied", user=user,
    )


def parse_requested_workspace_id(request: Request) -> uuid.UUID | None:
    values = request.headers.getlist("X-Workspace-Id")
    if not values:
        request.state.requested_workspace_id = None
        return None
    if len(values) != 1 or not values[0].strip():
        record_workspace_access_event(request, "denial", "malformed_selector")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Workspace-Id must be a UUID",
        )
    try:
        parsed = uuid.UUID(values[0].strip())
    except ValueError as exc:
        record_workspace_access_event(request, "denial", "malformed_selector")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Workspace-Id must be a UUID",
        ) from exc
    request.state.requested_workspace_id = parsed
    return parsed


def _requested_workspace_id(request: Request) -> uuid.UUID | None:
    return parse_requested_workspace_id(request)


def _session_selected_workspace_id(
    request: Request, db: Session, user: User
) -> uuid.UUID | None:
    row = getattr(request.state, "auth_session", None)
    if not isinstance(row, AuthSession) or row.selected_workspace_id is None:
        return None
    if workspace_is_selectable(db, user, row.selected_workspace_id):
        return row.selected_workspace_id
    return None


def _workspace_access(request: Request, db: Session, user: User) -> WorkspaceAccess:
    header = _requested_workspace_id(request)
    if _bearer_token(request) and header is None:
        _deny_workspace(
            request, status.HTTP_400_BAD_REQUEST,
            "bearer workspace requests require X-Workspace-Id",
            "missing_selector", user=user,
        )
    requested = header if header is not None else _session_selected_workspace_id(
        request, db, user
    )
    try:
        access = resolve_workspace_access(db, user, requested)
    except AuthorizationError as exc:
        record_workspace_access_event(
            request, "denial",
            {400: "unavailable_selection", 403: "unauthorized_selector", 404: "unavailable_selection"}.get(
                exc.status_code, "other"
            ),
            actor_id=user.id,
            workspace_id=requested,
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    path_workspace = request.path_params.get("workspace_id")
    if path_workspace is not None:
        try:
            path_workspace_id = uuid.UUID(str(path_workspace))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="workspace_id must be a UUID",
            ) from exc
        if path_workspace_id != access.workspace_id:
            _deny_workspace(
                request, status.HTTP_404_NOT_FOUND, "not found", "path_mismatch",
                user=user, workspace_id=access.workspace_id,
            )
    request.state.workspace_access = access
    return access


def require_workspace_read(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    access = _workspace_access(request, db, user)
    if not effective_capability_matrix(db, user, access.workspace_id)[WORKSPACE_READ]:
        _deny_workspace(
            request, status.HTTP_403_FORBIDDEN,
            "workspace read access is not permitted", "capability_denied",
            user=user, workspace_id=access.workspace_id,
        )
    return user


def require_workspace_admin(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    access = _workspace_access(request, db, user)
    if not effective_capability_matrix(db, user, access.workspace_id)[WORKSPACE_WRITE]:
        _deny_workspace(
            request, status.HTTP_403_FORBIDDEN,
            "workspace write access requires business_admin or dclab_admin",
            "capability_denied", user=user, workspace_id=access.workspace_id,
        )
    return user


def require_workspace_ml_execution(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    access = _workspace_access(request, db, user)
    if not effective_capability_matrix(db, user, access.workspace_id)[WORKSPACE_EXECUTE_ML]:
        _deny_workspace(
            request, status.HTTP_403_FORBIDDEN,
            "workspace ML execution access is not permitted", "capability_denied",
            user=user, workspace_id=access.workspace_id,
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
    if request.method in _READ_METHODS:
        return require_platform_read(request, user, db)
    return require_platform_admin(request, user, db)


def require_client(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    access = _workspace_access(request, db, user)
    capabilities = effective_capability_matrix(db, user, access.workspace_id)
    if not capabilities[APPLICATION_ACCESS]:
        _deny_workspace(
            request, status.HTTP_403_FORBIDDEN,
            "Personal Development accounts use the Development workspace",
            "capability_denied", user=user, workspace_id=access.workspace_id,
        )
    if request.method in _READ_METHODS:
        return user
    if not capabilities[WORKSPACE_WRITE]:
        _deny_workspace(
            request, status.HTTP_403_FORBIDDEN,
            "workspace write access requires business_admin or dclab_admin",
            "capability_denied", user=user, workspace_id=access.workspace_id,
        )
    return user


def require_development(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    access = _workspace_access(request, db, user)
    capabilities = effective_capability_matrix(db, user, access.workspace_id)
    if not capabilities[DEVELOPMENT_ACCESS]:
        _deny_workspace(
            request, status.HTTP_403_FORBIDDEN,
            "Development workspace access is not permitted",
            "capability_denied", user=user, workspace_id=access.workspace_id,
        )
    if request.method not in _READ_METHODS and not capabilities[WORKSPACE_EXECUTE_ML]:
        _deny_workspace(
            request, status.HTTP_403_FORBIDDEN,
            "workspace ML execution access is not permitted",
            "capability_denied", user=user, workspace_id=access.workspace_id,
        )
    return user
