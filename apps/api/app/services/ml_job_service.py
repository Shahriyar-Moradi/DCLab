"""Persist, claim, heartbeat, retry, and recover durable ML jobs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ClientLabUpload, MlJob
from app.domain.lab_run_stages import COMPLETED, SKIPPED
from app.domain.ml_jobs import (
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_TYPE_AUTO_TRAIN,
)

JobRunner = Callable[[Session, UUID], None]


def _now() -> datetime:
    return datetime.now(UTC)


def _max_attempts() -> int:
    try:
        return max(1, int(get_settings().ml_job_max_attempts))
    except Exception:  # noqa: BLE001
        return DEFAULT_MAX_ATTEMPTS


def _heartbeat_timeout_seconds() -> float:
    try:
        return float(get_settings().ml_job_heartbeat_timeout_seconds)
    except Exception:  # noqa: BLE001
        return DEFAULT_HEARTBEAT_TIMEOUT_SECONDS


def create_auto_train_job(
    db: Session,
    *,
    workspace_id: UUID,
    upload_id: UUID,
    project_id: UUID | None = None,
    max_attempts: int | None = None,
) -> MlJob:
    """Insert-once auto-train job for this upload. Second persist returns the row."""

    existing = db.scalar(select(MlJob).where(MlJob.upload_id == upload_id))
    if existing is not None:
        return existing
    now = _now()
    job = MlJob(
        workspace_id=workspace_id,
        project_id=project_id,
        job_type=JOB_TYPE_AUTO_TRAIN,
        target_id=upload_id,
        upload_id=upload_id,
        status=JOB_QUEUED,
        attempts=0,
        max_attempts=max_attempts if max_attempts is not None else _max_attempts(),
        queued_at=now,
    )
    db.add(job)
    db.flush()
    return job


def touch_heartbeat(db: Session, job: MlJob, *, now: datetime | None = None) -> None:
    job.heartbeat_at = now or _now()
    db.flush()


def recover_abandoned_jobs(
    db: Session,
    *,
    now: datetime | None = None,
    heartbeat_timeout_seconds: float | None = None,
) -> list[MlJob]:
    """Requeue or fail running jobs whose heartbeat has expired."""

    moment = now or _now()
    timeout = (
        heartbeat_timeout_seconds
        if heartbeat_timeout_seconds is not None
        else _heartbeat_timeout_seconds()
    )
    cutoff = moment - timedelta(seconds=timeout)
    stale = list(
        db.scalars(
            select(MlJob)
            .where(
                MlJob.status == JOB_RUNNING,
                or_(
                    and_(
                        MlJob.heartbeat_at.is_not(None),
                        MlJob.heartbeat_at < cutoff,
                    ),
                    and_(
                        MlJob.heartbeat_at.is_(None),
                        MlJob.started_at.is_not(None),
                        MlJob.started_at < cutoff,
                    ),
                ),
            )
            .with_for_update(skip_locked=True)
            .order_by(MlJob.queued_at.asc())
        )
    )
    recovered: list[MlJob] = []
    for job in stale:
        _apply_failure(
            job,
            reason="abandoned: heartbeat expired",
            now=moment,
        )
        recovered.append(job)
    if recovered:
        db.flush()
    return recovered


def claim_next_queued_job(
    db: Session,
    *,
    now: datetime | None = None,
    job_id: UUID | None = None,
) -> MlJob | None:
    """Atomically claim one queued job with FOR UPDATE SKIP LOCKED."""

    moment = now or _now()
    query = select(MlJob).where(MlJob.status == JOB_QUEUED)
    if job_id is not None:
        query = query.where(MlJob.id == job_id)
    job = db.scalar(
        query.order_by(MlJob.queued_at.asc()).limit(1).with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    if job.attempts >= job.max_attempts:
        job.status = JOB_FAILED
        job.completed_at = moment
        job.failure_reason = job.failure_reason or "max attempts exhausted before claim"
        db.flush()
        return None
    job.status = JOB_RUNNING
    job.attempts += 1
    job.started_at = moment
    job.heartbeat_at = moment
    db.flush()
    return job


def complete_job(db: Session, job: MlJob, *, now: datetime | None = None) -> None:
    moment = now or _now()
    job.status = JOB_COMPLETED
    job.completed_at = moment
    job.heartbeat_at = moment
    job.failure_reason = None
    db.flush()


def fail_or_retry_job(
    db: Session,
    job: MlJob,
    reason: str,
    *,
    now: datetime | None = None,
) -> None:
    _apply_failure(job, reason=reason, now=now or _now())
    db.flush()


def _apply_failure(job: MlJob, *, reason: str, now: datetime) -> None:
    job.failure_reason = str(reason)[:2048]
    job.heartbeat_at = now
    if job.attempts < job.max_attempts:
        job.status = JOB_QUEUED
        job.queued_at = now
        job.completed_at = None
        return
    job.status = JOB_FAILED
    job.completed_at = now


def execute_job(
    db: Session,
    job: MlJob,
    *,
    runner: JobRunner | None = None,
    now: datetime | None = None,
) -> MlJob:
    """Run a claimed job, then persist completed / failed / requeued state."""

    moment = now or _now()
    job_id = job.id
    target_id = job.target_id
    job_type = job.job_type

    def _heartbeat() -> None:
        current = db.get(MlJob, job_id)
        if current is not None:
            touch_heartbeat(db, current)

    try:
        if job_type == JOB_TYPE_AUTO_TRAIN:
            from app.services.auto_train_service import run_auto_train_job

            if runner is not None:
                runner(db, target_id)
            else:
                run_auto_train_job(db, target_id, on_heartbeat=_heartbeat)
        elif runner is not None:
            runner(db, target_id)
        else:
            raise ValueError(f"unsupported ml job type {job_type!r}")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        current = db.get(MlJob, job_id)
        if current is None:
            raise
        fail_or_retry_job(db, current, str(exc), now=moment)
        db.commit()
        db.refresh(current)
        return current

    current = db.get(MlJob, job_id)
    if current is None:
        raise RuntimeError("ml job disappeared during execution")
    if job_type == JOB_TYPE_AUTO_TRAIN:
        upload = db.get(ClientLabUpload, target_id)
        status = str(upload.pipeline_status or "") if upload is not None else ""
        if status in {COMPLETED, SKIPPED}:
            complete_job(db, current, now=moment)
        else:
            reason = ""
            if upload is not None and isinstance(upload.pipeline_log, dict):
                reason = str(upload.pipeline_log.get("reason") or "")
            fail_or_retry_job(
                db,
                current,
                reason or f"auto-train ended with status {status or 'missing'}",
                now=moment,
            )
    else:
        complete_job(db, current, now=moment)
    db.commit()
    db.refresh(current)
    return current


def process_next_job(
    db: Session,
    *,
    now: datetime | None = None,
    heartbeat_timeout_seconds: float | None = None,
    runner: JobRunner | None = None,
    job_id: UUID | None = None,
) -> MlJob | None:
    """Recover abandoned work, claim one queued job, run it, persist terminal state."""

    recover_abandoned_jobs(
        db, now=now, heartbeat_timeout_seconds=heartbeat_timeout_seconds
    )
    db.commit()
    job = claim_next_queued_job(db, now=now, job_id=job_id)
    if job is None:
        db.commit()
        return None
    db.commit()
    return execute_job(db, job, runner=runner, now=now)
