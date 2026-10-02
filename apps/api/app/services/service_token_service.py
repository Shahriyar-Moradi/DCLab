"""Service tokens (P3.2-A): create / list / revoke for humans, bearer resolution for /v1.

The raw token is returned once by ``create_service_token`` and never stored or
logged: rows keep ``auth_hashing.token_hash`` of the whole string (HMAC with
``AUTH_TOKEN_HASH_SECRET``; rotation candidates accepted on lookup). Every
authentication failure is the same ``AuthError`` (the reason goes to metrics and
logs only, with the token id once the secret matched). A token borrows its
creator's *explicit, unsuspended* workspace membership (no platform-role
fallthrough); creating one needs an ML-write role and the account password.

Tokens die with their creator's credentials or authority: password reset,
logout-all and role changes call ``revoke_tokens_created_by``; the ``before_flush``
hook registered in ``app.db.session`` (``revoke_on_authority_change``) does the same
for any ORM membership role change, suspension or removal, platform-role change and
account deactivation. Lifecycle events are audited as structured log lines (no
secret) emitted when the root transaction commits.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Iterator
from uuid import UUID, uuid4

from sqlalchemy import event, func, inspect, literal, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm.util import identity_key

from app.config import get_settings
from app.db.models import PlatformMembership, ServiceToken, User, WorkspaceMembership, WorkspaceRole
from app.domain.errors import IdentityError, ServiceTokenError, ServiceTokenNotFoundError
from app.domain.service_tokens import (
    ACTIVE_TOKENS_PER_CREATOR_MAX,
    LAST_USED_RESOLUTION_SECONDS,
    LIST_MAX,
    NAME_MAX_CHARS,
    SCOPE_DECISIONS_PROPOSE,
    SERVICE_TOKEN_SCOPES,
    TOKEN_PREFIX,
    TTL_DAYS_MAX,
    ServiceTokenRead,
    display_prefix,
    parse_service_token,
)
from app.services.auth_hashing import token_hash, token_hash_candidates
from app.services.auth_metrics import record_auth_event
from app.services.auth_service import AuthError, verify_password
from app.services.authorization_service import (
    can_manage_workspace_members,
    can_read_workspace,
    explicit_workspace_role,
    is_ml_write_role,
)

logger = logging.getLogger("dclab.service_tokens")

INVALID_TOKEN_MESSAGE = "invalid service token"
_UNUSABLE_HASH = "0" * 64
_AUDIT_KEY = "dclab_service_token_audit"
# The service token this request authenticated with (set by the /v1 transport
# when it builds an agent actor); the agent binding must name this token.
_REQUEST_TOKEN: ContextVar[UUID | None] = ContextVar("dclab_request_service_token", default=None)


def utcnow() -> datetime:
    return datetime.now(UTC)


def token_status(row: ServiceToken, now: datetime | None = None) -> str:
    if row.revoked_at is not None:
        return "revoked"
    if row.expires_at <= (now or utcnow()):
        return "expired"
    return "active"


def token_read(row: ServiceToken, now: datetime | None = None) -> ServiceTokenRead:
    return ServiceTokenRead(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        prefix=display_prefix(row.id),
        scopes=list(row.scopes),
        status=token_status(row, now),
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        revoked_by_user_id=row.revoked_by_user_id,
    )


# --- audit (emitted only once the transaction commits) ---------------------------------------


def _audit(db: Session, action: str, row: Any, *, actor_id: UUID | None, reason: str,
           request_id: str | None = None) -> None:
    """Queue an audit line for ``row`` (a ServiceToken or a RETURNING row of one)."""

    db.info.setdefault(_AUDIT_KEY, []).append({
        "event": "service_token",
        "action": action,
        "reason": reason,
        "token_id": str(row.id),
        "workspace_id": str(row.workspace_id),
        "actor": str(actor_id) if actor_id is not None else "system",
        "scopes": list(row.scopes),
        "expires_at": row.expires_at.isoformat(),
        "request_id": request_id,
    })


# Root transactions only: a savepoint release (``idempotency_service.bind``) must not
# emit early, and a savepoint rollback must not drop the queue.
@event.listens_for(Session, "after_commit")
def _emit_audit(session: Session) -> None:
    if session.in_nested_transaction():
        return
    for item in session.info.pop(_AUDIT_KEY, []):
        logger.info("service_token_audit %s", json.dumps(item, sort_keys=True))


@event.listens_for(Session, "after_rollback")
def _drop_audit(session: Session) -> None:
    if not session.in_nested_transaction():
        session.info.pop(_AUDIT_KEY, None)


# --- lifecycle ---------------------------------------------------------------------------------


def _clean_scopes(scopes: Iterable[str]) -> list[str]:
    requested = set(scopes)
    unknown = requested - set(SERVICE_TOKEN_SCOPES)
    if unknown or not requested:
        raise ServiceTokenError("invalid_scopes", "scopes must be a non-empty subset of the known scopes", 422)
    return [scope for scope in SERVICE_TOKEN_SCOPES if scope in requested]


def _clean_name(name: str) -> str:
    label = (name or "").strip()
    if not label or len(label) > NAME_MAX_CHARS or any(unicodedata.category(ch) in ("Cc", "Cf") for ch in label):
        raise ServiceTokenError("invalid_name", f"name must be 1-{NAME_MAX_CHARS} printable characters", 422)
    return label


def create_service_token(
    db: Session,
    *,
    creator: User,
    workspace_id: UUID,
    name: str,
    scopes: Iterable[str],
    expires_in_days: int,
    current_password: str,
    request_id: str | None = None,
    now: datetime | None = None,
) -> tuple[ServiceToken, str]:
    """Mint a token for ``workspace_id``; returns the row and the raw secret (shown once).

    Needs the creator's password (re-authentication) and an explicit, unsuspended
    ML-write membership of the workspace; scopes never exceed that role (re-checked
    on every use). At most ``ACTIVE_TOKENS_PER_CREATOR_MAX`` active tokens per
    creator and workspace (serialized by an advisory lock).
    """

    moment = now or utcnow()
    if not get_settings().service_tokens_enabled:
        raise ServiceTokenError("service_tokens_disabled", "service tokens are disabled", 403)
    if not current_password or not verify_password(current_password, creator.password_hash):
        raise ServiceTokenError("reauthentication_failed", "re-authentication failed", 403)
    if not is_ml_write_role(explicit_workspace_role(db, creator, workspace_id)):
        raise ServiceTokenError(
            "token_creation_not_permitted",
            "creating a service token needs an ML-write role as a member of this workspace",
            403,
        )
    cleaned = _clean_scopes(scopes)
    label = _clean_name(name)
    days = int(expires_in_days)
    if not 1 <= days <= TTL_DAYS_MAX:
        raise ServiceTokenError("invalid_expiry", f"expires_in_days must be between 1 and {TTL_DAYS_MAX}", 422)
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"service_token_cap:{workspace_id}:{creator.id}"},
    )
    active = db.scalar(
        select(func.count()).select_from(ServiceToken).where(
            ServiceToken.workspace_id == workspace_id,
            ServiceToken.created_by_user_id == creator.id,
            ServiceToken.revoked_at.is_(None),
            ServiceToken.expires_at > moment,
        )
    )
    if active >= ACTIVE_TOKENS_PER_CREATOR_MAX:
        raise ServiceTokenError(
            "service_token_limit_reached",
            f"you already hold {ACTIVE_TOKENS_PER_CREATOR_MAX} active tokens in this workspace; revoke one",
            409,
            limit=ACTIVE_TOKENS_PER_CREATOR_MAX,
        )
    token_id = uuid4()
    raw = f"{TOKEN_PREFIX}{token_id.hex}_{secrets.token_urlsafe(32)}"
    row = ServiceToken(
        id=token_id,
        workspace_id=workspace_id,
        created_by_user_id=creator.id,
        name=label,
        scopes=cleaned,
        secret_hash=token_hash(raw),
        created_at=moment,
        expires_at=moment + timedelta(days=days),
    )
    db.add(row)
    db.flush()
    record_auth_event("service_token", "issued")
    _audit(db, "created", row, actor_id=creator.id, reason="user", request_id=request_id)
    return row, raw


def _visible(db: Session, actor: User, workspace_id: UUID):
    stmt = select(ServiceToken).where(ServiceToken.workspace_id == workspace_id)
    if not can_manage_workspace_members(db, actor, workspace_id):
        stmt = stmt.where(ServiceToken.created_by_user_id == actor.id)
    return stmt


def list_service_tokens(db: Session, *, actor: User, workspace_id: UUID) -> list[ServiceToken]:
    """Workspace admins see every token of the workspace; other members their own."""

    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)
    stmt = _visible(db, actor, workspace_id).order_by(ServiceToken.created_at.desc(), ServiceToken.id.desc())
    return list(db.scalars(stmt.limit(LIST_MAX)))


def _mark_revoked(db: Session, row: ServiceToken, *, actor_id: UUID | None, reason: str, moment: datetime,
                  request_id: str | None = None) -> None:
    row.revoked_at = max(moment, row.created_at)
    row.revoked_by_user_id = actor_id
    record_auth_event("service_token", "revoked")
    _audit(db, "revoked", row, actor_id=actor_id, reason=reason, request_id=request_id)


def revoke_service_token(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    token_id: UUID,
    request_id: str | None = None,
    now: datetime | None = None,
) -> ServiceToken:
    """Revoke (final). The creator or a workspace admin; anyone else sees 404.
    Revoking a revoked token returns it unchanged."""

    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)
    # FOR UPDATE + populate_existing: a concurrent revoke is seen (never re-revoked,
    # which the revocation-final trigger would refuse).
    row = db.scalar(
        _visible(db, actor, workspace_id).where(ServiceToken.id == token_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if row is None:
        raise ServiceTokenNotFoundError("service token not found")
    if row.revoked_at is None:
        _mark_revoked(db, row, actor_id=actor.id, reason="user", moment=now or utcnow(), request_id=request_id)
        db.flush()
    return row


def revoke_tokens_created_by(
    db: Session,
    user_id: UUID,
    *,
    reason: str,
    workspace_id: UUID | None = None,
    now: datetime | None = None,
) -> int:
    """System revocation (``revoked_by_user_id`` NULL) of every unrevoked token the user
    created (in ``workspace_id`` only, when given), in ONE conditional UPDATE: a token a
    concurrent request revoked first is skipped, never re-revoked (which the
    revocation-final trigger would refuse). Core SQL on the session's connection, so it
    is safe inside ``before_flush``; the caller's commit persists it."""

    table = ServiceToken.__table__
    moment = now or utcnow()
    stmt = (
        table.update()
        .where(table.c.created_by_user_id == user_id, table.c.revoked_at.is_(None))
        .values(revoked_at=func.greatest(literal(moment), table.c.created_at), revoked_by_user_id=None)
        .returning(table.c.id, table.c.workspace_id, table.c.scopes, table.c.expires_at, table.c.revoked_at)
    )
    if workspace_id is not None:
        stmt = stmt.where(table.c.workspace_id == workspace_id)
    rows = db.connection().execute(stmt).all()
    for row in rows:
        loaded = db.identity_map.get(identity_key(ServiceToken, row.id))
        if loaded is not None:  # keep loaded objects truthful without another UPDATE
            set_committed_value(loaded, "revoked_at", row.revoked_at)
            set_committed_value(loaded, "revoked_by_user_id", None)
        record_auth_event("service_token", "revoked")
        _audit(db, "revoked", row, actor_id=None, reason=reason)
    return len(rows)


