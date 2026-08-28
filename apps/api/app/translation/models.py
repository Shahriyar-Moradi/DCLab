from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class ConfidenceBand(str, enum.Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class InsightCategory(str, enum.Enum):
    """Business function, not ML task type — this is what client navigation and
    layout are organized by (Step 3), never "classification" or "regression"."""

    MARKETING = "Marketing"
    SALES = "Sales"
    REVENUE = "Revenue"
    CHURN_RETENTION = "Churn & Retention"
    CUSTOMER_VALUE = "Customer Value"
    CUSTOM = "Custom"


class ClientFacingInsight(BaseModel):
    """The only shape a business customer ever sees. Every field here is either a
    business fact (an id, an amount, an action) or something already reduced to a
    qualitative band or a plain sentence. There is no field for a model name, a
    raw probability, a metric, or a count of anything the engine tried internally.
    """

    subject_id: str = Field(description="The business entity this insight is about (a customer, lead, or opportunity id).")
    category: InsightCategory
    headline: str = Field(description="A short plain-language summary, e.g. 'High retention risk'.")
    confidence_band: ConfidenceBand
    recommended_action: str
    expected_value: float
    currency: str = "AED"
    reasoning: list[str] = Field(
        description="2-4 plain sentences explaining the recommendation. No metrics, no raw scores."
    )
    generated_at: datetime
