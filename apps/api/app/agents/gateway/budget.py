"""Budgets: reserve → per-call worst case → settle → release (ADR 0009 §2.7, §4).

``reserve`` creates missing counter rows (``INSERT … ON CONFLICT DO NOTHING``),
locks them in the fixed order workspace → project → run_kind (``FOR UPDATE``),
rolls month/day periods, and refuses when ``spent + reserved + estimate`` exceeds
``min(row limit, effective policy budget)`` and hard stop is on (stricter wins:
``row.hard_stop OR policy.hard_stop``). ``run``-period rows (per-run caps) bound
each reservation alone. A reservation is sealed (HMAC) and expires after the run
kind's policy wall limit.

Per call (``available_hold``, under a per-reservation advisory lock held until the
pending ledger row is committed): held − costs of finished rows − the worst case
stored on still-pending rows (``estimated_cost``), so concurrent calls on one
reservation cannot together exceed it. ``settle`` charges the ledger row's
``cost_micros`` (the truth) to ``spent`` and frees ``min(actual, remaining hold)``
from ``reserved``, once per invocation (``llm_invocations.budget_settled``), so a
settle after ``release`` still charges ``spent`` without driving ``reserved``
below zero.

Release markers: a reservation bound to an agent run is released by moving the
run from a live to a terminal status (compare-and-set under the counter locks;
durable and idempotent across processes). A reservation without an agent run has
only a process-local marker: a second release from another process would free
the hold twice. A durable marker for run-less reservations needs schema (P6.10-A).
Crossing ``alert_fraction`` of a period limit is reported once per period (spend
only grows within a period) and logged; Inbox notices come later.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.agents.gateway.contract import BudgetReservation, GatewayRefusal
from app.agents.governance.platform_default import AiPolicyV1
from app.agents.governance.policy import advisory_lock
from app.db.models import AgentRun, LlmInvocation, WorkspaceLlmBudget

logger = logging.getLogger(__name__)

CURRENCY = "USD"
# Micro-USD per 1M tokens (input, output); ADR 0009 §9, founder decision Q1.
PRICES_MICROS_PER_MTOK: dict[str, tuple[int, int]] = {
    "gpt-6.1-sol": (2_000_000, 10_000_000),
    "gpt-6-luna": (100_000, 500_000),
    "jev-1.13.0": (42_000, 0),
}
# The ADR names no Jev per-run cap: a Jev batch is bounded like a specialist run.
_RUN_KIND_POLICY_FIELD = {
    "assistant_turn": "assistant_turn_micros",
    "assistant_thread": "assistant_thread_micros",
    "specialist": "specialist_run_micros",
    "jev": "specialist_run_micros",
}
LOCK_NS_BUDGET_HOLD = 72063  # next to governance's 72061 / 72062
LIVE_RUN_STATUSES = ("queued", "running", "waiting_user")
TERMINAL_RUN_STATUSES = ("completed", "failed", "rejected_by_validator", "over_budget", "timed_out",
                         "cancelled", "closed")
_RELEASED: OrderedDict[UUID, None] = OrderedDict()
_RELEASED_MAX = 100_000
_RELEASED_LOCK = threading.Lock()


def cost_micros(model: str, input_tokens: int, output_tokens: int) -> int:
    price_in, price_out = PRICES_MICROS_PER_MTOK[model]
    return math.ceil((input_tokens * price_in + output_tokens * price_out) / 1_000_000)


def estimate_tokens(text: str) -> int:
    """Conservative token estimate (3 characters per token)."""

    return math.ceil(len(text) / 3)


def worst_case_micros(model: str, *, input_tokens: int, max_output_tokens: int) -> int:
    return cost_micros(model, input_tokens, max_output_tokens)


# --- seal ------------------------------------------------------------------------------


def _seal_key() -> bytes:
    from app.config import get_settings

    return hashlib.sha256(b"dclab.gateway.budget-seal\x1f" + get_settings().jwt_secret.encode()).digest()


def _seal_body(values: dict) -> bytes:
    parts = [str(values.get(name)) for name in (
        "id", "workspace_id", "project_id", "run_kind", "agent_run_id", "held_micros", "currency",
    )]
    parts.append(",".join(str(item) for item in values["counter_ids"]))
    parts.append(values["expires_at"].astimezone(UTC).isoformat(timespec="microseconds"))
    return "\x1f".join(parts).encode()


def seal_valid(reservation: BudgetReservation) -> bool:
    expected = hmac.new(_seal_key(), _seal_body(reservation.model_dump()), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, reservation.seal)


def _sealed(**values) -> BudgetReservation:
    values["seal"] = hmac.new(_seal_key(), _seal_body(values), hashlib.sha256).hexdigest()
    return BudgetReservation(**values)


# --- counters --------------------------------------------------------------------------


def _wall_s(policy: AiPolicyV1, run_kind: str | None) -> int:
    """The run kind's policy wall limit; run-less and Jev holds get the longest (thread) limit."""

    limits = policy.limits
    walls = {"assistant_turn": limits.assistant_turn.wall_s, "assistant_thread": limits.assistant_thread.wall_s,
             "specialist": limits.specialist.wall_s}
    return walls.get(run_kind, max(*walls.values()))


