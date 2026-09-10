"""Opaque browser sessions. Raw tokens are never stored or logged."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import Response
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.config import Settings, cookie_secure, get_settings
from app.db.models import AuthSession, User
from app.services.auth_service import AuthError

logger = logging.getLogger(__name__)

SESSION_HEADER = "X-DCLab-Session"


def utcnow() -> datetime:
    return datetime.now(UTC)


def hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_user_agent(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def purge_stale_sessions(db: Session, now: datetime | None = None) -> int:
    settings = get_settings()
    moment = now or utcnow()
    cutoff = moment - timedelta(days=settings.session_retention_days)
    deleted = (
        db.query(AuthSession)
        .filter(
            or_(
                AuthSession.absolute_expires_at < cutoff,
                and_(
                    AuthSession.revoked_at.is_not(None),
                    AuthSession.revoked_at < cutoff,
                ),
            )
        )
        .delete(synchronize_session=False)
    )
    return int(deleted or 0)


def revoke_session_token(
    db: Session, raw: str, *, now: datetime | None = None
) -> AuthSession | None:
    moment = now or utcnow()
    row = (
        db.query(AuthSession)
        .filter(AuthSession.token_hash == hash_session_token(raw))
        .one_or_none()
    )
    if row is None or row.revoked_at is not None:
        return row
    row.revoked_at = moment
    logger.info("session revoked session_id=%s user_id=%s", row.id, row.user_id)
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
        logger.info("sessions revoked_for_user user_id=%s count=%s", user_id, len(rows))
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
    purge_stale_sessions(db, moment)
    rotated_from_id = None
    previous_selected = None
    if replace_raw:
        previous = revoke_session_token(db, replace_raw, now=moment)
        if previous is not None:
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
    logger.info("session issued session_id=%s user_id=%s", row.id, user.id)
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
    logger.info("sessions capped user_id=%s cap=%s", user_id, cap)


def _session_expired(row: AuthSession, now: datetime) -> bool:
    return row.absolute_expires_at <= now or row.idle_expires_at <= now


def lookup_session(db: Session, raw: str, *, now: datetime | None = None) -> AuthSession:
    moment = now or utcnow()
    purge_stale_sessions(db, moment)
    row = (
        db.query(AuthSession)
        .filter(AuthSession.token_hash == hash_session_token(raw))
        .one_or_none()
    )
    if row is None or row.revoked_at is not None:
        raise AuthError("session is not valid")
    if _session_expired(row, moment):
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
