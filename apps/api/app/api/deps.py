from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.models import User, UserRole
from app.db.session import get_db
from app.services.auth_service import AuthError, user_from_token


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


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Guard for the /admin tree. Attached at router level so every admin route
    inherits it — a new endpoint cannot be added without this check."""
    if user.role != UserRole.DCLAB_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this area is restricted to DCLab administrators",
        )
    return user


def require_client(user: User = Depends(get_current_user)) -> User:
    """Guard for the /app tree. Admins are allowed through so DCLab staff can
    support an account, but a client user can never reach the admin tree."""
    if user.role not in {UserRole.CLIENT_USER.value, UserRole.DCLAB_ADMIN.value}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not authorized")
    return user
