"""Webhook endpoints (P8.8-A): create, list, change, rotate the secret, send a test, read deliveries.

Authority: an explicit, unsuspended ``workspace_owner`` / ``workspace_admin`` membership of the
workspace, on a signed-in session (the routes refuse any ``Authorization`` header); viewers,
ML engineers, legacy client accounts, platform staff, service tokens and agents are refused.
Every query filters by workspace (another workspace's endpoint is 404). At most
``WEBHOOKS_PER_WORKSPACE_MAX`` endpoints per workspace (disabled ones count; endpoints are
disabled, never deleted, because the append-only delivery log references them).

Secrets: nothing secret is stored. An endpoint's signing secret is ``dclab_whsec_`` +
base64url(HMAC-SHA256(server key, key id, workspace, endpoint, secret_version)), shown once by the
create and rotate responses; rotation increments ``secret_version`` and moves the endpoint to the
current key id (the old secret stops verifying at once). The server keys are ``{"k1":
WEBHOOK_SECRET_KEY}`` today; a key map (more ids, gradual rotation) needs only ``_server_keys`` and
settings, no migration. Without a key of at least 32 characters webhooks can be neither created
nor delivered. Every change is recorded in ``webhook_changes`` (append-only, same transaction:
actor, action, the URL's host only).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import NotificationOutbox, User, WebhookChange, WebhookDelivery, WebhookEndpoint, WorkspaceRole
from app.domain import notifications as n
from app.services.authorization_service import explicit_workspace_role, platform_role_for
from app.services.notification_delivery import WebhookTarget, WebhookUrlError, allowed_ports, validate_webhook_url
from app.services.notification_service import NotificationError, insert_outbox

_MANAGERS = frozenset({WorkspaceRole.WORKSPACE_OWNER, WorkspaceRole.WORKSPACE_ADMIN})
SECRET_KEY_MIN_CHARS = 32


def _now() -> datetime:
    return datetime.now(UTC)


def _server_keys() -> dict[str, str]:
    """Key id -> key (never logged). Only ``k1`` (WEBHOOK_SECRET_KEY) for now."""

    key = get_settings().webhook_secret_key.get_secret_value().strip()
    return {n.WEBHOOK_SECRET_KEY_ID: key} if len(key) >= SECRET_KEY_MIN_CHARS else {}


def _secret_key() -> str | None:
    return _server_keys().get(n.WEBHOOK_SECRET_KEY_ID)


def webhook_secret(endpoint: WebhookEndpoint) -> str | None:
    key_id = endpoint.secret_key_id or n.WEBHOOK_SECRET_KEY_ID
    key = _server_keys().get(key_id)
    if key is None:
        return None
    message = f"dclab-webhook-secret:v2:{key_id}:{endpoint.workspace_id}:{endpoint.id}:{endpoint.secret_version}"
    mac = hmac.new(key.encode("utf-8"), message.encode("ascii"), hashlib.sha256).digest()
    return n.WEBHOOK_SECRET_PREFIX + base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def can_manage_webhooks(db: Session, actor: User, workspace_id: UUID) -> bool:
    if platform_role_for(db, actor) is not None:
        return False
    return explicit_workspace_role(db, actor, workspace_id) in _MANAGERS


def require_manager(db: Session, actor: User, workspace_id: UUID) -> None:
    """Called by every route before an Idempotency-Key replay too (authority is re-checked)."""

    if not can_manage_webhooks(db, actor, workspace_id):
        raise NotificationError(403, "webhooks_not_permitted",
                                "webhooks are managed by the workspace's owners and administrators")


def _require_configured() -> None:
    if _secret_key() is None:
        raise NotificationError(409, "webhooks_not_configured",
                                "webhooks are not set up on this server (WEBHOOK_SECRET_KEY is missing)")


def _url(url: str) -> tuple[str, WebhookTarget]:
    url = url.strip()
    try:
        target = validate_webhook_url(url, allowed_ports(get_settings().webhook_allowed_ports))
    except WebhookUrlError as exc:
        raise NotificationError(422, exc.code, str(exc)) from exc
    return url, target


def _changed(db: Session, row: WebhookEndpoint, actor: User, action: str, host: str | None = None) -> None:
    db.add(WebhookChange(workspace_id=row.workspace_id, webhook_endpoint_id=row.id, actor_user_id=actor.id,
                         action=action, url_host=host))


def _events(events: list[str]) -> list[str]:
    return [kind for kind in n.WEBHOOK_KINDS if kind in set(events)]


def _name(name: str) -> str:
    cleaned = n.clean_untrusted_text(name, n.WEBHOOK_NAME_MAX)
    if not cleaned:
        raise NotificationError(422, "invalid_name", "the name must not be blank")
    return cleaned


def _load(db: Session, workspace_id: UUID, webhook_id: UUID, *, lock: bool = False) -> WebhookEndpoint:
    query = select(WebhookEndpoint).where(WebhookEndpoint.workspace_id == workspace_id, WebhookEndpoint.id == webhook_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise NotificationError(404, "not_found", "webhook not found")
    return row


def webhook_read(db: Session, row: WebhookEndpoint) -> n.WebhookRead:
    last = db.execute(select(WebhookDelivery.created_at, WebhookDelivery.outcome, WebhookDelivery.error_code).where(
        WebhookDelivery.workspace_id == row.workspace_id, WebhookDelivery.webhook_endpoint_id == row.id)
        .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc()).limit(1)).first()
    return n.WebhookRead(
        id=row.id, workspace_id=row.workspace_id, name=row.name, url=row.url, events=list(row.events or []),
        enabled=row.enabled, secret_version=row.secret_version, created_by_user_id=row.created_by_user_id,
        created_at=row.created_at, updated_at=row.updated_at, secret_rotated_at=row.secret_rotated_at,
        consecutive_failures=row.consecutive_failures, paused_until=row.paused_until,
        last_delivery_at=last[0] if last else None, last_delivery_outcome=last[1] if last else None,
        last_error_code=last[2] if last else None)


def list_webhooks(db: Session, *, actor: User, workspace_id: UUID) -> list[n.WebhookRead]:
    require_manager(db, actor, workspace_id)
    rows = db.scalars(select(WebhookEndpoint).where(WebhookEndpoint.workspace_id == workspace_id)
                      .order_by(WebhookEndpoint.created_at, WebhookEndpoint.id)).all()
    return [webhook_read(db, row) for row in rows]


def create_webhook(db: Session, *, actor: User, workspace_id: UUID, request: n.WebhookCreate) -> WebhookEndpoint:
    require_manager(db, actor, workspace_id)
    _require_configured()
    (url, target), events, name = _url(request.url), _events(request.events), _name(request.name)
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"webhooks:{workspace_id}", 0))))
    count = db.scalar(select(func.count()).select_from(WebhookEndpoint)
                      .where(WebhookEndpoint.workspace_id == workspace_id)) or 0
    if count >= n.WEBHOOKS_PER_WORKSPACE_MAX:
        raise NotificationError(409, "webhook_limit_reached",
                                f"a workspace has at most {n.WEBHOOKS_PER_WORKSPACE_MAX} webhooks (disable or change one)")
    row = WebhookEndpoint(workspace_id=workspace_id, name=name, url=url, events=events, enabled=request.enabled,
                          secret_version=1, secret_key_id=n.WEBHOOK_SECRET_KEY_ID, created_by_user_id=actor.id,
                          updated_by_user_id=actor.id)
    db.add(row)
    db.flush()
    _changed(db, row, actor, "create", target.host)
    if not request.enabled:
        _changed(db, row, actor, "disable")
    db.flush()
    return row


def update_webhook(db: Session, *, actor: User, workspace_id: UUID, webhook_id: UUID,
                   request: n.WebhookUpdate) -> WebhookEndpoint:
    require_manager(db, actor, workspace_id)
    row = _load(db, workspace_id, webhook_id, lock=True)
    if request.name is not None:
        row.name = _name(request.name)
    if request.url is not None:
        url, target = _url(request.url)
        if url != row.url:
            row.url = url
            _changed(db, row, actor, "update_url", target.host)
    if request.events is not None and _events(request.events) != list(row.events or []):
        row.events = _events(request.events)
        _changed(db, row, actor, "update_events")
    if request.enabled is not None and request.enabled != row.enabled:
        row.enabled = request.enabled
        _changed(db, row, actor, "enable" if request.enabled else "disable")
        if request.enabled:  # a person turning it back on closes the breaker
            row.consecutive_failures, row.paused_until = 0, None
    row.updated_by_user_id, row.updated_at = actor.id, _now()
    db.flush()
    return row


def rotate_secret(db: Session, *, actor: User, workspace_id: UUID, webhook_id: UUID) -> WebhookEndpoint:
    require_manager(db, actor, workspace_id)
    _require_configured()
    row = _load(db, workspace_id, webhook_id, lock=True)
    now = _now()
    row.secret_version += 1
    row.secret_key_id = n.WEBHOOK_SECRET_KEY_ID  # rotation moves the endpoint to the current key
    row.secret_rotated_at = row.updated_at = now
    row.updated_by_user_id = actor.id
    _changed(db, row, actor, "rotate")
    db.flush()
    return row


def send_test(db: Session, *, actor: User, workspace_id: UUID, webhook_id: UUID) -> n.WebhookTestRead:
    """Queue a ``ping`` (one per endpoint per minute, a repeat returns the same one; at most
    ``PINGS_PER_ENDPOINT_PER_HOUR`` per endpoint per hour; a ping is tried once)."""

    require_manager(db, actor, workspace_id)
    _require_configured()
    if not get_settings().notifications_enabled:
        raise NotificationError(409, "notifications_paused", n.PAUSED_NOTE)
    row = _load(db, workspace_id, webhook_id)
    if not row.enabled:
        raise NotificationError(409, "webhook_disabled", "turn the webhook on before testing it")
    key = f"ping:{row.id}:{int(_now().timestamp()) // 60}"
    pings = db.scalar(select(func.count()).select_from(NotificationOutbox).where(
        NotificationOutbox.workspace_id == workspace_id, NotificationOutbox.webhook_endpoint_id == row.id,
        NotificationOutbox.kind == n.EVENT_PING, NotificationOutbox.dedupe_key != key,
        NotificationOutbox.created_at >= func.now() - timedelta(hours=1))) or 0
    if pings >= n.PINGS_PER_ENDPOINT_PER_HOUR:
        raise NotificationError(409, "ping_limit_reached",
                                f"at most {n.PINGS_PER_ENDPOINT_PER_HOUR} tests per webhook per hour; try later")
    payload = {"kind": n.EVENT_PING, "workspace_id": str(workspace_id),
               "subject": {"kind": "webhook_endpoint", "id": str(row.id)}, "occurred_at": _now().isoformat()}
    insert_outbox(db, kind=n.EVENT_PING, workspace_id=workspace_id, dedupe_key=key, payload=payload,
                  project_id=None, endpoint_ids=[row.id])
    outbox = db.scalar(select(NotificationOutbox).where(
        NotificationOutbox.workspace_id == workspace_id, NotificationOutbox.webhook_endpoint_id == row.id,
        NotificationOutbox.dedupe_key == key))
    return n.WebhookTestRead(outbox_id=outbox.id, status=outbox.status,
                             note="Queued: the worker sends it within a minute; see the delivery log.")


def list_deliveries(db: Session, *, actor: User, workspace_id: UUID, webhook_id: UUID) -> list[n.WebhookDeliveryRead]:
    require_manager(db, actor, workspace_id)
    _load(db, workspace_id, webhook_id)
    rows = db.scalars(select(WebhookDelivery).where(
        WebhookDelivery.workspace_id == workspace_id, WebhookDelivery.webhook_endpoint_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc()).limit(n.DELIVERIES_LIST_MAX)).all()
    return [n.WebhookDeliveryRead(id=r.id, delivery_id=r.outbox_id, event=r.event, attempt=r.attempt,
                                  outcome=r.outcome, status_code=r.status_code, error_code=r.error_code,
                                  duration_ms=r.duration_ms, created_at=r.created_at) for r in rows]