def _changed(obj: Any, attr: str) -> bool:
    return inspect(obj).attrs[attr].history.has_changes()


def revoke_on_authority_change(session: Session) -> None:
    """``before_flush`` backstop (registered in ``app.db.session``) for every ORM path: a
    creator's membership role change, suspension or removal kills their tokens in that
    workspace; a platform-role change or account deactivation kills all of them."""

    targets: set[tuple[UUID, UUID | None]] = set()
    for obj in session.dirty:
        if isinstance(obj, WorkspaceMembership):
            if _changed(obj, "role") or (_changed(obj, "suspended_at") and obj.suspended_at is not None):
                targets.add((obj.user_id, obj.workspace_id))
        elif isinstance(obj, PlatformMembership) and _changed(obj, "role"):
            targets.add((obj.user_id, None))
        elif isinstance(obj, User) and _changed(obj, "is_active") and not obj.is_active:
            targets.add((obj.id, None))
    for obj in session.deleted:
        if isinstance(obj, WorkspaceMembership):
            targets.add((obj.user_id, obj.workspace_id))
        elif isinstance(obj, PlatformMembership):
            targets.add((obj.user_id, None))
    for user_id, workspace_id in targets:
        if user_id is not None:
            revoke_tokens_created_by(session, user_id, workspace_id=workspace_id, reason="authority_change")


