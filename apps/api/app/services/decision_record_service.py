"""Rule-actor write paths into ``project_decision_records`` (ADR 0006 §5).

P2.2-B scope only: the records the engine writes automatically
(``winner_locked``, ``split_plan_created``, ``ref_initialized``). Proposals,
accept/reject/supersede and the read API arrive with P2.5-A. Rows are
append-only; idempotency keys make every rule write replay-safe.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ExperimentCandidate, ModelSelectionDecision, ProjectDecisionRecord
from app.domain.decision_records import (
    ACTOR_RULE,
    DECISION_POLICY_VERSION,
    DECISION_TYPES,
    DECISION_WINNER_LOCKED,
    EVIDENCE_REFS_MAX,
    RATIONALE_MAX_CHARS,
    RULE_WINNER_LOCKED,
    RULE_WINNER_LOCKED_BACKFILL,
    STATE_ACCEPTED,
    SUBJECT_COLUMNS,
    WINNER_LOCKED_SCHEMA_VERSION,
    winner_locked_idempotency_key,
)

_DIGEST = re.compile(r"^[0-9a-f]{1,64}$")
CV_AGGREGATE_SCOPE = "cv_aggregate"
FINAL_HOLDOUT_SCOPE = "final_holdout"


def evidence_ref(kind: str, node_id: Any, *, metric: str | None = None, scope: str | None = None) -> dict[str, str]:
    ref = {"kind": kind, "id": str(node_id)}
    if metric:
        ref["metric"] = str(metric)
    if scope:
        ref["scope"] = scope
    return ref


def existing_record(db: Session, *, workspace_id: UUID, idempotency_key: str) -> ProjectDecisionRecord | None:
    return db.scalar(
        select(ProjectDecisionRecord).where(
            ProjectDecisionRecord.workspace_id == workspace_id,
            ProjectDecisionRecord.idempotency_key == idempotency_key,
        )
    )


def rule_record(
    *,
    workspace_id: UUID,
    project_id: UUID,
    decision_type: str,
    subject_kind: str,
    subject_id: UUID,
    actor_rule: str,
    rationale: str,
    schema_version: int,
    idempotency_key: str,
    facts: dict[str, Any] | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
    subject_digest: str | None = None,
    event_at: datetime | None = None,
) -> ProjectDecisionRecord:
    """Build one accepted, rule-actor record (not added to the session)."""

    if decision_type not in DECISION_TYPES:
        raise ValueError(f"unknown decision_type {decision_type!r}")
    column = SUBJECT_COLUMNS.get(subject_kind)
    if column is None:
        raise ValueError(f"subject_kind {subject_kind!r} needs a typed subject")
    text = str(rationale or "").strip()[:RATIONALE_MAX_CHARS] or decision_type
    refs = list(evidence_refs or [])
    if len(refs) > EVIDENCE_REFS_MAX:
        raise ValueError("too many evidence refs")
    row = ProjectDecisionRecord(
        workspace_id=workspace_id,
        project_id=project_id,
        decision_type=decision_type,
        state=STATE_ACCEPTED,
        subject_kind=subject_kind,
        subject_digest=subject_digest if _DIGEST.match(subject_digest or "") else None,
        actor_kind=ACTOR_RULE,
        actor_rule=actor_rule,
        rationale=text,
        rationale_untrusted=False,
        facts=dict(facts or {}),
        evidence_refs=refs,
        details=dict(details or {}),
        schema_version=schema_version,
        policy_version=DECISION_POLICY_VERSION,
        idempotency_key=idempotency_key,
    )
    setattr(row, column, subject_id)
    if event_at is not None:
        row.event_at = event_at
    return row


def winner_locked_record(
    selection: ModelSelectionDecision, fingerprint: str | None, *, backfilled: bool
) -> ProjectDecisionRecord:
    """The ``winner_locked`` fact for one ModelSelection; same shape live and backfilled."""

    evidence = [
        evidence_ref(
            "candidate",
            selection.selected_candidate_id,
            metric=selection.selection_metric,
            scope=CV_AGGREGATE_SCOPE,
        )
    ]
    if selection.runner_up_candidate_id is not None:
        evidence.append(
            evidence_ref(
                "candidate",
                selection.runner_up_candidate_id,
                metric=selection.selection_metric,
                scope=CV_AGGREGATE_SCOPE,
            )
        )
    score = selection.selected_score
    finite = score is not None and math.isfinite(score)
    details: dict[str, object] = {"model_selection_decision_id": str(selection.id)}
    if backfilled:
        details["backfilled"] = True
    if not finite:
        details["selected_score_non_finite"] = True
    rationale = (selection.reason or "").strip() or (
        f"CV winner locked by selection policy {selection.selection_policy}"
    )
    return rule_record(
        workspace_id=selection.workspace_id,
        project_id=selection.project_id,
        decision_type=DECISION_WINNER_LOCKED,
        subject_kind="candidate",
        subject_id=selection.selected_candidate_id,
        subject_digest=fingerprint,
        actor_rule=RULE_WINNER_LOCKED_BACKFILL if backfilled else RULE_WINNER_LOCKED,
        rationale=rationale,
        facts={
            "selected_score": score if finite else None,
            "selection_metric": selection.selection_metric,
            "selection_policy": selection.selection_policy,
        },
        evidence_refs=evidence,
        details=details,
        schema_version=WINNER_LOCKED_SCHEMA_VERSION,
        idempotency_key=winner_locked_idempotency_key(selection.id),
        event_at=selection.locked_at,
    )


def record_winner_locked(
    db: Session, selection: ModelSelectionDecision, winner: ExperimentCandidate
) -> ProjectDecisionRecord | None:
    """Live path: one record in the selection's transaction (skips project-less runs)."""

    if selection.project_id is None or winner.project_id != selection.project_id:
        return None
    if selection.id is None:
        db.flush()
    key = winner_locked_idempotency_key(selection.id)
    found = existing_record(db, workspace_id=selection.workspace_id, idempotency_key=key)
    if found is not None:
        return found
    row = winner_locked_record(selection, winner.fingerprint, backfilled=False)
    db.add(row)
    return row
