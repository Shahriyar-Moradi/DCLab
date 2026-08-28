from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.translation.models import ConfidenceBand


class DecisionGenerateResponse(BaseModel):
    """Client-facing decision output. Everything here has passed through
    `app.translation` — there is deliberately no field for the raw conversion
    probability or the model that produced it."""

    opportunity_id: str
    recommended_action: str
    confidence_band: ConfidenceBand
    expected_revenue: float
    reasoning: list[str]
    policy_version: str


class DecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    opportunity_id: UUID
    external_id: str | None = None
    recommended_action: str
    expected_revenue: float
    confidence_band: ConfidenceBand
    reasoning: list[str]
    policy_version: str
    status: str
    created_at: datetime


class GenerateDecisionsRequest(BaseModel):
    opportunity_id: str | None = None
    generate_all: bool = False


class DecisionListResponse(BaseModel):
    items: list[DecisionRead]
    total: int
    limit: int
    offset: int
