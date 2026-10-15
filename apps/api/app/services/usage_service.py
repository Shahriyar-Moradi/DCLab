"""Usage metering, plan limits and the usage read (P8.4-A). Vocabulary: ``app.domain.usage``.

Aggregation (worker): ``run_usage_metering_tick`` runs inside ``dclab worker run`` between jobs
(like the weekly-scoring tick; no new process or queue). Each tick takes at most
``TICK_MAX_WORKSPACES`` workspaces with ledger activity since yesterday 00:00 UTC (round robin),
each in its own transaction under a per-workspace ``pg_try_advisory_xact_lock`` (another worker
holding it: skipped this tick), and writes yesterday's and today's-so-far numbers. A row is
written only when a number changed since the latest record, with ``source_ref`` =
``ledgers.v1:asof:<UTC hour>``; a second tick in the same hour conflicts and writes nothing
(the series lags by at most an hour plus one tick). "Latest" is the newest row by
``created_at`` (``clock_timestamp()``; ticks of one workspace are serialized by the advisory
lock), so a correction wins by being written later, whatever its ``source_ref``. One
workspace's failure is logged and never blocks the others. ``USAGE_METERING_ENABLED=false``
stops it (read by the worker: restart). Only yesterday and today are (re)computed: after more
than a day of worker downtime the missed days are not backfilled.

Limits (API): ``ensure_within_quota`` is THE limit check, called from ``ensure_run_capacity``
(training runs, branches, scorings, scheduled scorings, legacy Labs uploads, and resuming a run
parked for a target or split confirmation) and from the upload staging step (storage). It
measures ``used`` live from the ledgers (the read's quota bars use the same measure).
``runs_per_day`` is counted under the existing ``ml-runs`` advisory lock, so concurrent creators
cannot overshoot it; the monthly compute total and storage are check-then-act (a request in
flight can go slightly past a limit; work already running is never stopped, and the worker
never checks). Reads, downloads, approvals and rollbacks never call it. ``QUOTAS_ENFORCED=false``
shows limits without refusing anything.

Compute time counts training and scoring jobs only (``RUN_JOB_TYPES``), the wall-clock of each
job's last attempt (an estimate: time of earlier failed attempts is not counted). Agent runs are
bounded by the AI budget (``workspace_llm_budgets``); monitoring and session clean-up jobs are
platform work and are not counted.

Offboarding: ``usage_records`` is append-only, so deleting a metered workspace needs an operator
to remove its records first (TRUNCATE, or disabling ``usage_records_append_only`` in a
maintenance transaction), like the other append-only tables.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Date, cast, func, select, union
from sqlalchemy.dialects.postgresql import distinct_on, insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AgentRun, Artifact, LlmInvocation, MlJob, UsageRecord, User, Workspace
from app.domain.errors import IdentityError, QuotaExceededError
from app.domain.ml_jobs import JOB_TYPE_AUTO_TRAIN, JOB_TYPE_BATCH_PREDICT
from app.domain.usage import (
    AGENT_RUN_KINDS_COUNTED,
    ASSISTANT_TURN_PURPOSE,
    BETA_LIMITS,
    DEFAULT_PLAN,
    DISCLAIMER,
    ENFORCED_QUOTAS,
    ENTITLEMENT_SOURCE_PLATFORM,
    KIND_AGENT_RUNS,
    KIND_ASSISTANT_RUNS,
    KIND_COMPUTE_SECONDS,
    KIND_LLM_COST_MICROS,
    KIND_LLM_TOKENS,
    KIND_STORAGE_BYTES,
    KIND_UNITS,
    LEDGER_VERSION,
    NOTES,
    PLAN_ENTITLEMENT_KEY,
    PLAN_NAME_PATTERN,
    QUOTA_COMPUTE_SECONDS_MONTH,
    QUOTA_MEMBERS,
    QUOTA_PERIODS,
    QUOTA_RUNS_PER_DAY,
    QUOTA_STORAGE_BYTES,
    QUOTA_UNITS,
    SERIES_DAYS,
    SNAPSHOT_KINDS,
    USAGE_KINDS,
    WARNING_FRACTION,
    AiSpendRead,
    UsageDayRead,
    UsageQuotaRead,
    UsageRead,
    UsageTotalsRead,
)
from app.services.authorization_service import can_perform_ml_write, can_write_platform
from app.services.workspace_entitlement_service import (
    MAX_MEMBERS,
    SOURCE_SYSTEM_DEFAULT,
    get_entitlement,
    max_members_for,
    member_count,
    set_entitlement,
)

logger = logging.getLogger(__name__)

RUN_JOB_TYPES = (JOB_TYPE_AUTO_TRAIN, JOB_TYPE_BATCH_PREDICT)  # the set ensure_run_capacity counts
TICK_MAX_WORKSPACES = 100
_ACTIVE_SCAN_MAX = 10_000
_ROTATION: dict[str, str | None] = {"after": None}  # in-process round robin over active workspaces


def _now() -> datetime:
    return datetime.now(UTC)


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def next_month(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def source_ref(moment: datetime) -> str:
    return f"{LEDGER_VERSION}:asof:{moment.astimezone(UTC):%Y-%m-%dt%H}"


def _utc_day(column: Any) -> Any:
    return cast(func.timezone("UTC", column), Date)


# --- measures (the ledgers) ---------------------------------------------------------------------


def _compute_seconds(workspace_id: UUID) -> Any:
    return (select(func.coalesce(func.sum(func.extract("epoch", MlJob.completed_at - MlJob.started_at)), 0))
            .where(MlJob.workspace_id == workspace_id, MlJob.job_type.in_(RUN_JOB_TYPES),
                   MlJob.started_at.is_not(None), MlJob.completed_at.is_not(None),
                   MlJob.completed_at >= MlJob.started_at))


def storage_bytes_at(db: Session, workspace_id: UUID, moment: datetime) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(Artifact.size_bytes), 0))
                         .where(Artifact.workspace_id == workspace_id, Artifact.created_at < moment)) or 0)


def daily_quantities(db: Session, workspace_id: UUID, start: datetime, end: datetime) -> dict[str, dict[date, int]]:
    """Per-UTC-day totals of every summed kind for ``[start, end)`` (storage is a snapshot: see
    ``storage_bytes_at``)."""

    out: dict[str, dict[date, int]] = {kind: {} for kind in USAGE_KINDS}

    def grouped(kind: str, statement: Any, column: Any) -> None:
        day = _utc_day(column)
        query = statement.add_columns(day).where(column >= start, column < end).group_by(day)
        for value, when in db.execute(query).all():
            out[kind][when] = int(value or 0)

    grouped(KIND_COMPUTE_SECONDS, _compute_seconds(workspace_id), MlJob.completed_at)
    tokens = func.coalesce(LlmInvocation.total_tokens,
                           func.coalesce(LlmInvocation.input_tokens, 0) + func.coalesce(LlmInvocation.output_tokens, 0))
    llm = LlmInvocation.workspace_id == workspace_id
    grouped(KIND_LLM_TOKENS, select(func.coalesce(func.sum(tokens), 0)).where(llm), LlmInvocation.created_at)
    grouped(KIND_LLM_COST_MICROS, select(func.coalesce(func.sum(LlmInvocation.cost_micros), 0)).where(llm),
            LlmInvocation.created_at)
    grouped(KIND_AGENT_RUNS, select(func.count()).where(
        AgentRun.workspace_id == workspace_id, AgentRun.kind.in_(AGENT_RUN_KINDS_COUNTED),
        AgentRun.purpose != ASSISTANT_TURN_PURPOSE), AgentRun.created_at)
    grouped(KIND_ASSISTANT_RUNS, select(func.count()).where(
        AgentRun.workspace_id == workspace_id, AgentRun.purpose == ASSISTANT_TURN_PURPOSE), AgentRun.created_at)
    return out


def measure_quota(db: Session, workspace_id: UUID, key: str, moment: datetime) -> int:
    """``used`` of a quota now: the number the limit check compares and the read shows."""

    today = moment.astimezone(UTC).date()
    if key == QUOTA_COMPUTE_SECONDS_MONTH:
        since = day_start(today.replace(day=1))
        return int(db.scalar(_compute_seconds(workspace_id).where(MlJob.completed_at >= since)) or 0)
    if key == QUOTA_STORAGE_BYTES:
        return int(db.scalar(select(func.coalesce(func.sum(Artifact.size_bytes), 0))
                             .where(Artifact.workspace_id == workspace_id)) or 0)
    if key == QUOTA_RUNS_PER_DAY:
        return int(db.scalar(select(func.count()).select_from(MlJob).where(
            MlJob.workspace_id == workspace_id, MlJob.job_type.in_(RUN_JOB_TYPES),
            MlJob.created_at >= day_start(today))) or 0)
    if key == QUOTA_MEMBERS:
        return member_count(db, workspace_id)
    raise ValueError(f"unknown quota {key!r}")


def resets_at(key: str, moment: datetime) -> datetime | None:
    today = moment.astimezone(UTC).date()
    period = QUOTA_PERIODS[key]
    if period == "month":
        return day_start(next_month(today))
    if period == "day":
        return day_start(today + timedelta(days=1))
    return None


# --- plans --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class QuotaLimit:
    value: int | None  # None: unlimited
    source: str  # plan_default | workspace


def _as_limit(raw: Any) -> int | None | bool:
    """A stored value: int >= 0, or None (unlimited). Anything else returns False (invalid)."""

    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return False
    return raw


def quota_limit(db: Session, workspace_id: UUID, key: str) -> QuotaLimit:
    if key == QUOTA_MEMBERS:
        row = get_entitlement(db, workspace_id, MAX_MEMBERS)
        source = "workspace" if row is not None and row.source != SOURCE_SYSTEM_DEFAULT else "plan_default"
        return QuotaLimit(max_members_for(db, workspace_id), source)
    row = get_entitlement(db, workspace_id, key)
    if row is None:
        return QuotaLimit(BETA_LIMITS[key], "plan_default")
    value = _as_limit(row.value_json)
    if value is False:  # an invalid stored value never unlocks anything: the default applies
        logger.warning("workspace %s: invalid %s entitlement; using the default plan", workspace_id, key)
        return QuotaLimit(BETA_LIMITS[key], "plan_default")
    return QuotaLimit(value, "workspace")  # type: ignore[arg-type]


def plan_name(db: Session, workspace_id: UUID) -> str:
    row = get_entitlement(db, workspace_id, PLAN_ENTITLEMENT_KEY)
    value = row.value_json if row is not None else None
    return value if isinstance(value, str) and re.fullmatch(PLAN_NAME_PATTERN, value) else DEFAULT_PLAN


UNSET = object()  # set_plan: remove the row (the default plan's value applies again)


def set_plan(db: Session, *, actor: User, workspace_id: UUID, plan: str | None = None,
             limits: dict[str, Any] | None = None) -> dict[str, Any]:
    """Platform staff (``dclab_admin``) only: set the plan name and limits of a workspace. Each
    value is an int >= 0, ``None`` (unlimited) or ``UNSET`` (back to the default plan). Tenants
    have no path to this (no HTTP route). The caller commits."""

    if not can_write_platform(db, actor):
        raise IdentityError("setting a workspace plan requires a DCLab platform administrator", status_code=403)
    if db.get(Workspace, workspace_id) is None:
        raise IdentityError("workspace not found", status_code=404)
    source = f"{ENTITLEMENT_SOURCE_PLATFORM}:{actor.id}"
    if plan is not None:
        if not re.fullmatch(PLAN_NAME_PATTERN, plan):
            raise IdentityError(f"plan name must match {PLAN_NAME_PATTERN}", status_code=422)
        set_entitlement(db, workspace_id, PLAN_ENTITLEMENT_KEY, plan, source=source)
    for key, value in (limits or {}).items():
        if key not in ENFORCED_QUOTAS:
            raise IdentityError(f"unknown limit {key!r}; one of {', '.join(ENFORCED_QUOTAS)}", status_code=422)
        if value is UNSET:
            row = get_entitlement(db, workspace_id, key)
            if row is not None:
                db.delete(row)
                db.flush()
            continue
        if _as_limit(value) is False:
            raise IdentityError(f"{key} must be a whole number >= 0 or unlimited", status_code=422)
        set_entitlement(db, workspace_id, key, value, source=source)
    return {"workspace_id": str(workspace_id), "plan": plan_name(db, workspace_id),
            "limits": {key: quota_limit(db, workspace_id, key).value for key in ENFORCED_QUOTAS}}


# --- the limit check ----------------------------------------------------------------------------


def _hours(seconds: int) -> str:
    return f"{seconds / 3600:.1f}"


def _gb(size: int) -> str:
    return f"{size / 1024**3:.1f}"


def _message(key: str, used: int, limit: int, reset: datetime | None) -> str:
    if key == QUOTA_COMPUTE_SECONDS_MONTH:
        return (f"This workspace has used its compute time for this month ({_hours(used)} of {_hours(limit)} "
                f"hours). New runs and scorings can start again on {reset:%Y-%m-%d} (UTC). Work already running "
                "continues.")
    if key == QUOTA_RUNS_PER_DAY:
        return (f"This workspace has started its {limit} runs and scorings for today. New ones can start again "
                f"at 00:00 UTC on {reset:%Y-%m-%d}. Work already running continues.")
    return (f"This workspace has used its storage ({_gb(used)} of {_gb(limit)} GB). New files, runs and scorings "
            "are refused because storage is full, until DCLab staff raise the limit. Work already running continues.")


def _plain_message(key: str, reset: datetime | None) -> str:
    """No numbers (legacy surfaces): what ran out and when it resets."""

    if key == QUOTA_COMPUTE_SECONDS_MONTH:
        return f"This workspace's monthly compute limit is reached; it resets on {reset:%Y-%m-%d} (UTC)."
    if key == QUOTA_RUNS_PER_DAY:
        return f"This workspace's daily run limit is reached; it resets on {reset:%Y-%m-%d} (UTC)."
    return "This workspace's storage limit is reached; new files, runs and scorings are refused until it is raised."


def ensure_within_quota(db: Session, workspace_id: UUID, key: str, requested: int = 0, *,
                        now: datetime | None = None, lock: bool = True) -> None:
    """Refuse new work when a plan limit is reached (``QuotaExceededError``, 429 ``quota_exceeded``).

    ``requested``: what this request adds (1 for a run). Refused when ``used`` is at the limit or
    ``used + requested`` passes it. No limit (unlimited) or ``QUOTAS_ENFORCED=false``: no check.
    ``lock=False``: a cheap early refusal only (no advisory lock); the locked check still decides."""

    if key not in ENFORCED_QUOTAS:
        raise ValueError(f"unknown quota {key!r}")
    if not get_settings().quotas_enforced:
        return
    limit = quota_limit(db, workspace_id, key).value
    if limit is None:
        return
    if key == QUOTA_RUNS_PER_DAY and lock:  # the same transaction lock ensure_run_capacity holds (re-entrant)
        db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"ml-runs:{workspace_id}", 0))))
    moment = now or _now()
    used = measure_quota(db, workspace_id, key, moment)
    if used >= limit or used + max(int(requested), 0) > limit:
        reset = resets_at(key, moment)
        raise QuotaExceededError(key, used=used, limit=limit, resets_at=reset,
                                 message=_message(key, used, limit, reset), plain_message=_plain_message(key, reset))


# --- aggregation (worker) -----------------------------------------------------------------------


def _latest_records(db: Session, workspace_id: UUID, first: date, last: date,
                    kinds: tuple[str, ...] = USAGE_KINDS) -> dict[tuple[str, date], int]:
    """The newest record (by ``created_at``) per (kind, day) of the workspace-level series."""

    rows = db.execute(
        select(UsageRecord.kind, UsageRecord.period_day, UsageRecord.quantity)
        .where(UsageRecord.workspace_id == workspace_id, UsageRecord.project_id.is_(None),
               UsageRecord.kind.in_(kinds), UsageRecord.period_day >= first, UsageRecord.period_day <= last)
        .ext(distinct_on(UsageRecord.kind, UsageRecord.period_day))
        .order_by(UsageRecord.kind, UsageRecord.period_day, UsageRecord.created_at.desc(), UsageRecord.id.desc())
    ).all()
    return {(kind, day): int(quantity) for kind, day, quantity in rows}


def aggregate_workspace(db: Session, workspace_id: UUID, moment: datetime) -> int:
    """Write yesterday's and today's-so-far records that changed. Returns rows written; the caller commits."""

    moment = moment.astimezone(UTC)
    today = moment.date()
    days = (today - timedelta(days=1), today)
    measured = daily_quantities(db, workspace_id, day_start(days[0]), moment)
    values: dict[tuple[str, date], int] = {}
    for kind in USAGE_KINDS:
        for day in days:
            values[(kind, day)] = (storage_bytes_at(db, workspace_id, min(day_start(day + timedelta(days=1)), moment))
                                   if kind in SNAPSHOT_KINDS else measured[kind].get(day, 0))
    latest = _latest_records(db, workspace_id, days[0], days[1])
    ref = source_ref(moment)
    rows = [
        {"id": uuid4(), "workspace_id": workspace_id, "project_id": None, "kind": kind, "period_day": day,
         "quantity": quantity, "source_ref": ref}
        for (kind, day), quantity in values.items()
        if quantity != latest.get((kind, day), 0)
    ]
    if not rows:
        return 0
    result = db.execute(pg_insert(UsageRecord).values(rows).on_conflict_do_nothing())
    return int(result.rowcount or 0)


def _active_workspaces(db: Session, since: datetime) -> list[UUID]:
    query = union(
        select(MlJob.workspace_id).where(MlJob.completed_at >= since),
        select(MlJob.workspace_id).where(MlJob.created_at >= since),
        select(LlmInvocation.workspace_id).where(LlmInvocation.created_at >= since),
        select(AgentRun.workspace_id).where(AgentRun.created_at >= since),
        select(Artifact.workspace_id).where(Artifact.created_at >= since),
    ).limit(_ACTIVE_SCAN_MAX)
    return sorted((row[0] for row in db.execute(query).all()), key=str)


def run_usage_metering_tick(db: Session, *, now: datetime | None = None) -> list[UUID]:
    """One bounded aggregation pass (worker only). Returns the workspaces aggregated this tick."""

    if not get_settings().usage_metering_enabled:
        return []
    moment = (now or _now()).astimezone(UTC)
    ids = _active_workspaces(db, day_start(moment.date() - timedelta(days=1)))
    db.rollback()  # ends the scan's transaction
    after = _ROTATION["after"]
    if after is not None:
        ids = [i for i in ids if str(i) > after] + [i for i in ids if str(i) <= after]
    done: list[UUID] = []
    for workspace_id in ids[:TICK_MAX_WORKSPACES]:
        _ROTATION["after"] = str(workspace_id)
        try:
            locked = db.scalar(select(func.pg_try_advisory_xact_lock(
                func.hashtextextended(f"usage:{workspace_id}", 0))))
            if not locked:  # another worker is aggregating this workspace
                db.rollback()
                continue
            aggregate_workspace(db, workspace_id, moment)
            db.commit()
            done.append(workspace_id)
        except Exception:  # noqa: BLE001 - one workspace never blocks the others
            logger.exception("usage metering: workspace %s failed", workspace_id)
            db.rollback()
    return done


# --- the read -----------------------------------------------------------------------------------


def _percent(used: int, limit: int | None) -> float | None:
    return None if not limit else round(used * 100.0 / limit, 1)


def _quota_read(db: Session, workspace_id: UUID, key: str, moment: datetime, enforced: bool) -> UsageQuotaRead:
    limit = quota_limit(db, workspace_id, key)
    used = measure_quota(db, workspace_id, key, moment)
    over = limit.value is not None and used >= limit.value
    warning = limit.value is not None and not over and used >= WARNING_FRACTION * limit.value
    return UsageQuotaRead(
        key=key, unit=QUOTA_UNITS[key], period=QUOTA_PERIODS[key], limit=limit.value, used=used,
        percent=_percent(used, limit.value), warning=warning, over=over, resets_at=resets_at(key, moment),
        enforced=limit.value is not None and (enforced or key == QUOTA_MEMBERS), source=limit.source)


def _storage_at_or_before(db: Session, workspace_id: UUID, day: date) -> int:
    value = db.scalar(
        select(UsageRecord.quantity)
        .where(UsageRecord.workspace_id == workspace_id, UsageRecord.project_id.is_(None),
               UsageRecord.kind == KIND_STORAGE_BYTES, UsageRecord.period_day <= day)
        .order_by(UsageRecord.period_day.desc(), UsageRecord.created_at.desc(), UsageRecord.id.desc())
        .limit(1))
    return int(value or 0)


def _ai_spend(db: Session, workspace_id: UUID, month_start: date) -> AiSpendRead | None:
    from app.agents.governance.console import workspace_month_spend

    month = workspace_month_spend(db, workspace_id)
    if month is None:
        return None
    limit, spent = int(month.limit_micros), int(month.spent_micros)
    over = limit > 0 and spent >= limit
    return AiSpendRead(
        period_start=date.fromisoformat(month.period_start) if month.period_start else month_start,
        limit_micros=limit, spent_micros=spent, reserved_micros=int(month.reserved_micros), currency=month.currency,
        percent=_percent(spent, limit), warning=limit > 0 and not over and spent >= month.alert_fraction * limit,
        over=over, hard_stop=month.hard_stop)


def usage_read(db: Session, *, actor: User, workspace_id: UUID, now: datetime | None = None) -> UsageRead:
    """Owners, administrators and ML engineers (and DCLab admins); viewers and client users: 403."""

    if not can_perform_ml_write(db, actor, workspace_id):
        raise IdentityError("usage is shown to workspace_owner, workspace_admin and ml_engineer", status_code=403)
    settings = get_settings()
    moment = (now or _now()).astimezone(UTC)
    today = moment.date()
    month_start = today.replace(day=1)
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    first = min(today - timedelta(days=SERIES_DAYS - 1), last_month_start)
    latest = _latest_records(db, workspace_id, first, today)

    totals = []
    for kind in USAGE_KINDS:
        if kind in SNAPSHOT_KINDS:
            this_month = _storage_at_or_before(db, workspace_id, today)
            last_month = _storage_at_or_before(db, workspace_id, month_start - timedelta(days=1))
        else:
            this_month = sum(q for (k, d), q in latest.items() if k == kind and d >= month_start)
            last_month = sum(q for (k, d), q in latest.items() if k == kind and last_month_start <= d < month_start)
        totals.append(UsageTotalsRead(kind=kind, unit=KIND_UNITS[kind],
                                      aggregate="latest" if kind in SNAPSHOT_KINDS else "sum",
                                      this_month=this_month, last_month=last_month))

    series_first = today - timedelta(days=SERIES_DAYS - 1)
    storage = _storage_at_or_before(db, workspace_id, series_first - timedelta(days=1))
    daily = []
    for offset in range(SERIES_DAYS):
        day = series_first + timedelta(days=offset)
        storage = latest.get((KIND_STORAGE_BYTES, day), storage)  # a snapshot carries forward
        daily.append(UsageDayRead(day=day, storage_bytes=storage, **{
            kind: latest.get((kind, day), 0) for kind in USAGE_KINDS if kind not in SNAPSHOT_KINDS}))

    enforced = bool(settings.quotas_enforced)
    return UsageRead(
        workspace_id=workspace_id, plan=plan_name(db, workspace_id), quotas_enforced=enforced,
        metering_enabled=bool(settings.usage_metering_enabled),
        measured_at=db.scalar(select(func.max(UsageRecord.created_at)).where(UsageRecord.workspace_id == workspace_id)),
        month_start=month_start, resets_at=day_start(next_month(today)),
        quotas=[_quota_read(db, workspace_id, key, moment, enforced) for key in (*ENFORCED_QUOTAS, QUOTA_MEMBERS)],
        totals=totals, daily=daily, ai_spend=_ai_spend(db, workspace_id, month_start), disclaimer=DISCLAIMER, notes=list(NOTES))
