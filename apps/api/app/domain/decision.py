from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DecisionGenerateResponse(BaseModel):
    """Exact Milestone 1 generate-response shape."""

    opportunity_id: str
    conversion_probability: float
    expected_revenue: float
    recommended_action: str
    confidence: float
    reasoning: list[str]
    model_version: str
    policy_version: str


class DecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    opportunity_id: UUID
    prediction_id: UUID
    recommended_action: str
    expected_revenue: float
    confidence: float
    reasoning: list[str]
    policy_version: str
    status: str
    created_at: datetime
    conversion_probability: float | None = None
    model_version: str | None = None
    external_id: str | None = None


class GenerateDecisionsRequest(BaseModel):
    opportunity_id: str | None = None
    generate_all: bool = False


class DecisionListResponse(BaseModel):
    items: list[DecisionRead]
    total: int
    limit: int
    offset: int
