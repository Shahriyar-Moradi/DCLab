"""/v1 service tokens (P3.2-A): list, create (secret shown once), revoke.

Session humans only: any ``Authorization`` header is ``403 session_required``
(a service token can never mint, list or revoke tokens, and an API bearer cannot
turn itself into longer-lived machine credentials). Creating a token needs the
account password (re-authentication, login-throttled) and an explicit ML-write
membership of the workspace; workspace admins see and revoke every token of the
workspace, other members their own (anyone else's is ``404``). Transport only:
``service_token_service`` owns the rules.
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id, require_session_workspace_member
from app.api.v1 import _created, _key_scope, _keyed_command, _not_found
from app.api.v1_conventions import (
    COMMON_ERROR_STATUSES,
    REPLAY_HEADER_DOC,
    V1APIError,
    domain_error,
    error_responses,
    idempotency_binding,
    idempotency_key_header,
)
from app.db.models import ServiceToken, User
from app.db.session import get_db
from app.domain.errors import IdentityError, ServiceTokenError, ServiceTokenNotFoundError
from app.domain.idempotency import RESOURCE_SERVICE_TOKEN
from app.domain.service_tokens import ServiceTokenCreatedRead, ServiceTokenCreateRequest, ServiceTokenRead
from app.services.login_throttle import ThrottleError, check_login_throttle, client_ip, record_login_failure
from app.services.request_ids import request_id_of
from app.services.service_token_service import (
    create_service_token,
    list_service_tokens,
    revoke_service_token,
    token_read,
)

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))

_CREATE = "POST /v1/service-tokens"


@router.get("/service-tokens", response_model=list[ServiceTokenRead])
def read_service_tokens(
    request: Request,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
) -> list[ServiceTokenRead]:
    """Tokens of the selected workspace, newest first (admins: all; others: their own)."""

    try:
        rows = list_service_tokens(db, actor=user, workspace_id=request_workspace_id(request))
    except IdentityError as exc:
        raise domain_error(exc) from exc
    return [token_read(row) for row in rows]


@router.post(
    "/service-tokens",
    response_model=ServiceTokenCreatedRead,
    status_code=201,
    responses={201: {"headers": REPLAY_HEADER_DOC}, **error_responses(409, 429)},
)
def create_service_token_v1(
    payload: ServiceTokenCreateRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> ServiceTokenCreatedRead:
    """Create a token for the selected workspace. ``secret`` is returned once, by this
    response only (an ``Idempotency-Key`` replay answers the token with ``secret: null``).
    Needs ``current_password`` (``403 reauthentication_failed``) and an ML-write role
    as a member of the workspace (``403 token_creation_not_permitted``). Requires
    ``Idempotency-Key``; the password is never part of its digest."""

    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(
        operation=_CREATE, principal_id=user.id, header_key=idempotency_key,
        body=payload.model_dump(mode="json", exclude={"current_password"}), required=True,
    )
    ip = client_ip(request)
    try:
        check_login_throttle(user.email, ip)
    except ThrottleError as exc:
        raise V1APIError(429, "rate_limited", "too many attempts; retry later", retryable=True) from exc
    minted: dict[str, str] = {}

    def load(resource_id: Any) -> ServiceToken | None:
        return db.scalar(
            select(ServiceToken).where(ServiceToken.id == resource_id, ServiceToken.workspace_id == workspace_id)
        )

    def execute(bind: Callable[[Any], None]) -> ServiceToken:
        row, raw = create_service_token(
            db, creator=user, workspace_id=workspace_id, name=payload.name, scopes=payload.scopes,
            expires_in_days=payload.expires_in_days,
            current_password=payload.current_password.get_secret_value(), request_id=request_id_of(request),
        )
        bind(row.id)
        db.commit()
        minted["secret"] = raw
        return row

    try:
        row, status, replayed = _keyed_command(
            db, _key_scope(request, user, _CREATE), binding,
            resource_kind=RESOURCE_SERVICE_TOKEN, load=load, execute=execute,
        )
    except (IdentityError, ServiceTokenError) as exc:
        db.rollback()
        if getattr(exc, "code", None) == "reauthentication_failed":
            record_login_failure(user.email, ip)
        raise domain_error(exc) from exc
    _created(response, status, replayed, etag=None, location=None)
    response.headers["Cache-Control"] = "no-store"
    return ServiceTokenCreatedRead(**token_read(row).model_dump(), secret=None if replayed else minted.get("secret"))


@router.post(
    "/service-tokens/{token_id}/revoke",
    response_model=ServiceTokenRead,
    responses=error_responses(409),
)
def revoke_service_token_v1(
    token_id: UUID,
    request: Request,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> ServiceTokenRead:
    """Revoke now (final; the token is refused from its next request). Replay-safe by
    state: revoking a revoked token returns it unchanged."""

    del idempotency_key  # validated by the dependency; revocation is idempotent by state
    try:
        row = revoke_service_token(
            db, actor=user, workspace_id=request_workspace_id(request), token_id=token_id,
            request_id=request_id_of(request),
        )
    except ServiceTokenNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except IdentityError as exc:
        raise domain_error(exc) from exc
    db.commit()
    return token_read(row)