# --- bearer resolution -----------------------------------------------------------------------


def authenticate_service_token(
    db: Session, raw: str, *, now: datetime | None = None
) -> tuple[ServiceToken, User, WorkspaceRole]:
    """Resolve a bearer string to (token, creator, creator's explicit workspace role)
    or raise the one uniform ``AuthError``."""

    moment = now or utcnow()
    token_id = parse_service_token(raw)
    candidates = token_hash_candidates(raw)  # always computed: no shape/id timing oracle
    row = db.get(ServiceToken, token_id) if token_id is not None else None
    stored = row.secret_hash if row is not None else _UNUSABLE_HASH
    matched = False
    for digest in candidates:
        matched = hmac.compare_digest(stored, digest) or matched
    reason = None
    creator = None
    role = None
    if not get_settings().service_tokens_enabled:
        reason = "kill_switch"
    elif row is None or not matched:
        reason = "invalid"
    elif row.revoked_at is not None:
        reason = "revoked"
    elif row.expires_at <= moment:
        reason = "expired"
    else:
        creator = db.get(User, row.created_by_user_id)
        if creator is not None and creator.is_active:
            role = explicit_workspace_role(db, creator, row.workspace_id)
        if role is None:
            reason = "other"  # the creator lost the membership the token borrows
    if reason is not None:
        record_auth_event("service_token", reason)
        if row is not None and matched:  # a genuine token: name it (never the secret)
            logger.info(
                "service_token_auth_failed reason=%s token_id=%s workspace_id=%s",
                "creator_unauthorized" if reason == "other" else reason, row.id, row.workspace_id,
            )
        raise AuthError(INVALID_TOKEN_MESSAGE)
    if row.last_used_at is None or moment - row.last_used_at >= timedelta(seconds=LAST_USED_RESOLUTION_SECONDS):
        row.last_used_at = moment
    return row, creator, role


