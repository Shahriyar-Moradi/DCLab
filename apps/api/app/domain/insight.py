from __future__ import annotations

from pydantic import BaseModel

from app.translation.models import ClientFacingInsight, InsightCategory


class InsightCategoryGroup(BaseModel):
    category: InsightCategory
    insights: list[ClientFacingInsight]


class InsightListResponse(BaseModel):
    """Client-facing Insights section (Step 3): one group per business function,
    always present even when empty, so the frontend can render a consistent set
    of sections regardless of which use cases an admin has run data for."""

    categories: list[InsightCategoryGroup]
