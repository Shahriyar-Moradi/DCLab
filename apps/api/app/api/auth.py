from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_user,
    parse_requested_workspace_id,
    require_browser_session,
    session_credential,
)
from app.config import get_settings
from app.db.models import AuthSession, User
from app.db.session import get_db
from app.domain.application_api import PrincipalWorkspaceRead
from app.domain.errors import IdentityError
from app.services.auth_metrics import record_auth_event
from app.services.auth_service import (
    AuthError,
    authenticate,
    create_access_token,
    register_customer,
)
from app.services.csrf_service import (
    attach_csrf_cookie,
    clear_csrf_cookie,
    csrf_token_for_session,
    generate_anonymous_csrf_token,
)
from app.services.login_throttle import (
    ThrottleError,
    check_login_throttle,
    client_ip,
    record_login_failure,
    record_login_success,
)
from app.services.recovery_service import (
    PURPOSE_EMAIL_VERIFICATION,
    PURPOSE_PASSWORD_RESET,
    confirm_email_verification,
    confirm_password_reset,
    request_recovery,
)
from app.services.session_service import (
    attach_session_cookie,
    clear_session_cookie,
    issue_session,
    revoke_session_token,
    revoke_sessions_for_user,
    user_from_session,
)
from app.services.authorization_service import AuthorizationError
from app.services.workspace_selection_service import persist_session_workspace, principal_read

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=256)
    full_name: str = ""


class RecoveryRequest(BaseModel):
    email: str


class PasswordResetConfirmRequest(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=256)


class EmailVerificationConfirmRequest(BaseModel):
    token: str


class UserRead(BaseModel):
    id: UUID
    email: str
    role: str
    full_name: str
    workspace_id: UUID | None
    email_verified_at: datetime | None = None
    active_workspace_id: UUID | None = None
    workspaces: list[PrincipalWorkspaceRead] = Field(default_factory=list)
    request_id: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


class SessionResponse(BaseModel):
    user: UserRead


class SessionInfo(BaseModel):
    user: UserRead
    session_id: UUID
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime


class CsrfResponse(BaseModel):
    csrf_token: str


class SelectWorkspaceRequest(BaseModel):
    workspace_id: UUID | None = None


def _to_read(db: Session, user: User, request: Request) -> UserRead:
    parse_requested_workspace_id(request)
    principal = principal_read(db, user, request)
    return UserRead(
        **principal.model_dump(),
        email_verified_at=user.email_verified_at,
    )


def _session_info(db: Session, user: User, request: Request, row: AuthSession) -> SessionInfo:
    request.state.auth_session = row
    return SessionInfo(
        user=_to_read(db, user, request),
        session_id=row.id,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        idle_expires_at=row.idle_expires_at,
        absolute_expires_at=row.absolute_expires_at,
    )


def _authenticate_posted(
    db: Session, request: Request, email: str, password: str
) -> User:
    ip = client_ip(request)
    try:
        check_login_throttle(email, ip)
    except ThrottleError as exc:
        record_auth_event("login", "throttled")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    try:
        user = authenticate(db, email, password)
    except AuthError as exc:
        record_login_failure(email, ip)
        record_auth_event("login", "invalid_credentials")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    record_login_success(email, ip)
    record_auth_event("login", "success")
    return user


def _require_browser_sessions() -> None:
    if not get_settings().auth_browser_sessions_enabled:
        record_auth_event("session", "kill_switch")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="browser sessions are disabled",
        )


def _issue_browser_session(
    db: Session,
    user: User,
    request: Request,
    response: Response,
) -> SessionResponse:
    settings = get_settings()
    existing = request.cookies.get(settings.session_cookie_name)
    _row, raw = issue_session(
        db,
        user,
        replace_raw=existing,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    request.state.auth_session = _row
    attach_session_cookie(response, raw, settings)
    attach_csrf_cookie(response, csrf_token_for_session(raw, settings), settings)
    return SessionResponse(user=_to_read(db, user, request))


def _clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    clear_session_cookie(response, settings)
    clear_csrf_cookie(response, settings)


@router.get("/csrf", response_model=CsrfResponse)
def issue_csrf(request: Request, response: Response) -> CsrfResponse:
    settings = get_settings()
    raw = session_credential(request)
    if raw:
        token = csrf_token_for_session(raw, settings)
    else:
        token = request.cookies.get(settings.csrf_cookie_name) or generate_anonymous_csrf_token()
    attach_csrf_cookie(response, token, settings)
    return CsrfResponse(csrf_token=token)


@router.post("/register", response_model=SessionResponse)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> SessionResponse:
    _require_browser_sessions()
    try:
        user = register_customer(
            db,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
        )
    except IdentityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _issue_browser_session(db, user, request, response)


@router.post("/login", response_model=SessionResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> SessionResponse:
    _require_browser_sessions()
    user = _authenticate_posted(db, request, payload.email, payload.password)
    return _issue_browser_session(db, user, request, response)


@router.post("/tokens", response_model=TokenResponse)
def issue_tokens(
    payload: LoginRequest, request: Request, db: Session = Depends(get_db)
) -> TokenResponse:
    try:
        user = _authenticate_posted(db, request, payload.email, payload.password)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            exc.headers = {"WWW-Authenticate": "Bearer"}
        raise
    return TokenResponse(access_token=create_access_token(user), user=_to_read(db, user, request))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> Response:
    raw = session_credential(request)
    if raw:
        revoke_session_token(db, raw.strip())
        db.commit()
    _clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    revoke_sessions_for_user(db, user.id)
    db.commit()
    _clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/password-reset/request", status_code=status.HTTP_204_NO_CONTENT)
def password_reset_request(
    payload: RecoveryRequest, db: Session = Depends(get_db)
) -> Response:
    request_recovery(db, payload.email, PURPOSE_PASSWORD_RESET)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def password_reset_confirm(
    payload: PasswordResetConfirmRequest, db: Session = Depends(get_db)
) -> Response:
    try:
        confirm_password_reset(db, payload.token, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/email-verification/request", status_code=status.HTTP_204_NO_CONTENT)
def email_verification_request(
    payload: RecoveryRequest, db: Session = Depends(get_db)
) -> Response:
    request_recovery(db, payload.email, PURPOSE_EMAIL_VERIFICATION)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/email-verification/confirm", status_code=status.HTTP_204_NO_CONTENT)
def email_verification_confirm(
    payload: EmailVerificationConfirmRequest, db: Session = Depends(get_db)
) -> Response:
    try:
        confirm_email_verification(db, payload.token)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserRead)
def me(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserRead:
    return _to_read(db, user, request)


@router.put("/workspace", response_model=UserRead)
def select_workspace(
    payload: SelectWorkspaceRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: tuple[User, AuthSession] = Depends(require_browser_session),
) -> UserRead:
    user, row = actor
    try:
        persist_session_workspace(db, row, user, payload.workspace_id)
    except AuthorizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    db.commit()
    request.state.auth_session = row
    return _to_read(db, user, request)


@router.get("/session", response_model=SessionInfo)
def current_session(
    request: Request, db: Session = Depends(get_db)
) -> SessionInfo:
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
    db.commit()
    return _session_info(db, user, request, row)
