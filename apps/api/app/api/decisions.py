from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Decision, Opportunity, Prediction
from app.db.session import get_db
from app.domain.decision import (
    DecisionGenerateResponse,
    DecisionListResponse,
    DecisionRead,
    GenerateDecisionsRequest,
)
from app.ml.predict import ModelNotTrainedError, predict_with_evidence
from app.services.decision_service import decide, load_policy

router = APIRouter(prefix="/decisions", tags=["decisions"])
MAX_LIMIT = 100


def _to_generate_response(
    opportunity: Opportunity, prediction: Prediction, decision: Decision
) -> DecisionGenerateResponse:
    return DecisionGenerateResponse(
        opportunity_id=opportunity.external_id,
        conversion_probability=prediction.conversion_probability,
        expected_revenue=float(decision.expected_revenue),
        recommended_action=decision.recommended_action,
        confidence=decision.confidence,
        reasoning=list(decision.reasoning),
        model_version=prediction.model_version,
        policy_version=decision.policy_version,
    )


def _generate_one(db: Session, opportunity: Opportunity) -> DecisionGenerateResponse:
    try:
        scored = predict_with_evidence(opportunity)
    except ModelNotTrainedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    probability = scored["probability"]
    model_version = scored["model_version"]
    policy = load_policy()
    result = decide(opportunity, probability, policy)

    prediction = Prediction(
        opportunity_id=opportunity.id,
        model_version=model_version,
        conversion_probability=probability,
        evidence=scored["evidence"],
    )
    db.add(prediction)
    db.flush()

    existing = db.scalars(select(Decision).where(Decision.opportunity_id == opportunity.id)).first()
    if existing is None:
        existing = Decision(
            opportunity_id=opportunity.id,
            prediction_id=prediction.id,
            recommended_action=result["recommended_action"],
            expected_revenue=result["expected_revenue"],
            confidence=result["confidence"],
            reasoning=result["reasoning"],
            policy_version=result["policy_version"],
            status="pending_review",
        )
        db.add(existing)
    else:
        existing.prediction_id = prediction.id
        existing.recommended_action = result["recommended_action"]
        existing.expected_revenue = result["expected_revenue"]
        existing.confidence = result["confidence"]
        existing.reasoning = result["reasoning"]
        existing.policy_version = result["policy_version"]

    db.commit()
    db.refresh(existing)
    db.refresh(prediction)
    return _to_generate_response(opportunity, prediction, existing)


@router.post("/generate")
def generate_decisions(
    payload: GenerateDecisionsRequest | None = None,
    db: Session = Depends(get_db),
):
    payload = payload or GenerateDecisionsRequest()
    if payload.opportunity_id:
        try:
            uid = UUID(payload.opportunity_id)
            opportunity = db.get(Opportunity, uid)
        except ValueError:
            opportunity = db.scalars(
                select(Opportunity).where(Opportunity.external_id == payload.opportunity_id)
            ).first()
        if opportunity is None:
            raise HTTPException(status_code=404, detail="opportunity not found")
        return _generate_one(db, opportunity)

    if payload.generate_all:
        decided_ids = select(Decision.opportunity_id)
        opportunities = db.scalars(
            select(Opportunity).where(Opportunity.id.not_in(decided_ids))
        ).all()
        return [_generate_one(db, row) for row in opportunities]

    raise HTTPException(
        status_code=400,
        detail="provide opportunity_id or set generate_all to true",
    )


@router.get("", response_model=DecisionListResponse)
def list_decisions(
    limit: int = Query(20, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    recommended_action: str | None = None,
    db: Session = Depends(get_db),
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

    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(Decision.created_at.desc()).offset(offset).limit(limit)).all()
    items = [
        DecisionRead(
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
        for row in rows
    ]
    return DecisionListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{decision_id}", response_model=DecisionRead)
def get_decision(decision_id: UUID, db: Session = Depends(get_db)) -> DecisionRead:
    row = db.scalars(
        select(Decision)
        .options(selectinload(Decision.prediction), selectinload(Decision.opportunity))
        .where(Decision.id == decision_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="decision not found")
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
