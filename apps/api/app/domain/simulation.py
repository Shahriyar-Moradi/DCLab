from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.sim import USE_CASES


class SimulationRunRequest(BaseModel):
    use_case: str = Field(..., description="One of the eight simulation questions, or 'all'")


class SimulationHeroDecision(BaseModel):
    external_id: str
    probability: float
    recommended_action: str
    expected_value: float
    incremental_value: float
    agreement: float
    action_table: list[dict]
    evidence: dict
    uplift_is_simulated: bool = True
    model_version: str
    policy_version: str


class SimulationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    use_case: str
    model_version: str
    policy_version: str
    fusion: str
    payload: dict
    created_at: datetime


class SimulationRunListResponse(BaseModel):
    items: list[SimulationRunRead]
    total: int


class SimulationDecisionResponse(BaseModel):
    run_id: UUID
    use_case: str
    external_id: str
    conversion_probability: float
    expected_revenue: float
    recommended_action: str
    confidence: float
    reasoning: list[str]
    model_version: str
    policy_version: str
    action_table: list[dict]
    evidence: dict
    uplift_is_simulated: bool = True


KNOWN_USE_CASES = set(USE_CASES)
