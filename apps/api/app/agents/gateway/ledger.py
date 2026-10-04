"""The gateway's ``llm_invocations`` writer (ADR 0009 §4 step 10, §2.10).

Exactly one row per gateway call, refusals and cache hits included. The write
order respects ``guard_llm_invocation_ledger``: identity columns are written at
INSERT and never again; an in-flight call is a ``pending`` row whose outcome,
cost, provider ids and ``completed_at`` are written by ONE finalizing UPDATE
(refusals, cache hits and other no-call rows are inserted already completed);
``budget_settled`` flips false → true afterwards (``budget.settle``). Raw
prompts are never stored: ``input_evidence_digest`` is the cache key and
``safe_output`` is the validated output passed through the shared observability
sanitizer (bounded, secrets redacted).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from app.agents.gateway.contract import Usage
from app.db.models import AgentRun, Experiment, LlmInvocation, Project, WorkflowRun
from app.services.observability_service import sanitize_observability_payload

CURRENCY = "USD"
# Longer than the largest provider timeout a request may set (300 s), plus margin.
STALE_PENDING_AFTER = timedelta(seconds=360)


@dataclass
class LedgerEntry:
    """Columns fixed at INSERT (frozen by the ledger guard)."""

    invocation_id: UUID
    workspace_id: UUID
    purpose: str
    provider_kind: str
    mode: str
    prompt_version: str
    schema_version: str
    input_evidence_digest: str
    data_class: str
    outcome_scope: str
    started_at: datetime
    llm_used: bool
    project_id: UUID | None = None
    experiment_id: UUID | None = None
    workflow_run_id: UUID | None = None
    agent_run_id: UUID | None = None
    prompt_release_id: UUID | None = None
    agent_role: str | None = None
    decision_point_key: str | None = None
    budget_reservation_id: UUID | None = None
    provider: str | None = None
    model: str | None = None
    redaction_summary: dict[str, Any] = field(default_factory=dict)


def attribution_in_workspace(db: Session, entry: LedgerEntry) -> bool:
    """Every attribution id must belong to the call's workspace (checked before any write)."""

    for model, value in (
        (Project, entry.project_id), (Experiment, entry.experiment_id),
        (WorkflowRun, entry.workflow_run_id), (AgentRun, entry.agent_run_id),
    ):
        if value is not None and not db.scalar(
            select(exists().where(model.id == value, model.workspace_id == entry.workspace_id))
        ):
            return False
    return True


