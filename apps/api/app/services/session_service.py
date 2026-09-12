"""Opaque browser sessions. Raw tokens are never stored or logged."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import Response
from sqlalchemy.orm import Session

from app.config import Settings, cookie_secure, get_settings
from app.db.models import AuthSession, User
from app.services.auth_hashing import token_hash, token_hash_candidates
from app.services.auth_metrics import record_auth_event
from app.services.auth_service import AuthError

SESSION_HEADER = "X-DCLab-Session"


def utcnow() -> datetime:
    return datetime.now(UTC)


def _session_row_for_raw(db: Session, raw: str) -> AuthSession | None:
    for digest in token_hash_candidates(raw):
        row = (
            db.query(AuthSession)
            .filter(AuthSession.token_hash == digest)
            .one_or_none()
        )
        if row is not None:
            return row
    return None


def hash_session_token(raw: str) -> str:
    return token_hash(raw)


def hash_user_agent(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def purge_stale_sessions(db: Session, now: datetime | None = None) -> int:
    from app.services.session_cleanup_service import cleanup_expired_auth_state

    return cleanup_expired_auth_state(db, now=now, max_batches=1).deleted


def revoke_session_token(
    db: Session, raw: str, *, now: datetime | None = None
) -> AuthSession | None:
    moment = now or utcnow()
    row = _session_row_for_raw(db, raw)
    if row is None or row.revoked_at is not None:
        return row
    row.revoked_at = moment
    record_auth_event("session", "logout")
    return row


def revoke_sessions_for_user(
    db: Session, user_id: UUID, *, now: datetime | None = None
) -> int:
    moment = now or utcnow()
    rows = (
        db.query(AuthSession)
        .filter(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .all()
    )
    for row in rows:
        row.revoked_at = moment
    if rows:
        record_auth_event("session", "logout_all")
    return len(rows)


def issue_session(
    db: Session,
    user: User,
    *,
    replace_raw: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> tuple[AuthSession, str]:
    moment = now or utcnow()
    settings = get_settings()
    if not settings.auth_browser_sessions_enabled:
        raise AuthError("browser sessions are disabled")
    from app.services.session_cleanup_service import cleanup_expired_auth_state

    cleanup_expired_auth_state(db, now=moment, max_batches=1)
    rotated_from_id = None
    previous_selected = None
    if replace_raw:
        previous = revoke_session_token(db, replace_raw, now=moment)
        if previous is not None and previous.user_id == user.id:
            rotated_from_id = previous.id
            previous_selected = previous.selected_workspace_id
    raw = generate_session_token()
    idle = moment + timedelta(minutes=settings.session_idle_minutes)
    absolute = moment + timedelta(minutes=settings.session_absolute_minutes)
    if idle > absolute:
        idle = absolute
    from app.services.authorization_service import (
        default_workspace_id,
        workspace_is_selectable,
    )

    selected_workspace_id = None
    if previous_selected is not None and workspace_is_selectable(
        db, user, previous_selected
    ):
        selected_workspace_id = previous_selected
    if selected_workspace_id is None:
        selected_workspace_id = default_workspace_id(db, user)
    row = AuthSession(
        id=uuid4(),
        user_id=user.id,
        token_hash=hash_session_token(raw),
        created_at=moment,
        last_seen_at=moment,
        idle_expires_at=idle,
        absolute_expires_at=absolute,
        rotated_from_id=rotated_from_id,
        user_agent_hash=hash_user_agent(user_agent),
        selected_workspace_id=selected_workspace_id,
    )
    db.add(row)
    db.flush()
    _enforce_concurrent_session_cap(db, user.id, keep_id=row.id, now=moment)
    record_auth_event("session", "issued")
    return row, raw


def _enforce_concurrent_session_cap(
    db: Session, user_id: UUID, *, keep_id: UUID, now: datetime
) -> None:
    cap = max(1, get_settings().session_max_concurrent)
    rows = (
        db.query(AuthSession)
        .filter(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.absolute_expires_at > now,
            AuthSession.idle_expires_at > now,
        )
        .order_by(AuthSession.created_at.asc(), AuthSession.id.asc())
        .all()
    )
    if len(rows) <= cap:
        return
    for row in rows[:-cap]:
        if row.id == keep_id:
            continue
        row.revoked_at = now
    record_auth_event("session", "capped")


def _session_expired(row: AuthSession, now: datetime) -> bool:
    return row.absolute_expires_at <= now or row.idle_expires_at <= now


def lookup_session(db: Session, raw: str, *, now: datetime | None = None) -> AuthSession:
    moment = now or utcnow()
    from app.services.session_cleanup_service import cleanup_expired_auth_state

    cleanup_expired_auth_state(db, now=moment, max_batches=1)
    if not get_settings().auth_browser_sessions_enabled:
        record_auth_event("session", "kill_switch")
        raise AuthError("browser sessions are disabled")
    row = _session_row_for_raw(db, raw)
    if row is None or row.revoked_at is not None:
        record_auth_event("session", "invalid")
        raise AuthError("session is not valid")
    if _session_expired(row, moment):
        record_auth_event("session", "expired")
        raise AuthError("session expired")
    return row


def touch_session(db: Session, row: AuthSession, *, now: datetime | None = None) -> None:
    settings = get_settings()
    moment = now or utcnow()
    if row.last_seen_at is not None and (moment - row.last_seen_at) < timedelta(seconds=60):
        return
    row.last_seen_at = moment
    idle = moment + timedelta(minutes=settings.session_idle_minutes)
    if idle > row.absolute_expires_at:
        idle = row.absolute_expires_at
    row.idle_expires_at = idle


def user_from_session(
    db: Session, raw: str, *, now: datetime | None = None
) -> tuple[User, AuthSession]:
    row = lookup_session(db, raw, now=now)
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AuthError("user no longer exists or is disabled")
    touch_session(db, row, now=now)
    return user, row


def attach_session_cookie(
    response: Response, raw: str, settings: Settings | None = None
) -> None:
    cfg = settings or get_settings()
    response.set_cookie(
        key=cfg.session_cookie_name,
        value=raw,
        max_age=int(cfg.session_absolute_minutes * 60),
        path=cfg.session_cookie_path,
        httponly=True,
        secure=cookie_secure(cfg),
        samesite=cfg.session_cookie_samesite,
    )


def clear_session_cookie(response: Response, settings: Settings | None = None) -> None:
    cfg = settings or get_settings()
    response.delete_cookie(
        key=cfg.session_cookie_name,
        path=cfg.session_cookie_path,
        httponly=True,
        secure=cookie_secure(cfg),
        samesite=cfg.session_cookie_samesite,
    )
