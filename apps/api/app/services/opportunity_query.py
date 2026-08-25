"""Read opportunities. Persistence stays in the session; this is the query use case."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Opportunity
from app.domain.opportunity import OpportunityListResponse, OpportunityRead


def get_opportunity(db: Session, opportunity_id: str) -> Opportunity | None:
    stmt = select(Opportunity)
    try:
        uid = UUID(opportunity_id)
        stmt = stmt.where(Opportunity.id == uid)
    except ValueError:
        stmt = stmt.where(Opportunity.external_id == opportunity_id)
    return db.scalars(stmt).first()


def list_opportunities(
    db: Session,
    *,
    limit: int,
    offset: int,
    stage: str | None,
    sort: str,
    order: str,
) -> OpportunityListResponse:
    stmt = select(Opportunity)
    count_stmt = select(func.count()).select_from(Opportunity)
    if stage:
        stmt = stmt.where(Opportunity.stage == stage)
        count_stmt = count_stmt.where(Opportunity.stage == stage)
    column = Opportunity.amount if sort == "amount" else Opportunity.created_at
    stmt = stmt.order_by(column.asc() if order == "asc" else column.desc())
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.offset(offset).limit(limit)).all()
    return OpportunityListResponse(
        items=[OpportunityRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