def expired(reservation: BudgetReservation, now: datetime | None = None) -> bool:
    return reservation.expires_at <= (now or datetime.now(UTC))


def period_start(period: str, today: date) -> date:
    return today.replace(day=1) if period == "month" else today


def _roll(row: WorkspaceLlmBudget, today: date) -> None:
    # Outstanding holds carry over; spend and calls restart with the period.
    if row.period in ("month", "day") and row.period_start < period_start(row.period, today):
        row.period_start = period_start(row.period, today)
        row.spent_micros = 0
        row.calls = 0


def _policy_limit(policy: AiPolicyV1, scope: str, run_kind: str | None) -> int:
    budgets = policy.budgets
    if scope == "workspace":
        return budgets.workspace_month_micros
    if scope == "project":
        return budgets.project_month_micros
    return getattr(budgets, _RUN_KIND_POLICY_FIELD[run_kind])


def _specs(project_id: UUID | None, run_kind: str | None) -> list[tuple[str, UUID | None, str | None, str]]:
    specs = [("workspace", None, None, "month")]
    if project_id is not None:
        specs.append(("project", project_id, None, "month"))
    if run_kind is not None:
        if run_kind not in _RUN_KIND_POLICY_FIELD:
            raise ValueError(f"unknown run kind {run_kind}")
        specs.append(("run_kind", None, run_kind, "run"))
    return specs


