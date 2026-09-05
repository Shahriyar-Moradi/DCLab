"""How durable ML jobs leave the API process.

Production default is PostgreSQL: persist the row and return. A separate
worker claims with FOR UPDATE SKIP LOCKED. `inline` and `thread` exist only as
explicit local-development adapters.
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
    raw = str(get_settings().ml_job_dispatcher or "postgres").strip().lower()
    if raw in {"inline", "sync", "synchronous"}:
        return InlineJobDispatcher()
    if raw in {"thread", "threads", "daemon"}:
        return ThreadJobDispatcher()
    return PostgresJobDispatcher()
