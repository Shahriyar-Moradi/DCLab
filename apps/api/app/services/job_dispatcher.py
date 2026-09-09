"""How durable ML jobs leave the API process.

Production default is PostgreSQL: persist the row and return. A separate
worker claims with FOR UPDATE SKIP LOCKED. `inline` and `thread` exist only as
explicit local-development adapters.

`thread` also starts one in-process claim loop so leftover queued rows (from
before the API was in thread mode) are drained, not only newly dispatched jobs.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from uuid import UUID

from app.config import get_settings
from app.db.session import get_session_factory
from app.services.ml_job_service import process_next_job

logger = logging.getLogger(__name__)

_local_worker_lock = threading.Lock()
_local_worker_stop: threading.Event | None = None


def _dispatcher_name() -> str:
    return str(get_settings().ml_job_dispatcher or "postgres").strip().lower()


def uses_in_process_worker() -> bool:
    return _dispatcher_name() in {"thread", "threads", "daemon"}


def start_local_ml_worker() -> threading.Event | None:
    """Claim loop for the thread dispatcher. No-op for postgres / inline."""

    if not uses_in_process_worker():
        return None
    global _local_worker_stop
    with _local_worker_lock:
        if _local_worker_stop is not None and not _local_worker_stop.is_set():
            return _local_worker_stop
        stop = threading.Event()
        poll = max(0.1, float(get_settings().ml_job_poll_seconds))

        def _loop() -> None:
            while not stop.is_set():
                session = get_session_factory()()
                job = None
                try:
                    job = process_next_job(session)
                except Exception:  # noqa: BLE001
                    logger.exception("local ml worker failed")
                finally:
                    session.close()
                if job is None:
                    stop.wait(poll)

        threading.Thread(target=_loop, daemon=True, name="ml-job-worker").start()
        _local_worker_stop = stop
        logger.info("local ml worker claiming queued jobs in this process")
        return stop


class JobDispatcher(ABC):
    """Enqueue side of the job boundary. Persistence already happened."""

    name: str

    @abstractmethod
    def dispatch(self, upload_id: UUID, job_id: UUID | None = None) -> None:
        """Start work for an already-persisted job, or no-op if a worker will claim it."""


class PostgresJobDispatcher(JobDispatcher):
    """Production adapter: the queued row is the work queue. No in-process execution."""

    name = "postgres"

    def dispatch(self, upload_id: UUID, job_id: UUID | None = None) -> None:
        return None


class InlineJobDispatcher(JobDispatcher):
    """Synchronous in-process run. Opt-in for local scripts; blocks the caller."""

    name = "inline"

    def dispatch(self, upload_id: UUID, job_id: UUID | None = None) -> None:
        session = get_session_factory()()
        try:
            process_next_job(session, job_id=job_id)
        except Exception:  # noqa: BLE001
            logger.exception("inline ml job failed upload=%s job=%s", upload_id, job_id)
        finally:
            session.close()


class ThreadJobDispatcher(JobDispatcher):
    """Background thread in this process. Opt-in local adapter; not durable across restart."""

    name = "thread"

    def dispatch(self, upload_id: UUID, job_id: UUID | None = None) -> None:
        start_local_ml_worker()

        def _worker() -> None:
            session = get_session_factory()()
            try:
                process_next_job(session, job_id=job_id)
            except Exception:  # noqa: BLE001
                logger.exception("thread ml job failed upload=%s job=%s", upload_id, job_id)
            finally:
                session.close()

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"auto-train-{upload_id}",
        ).start()


def get_job_dispatcher() -> JobDispatcher:
    raw = _dispatcher_name()
    if raw in {"inline", "sync", "synchronous"}:
        return InlineJobDispatcher()
    if uses_in_process_worker():
        return ThreadJobDispatcher()
    return PostgresJobDispatcher()
