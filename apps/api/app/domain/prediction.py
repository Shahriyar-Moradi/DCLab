from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PredictionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    opportunity_id: UUID
    model_version: str
    conversion_probability: float
    created_at: datetime
    evidence: dict | None = None
