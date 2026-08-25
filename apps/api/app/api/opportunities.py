from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.opportunity import OpportunityListResponse, OpportunityRead, OpportunityUploadResult, RowError
from app.services.ingestion_service import ingest_opportunities_csv
from app.services.opportunity_query import get_opportunity, list_opportunities

router = APIRouter(prefix="/opportunities", tags=["opportunities"])

MAX_LIMIT = 100


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
def list_opportunities_endpoint(
    limit: int = Query(20, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    stage: str | None = None,
    sort: str = Query("created_at", pattern="^(created_at|amount)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> OpportunityListResponse:
    return list_opportunities(db, limit=limit, offset=offset, stage=stage, sort=sort, order=order)


@router.get("/{opportunity_id}", response_model=OpportunityRead)
def get_opportunity_endpoint(opportunity_id: str, db: Session = Depends(get_db)) -> OpportunityRead:
    row = get_opportunity(db, opportunity_id)
    if row is None:
        raise HTTPException(status_code=404, detail="opportunity not found")
    return OpportunityRead.model_validate(row)
