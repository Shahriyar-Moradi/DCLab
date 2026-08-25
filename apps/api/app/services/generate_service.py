"""Generate a decision for one opportunity (or all undecided rows).

Orchestrates predict → policy → persist. The HTTP adapter maps domain errors
to status codes; this module does not import FastAPI.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Decision, Opportunity, Prediction
from app.domain.decision import DecisionGenerateResponse, GenerateDecisionsRequest
from app.domain.errors import InvalidGenerateRequestError, OpportunityNotFoundError
from app.ml.predict import predict_with_evidence
from app.services.decision_service import decide, load_policy
from app.services.opportunity_query import get_opportunity


def to_generate_response(
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


def generate_one(db: Session, opportunity: Opportunity) -> DecisionGenerateResponse:
    scored = predict_with_evidence(opportunity)
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
    return to_generate_response(opportunity, prediction, existing)


def generate_decisions(
    db: Session, payload: GenerateDecisionsRequest
) -> DecisionGenerateResponse | list[DecisionGenerateResponse]:
    if payload.opportunity_id:
        opportunity = get_opportunity(db, payload.opportunity_id)
        if opportunity is None:
            raise OpportunityNotFoundError("opportunity not found")
        return generate_one(db, opportunity)

    if payload.generate_all:
        decided_ids = select(Decision.opportunity_id)
        opportunities = db.scalars(select(Opportunity).where(Opportunity.id.not_in(decided_ids))).all()
        return [generate_one(db, row) for row in opportunities]

    raise InvalidGenerateRequestError("provide opportunity_id or set generate_all to true")
