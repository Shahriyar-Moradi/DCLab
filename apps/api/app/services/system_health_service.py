"""Readiness checks, queue and worker liveness (P8.5-A). Read-only, bounded queries.

Worker liveness reuses what exists: each ``dclab worker run`` process names its database
connections ``dclab-worker:<random instance>`` (``PGAPPNAME``), so ``pg_stat_activity`` shows
when each worker last talked to the database (its poll runs every ``ML_JOB_POLL_SECONDS``),
and a running job's own ``ml_jobs.heartbeat_at`` covers a worker busy in a long stage. Nothing
new is stored. ``pg_stat_activity`` shows ``state`` / ``state_change`` of other sessions only to
the same database role: run the API and the worker as one role (or expose only (application_name,
state_change) of ``dclab-worker:%`` sessions through a narrow SECURITY DEFINER function). Do not grant
``pg_read_all_stats``: it also exposes every session's query text. Not visible -> ``unknown``.

``readiness`` is cached process-wide for ``READINESS_CACHE_SECONDS`` (``/health/ready`` is
unauthenticated; probes must not fan out to the database and object storage on every hit).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import REPO_ROOT, get_settings
from app.db.models import MlJob, ScoringSchedule
from app.domain.ml_jobs import JOB_QUEUED, JOB_RUNNING
from app.domain.operator import ReadinessCheckRead, ReadinessRead, WorkersRead

logger = logging.getLogger("dclab.health")
WORKER_APPLICATION_PREFIX = "dclab-worker"
MAX_WORKERS_LISTED = 50
SCHEDULE_OVERDUE_GRACE_SECONDS = 15 * 60
READINESS_CHECKS = ("database", "migrations", "object_storage", "worker")
REQUIRED_CHECKS = frozenset({"database", "migrations", "object_storage"})


def worker_application_name() -> str:
    return f"{WORKER_APPLICATION_PREFIX}:{uuid4().hex[:12]}"


def mark_worker_connections() -> str:
    """Called by ``dclab worker run`` before its first database connection (libpq reads PGAPPNAME)."""

    current = os.environ.get("PGAPPNAME", "")
    if not current.startswith(WORKER_APPLICATION_PREFIX + ":"):
        os.environ["PGAPPNAME"] = worker_application_name()
    return os.environ["PGAPPNAME"]


def _now() -> datetime:
    return datetime.now(UTC)


def _check(name: str, status: str, code: str | None = None) -> ReadinessCheckRead:
    return ReadinessCheckRead(name=name, status=status, required=name in REQUIRED_CHECKS, code=code)


def _failed(name: str, exc: BaseException) -> None:
    # The exception type only: messages can carry hosts, bucket names or credentials.
    logger.warning("readiness check %s failed: %s", name, type(exc).__name__)


def check_database(db: Session) -> ReadinessCheckRead:
    try:
        with db.get_bind().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any failure is "unreachable"
        _failed("database", exc)
        return _check("database", "failing", "unreachable")
    return _check("database", "ok")


@lru_cache(maxsize=1)
def _script_heads() -> frozenset[str] | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    location = REPO_ROOT / "apps" / "api" / "alembic"
    if not location.is_dir():
        return None
    config = Config()
    config.set_main_option("script_location", str(location))
    plugins = logging.getLogger("alembic.runtime.plugins")  # one INFO line per plugin on first load
    plugins.setLevel(max(plugins.getEffectiveLevel(), logging.WARNING))
    return frozenset(ScriptDirectory.from_config(config).get_heads())


def check_migrations(db: Session) -> ReadinessCheckRead:
    try:
        heads = _script_heads()
    except Exception:  # noqa: BLE001
        heads = None
    if heads is None:
        return _check("migrations", "unknown", "scripts_unavailable")
    try:
        with db.get_bind().connect() as connection:
            if connection.execute(text("SELECT to_regclass('alembic_version')")).scalar() is None:
                return _check("migrations", "unknown", "not_versioned")
            current = frozenset(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    except Exception as exc:  # noqa: BLE001
        _failed("migrations", exc)
        return _check("migrations", "failing", "unreachable")
    return _check("migrations", "ok") if current == heads else _check("migrations", "failing", "not_at_head")


def check_object_storage() -> ReadinessCheckRead:
    from app.storage.factory import get_object_storage

    try:
        get_object_storage().exists("_dclab/readiness-probe")
    except Exception as exc:  # noqa: BLE001
        _failed("object_storage", exc)
        return _check("object_storage", "failing", "unreachable")
    return _check("object_storage", "ok")


def worker_heartbeat_ages(db: Session, *, now: datetime | None = None) -> list[float] | None:
    """Seconds since each connected worker process last talked to the database (freshest
    first); None when ``pg_stat_activity`` cannot be read or hides the workers' state (another
    database role: ``state_change`` is NULL), never a false "no worker". Running jobs count too."""

    moment = now or _now()
    try:
        with db.get_bind().connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT application_name, max(CASE WHEN state = 'active' THEN now() "
                    "ELSE state_change END) AS seen FROM pg_stat_activity "
                    "WHERE datname = current_database() AND application_name LIKE :prefix "
                    "GROUP BY application_name ORDER BY seen DESC NULLS LAST LIMIT :limit"
                ),
                {"prefix": WORKER_APPLICATION_PREFIX + ":%", "limit": MAX_WORKERS_LISTED},
            ).all()
    except Exception:  # noqa: BLE001
        return None
    if any(seen is None for _name, seen in rows):
        return None
    ages = [max(0.0, (moment - seen).total_seconds()) for _name, seen in rows]
    running = db.scalar(select(func.max(MlJob.heartbeat_at)).where(MlJob.status == JOB_RUNNING))
    if running is not None and ages:
        ages[0] = min(ages[0], max(0.0, (moment - running).total_seconds()))
    return sorted(ages)


def workers(db: Session, *, now: datetime | None = None) -> WorkersRead:
    settings = get_settings()
    ages = worker_heartbeat_ages(db, now=now)
    return WorkersRead(
        dispatcher=settings.ml_job_dispatcher,
        count=len(ages or []),
        heartbeat_age_seconds=[round(age, 1) for age in ages or []],
        visible=ages is not None,
    )


def check_worker(db: Session, *, now: datetime | None = None) -> ReadinessCheckRead:
    settings = get_settings()
    if settings.ml_job_dispatcher != "postgres":
        return _check("worker", "skipped", "in_process_dispatcher")
    ages = worker_heartbeat_ages(db, now=now)
    if ages is None:
        return _check("worker", "unknown", "activity_not_visible")
    if not ages:
        return _check("worker", "stale", "no_worker_seen")
    if ages[0] > float(settings.worker_heartbeat_stale_seconds):
        return _check("worker", "stale", "heartbeat_old")
    return _check("worker", "ok")


_READINESS_CACHE: dict[str, Any] = {"at": None, "value": None}
_READINESS_LOCK = threading.Lock()
_clock = time.monotonic  # patched by tests


def reset_readiness_cache() -> None:
    with _READINESS_LOCK:
        _READINESS_CACHE.update(at=None, value=None)


def cached_readiness(db: Session) -> ReadinessRead:
    """``readiness`` at most once per ``READINESS_CACHE_SECONDS`` per process (0: no cache)."""

    ttl = float(get_settings().readiness_cache_seconds)
    with _READINESS_LOCK:
        at, value = _READINESS_CACHE["at"], _READINESS_CACHE["value"]
        if ttl > 0 and value is not None and at is not None and _clock() - at < ttl:
            return value
        value = readiness(db)
        _READINESS_CACHE.update(at=_clock(), value=value)
        return value


def readiness(db: Session) -> ReadinessRead:
    checks = [check_database(db)]
    if checks[0].status == "ok":
        checks += [check_migrations(db), check_object_storage(), check_worker(db)]
    else:
        checks += [_check("migrations", "unknown", "database_unreachable"), check_object_storage(),
                   _check("worker", "unknown", "database_unreachable")]
    if any(check.required and check.status == "failing" for check in checks):
        status = "not_ready"
    elif all(check.status in ("ok", "skipped") for check in checks):
        status = "ready"
    else:
        status = "degraded"
    return ReadinessRead(status=status, checks=checks)


def queue_stats(db: Session, *, now: datetime | None = None) -> tuple[int, float | None]:
    """(queued jobs, age in seconds of the oldest queued job)."""

    moment = now or _now()
    depth, oldest = db.execute(
        select(func.count(MlJob.id), func.min(MlJob.queued_at)).where(MlJob.status == JOB_QUEUED)
    ).one()
    age = max(0.0, (moment - oldest).total_seconds()) if oldest is not None else None
    return int(depth or 0), age


def overdue_schedules(db: Session, *, now: datetime | None = None) -> int:
    cutoff = (now or _now()) - timedelta(seconds=SCHEDULE_OVERDUE_GRACE_SECONDS)
    return int(db.scalar(
        select(func.count(ScoringSchedule.id)).where(
            ScoringSchedule.enabled.is_(True), ScoringSchedule.next_planned_for < cutoff
        )
    ) or 0)


def gauge_snapshot() -> dict[str, list[tuple[float, dict[str, Any]]]]:
    """Every gauge of ``observability.metrics`` from one short session (API process). Readiness
    is computed first so ``dclab_readiness_ok`` is exported even when the database is down;
    worker gauges only when ``pg_stat_activity`` is visible (no false "no worker" alert)."""

    from app.db.session import get_session_factory

    db = get_session_factory()()
    try:
        ready = cached_readiness(db)
        values: dict[str, list[tuple[float, dict[str, Any]]]] = {
            "dclab_readiness_ok": [
                (0.0 if check.status == "failing" else 1.0, {"check": check.name}) for check in ready.checks
            ],
        }
        if ready.checks[0].status != "ok":
            return values
        depth, age = queue_stats(db)
        values["dclab_ml_job_queue_depth"] = [(float(depth), {})]
        values["dclab_ml_job_queue_age_seconds"] = [(float(age or 0.0), {})]
        values["dclab_scoring_schedules_overdue"] = [(float(overdue_schedules(db)), {})]
        ages = worker_heartbeat_ages(db)
        if ages is not None:
            values["dclab_workers_seen"] = [(float(len(ages)), {})]
            if ages:
                values["dclab_worker_heartbeat_age_seconds"] = [(ages[0], {})]
        return values
    finally:
        db.close()
