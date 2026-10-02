"""Operator backfill: ``winner_locked`` decision records for existing winner locks.

ADR 0006 §8 (founder Q7). Each ``model_selection_decisions`` row is a 1:1 fact,
so it materializes exactly one accepted ``winner_locked`` record (actor rule
``selection.cv_winner.backfill.v1``, ``event_at = locked_at``,
``details.backfilled = true``). The idempotency key is the same one the live
write path uses, so reruns, or a later live record, never duplicate it.

Selections without a project, or whose winning candidate belongs to another
project, are skipped and counted separately: a decision record is project-scoped
and its subject FK requires the same project (honest gaps, no guessing). A
non-finite ``selected_score`` is stored as ``null`` and flagged in ``details``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db.models import ExperimentCandidate, ModelSelectionDecision, ProjectDecisionRecord
from app.domain.decision_records import DECISION_WINNER_LOCKED, winner_locked_idempotency_key
from app.services.decision_record_service import winner_locked_record


@dataclass(frozen=True)
class WinnerBackfillResult:
    created: int
    already_present: int
    skipped_without_project: int
    skipped_project_mismatch: int

    def as_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "already_present": self.already_present,
            "skipped_without_project": self.skipped_without_project,
            "skipped_project_mismatch": self.skipped_project_mismatch,
        }


def _record_for(selection: ModelSelectionDecision, fingerprint: str) -> ProjectDecisionRecord:
    return winner_locked_record(selection, fingerprint, backfilled=True)


def backfill_winner_records(db: Session) -> WinnerBackfillResult:
    """Insert missing ``winner_locked`` records in one transaction; idempotent."""

    rows = db.execute(
        select(
            ModelSelectionDecision,
            ExperimentCandidate.project_id,
            ExperimentCandidate.fingerprint,
        )
        .join(
            ExperimentCandidate,
            and_(
                ExperimentCandidate.id == ModelSelectionDecision.selected_candidate_id,
                ExperimentCandidate.workspace_id == ModelSelectionDecision.workspace_id,
            ),
        )
        .order_by(ModelSelectionDecision.locked_at, ModelSelectionDecision.id)
    ).all()
    existing = set(
        db.execute(
            select(ProjectDecisionRecord.workspace_id, ProjectDecisionRecord.idempotency_key)
            .where(ProjectDecisionRecord.decision_type == DECISION_WINNER_LOCKED)
            .where(ProjectDecisionRecord.idempotency_key.is_not(None))
        ).all()
    )
    created = already = without_project = mismatch = 0
    for selection, candidate_project_id, fingerprint in rows:
        key = winner_locked_idempotency_key(selection.id)
        if (selection.workspace_id, key) in existing:
            already += 1
            continue
        if selection.project_id is None:
            without_project += 1
            continue
        if candidate_project_id != selection.project_id:
            mismatch += 1
            continue
        db.add(_record_for(selection, fingerprint))
        created += 1
    db.commit()
    return WinnerBackfillResult(
        created=created,
        already_present=already,
        skipped_without_project=without_project,
        skipped_project_mismatch=mismatch,
    )