def _lock_rows(db: Session, workspace_id: UUID, ids: tuple[UUID, ...]) -> list[WorkspaceLlmBudget]:
    rows = []
    for row_id in ids:  # one statement per row keeps the lock order fixed
        row = db.scalar(
            select(WorkspaceLlmBudget)
            .where(WorkspaceLlmBudget.id == row_id, WorkspaceLlmBudget.workspace_id == workspace_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise GatewayRefusal("policy_denied", "budget counter not found in this workspace", scope="budget")
        rows.append(row)
    return rows


@dataclass
class SettleResult:
    settled: bool
    from_hold: int = 0
    alerts: list[str] = field(default_factory=list)


def reserve(
    db: Session,
    *,
    policy: AiPolicyV1,
    workspace_id: UUID,
    estimate_micros: int,
    project_id: UUID | None = None,
    run_kind: str | None = None,
    agent_run_id: UUID | None = None,
    now: datetime | None = None,
) -> BudgetReservation:
    """Hold ``estimate_micros`` on every counter row or raise ``GatewayRefusal(budget_exhausted)``."""

    if estimate_micros < 0:
        raise ValueError("estimate must be non-negative")
    now = now or datetime.now(UTC)
    today = now.date()
    budgets = policy.budgets
    rows: list[WorkspaceLlmBudget] = []
    for scope, project, kind, period in _specs(project_id, run_kind):
        db.execute(
            insert(WorkspaceLlmBudget)
            .values(
                id=uuid4(), workspace_id=workspace_id, scope=scope, project_id=project, run_kind=kind,
                period=period, period_start=period_start(period, today),
                limit_micros=_policy_limit(policy, scope, kind), currency=CURRENCY,
                alert_fraction=budgets.alert_fraction, hard_stop=budgets.hard_stop,
            )
            .on_conflict_do_nothing(constraint="uq_workspace_llm_budgets_scope")
        )
        row = db.scalar(
            select(WorkspaceLlmBudget)
            .where(
                WorkspaceLlmBudget.workspace_id == workspace_id,
                WorkspaceLlmBudget.scope == scope,
                WorkspaceLlmBudget.project_id.is_not_distinct_from(project),
                WorkspaceLlmBudget.run_kind.is_not_distinct_from(kind),
                WorkspaceLlmBudget.period == period,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        _roll(row, today)
        limit = min(row.limit_micros, _policy_limit(policy, scope, kind))
        needed = estimate_micros if period == "run" else row.spent_micros + row.reserved_micros + estimate_micros
        if needed > limit:
            if row.hard_stop or budgets.hard_stop:
                raise GatewayRefusal("budget_exhausted", f"{scope} budget exhausted", scope=scope)
            logger.warning("llm budget exceeded without hard stop", extra={
                "workspace_id": str(workspace_id), "scope": scope, "limit_micros": limit, "needed": needed,
            })
        rows.append(row)
    for row in rows:
        row.reserved_micros += estimate_micros
        row.updated_at = func.now()
    if agent_run_id is not None:
        stamped = db.execute(
            update(AgentRun)
            .where(AgentRun.workspace_id == workspace_id, AgentRun.id == agent_run_id, AgentRun.held_micros == 0)
            .values(held_micros=estimate_micros)
        ).rowcount
        if stamped != 1:
            raise GatewayRefusal("policy_denied", "agent run not found or already holds a budget", scope="budget")
    db.flush()
    return _sealed(
        id=uuid4(), workspace_id=workspace_id, project_id=project_id, run_kind=run_kind,
        agent_run_id=agent_run_id, counter_ids=tuple(row.id for row in rows),
        held_micros=estimate_micros, currency=CURRENCY,
        expires_at=now + timedelta(seconds=_wall_s(policy, run_kind)),
    )


def lock_hold(db: Session, reservation: BudgetReservation) -> None:
    """Serialize the per-call hold check with the pending-row insert (transaction scoped)."""

    advisory_lock(db, LOCK_NS_BUDGET_HOLD, str(reservation.id))


def _released_in_process(reservation: BudgetReservation) -> bool:
    with _RELEASED_LOCK:
        return reservation.id in _RELEASED


def is_released(db: Session, reservation: BudgetReservation) -> bool:
    if _released_in_process(reservation):
        return True
    if reservation.agent_run_id is None:
        return False
    status = db.scalar(select(AgentRun.status).where(
        AgentRun.workspace_id == reservation.workspace_id, AgentRun.id == reservation.agent_run_id))
    return status is None or status in TERMINAL_RUN_STATUSES


def mark_released(reservation: BudgetReservation) -> None:
    """Call after the releasing transaction committed."""

    with _RELEASED_LOCK:
        _RELEASED[reservation.id] = None
        while len(_RELEASED) > _RELEASED_MAX:
            _RELEASED.popitem(last=False)


def _settled_micros(db: Session, reservation: BudgetReservation) -> int:
    return int(db.scalar(
        select(func.coalesce(func.sum(LlmInvocation.cost_micros), 0)).where(
            LlmInvocation.workspace_id == reservation.workspace_id,
            LlmInvocation.budget_reservation_id == reservation.id,
            LlmInvocation.budget_settled.is_(True),
        )
    ) or 0)


def remaining_hold(db: Session, reservation: BudgetReservation) -> int:
    """What is still held on the counters (settle / release arithmetic)."""

    if is_released(db, reservation):
        return 0
    return max(0, reservation.held_micros - _settled_micros(db, reservation))


def available_hold(db: Session, reservation: BudgetReservation, *, exclude: UUID | None = None) -> int:
    """What a new call may still spend: finished rows count their cost, pending rows
    their stored worst case. Call under ``lock_hold``."""

    if is_released(db, reservation):
        return 0
    used = case(
        (LlmInvocation.completed_at.is_(None),
         func.round(func.coalesce(LlmInvocation.estimated_cost, 0) * 1_000_000)),
        else_=func.coalesce(LlmInvocation.cost_micros, 0),
    )
    query = select(func.coalesce(func.sum(used), 0)).where(
        LlmInvocation.workspace_id == reservation.workspace_id,
        LlmInvocation.budget_reservation_id == reservation.id,
    )
    if exclude is not None:
        query = query.where(LlmInvocation.id != exclude)
    return max(0, reservation.held_micros - int(db.scalar(query) or 0))


def settle(
    db: Session, reservation: BudgetReservation, invocation_id: UUID, *, provider_called: bool = True,
    today: date | None = None,
) -> SettleResult:
    """Charge the ledger row's ``cost_micros``; idempotent by ``budget_settled``."""

    today = today or datetime.now(UTC).date()
    rows = _lock_rows(db, reservation.workspace_id, reservation.counter_ids)
    invocation = db.scalar(
        select(LlmInvocation)
        .where(LlmInvocation.id == invocation_id, LlmInvocation.workspace_id == reservation.workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if invocation is None or invocation.budget_reservation_id != reservation.id:
        raise ValueError("invocation is not on this reservation")
    if invocation.budget_settled:
        return SettleResult(settled=False)
    if invocation.cost_micros is None or invocation.completed_at is None:
        raise ValueError("only a finalized invocation with a cost can be settled")
    actual = invocation.cost_micros
    from_hold = min(actual, remaining_hold(db, reservation))
    result = SettleResult(settled=True, from_hold=from_hold)
    for row in rows:
        _roll(row, today)
        before = row.spent_micros
        row.spent_micros += actual
        if row.reserved_micros < from_hold:  # never below zero; signals a release race
            logger.warning("llm budget reserved below hold", extra={"counter_id": str(row.id)})
        row.reserved_micros = max(0, row.reserved_micros - from_hold)
        row.calls += 1 if provider_called else 0
        row.updated_at = func.now()
        threshold = float(row.alert_fraction) * row.limit_micros
        if row.period != "run" and before < threshold <= row.spent_micros:
            result.alerts.append(row.scope)
            logger.warning("llm budget alert threshold crossed", extra={
                "workspace_id": str(row.workspace_id), "scope": row.scope, "spent_micros": row.spent_micros,
            })
    invocation.budget_settled = True
    db.flush()
    return result


def release(db: Session, reservation: BudgetReservation, *, final_status: str = "completed") -> int:
    """Free the remaining hold. With an agent run, the run's move from a live to
    ``final_status`` is the durable, idempotent marker (0 when it already ended).
    Pair with ``mark_released`` after commit."""

    if _released_in_process(reservation):
        return 0
    rows = _lock_rows(db, reservation.workspace_id, reservation.counter_ids)
    remaining = remaining_hold(db, reservation)
    if reservation.agent_run_id is not None:
        if final_status not in TERMINAL_RUN_STATUSES:
            raise ValueError(f"{final_status} is not a terminal run status")
        moved = db.execute(
            update(AgentRun)
            .where(AgentRun.workspace_id == reservation.workspace_id, AgentRun.id == reservation.agent_run_id,
                   AgentRun.status.in_(LIVE_RUN_STATUSES))
            .values(status=final_status, finished_at=func.coalesce(AgentRun.finished_at, func.now()),
                    cost_micros=_settled_micros(db, reservation))
        ).rowcount
        if moved != 1:
            return 0
    for row in rows:
        row.reserved_micros = max(0, row.reserved_micros - remaining)
        row.updated_at = func.now()
    db.flush()
    return remaining
