from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id
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


def _normalize_action_filter(value: str | None) -> str | None:
    """Accepts either the internal action key style (CONTACT_TODAY) or the
    translated label shown to the client (Contact today) and normalizes both to
    the form stored in the database."""
    if not value:
        return value
    return value.strip().upper().replace(" ", "_")


@router.post("/generate")
def generate_decisions_endpoint(
    request: Request,
    payload: GenerateDecisionsRequest | None = None,
    db: Session = Depends(get_db),
) -> DecisionGenerateResponse | list[DecisionGenerateResponse]:
    payload = payload or GenerateDecisionsRequest()
    try:
        return generate_decisions(db, payload, workspace_id=request_workspace_id(request))
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
    request: Request,
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
        recommended_action=_normalize_action_filter(recommended_action),
        opportunity_id=opportunity_id,
        workspace_id=request_workspace_id(request),
    )


@router.get("/{decision_id}", response_model=DecisionRead)
def get_decision_endpoint(
    decision_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> DecisionRead:
    row = get_decision(db, decision_id, workspace_id=request_workspace_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return serialize_decision(row)