# --- agent binding (decision_record_service.agent_binding_verifier) ---------------------------


def bind_request_token(token_id: UUID | None) -> None:
    """Record the service token this request authenticated with (per request context)."""

    _REQUEST_TOKEN.set(token_id)


@contextmanager
def request_token_bound(token_id: UUID | None) -> Iterator[None]:
    reset = _REQUEST_TOKEN.set(token_id)
    try:
        yield
    finally:
        _REQUEST_TOKEN.reset(reset)


def verify_agent_binding(db: Session, actor: Any, workspace_id: UUID) -> bool:
    """An agent actor is bound to ``workspace_id`` only as the token this request
    authenticated with: active, of that workspace, allowed to propose, and whose
    creator still holds an explicit ML-write membership there. Internal agent runs
    (Phase 6 ``agent_runs``) stay unbound (fail closed)."""

    if getattr(actor, "agent_run_id", None) is not None:
        return False
    token_id = getattr(actor, "service_token_id", None)
    if token_id is None or token_id != _REQUEST_TOKEN.get():
        return False
    row = db.scalar(
        select(ServiceToken).where(ServiceToken.id == token_id, ServiceToken.workspace_id == workspace_id)
    )
    if row is None or token_status(row) != "active" or SCOPE_DECISIONS_PROPOSE not in row.scopes:
        return False
    creator = db.get(User, row.created_by_user_id)
    return (
        creator is not None
        and creator.is_active
        and is_ml_write_role(explicit_workspace_role(db, creator, workspace_id))
    )
