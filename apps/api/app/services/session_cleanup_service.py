"""Bounded, race-safe cleanup for hashed sessions and recovery tokens.

Identity-plane: this does not touch tenant tables. Durable invocation uses
the ``auth.session_cleanup`` worker handler. Session issue/lookup still run
one opportunistic batch so idle traffic is not required for progress.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AuthRecoveryToken, AuthSession, MlJob
from app.domain.ml_jobs import (
    HANDLER_AUTH_SESSION_CLEANUP,
    HANDLER_VERSION_AUTH_SESSION_CLEANUP,
    JOB_QUEUED,
    JOB_TYPE_AUTH_CLEANUP,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AuthCleanupResult:
    sessions: int
    recovery_tokens: int

    @property
    def deleted(self) -> int:
        return self.sessions + self.recovery_tokens


def _batch_size(limit: int | None) -> int:
    configured = get_settings().session_cleanup_batch_size
    raw = configured if limit is None else limit
    return max(1, int(raw))


def _max_batches(max_batches: int | None) -> int:
    configured = get_settings().session_cleanup_max_batches
    raw = configured if max_batches is None else max_batches
    return max(1, int(raw))


def _delete_stale_sessions(
    db: Session, *, moment: datetime, cutoff: datetime, limit: int
) -> int:
    ids = [
        row[0]
        for row in (
            db.query(AuthSession.id)
            .filter(
                or_(
                    AuthSession.absolute_expires_at < cutoff,
                    and_(
                        AuthSession.revoked_at.is_not(None),
                        AuthSession.revoked_at < cutoff,
                    ),
                )
            )
            .order_by(AuthSession.absolute_expires_at.asc(), AuthSession.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
            .all()
        )
    ]
    if not ids:
        return 0
    deleted = (
        db.query(AuthSession)
        .filter(AuthSession.id.in_(ids))
        .delete(synchronize_session=False)
    )
    return int(deleted or 0)


def _delete_stale_recovery_tokens(
    db: Session, *, moment: datetime, cutoff: datetime, limit: int
) -> int:
    ids = [
        row[0]
        for row in (
            db.query(AuthRecoveryToken.id)
            .filter(
                or_(
                    AuthRecoveryToken.expires_at < moment,
                    and_(
                        AuthRecoveryToken.consumed_at.is_not(None),
                        AuthRecoveryToken.consumed_at < cutoff,
                    ),
                )
            )
            .order_by(AuthRecoveryToken.expires_at.asc(), AuthRecoveryToken.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
            .all()
        )
    ]
    if not ids:
        return 0
    deleted = (
        db.query(AuthRecoveryToken)
        .filter(AuthRecoveryToken.id.in_(ids))
        .delete(synchronize_session=False)
    )
    return int(deleted or 0)


def cleanup_expired_auth_state(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int | None = None,
    max_batches: int | None = None,
) -> AuthCleanupResult:
    """Delete retained sessions and expired/consumed recovery hashes.

    Idempotent: a second call with nothing eligible returns zeros. Bounded by
    ``limit`` rows per batch and ``max_batches`` loops. Concurrent callers use
    ``FOR UPDATE SKIP LOCKED`` so overlapping jobs do not block each other.
    """

    moment = now or utcnow()
    settings = get_settings()
    cutoff = moment - timedelta(days=settings.session_retention_days)
    batch = _batch_size(limit)
    loops = _max_batches(max_batches)
    sessions = 0
    recovery = 0
    for _ in range(loops):
        deleted = _delete_stale_sessions(
            db, moment=moment, cutoff=cutoff, limit=batch
        )
        sessions += deleted
        if deleted < batch:
            break
    for _ in range(loops):
        deleted = _delete_stale_recovery_tokens(
            db, moment=moment, cutoff=cutoff, limit=batch
        )
        recovery += deleted
        if deleted < batch:
            break
    return AuthCleanupResult(sessions=sessions, recovery_tokens=recovery)


def enqueue_auth_cleanup(db: Session, *, workspace_id: UUID) -> MlJob:
    """Queue at most one ``auth.session_cleanup`` job. Payload is empty IDs only.

    ``ml_jobs.workspace_id`` is required by the existing queue row. The handler
    ignores it and cleans identity-plane tables. Do not put tokens in payload.
    """

    from app.services.ml_job_service import create_ml_job

    existing = db.scalar(
        select(MlJob).where(
            MlJob.handler_key == HANDLER_AUTH_SESSION_CLEANUP,
            MlJob.status == JOB_QUEUED,
        )
    )
    if existing is not None:
        return existing
    return create_ml_job(
        db,
        workspace_id=workspace_id,
        job_type=JOB_TYPE_AUTH_CLEANUP,
        handler_key=HANDLER_AUTH_SESSION_CLEANUP,
        handler_version=HANDLER_VERSION_AUTH_SESSION_CLEANUP,
        target_id=workspace_id,
        payload={},
    )
