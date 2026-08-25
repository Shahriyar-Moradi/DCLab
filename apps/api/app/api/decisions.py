from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.decision import (
    DecisionGenerateResponse,
    DecisionListResponse,
    DecisionRead,
    GenerateDecisionsRequest,
)
from app.domain.errors import InvalidGenerateRequestError, OpportunityNotFoundError
from app.ml.predict import ModelNotTrainedError
from app.services.decision_query import get_decision, list_decisions, serialize_decision
from app.services.generate_service import generate_decisions

router = APIRouter(prefix="/decisions", tags=["decisions"])
MAX_LIMIT = 100


@router.post("/generate")
def generate_decisions_endpoint(
    payload: GenerateDecisionsRequest | None = None,
    db: Session = Depends(get_db),
) -> DecisionGenerateResponse | list[DecisionGenerateResponse]:
    payload = payload or GenerateDecisionsRequest()
    try:
        return generate_decisions(db, payload)
    except OpportunityNotFoundError as exc:
        raise HTTPException(status_code=404, detail="opportunity not found") from exc
    except InvalidGenerateRequestError as exc:
        raise HTTPException(
            status_code=400,
            detail="provide opportunity_id or set generate_all to true",
        ) from exc
    except ModelNotTrainedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("", response_model=DecisionListResponse)
def list_decisions_endpoint(
    limit: int = Query(20, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    recommended_action: str | None = Query(None, alias="action"),
    opportunity_id: str | None = None,
    db: Session = Depends(get_db),
) -> DecisionListResponse:
    return list_decisions(
        db,
        limit=limit,
        offset=offset,
        status=status,
        recommended_action=recommended_action,
        opportunity_id=opportunity_id,
    )


@router.get("/{decision_id}", response_model=DecisionRead)
def get_decision_endpoint(decision_id: UUID, db: Session = Depends(get_db)) -> DecisionRead:
    row = get_decision(db, decision_id)
    if row is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return serialize_decision(row)
