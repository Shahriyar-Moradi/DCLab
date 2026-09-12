"""Hashed password-reset and email-verification tokens. Raw secrets are never stored."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AuthRecoveryToken, User
from app.services.auth_hashing import token_hash, token_hash_candidates
from app.services.auth_metrics import record_auth_event
from app.services.auth_service import AuthError, hash_password
from app.services.session_service import revoke_sessions_for_user, utcnow

PURPOSE_PASSWORD_RESET = "password_reset"
PURPOSE_EMAIL_VERIFICATION = "email_verification"
RECOVERY_PURPOSES = frozenset({PURPOSE_PASSWORD_RESET, PURPOSE_EMAIL_VERIFICATION})


def hash_recovery_token(raw: str) -> str:
    return token_hash(raw)


def generate_recovery_token() -> str:
    return secrets.token_urlsafe(32)


def issue_recovery_token(
    db: Session,
    user: User,
    purpose: str,
    *,
    now: datetime | None = None,
) -> str:
    if purpose not in RECOVERY_PURPOSES:
        raise ValueError(f"unknown recovery purpose {purpose}")
    moment = now or utcnow()
    settings = get_settings()
    previous = (
        db.query(AuthRecoveryToken)
        .filter(
            AuthRecoveryToken.user_id == user.id,
            AuthRecoveryToken.purpose == purpose,
            AuthRecoveryToken.consumed_at.is_(None),
        )
        .all()
    )
    for row in previous:
        row.consumed_at = moment
    raw = generate_recovery_token()
    row = AuthRecoveryToken(
        id=uuid4(),
        user_id=user.id,
        purpose=purpose,
        token_hash=hash_recovery_token(raw),
        created_at=moment,
        expires_at=moment + timedelta(minutes=settings.recovery_token_minutes),
    )
    db.add(row)
    db.flush()
    record_auth_event("recovery", "issued")
    return raw


def request_recovery(db: Session, email: str, purpose: str) -> None:
    """Issue a hashed token when the account exists. Never reveals that fact."""
    normalized = email.strip().lower()
    user = db.query(User).filter(User.email == normalized).one_or_none()
    if user is None or not user.is_active:
        record_auth_event("recovery", "ignored")
        return
    issue_recovery_token(db, user, purpose)


def _recovery_row_for_raw(db: Session, raw: str) -> AuthRecoveryToken | None:
    for digest in token_hash_candidates(raw.strip()):
        row = (
            db.query(AuthRecoveryToken)
            .filter(AuthRecoveryToken.token_hash == digest)
            .one_or_none()
        )
        if row is not None:
            return row
    return None


def _consume_recovery_token(
    db: Session, raw: str, purpose: str, *, now: datetime | None = None
) -> tuple[AuthRecoveryToken, User]:
    moment = now or utcnow()
    row = _recovery_row_for_raw(db, raw)
    if (
        row is None
        or row.purpose != purpose
        or row.consumed_at is not None
        or row.expires_at <= moment
    ):
        raise AuthError("invalid or expired token")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AuthError("invalid or expired token")
    row.consumed_at = moment
    record_auth_event("recovery", "consumed")
    return row, user


def confirm_password_reset(
    db: Session, raw: str, new_password: str, *, now: datetime | None = None
) -> User:
    _row, user = _consume_recovery_token(
        db, raw, PURPOSE_PASSWORD_RESET, now=now
    )
    user.password_hash = hash_password(new_password)
    revoke_sessions_for_user(db, user.id, now=now)
    return user


def confirm_email_verification(
    db: Session, raw: str, *, now: datetime | None = None
) -> User:
    moment = now or utcnow()
    _row, user = _consume_recovery_token(
        db, raw, PURPOSE_EMAIL_VERIFICATION, now=moment
    )
    user.email_verified_at = moment
    return user
