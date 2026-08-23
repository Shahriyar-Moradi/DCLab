from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Opportunity
from app.db.session import get_db
from app.domain.opportunity import OpportunityListResponse, OpportunityRead, OpportunityUploadResult, RowError
from app.services.ingestion_service import ingest_opportunities_csv

router = APIRouter(prefix="/opportunities", tags=["opportunities"])

MAX_LIMIT = 100


def _lookup_opportunity(db: Session, opportunity_id: str) -> Opportunity | None:
    stmt = select(Opportunity)
    try:
        uid = UUID(opportunity_id)
        stmt = stmt.where(Opportunity.id == uid)
    except ValueError:
        stmt = stmt.where(Opportunity.external_id == opportunity_id)
    return db.scalars(stmt).first()


@router.post("/upload", response_model=OpportunityUploadResult)
async def upload_opportunities(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> OpportunityUploadResult:
    content = await file.read()
    if not content:
        return OpportunityUploadResult(
            inserted=0,
            rejected=1,
            errors=[RowError(row=0, reason="empty file")],
        )
    result = ingest_opportunities_csv(db, content)
    db.commit()
    return result


@router.get("", response_model=OpportunityListResponse)
def list_opportunities(
    limit: int = Query(20, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> OpportunityListResponse:
    total = db.scalar(select(func.count()).select_from(Opportunity)) or 0
    rows = db.scalars(
        select(Opportunity).order_by(Opportunity.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return OpportunityListResponse(
        items=[OpportunityRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{opportunity_id}", response_model=OpportunityRead)
def get_opportunity(opportunity_id: str, db: Session = Depends(get_db)) -> OpportunityRead:
    row = _lookup_opportunity(db, opportunity_id)
    if row is None:
        raise HTTPException(status_code=404, detail="opportunity not found")
    return OpportunityRead.model_validate(row)