def _safe(output: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if output is None:
        return None, None
    return sanitize_observability_payload(output)


def _row(entry: LedgerEntry, **outcome: Any) -> LlmInvocation:
    return LlmInvocation(
        id=entry.invocation_id,
        workspace_id=entry.workspace_id,
        workflow_run_id=entry.workflow_run_id,
        experiment_id=entry.experiment_id,
        project_id=entry.project_id,
        agent_run_id=entry.agent_run_id,
        provider_kind=entry.provider_kind,
        purpose=entry.purpose,
        mode=entry.mode,
        prompt_version=entry.prompt_version[:64],
        schema_version=entry.schema_version[:64],
        input_evidence_digest=entry.input_evidence_digest,
        llm_used=entry.llm_used,
        started_at=entry.started_at,
        prompt_release_id=entry.prompt_release_id,
        data_class=entry.data_class,
        outcome_scope=entry.outcome_scope,
        agent_role=entry.agent_role,
        decision_point_key=entry.decision_point_key,
        budget_reservation_id=entry.budget_reservation_id,
        provider=entry.provider,
        model=entry.model,
        **outcome,
    )


def insert_pending(db: Session, entry: LedgerEntry, *, worst_case_micros: int) -> LlmInvocation:
    """The in-flight row. ``estimated_cost`` holds the call's worst case (USD) until the
    finalizing UPDATE replaces it with the actual cost; ``budget.available_hold`` reads it."""

    row = _row(entry, redaction_summary=entry.redaction_summary, reason="provider call in flight",
               status="pending", validator_verdict="pending", cache_hit=False,
               estimated_cost=worst_case_micros / 1_000_000)
    db.add(row)
    db.flush()
    return row


def raise_pending_estimate(db: Session, *, invocation_id: UUID, workspace_id: UUID, worst_case_micros: int) -> None:
    """A retry widens the in-flight worst case (still before completion)."""

    db.execute(
        update(LlmInvocation)
        .where(LlmInvocation.id == invocation_id, LlmInvocation.workspace_id == workspace_id,
               LlmInvocation.completed_at.is_(None))
        .values(estimated_cost=worst_case_micros / 1_000_000)
    )


def reconcile_stale_pending(db: Session, *, older_than: timedelta = STALE_PENDING_AFTER,
                            workspace_id: UUID | None = None) -> int:
    """Finalize gateway rows left ``pending`` by a dead process (older than the longest
    provider timeout) as ``failed`` / ``timeout`` with zero cost; returns the count.
    Legacy pending rows (no prompt release) are never touched. For a reconciliation job."""

    query = (
        update(LlmInvocation)
        .where(LlmInvocation.status == "pending", LlmInvocation.completed_at.is_(None),
               LlmInvocation.prompt_release_id.is_not(None),
               LlmInvocation.started_at < datetime.now(UTC) - older_than)
        .values(status="failed", validator_verdict="not_run", refusal_code="timeout",
                reason="stale pending row reconciled; no outcome was recorded", cost_micros=0,
                currency=CURRENCY, estimated_cost=0.0, budget_settled=True, completed_at=func.now())
    )
    if workspace_id is not None:
        query = query.where(LlmInvocation.workspace_id == workspace_id)
    return db.execute(query).rowcount


def insert_completed(
    db: Session,
    entry: LedgerEntry,
    *,
    status: str,
    validator_verdict: str,
    reason: str,
    refusal_code: str | None = None,
    output: dict[str, Any] | None = None,
    cost_micros: int = 0,
    cache_hit: bool = False,
    budget_settled: bool = False,
    provider_resolved_model: str | None = None,
    provider_request_id: str | None = None,
    usage: Usage | None = None,
    latency_ms: float | None = None,
) -> LlmInvocation:
    """A row that is final at INSERT: refusals, cache hits, calls made without a provider."""

    safe_output, output_summary = _safe(output)
    usage = usage or Usage()
    row = _row(
        entry,
        redaction_summary={**entry.redaction_summary, "safe_output": output_summary},
        reason=reason[:1024],
        status=status,
        validator_verdict=validator_verdict[:1024],
        refusal_code=refusal_code,
        safe_output=safe_output,
        cost_micros=cost_micros,
        currency=CURRENCY,
        estimated_cost=cost_micros / 1_000_000,
        cache_hit=cache_hit,
        budget_settled=budget_settled,
        provider_resolved_model=provider_resolved_model,
        provider_request_id=provider_request_id,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        latency_ms=latency_ms,
        completed_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def finalize(
    db: Session,
    *,
    invocation_id: UUID,
    workspace_id: UUID,
    status: str,
    validator_verdict: str,
    reason: str,
    refusal_code: str | None,
    output: dict[str, Any] | None,
    cost_micros: int,
    usage: Usage,
    latency_ms: float,
    provider_request_id: str | None,
    provider_resolved_model: str | None,
) -> LlmInvocation:
    """The one UPDATE that completes a pending row (outcome and cost together)."""

    row = db.scalar(
        select(LlmInvocation)
        .where(LlmInvocation.id == invocation_id, LlmInvocation.workspace_id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.completed_at is not None:
        raise ValueError("no pending invocation to finalize")
    safe_output, output_summary = _safe(output)
    row.status = status
    row.validator_verdict = validator_verdict[:1024]
    row.reason = reason[:1024]
    row.refusal_code = refusal_code
    row.safe_output = safe_output
    row.redaction_summary = {**dict(row.redaction_summary or {}), "safe_output": output_summary}
    row.cost_micros = cost_micros
    row.currency = CURRENCY
    row.estimated_cost = cost_micros / 1_000_000
    row.input_tokens = usage.input_tokens
    row.output_tokens = usage.output_tokens
    row.total_tokens = usage.total_tokens
    row.latency_ms = latency_ms
    row.provider_request_id = (provider_request_id or None) and provider_request_id[:128]
    row.provider_resolved_model = (provider_resolved_model or None) and provider_resolved_model[:128]
    row.completed_at = datetime.now(UTC)
    db.flush()
    return row
