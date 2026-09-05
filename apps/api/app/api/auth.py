from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.domain.errors import IdentityError
from app.services.auth_service import (
    AuthError,
    authenticate,
    create_access_token,
    register_customer,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=256)
    full_name: str = ""


class UserRead(BaseModel):
    id: UUID
    email: str
    role: str
    full_name: str
    workspace_id: UUID | None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


def _to_read(user: User) -> UserRead:
    return UserRead(
        id=user.id,
        email=user.email,
        role=user.role,
        full_name=user.full_name,
        workspace_id=user.workspace_id,
    )


@router.post("/register", response_model=LoginResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> LoginResponse:
    try:
        user = register_customer(
            db,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
        )
    except IdentityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    db.commit()
    db.refresh(user)
    return LoginResponse(access_token=create_access_token(user), user=_to_read(user))


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    try:
        user = authenticate(db, payload.email, payload.password)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return LoginResponse(access_token=create_access_token(user), user=_to_read(user))


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)) -> UserRead:
    return _to_read(user)
