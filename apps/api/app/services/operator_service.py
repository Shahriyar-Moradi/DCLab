"""Platform-staff operator reads (P8.5-A): cross-tenant ids and counts, never customer rows.

The route layer authorizes (human session + platform role); this module only reads. Failure
reasons are free text (exception messages can carry customer values), so they are mapped to a
fixed set of codes here and never returned.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.orm import Session

from app.agents.governance.platform_default import CAPS, PLATFORM_MODEL_ALLOWLIST
from app.config import get_settings
from app.db.models import MlJob, Project, Workspace, WorkspaceMembership
from app.domain import batch_predictions as bp
from app.domain.errors import InvalidCursorError
from app.domain.ml_jobs import JOB_FAILED
from app.domain.operator import (
    FailureCodeCountRead,
    JobCountRead,
    OperatorFailedJobPage,
    OperatorFailedJobRead,
    OperatorOverviewRead,
    OperatorWorkspacePage,
    OperatorWorkspaceRead,
    PlatformAiCapsRead,
    QueueRead,
    WorkspaceTotalsRead,
)
from app.services import system_health_service as health
from app.services.cursor_codec import open_cursor, sign_cursor

PAGE_DEFAULT, PAGE_MAX = 50, 200
FAILED_CODES_SCAN_MAX = 5000
SEE_LOGS = "see_logs"
# Codes the worker itself writes as ``failure_reason`` (batch scoring); anything else is free text.
KNOWN_FAILURE_CODES = frozenset(
    {
        bp.FEATURE_CONTRACT_FAILED, bp.UNSUPPORTED_TRANSFORM, bp.UNSUPPORTED_MODEL, bp.SCORING_FAILED,
        bp.DATASET_UNAVAILABLE, bp.RELEASE_MISMATCH, bp.SCHEDULE_OWNER_NOT_ALLOWED,
    }
)
_PREFIX_CODES = (
    ("abandoned", "heartbeat_expired"),
    ("unsupported ml job handler", "unknown_handler"),
    ("auto-train ended with status", "auto_train_failed"),
)


def _now() -> datetime:
    return datetime.now(UTC)


def failure_code(reason: str | None) -> str:
    text = (reason or "").strip()
    if text in KNOWN_FAILURE_CODES:
        return text
    lowered = text.lower()
    for prefix, code in _PREFIX_CODES:
        if lowered.startswith(prefix):
            return code
    return SEE_LOGS


def platform_ai_caps() -> PlatformAiCapsRead:
    return PlatformAiCapsRead(
        ai_enabled=bool(get_settings().ai_enabled),
        max_data_class=CAPS.data.max_class,
        sample_values_per_column=CAPS.data.sample_values_per_column,
        user_text_to_jev=CAPS.data.user_text_to_jev,
        auto_release=CAPS.autonomy.ops.auto_release,
        model_allowlist=sorted(PLATFORM_MODEL_ALLOWLIST),
        budgets=CAPS.budgets.model_dump(),
    )


def overview(db: Session, *, now: datetime | None = None) -> OperatorOverviewRead:
    moment = now or _now()
    day, week = moment - timedelta(hours=24), moment - timedelta(days=7)
    total = int(db.scalar(select(func.count(Workspace.id))) or 0)
    active = int(db.scalar(select(func.count(func.distinct(MlJob.workspace_id))).where(MlJob.created_at >= week)) or 0)
    jobs = db.execute(
        select(MlJob.job_type, MlJob.status, func.count(MlJob.id))
        .where(MlJob.created_at >= day)
        .group_by(MlJob.job_type, MlJob.status)
        .order_by(MlJob.job_type, MlJob.status)
    ).all()
    failed_at = func.coalesce(MlJob.completed_at, MlJob.created_at)
    reasons = db.scalars(
        select(MlJob.failure_reason)
        .where(MlJob.status == JOB_FAILED, failed_at >= day)
        .order_by(failed_at.desc(), MlJob.id.desc())
        .limit(FAILED_CODES_SCAN_MAX + 1)
    ).all()
    truncated = len(reasons) > FAILED_CODES_SCAN_MAX
    reasons = reasons[:FAILED_CODES_SCAN_MAX]
    codes: dict[str, int] = {}
    for reason in reasons:
        code = failure_code(reason)
        codes[code] = codes.get(code, 0) + 1
    depth, age = health.queue_stats(db, now=moment)
    return OperatorOverviewRead(
        generated_at=moment,
        workspaces=WorkspaceTotalsRead(total=total, active_last_7d=active),
        jobs_last_24h=[JobCountRead(job_type=t, status=s, count=int(c)) for t, s, c in jobs],
        queue=QueueRead(depth=depth, oldest_age_seconds=round(age, 1) if age is not None else None),
        workers=health.workers(db, now=moment),
        failed_jobs_last_24h=[FailureCodeCountRead(code=k, count=v) for k, v in sorted(codes.items())],
        failed_jobs_last_24h_truncated=truncated,
        platform_ai_caps=platform_ai_caps(),
        readiness=health.readiness(db),
    )


def _open(cursor: str | None, scope: str) -> list | None:
    if cursor is None or not cursor.strip():
        return None
    message = "cursor is not a cursor of this operator list"
    try:
        stamp, row_id = open_cursor(cursor, scope, message=message)
        moment = datetime.fromisoformat(stamp)
        if moment.tzinfo is None:
            raise ValueError(message)
        return [moment, UUID(row_id)]
    except (InvalidCursorError, ValueError, TypeError) as exc:
        raise InvalidCursorError(message) from exc


def _counts(db: Session, stmt: Select, ids: list[UUID]) -> dict[UUID, int]:
    return {row[0]: int(row[1]) for row in db.execute(stmt).all()} if ids else {}


def list_workspaces(db: Session, *, cursor: str | None = None, limit: int = PAGE_DEFAULT,
                    now: datetime | None = None) -> OperatorWorkspacePage:
    moment, scope, limit = now or _now(), "operator:workspaces", max(1, min(limit, PAGE_MAX))
    stmt = select(Workspace.id, Workspace.name, Workspace.kind, Workspace.created_at)
    position = _open(cursor, scope)
    if position is not None:
        stmt = stmt.where(tuple_(Workspace.created_at, Workspace.id) > tuple_(*position))
    rows = db.execute(stmt.order_by(Workspace.created_at, Workspace.id).limit(limit + 1)).all()
    page = rows[:limit]
    ids = [row.id for row in page]
    in_page = MlJob.workspace_id.in_(ids)
    members = _counts(db, select(WorkspaceMembership.workspace_id, func.count()).where(
        WorkspaceMembership.workspace_id.in_(ids), WorkspaceMembership.suspended_at.is_(None)
    ).group_by(WorkspaceMembership.workspace_id), ids)
    projects = _counts(db, select(Project.workspace_id, func.count()).where(Project.workspace_id.in_(ids))
                       .group_by(Project.workspace_id), ids)
    jobs_30d = _counts(db, select(MlJob.workspace_id, func.count()).where(
        in_page, MlJob.created_at >= moment - timedelta(days=30)).group_by(MlJob.workspace_id), ids)
    failed_24h = _counts(db, select(MlJob.workspace_id, func.count()).where(
        in_page, MlJob.status == JOB_FAILED,
        func.coalesce(MlJob.completed_at, MlJob.created_at) >= moment - timedelta(hours=24),
    ).group_by(MlJob.workspace_id), ids)
    active = _counts(db, select(MlJob.workspace_id, func.count()).where(
        in_page, MlJob.created_at >= moment - timedelta(days=7)).group_by(MlJob.workspace_id), ids)
    items = [
        OperatorWorkspaceRead(
            id=row.id, name=row.name, kind=row.kind, created_at=row.created_at,
            member_count=members.get(row.id, 0), project_count=projects.get(row.id, 0),
            jobs_last_30d=jobs_30d.get(row.id, 0), failed_jobs_last_24h=failed_24h.get(row.id, 0),
            active_last_7d=active.get(row.id, 0) > 0,
        )
        for row in page
    ]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = sign_cursor(scope, [last.created_at.isoformat(), str(last.id)])
    return OperatorWorkspacePage(items=items, next_cursor=next_cursor)


def list_failed_jobs(db: Session, *, cursor: str | None = None, limit: int = PAGE_DEFAULT) -> OperatorFailedJobPage:
    scope, limit = "operator:failed-jobs", max(1, min(limit, PAGE_MAX))
    failed_at = func.coalesce(MlJob.completed_at, MlJob.created_at)
    stmt = select(MlJob.id, MlJob.workspace_id, MlJob.job_type, MlJob.handler_key, MlJob.failure_reason,
                  MlJob.attempts, failed_at.label("failed_at")).where(MlJob.status == JOB_FAILED)
    position = _open(cursor, scope)
    if position is not None:
        stmt = stmt.where(tuple_(failed_at, MlJob.id) < tuple_(*position))
    rows = db.execute(stmt.order_by(failed_at.desc(), MlJob.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    items = [
        OperatorFailedJobRead(
            id=row.id, workspace_id=row.workspace_id, job_type=row.job_type, handler_key=row.handler_key,
            code=failure_code(row.failure_reason), attempts=row.attempts, failed_at=row.failed_at,
        )
        for row in page
    ]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = sign_cursor(scope, [last.failed_at.isoformat(), str(last.id)])
    return OperatorFailedJobPage(items=items, next_cursor=next_cursor)

