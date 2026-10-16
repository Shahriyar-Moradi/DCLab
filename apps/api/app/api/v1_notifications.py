"""/v1 notifications (P8.8-A): your email preferences, one-click unsubscribe, webhook endpoints.

Transport only (``notification_service``, ``webhook_service``). Preferences and webhooks are for
signed-in sessions only: any ``Authorization`` header is ``403 session_required`` (no service token,
API bearer or agent can read or change them). Preferences are the caller's own in the selected
workspace. Webhook management needs an owner or administrator membership (``403
webhooks_not_permitted``); another workspace is 404. ``POST /v1/notifications/unsubscribe`` needs
no sign-in: the signed, expiring token from an email is its only authority and it can only turn
one kind of email off for the person it was sent to (invalid or expired: ``400
invalid_unsubscribe_link``; the same answer whatever was wrong).
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id, require_session_workspace_member
from app.api.v1 import _created, _key_scope, _keyed_command
from app.api.v1_conventions import (
    COMMON_ERROR_STATUSES,
    REPLAY_HEADER_DOC,
    V1APIError,
    error_responses,
    idempotency_binding,
    idempotency_key_header,
)
from app.db.models import User, WebhookEndpoint
from app.db.session import get_db
from app.domain.notifications import (
    NotificationPreferencesRead,
    NotificationPreferencesWrite,
    UnsubscribeRead,
    UnsubscribeRequest,
    WebhookCreate,
    WebhookDeliveryRead,
    WebhookRead,
    WebhookSecretRead,
    WebhookTestRead,
    WebhookUpdate,
)
from app.services import webhook_service as webhooks
from app.services.notification_service import NotificationError, preferences_read, save_preferences, unsubscribe

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))

_CREATE = "POST /v1/workspaces/{workspace_id}/webhooks"
_ROTATE = "POST /v1/workspaces/{ws}/webhooks/{id}/rotate-secret"  # label (the key table caps its length)
_RESOURCE = "webhook_endpoint"


def _error(exc: NotificationError) -> V1APIError:
    return V1APIError(exc.status, exc.code, exc.message)


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization, Cookie"


@router.get("/me/notification-preferences", response_model=NotificationPreferencesRead)
def read_notification_preferences(
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
) -> NotificationPreferencesRead:
    """Your email choices for each notification kind in the selected workspace (defaults: decisions
    waiting, failed runs, drift alerts and usage warnings on; finished runs and weekly scoring
    results off), whether your role receives each kind at all, and whether this server sends email
    (`email_delivery`: `off` with EMAIL_PROVIDER=none, `paused` with the kill switch). Signed-in
    sessions only. Never cached."""

    _private(response)
    return preferences_read(db, actor=user, workspace_id=request_workspace_id(request))


@router.put("/me/notification-preferences", response_model=NotificationPreferencesRead,
            responses=error_responses(409))
def save_notification_preferences(
    payload: NotificationPreferencesWrite,
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> NotificationPreferencesRead:
    """Turn email on or off for one or more kinds (yours only; kinds not listed keep their value).
    Idempotent by state: an `Idempotency-Key` is validated and a replay changes nothing."""

    del idempotency_key
    workspace_id = request_workspace_id(request)
    try:
        save_preferences(db, actor=user, workspace_id=workspace_id, request=payload)
    except NotificationError as exc:
        db.rollback()
        raise _error(exc) from exc
    db.commit()
    _private(response)
    return preferences_read(db, actor=user, workspace_id=workspace_id)


@router.post("/notifications/unsubscribe", response_model=UnsubscribeRead, responses=error_responses(409))
def unsubscribe_v1(
    payload: UnsubscribeRequest,
    response: Response,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> UnsubscribeRead:
    """One-click unsubscribe from an email link (`/unsubscribe#token=…`; the token travels in the
    URL fragment, never to a server log). No sign-in and no session needed: the signed token
    (30 days) can only turn off email of its one kind for the person and workspace it was made
    for. Idempotent by state."""

    del idempotency_key
    try:
        kind = unsubscribe(db, payload.token)
    except NotificationError as exc:
        raise _error(exc) from exc
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return UnsubscribeRead(kind=kind)


@router.get("/workspaces/{workspace_id}/webhooks", response_model=list[WebhookRead])
def read_webhooks(
    workspace_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
) -> list[WebhookRead]:
    """The workspace's webhook endpoints (at most 5) with their last delivery. Never a secret.
    Owners and administrators on a signed-in session only."""

    del workspace_id  # matched to the caller's selected workspace by the dependency
    try:
        body = webhooks.list_webhooks(db, actor=user, workspace_id=request_workspace_id(request))
    except NotificationError as exc:
        raise _error(exc) from exc
    _private(response)
    return body


def _load(db: Session, workspace_id: UUID) -> Callable[[Any], WebhookEndpoint | None]:
    def load(resource_id: Any) -> WebhookEndpoint | None:
        return db.scalar(select(WebhookEndpoint).where(WebhookEndpoint.workspace_id == workspace_id,
                                                       WebhookEndpoint.id == resource_id))

    return load


@router.post("/workspaces/{workspace_id}/webhooks", response_model=WebhookSecretRead, status_code=201,
             responses={201: {"headers": REPLAY_HEADER_DOC}, **error_responses(409)})
def create_webhook_v1(
    workspace_id: UUID,
    payload: WebhookCreate,
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> WebhookSecretRead:
    """Add a webhook endpoint (https, port 443 unless the platform allows others; private and
    local destinations are refused when added and again at every delivery). `secret` (for the
    `X-DCLab-Signature` check) is returned by this response only; a replay of the
    `Idempotency-Key` returns `secret: null`. Requires `Idempotency-Key`. 409
    `webhooks_not_configured` when this server has no WEBHOOK_SECRET_KEY, `webhook_limit_reached`
    at 5 endpoints."""

    del workspace_id
    ws = request_workspace_id(request)
    binding = idempotency_binding(operation=_CREATE, principal_id=user.id, header_key=idempotency_key,
                                  body=payload.model_dump(mode="json"), required=True)

    def execute(bind: Callable[[Any], None]) -> WebhookEndpoint:
        row = webhooks.create_webhook(db, actor=user, workspace_id=ws, request=payload)
        bind(row.id)
        db.commit()
        return row

    try:
        webhooks.require_manager(db, user, ws)  # before any replay: a demoted person gets 403
        row, status, replayed = _keyed_command(db, _key_scope(request, user, _CREATE), binding,
                                               resource_kind=_RESOURCE, load=_load(db, ws), execute=execute)
    except NotificationError as exc:
        db.rollback()
        raise _error(exc) from exc
    _created(response, status, replayed, etag=None, location=None)
    response.headers["Cache-Control"] = "no-store"
    return WebhookSecretRead(**webhooks.webhook_read(db, row).model_dump(),
                             secret=None if replayed else webhooks.webhook_secret(row))


@router.patch("/workspaces/{workspace_id}/webhooks/{webhook_id}", response_model=WebhookRead,
              responses=error_responses(409))
def update_webhook_v1(
    workspace_id: UUID,
    webhook_id: UUID,
    payload: WebhookUpdate,
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> WebhookRead:
    """Change the name, URL or events, or turn the endpoint off / on (`enabled`; turning it on also
    clears a paused delivery breaker). Endpoints are turned off, never deleted (the delivery log and
    change trail stay). Every URL, events, on / off change is recorded with who made it. Idempotent
    by state."""

    del workspace_id, idempotency_key
    try:
        webhooks.require_manager(db, user, request_workspace_id(request))
        row = webhooks.update_webhook(db, actor=user, workspace_id=request_workspace_id(request),
                                      webhook_id=webhook_id, request=payload)
    except NotificationError as exc:
        db.rollback()
        raise _error(exc) from exc
    db.commit()
    _private(response)
    return webhooks.webhook_read(db, row)


@router.post("/workspaces/{workspace_id}/webhooks/{webhook_id}/rotate-secret", response_model=WebhookSecretRead,
             responses={200: {"headers": REPLAY_HEADER_DOC}, **error_responses(409)})
def rotate_webhook_secret_v1(
    workspace_id: UUID,
    webhook_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> WebhookSecretRead:
    """Replace the signing secret now (the old one stops verifying at once); the new `secret` is
    returned by this response only (a replay of the `Idempotency-Key` returns `secret: null` and
    rotates nothing). Requires `Idempotency-Key`."""

    del workspace_id
    ws = request_workspace_id(request)
    binding = idempotency_binding(operation=_ROTATE, principal_id=user.id, header_key=idempotency_key,
                                  path_params={"webhook_id": webhook_id}, body={}, required=True)

    def execute(bind: Callable[[Any], None]) -> WebhookEndpoint:
        row = webhooks.rotate_secret(db, actor=user, workspace_id=ws, webhook_id=webhook_id)
        bind(row.id)
        db.commit()
        return row

    try:
        webhooks.require_manager(db, user, ws)  # before any replay: a demoted person gets 403
        row, status, replayed = _keyed_command(db, _key_scope(request, user, _ROTATE), binding,
                                               resource_kind=_RESOURCE, load=_load(db, ws), execute=execute,
                                               status=200)
    except NotificationError as exc:
        db.rollback()
        raise _error(exc) from exc
    _created(response, status, replayed, etag=None, location=None)
    response.headers["Cache-Control"] = "no-store"
    return WebhookSecretRead(**webhooks.webhook_read(db, row).model_dump(),
                             secret=None if replayed else webhooks.webhook_secret(row))


@router.post("/workspaces/{workspace_id}/webhooks/{webhook_id}/test", response_model=WebhookTestRead,
             status_code=202, responses=error_responses(409))
def test_webhook_v1(
    workspace_id: UUID,
    webhook_id: UUID,
    request: Request,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> WebhookTestRead:
    """Queue a signed `ping` to the endpoint (at most one per minute, a repeat within the minute
    returns the same one; at most 6 per hour: 409 `ping_limit_reached`). The worker tries it once;
    the result appears in the delivery log."""

    del workspace_id, idempotency_key
    try:
        webhooks.require_manager(db, user, request_workspace_id(request))
        body = webhooks.send_test(db, actor=user, workspace_id=request_workspace_id(request), webhook_id=webhook_id)
    except NotificationError as exc:
        db.rollback()
        raise _error(exc) from exc
    db.commit()
    return body


@router.get("/workspaces/{workspace_id}/webhooks/{webhook_id}/deliveries", response_model=list[WebhookDeliveryRead])
def read_webhook_deliveries(
    workspace_id: UUID,
    webhook_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
) -> list[WebhookDeliveryRead]:
    """The endpoint's newest 50 delivery attempts: event, attempt, HTTP status, a closed error code
    and the duration. Response bodies are never stored."""

    del workspace_id
    try:
        body = webhooks.list_deliveries(db, actor=user, workspace_id=request_workspace_id(request),
                                        webhook_id=webhook_id)
    except NotificationError as exc:
        raise _error(exc) from exc
    _private(response)
    return body
