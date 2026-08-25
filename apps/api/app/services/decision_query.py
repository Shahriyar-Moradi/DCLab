"""Read decisions for the ledger. Mapping from ORM to DTOs stays here."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Decision
from app.domain.decision import DecisionListResponse, DecisionRead
from app.services.opportunity_query import get_opportunity


def serialize_decision(row: Decision) -> DecisionRead:
    return DecisionRead(
        id=row.id,
        opportunity_id=row.opportunity_id,
        prediction_id=row.prediction_id,
        recommended_action=row.recommended_action,
        expected_revenue=float(row.expected_revenue),
        confidence=row.confidence,
        reasoning=list(row.reasoning),
        policy_version=row.policy_version,
        status=row.status,
        created_at=row.created_at,
        conversion_probability=row.prediction.conversion_probability if row.prediction else None,
        model_version=row.prediction.model_version if row.prediction else None,
        external_id=row.opportunity.external_id if row.opportunity else None,
    )


def get_decision(db: Session, decision_id: UUID) -> Decision | None:
    return db.scalars(
        select(Decision)
        .options(selectinload(Decision.prediction), selectinload(Decision.opportunity))
        .where(Decision.id == decision_id)
    ).first()


def list_decisions(
    db: Session,
    *,
    limit: int,
    offset: int,
    status: str | None,
    recommended_action: str | None,
    opportunity_id: str | None,
) -> DecisionListResponse:
    stmt = select(Decision).options(
        selectinload(Decision.prediction), selectinload(Decision.opportunity)
    )
    count_stmt = select(func.count()).select_from(Decision)
    if status:
        stmt = stmt.where(Decision.status == status)
        count_stmt = count_stmt.where(Decision.status == status)
    if recommended_action:
        stmt = stmt.where(Decision.recommended_action == recommended_action)
        count_stmt = count_stmt.where(Decision.recommended_action == recommended_action)
    if opportunity_id:
        opportunity = get_opportunity(db, opportunity_id)
        if opportunity is None:
            return DecisionListResponse(items=[], total=0, limit=limit, offset=offset)
        stmt = stmt.where(Decision.opportunity_id == opportunity.id)
        count_stmt = count_stmt.where(Decision.opportunity_id == opportunity.id)

    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(Decision.created_at.desc()).offset(offset).limit(limit)).all()
    return DecisionListResponse(
        items=[serialize_decision(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
