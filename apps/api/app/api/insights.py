from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id
from app.db.session import get_db
from app.domain.insight import InsightCategoryGroup, InsightListResponse
from app.services.insight_query import list_client_insights
from app.translation.models import InsightCategory

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("", response_model=InsightListResponse)
def list_insights(request: Request, db: Session = Depends(get_db)) -> InsightListResponse:
    grouped = list_client_insights(db, request_workspace_id(request))
    return InsightListResponse(
        categories=[
            InsightCategoryGroup(category=category, insights=grouped[category])
            for category in InsightCategory
        ]
    )
