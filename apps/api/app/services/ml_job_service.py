"""Persist, claim, heartbeat, retry, and recover durable ML jobs."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import ClientLabUpload, MlJob
from app.db.session import get_session_factory
from app.domain.errors import MlJobSpecError, RunCancelledError, RunQuotaExceededError, UnknownJobHandlerError
from app.domain.lab_run_stages import CANCELLED, COMPLETED, IN_PROGRESS_STAGES, NEEDS_INPUT, SKIPPED
from app.services.target_intent_service import upload_has_unresolved_target
from app.domain.ml_jobs import (
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_PRIORITY,
    FORBIDDEN_JOB_PAYLOAD_KEYS,
    HANDLER_AGENTS_RUN,
    HANDLER_LABS_AUTO_TRAIN,
    HANDLER_MODELS_BATCH_PREDICT,
    HANDLER_VERSION_LABS_AUTO_TRAIN,
    JOB_CANCELLED,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_TYPE_AUTO_TRAIN,
    JOB_TYPE_BATCH_PREDICT,
    PAYLOAD_MAX_BYTES,
    PRIORITY_MAX,
    PRIORITY_MIN,
)
from app.services.job_handlers import get_handler, registered_handler_keys

JobRunner = Callable[[Session, UUID], None]

_JOB_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_HANDLER_KEY_RE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_HANDLER_VERSION_RE = re.compile(r"^[a-zA-Z0-9._-]{1,32}$")


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


def _lease_expires(now: datetime) -> datetime:
    return now + timedelta(seconds=_heartbeat_timeout_seconds())


def bound_job_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = {} if payload is None else payload
    if type(data) is not dict:
        raise MlJobSpecError("payload must be a JSON object")
    blocked = sorted(set(data) & set(FORBIDDEN_JOB_PAYLOAD_KEYS))
    if blocked:
        raise MlJobSpecError(f"payload must not contain {', '.join(blocked)}")
    encoded = json.dumps(data, default=str, separators=(",", ":")).encode("utf-8")
    if len(encoded) > PAYLOAD_MAX_BYTES:
        raise MlJobSpecError(f"payload exceeds {PAYLOAD_MAX_BYTES} bytes")
    return data


def _validate_slugs(
    *,
    job_type: str,
    handler_key: str,
    handler_version: str | None,
    priority: int,
) -> None:
    if _JOB_TYPE_RE.fullmatch(job_type) is None:
        raise MlJobSpecError(f"invalid job_type {job_type!r}")
    if _HANDLER_KEY_RE.fullmatch(handler_key) is None:
        raise MlJobSpecError(f"invalid handler_key {handler_key!r}")
    if handler_version is not None and _HANDLER_VERSION_RE.fullmatch(handler_version) is None:
        raise MlJobSpecError(f"invalid handler_version {handler_version!r}")
    if priority < PRIORITY_MIN or priority > PRIORITY_MAX:
        raise MlJobSpecError(f"priority {priority} is out of range")


def create_ml_job(
    db: Session,
    *,
    workspace_id: UUID,
    job_type: str,
    handler_key: str,
    target_id: UUID,
    upload_id: UUID | None = None,
    project_id: UUID | None = None,
    execution_request_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
    pipeline_run_id: UUID | None = None,
    handler_version: str | None = None,
    priority: int = DEFAULT_PRIORITY,
    available_at: datetime | None = None,
    payload: dict[str, Any] | None = None,
    max_attempts: int | None = None,
) -> MlJob:
    """Insert a queued worker job. Duplicate ``upload_id`` returns the existing row."""

    _validate_slugs(
        job_type=job_type,
        handler_key=handler_key,
        handler_version=handler_version,
        priority=priority,
    )
    if job_type == JOB_TYPE_AUTO_TRAIN and upload_id is None:
        raise MlJobSpecError("auto_train jobs require upload_id")
    if upload_id is not None:
        existing = db.scalar(select(MlJob).where(MlJob.upload_id == upload_id))
        if existing is not None:
            return existing
    now = _now()
    job = MlJob(
        workspace_id=workspace_id,
        project_id=project_id,
        execution_request_id=execution_request_id,
        workflow_run_id=workflow_run_id,
        pipeline_run_id=pipeline_run_id,
        job_type=job_type,
        handler_key=handler_key,
        handler_version=handler_version,
        target_id=target_id,
        upload_id=upload_id,
        status=JOB_QUEUED,
        priority=priority,
        available_at=available_at or now,
        payload=bound_job_payload(payload),
        attempts=0,
        max_attempts=max_attempts if max_attempts is not None else _max_attempts(),
        queued_at=now,
    )
    db.add(job)
    db.flush()
    return job


def create_auto_train_job(
    db: Session,
    *,
    workspace_id: UUID,
    upload_id: UUID,
    project_id: UUID | None = None,
    execution_request_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
    pipeline_run_id: UUID | None = None,
    max_attempts: int | None = None,
) -> MlJob:
    """Insert-once auto-train job for this upload. Second persist returns the row."""

    return create_ml_job(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        execution_request_id=execution_request_id,
        workflow_run_id=workflow_run_id,
        pipeline_run_id=pipeline_run_id,
        job_type=JOB_TYPE_AUTO_TRAIN,
        handler_key=HANDLER_LABS_AUTO_TRAIN,
        handler_version=HANDLER_VERSION_LABS_AUTO_TRAIN,
        target_id=upload_id,
        upload_id=upload_id,
        payload={"upload_id": str(upload_id)},
        max_attempts=max_attempts,
    )


def _heartbeat_engine(bind: Engine | Connection | None) -> Engine | None:
    if bind is None:
        return None
    if isinstance(bind, Connection):
        return bind.engine
    return bind


def touch_heartbeat(db: Session, job: MlJob, *, now: datetime | None = None) -> None:
    """In-session heartbeat for tests that already own the job row."""
    moment = now or _now()
    job.heartbeat_at = moment
    job.lease_expires_at = _lease_expires(moment)
    db.flush()


def commit_job_heartbeat(
    job_id: UUID,
    *,
    now: datetime | None = None,
    bind: Engine | Connection | None = None,
) -> bool:
    """Persist only lease/heartbeat on a short-lived session and commit it.

    Must not share the long-running training transaction or flush scientific rows.
    Returns True when cancellation of this running job was requested (P3.1-B2).
    """
    moment = now or _now()
    engine = _heartbeat_engine(bind)
    factory = (
        sessionmaker(bind=engine, autocommit=False, autoflush=False)
        if engine is not None
        else get_session_factory()
    )
    session = factory()
    try:
        requested = session.execute(
            update(MlJob)
            .where(MlJob.id == job_id, MlJob.status == JOB_RUNNING)
            .values(heartbeat_at=moment, lease_expires_at=_lease_expires(moment))
            .returning(MlJob.cancel_requested_at)
        ).first()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return requested is not None and requested[0] is not None


def ensure_run_capacity(
    db: Session, workspace_id: UUID, *, job_types: tuple[str, ...] = (JOB_TYPE_AUTO_TRAIN, JOB_TYPE_BATCH_PREDICT)
) -> None:
    """Refuse a new job of ``job_types`` (default: training runs and batch scoring, one
    shared worker budget) when the workspace holds its maximum of queued + running ones (``RunQuotaExceededError``, 429).
    Serialized per workspace by a transaction-scoped advisory lock, so concurrent
    creators cannot overshoot."""

    limit = int(get_settings().ml_max_active_runs_per_workspace)
    db.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"ml-runs:{workspace_id}", 0)))
    )
    active = db.scalar(
        select(func.count())
        .select_from(MlJob)
        .where(
            MlJob.workspace_id == workspace_id,
            MlJob.job_type.in_(job_types),
            MlJob.status.in_((JOB_QUEUED, JOB_RUNNING)),
        )
    )
    if int(active or 0) >= limit:
        raise RunQuotaExceededError(limit)


def _mark_job_cancelled(job: MlJob, *, now: datetime) -> None:
    job.status = JOB_CANCELLED
    job.completed_at = now
    job.heartbeat_at = now
    job.claimed_by = None
    job.lease_expires_at = None
    job.failure_reason = "cancelled by request"


def request_job_cancellation(
    db: Session, job: MlJob, *, now: datetime | None = None, actor_user_id: UUID | None = None
) -> str:
    """Cancel a queued job now, or ask its worker to stop (cooperative). Idempotent.

    The caller holds ``job`` ``FOR UPDATE`` (a worker claims queued rows with
    SKIP LOCKED, so it can never start a job being cancelled) and commits.
    Returns ``"cancelled"`` / ``"cancelling"``; any other state is returned
    unchanged (completed, failed, or a finished job waiting for input).
    """

    moment = now or _now()
    if job.status in {JOB_QUEUED, JOB_RUNNING} and job.cancel_requested_at is None:
        job.cancel_requested_by_user_id = actor_user_id  # audit: who cancelled
    if job.status == JOB_QUEUED:
        job.cancel_requested_at = job.cancel_requested_at or moment
        _mark_job_cancelled(job, now=moment)
        db.flush()
        return JOB_CANCELLED
    if job.status == JOB_RUNNING:
        if job.cancel_requested_at is None:
            job.cancel_requested_at = moment
            db.flush()
        return "cancelling"
    return str(job.status)


def recover_abandoned_jobs(
    db: Session,
    *,
    now: datetime | None = None,
    heartbeat_timeout_seconds: float | None = None,
) -> list[MlJob]:
    """Requeue or fail running jobs whose lease or heartbeat has expired."""

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
                        MlJob.lease_expires_at.is_not(None),
                        MlJob.lease_expires_at < moment,
                    ),
                    and_(
                        MlJob.lease_expires_at.is_(None),
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
    for job in recovered:
        _sync_batch_prediction(db, job)
        if job.status == JOB_CANCELLED:
            _record_cancelled_run(db, job)  # commits, one job at a time
    return recovered


def claim_next_queued_job(
    db: Session,
    *,
    now: datetime | None = None,
    job_id: UUID | None = None,
    claimed_by: str | None = None,
) -> MlJob | None:
    """Atomically claim one due queued job with FOR UPDATE SKIP LOCKED."""

    moment = now or _now()
    query = select(MlJob).where(
        MlJob.status == JOB_QUEUED,
        MlJob.available_at <= moment,
    )
    if job_id is not None:
        query = query.where(MlJob.id == job_id)
    job = db.scalar(
        query.order_by(
            MlJob.priority.desc(),
            MlJob.available_at.asc(),
            MlJob.queued_at.asc(),
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    if job.attempts >= job.max_attempts:
        job.status = JOB_FAILED
        job.completed_at = moment
        job.claimed_by = None
        job.lease_expires_at = None
        job.failure_reason = job.failure_reason or "max attempts exhausted before claim"
        db.flush()
        _sync_batch_prediction(db, job)
        return None
    worker = (claimed_by or "").strip() or None
    if worker is not None:
        worker = worker[:128]
    job.status = JOB_RUNNING
    job.attempts += 1
    job.started_at = moment
    job.heartbeat_at = moment
    job.claimed_by = worker
    job.lease_expires_at = _lease_expires(moment)
    db.flush()
    return job


def _fail_job_terminally(db: Session, job: MlJob, reason: str, *, now: datetime) -> None:
    """A deterministic failure (the handler recorded it): never retried."""

    job.status = JOB_FAILED
    job.completed_at = now
    job.heartbeat_at = now
    job.claimed_by = None
    job.lease_expires_at = None
    job.failure_reason = str(reason)[:2048]
    db.flush()


def complete_job(db: Session, job: MlJob, *, now: datetime | None = None) -> None:
    moment = now or _now()
    job.status = JOB_COMPLETED
    job.completed_at = moment
    job.heartbeat_at = moment
    job.lease_expires_at = None
    job.failure_reason = None
    db.flush()


def requeue_job(db: Session, job: MlJob, *, now: datetime | None = None) -> MlJob:
    """Return the same row to queued so resume does not insert a second job.

    A cancelled job, or one with a pending cancel request, is never revived.
    """

    if job.status == JOB_CANCELLED or job.cancel_requested_at is not None:
        raise MlJobSpecError("a cancelled job cannot be requeued")
    moment = now or _now()
    if job.status == JOB_QUEUED and job.completed_at is None:
        job.available_at = moment
        db.flush()
        return job
    job.status = JOB_QUEUED
    job.completed_at = None
    job.failure_reason = None
    job.queued_at = moment
    job.available_at = moment
    job.claimed_by = None
    job.lease_expires_at = None
    db.flush()
    return job


def fail_or_retry_job(
    db: Session,
    job: MlJob,
    reason: str,
    *,
    now: datetime | None = None,
) -> None:
    _apply_failure(job, reason=reason, now=now or _now())
    db.flush()
    _sync_batch_prediction(db, job)
    if job.status == JOB_CANCELLED:
        _record_cancelled_run(db, job)


def _sync_batch_prediction(db: Session, job: MlJob) -> None:
    """Keep a scoring job's ``batch_predictions`` row in step with a retry, a
    terminal failure or a cancellation (P4.9-A). Flushes; the caller commits.
    An ``agents.run`` job that ends failed or cancelled ends its agent run too
    (P6.10-A; the gateway frees the run's hold on its own session)."""

    if job.handler_key == HANDLER_MODELS_BATCH_PREDICT:
        from app.services.batch_prediction_service import sync_prediction_with_job

        sync_prediction_with_job(db, job)
    elif job.handler_key == HANDLER_AGENTS_RUN and job.status in (JOB_FAILED, JOB_CANCELLED):
        from app.agents.harness.service import end_run_for_job

        end_run_for_job(db, job)


def _record_cancelled_run(db: Session, job: MlJob) -> None:
    """Keep the run envelope (upload, WorkflowRun, Experiment) in step with a
    cancelled auto-train job. Commits."""

    if job.upload_id is not None and (
        job.job_type == JOB_TYPE_AUTO_TRAIN or job.handler_key == HANDLER_LABS_AUTO_TRAIN
    ):
        from app.services.auto_train_service import record_run_cancelled

        record_run_cancelled(db, job.upload_id, cancelled_by=job.cancel_requested_by_user_id)


def _apply_failure(job: MlJob, *, reason: str, now: datetime) -> None:
    if job.cancel_requested_at is not None:
        # Failed or abandoned after a cancel request: never retried.
        _mark_job_cancelled(job, now=now)
        return
    job.failure_reason = str(reason)[:2048]
    job.heartbeat_at = now
    job.claimed_by = None
    job.lease_expires_at = None
    if job.attempts < job.max_attempts:
        job.status = JOB_QUEUED
        job.queued_at = now
        job.available_at = now
        job.completed_at = None
        return
    job.status = JOB_FAILED
    job.completed_at = now


def execute_job(
    db: Session,
    job: MlJob,
    *,
    runner: JobRunner | None = None,
) -> MlJob:
    """Run a claimed job, then persist completed / failed / requeued state."""

    job_id = job.id
    target_id = job.target_id
    job_type = job.job_type
    handler_key = job.handler_key
    heartbeat_bind = _heartbeat_engine(db.get_bind())

    def _heartbeat() -> None:
        # Stage and candidate checkpoints (P3.1-B2 cooperative cancellation).
        if commit_job_heartbeat(job_id, bind=heartbeat_bind):
            raise RunCancelledError()

    try:
        if runner is not None:
            runner(db, target_id)
        else:
            get_handler(handler_key)(db, job, on_heartbeat=_heartbeat)
    except RunCancelledError:
        db.rollback()  # the interrupted stage's uncommitted work is discarded
        current = db.get(MlJob, job_id)
        if current is None:
            raise RuntimeError("ml job disappeared during execution") from None
        _mark_job_cancelled(current, now=_now())
        _sync_batch_prediction(db, current)
        _record_cancelled_run(db, current)
        db.commit()
        db.refresh(current)
        return current
    except UnknownJobHandlerError as exc:
        db.rollback()
        current = db.get(MlJob, job_id)
        if current is None:
            raise
        fail_or_retry_job(db, current, str(exc), now=_now())
        db.commit()
        db.refresh(current)
        return current
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        current = db.get(MlJob, job_id)
        if current is None:
            raise
        fail_or_retry_job(db, current, str(exc), now=_now())
        db.commit()
        db.refresh(current)
        return current

    current = db.get(MlJob, job_id)
    if current is None:
        raise RuntimeError("ml job disappeared during execution")
    terminal_at = _now()
    if job_type == JOB_TYPE_AUTO_TRAIN or handler_key == HANDLER_LABS_AUTO_TRAIN:
        upload = db.get(ClientLabUpload, target_id)
        status = str(upload.pipeline_status or "") if upload is not None else ""
        waiting = status == NEEDS_INPUT or (
            upload is not None and upload_has_unresolved_target(upload)
        )
        explicit = (
            str(getattr(upload, "explicit_target_column", "") or "").strip()
            if upload is not None
            else ""
        )
        cancel_pending = current.cancel_requested_at is not None and status not in {COMPLETED, SKIPPED}
        if status == CANCELLED or current.status == JOB_CANCELLED or cancel_pending:
            # Also a run that stopped for input (or would resume) after a cancel
            # request: it is cancelled, never completed/requeued.
            _mark_job_cancelled(current, now=terminal_at)
            if status != CANCELLED:
                _record_cancelled_run(db, current)
        elif status in {COMPLETED, SKIPPED} or waiting:
            complete_job(db, current, now=terminal_at)
            if waiting and explicit:
                requeue_job(db, current, now=terminal_at)
                from app.services.auto_train_service import enqueue_auto_train

                enqueue_auto_train(target_id)
        elif status in IN_PROGRESS_STAGES and explicit:
            complete_job(db, current, now=terminal_at)
            requeue_job(db, current, now=terminal_at)
            from app.services.auto_train_service import enqueue_auto_train

            enqueue_auto_train(target_id)
        else:
            reason = ""
            if upload is not None and isinstance(upload.pipeline_log, dict):
                reason = str(upload.pipeline_log.get("reason") or "")
            fail_or_retry_job(
                db,
                current,
                reason or f"auto-train ended with status {status or 'missing'}",
                now=terminal_at,
            )
    elif handler_key == HANDLER_MODELS_BATCH_PREDICT:
        from app.services.batch_prediction_service import prediction_failure_code

        failure = prediction_failure_code(db, current)
        if failure is None:
            complete_job(db, current, now=terminal_at)
        else:
            _fail_job_terminally(db, current, failure, now=terminal_at)
    else:
        complete_job(db, current, now=terminal_at)
    db.commit()
    db.refresh(current)
    return current


def _end_abandoned_assistant_turns(db: Session) -> None:
    """Assistant turns run in the API process, not as jobs; the worker's poll also frees the
    budget holds of turns whose process died (P6.3-B2). Never blocks job processing."""

    from app.agents.harness.service import end_abandoned_turns

    try:
        end_abandoned_turns(db)
    except Exception:  # noqa: BLE001 - logged; the next poll retries
        logging.getLogger("dclab.ml_jobs").exception("could not end abandoned assistant turns")
        db.rollback()


def process_next_job(
    db: Session,
    *,
    now: datetime | None = None,
    heartbeat_timeout_seconds: float | None = None,
    runner: JobRunner | None = None,
    job_id: UUID | None = None,
    claimed_by: str | None = None,
) -> MlJob | None:
    """Recover abandoned work, claim one queued job, run it, persist terminal state."""

    # Import side effect: register shipped handlers before dispatch.
    registered_handler_keys()
    recover_abandoned_jobs(
        db, now=now, heartbeat_timeout_seconds=heartbeat_timeout_seconds
    )
    db.commit()
    _end_abandoned_assistant_turns(db)
    job = claim_next_queued_job(db, now=now, job_id=job_id, claimed_by=claimed_by)
    if job is None:
        db.commit()
        return None
    db.commit()
    return execute_job(db, job, runner=runner)
