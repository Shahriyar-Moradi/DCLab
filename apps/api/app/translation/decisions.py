"""Translator for the opportunity → decision flow (the M1 pipeline)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.translation.bands import probability_to_band
from app.translation.models import ClientFacingInsight, InsightCategory

ACTION_LABELS = {
    "CONTACT_TODAY": "Contact today",
    "SCHEDULE_FOLLOWUP": "Schedule a follow-up",
    "SEND_EMAIL": "Send an email",
    "NO_ACTION": "No action needed",
}
_ACTION_KEY_BY_API_NAME = {
    "CONTACT_TODAY": "contact_today",
    "SCHEDULE_FOLLOWUP": "schedule_followup",
    "SEND_EMAIL": "send_email",
    "NO_ACTION": "no_action",
}


def _get(opportunity: Any, name: str, default: Any = None) -> Any:
    if isinstance(opportunity, dict):
        return opportunity.get(name, default)
    return getattr(opportunity, name, default)


def _headline(action_key: str) -> str:
    if action_key == "no_action":
        return "Low near-term priority"
    return "Likely to convert"


def _reasoning(opportunity: Any, incremental_value: float, currency: str) -> list[str]:
    """Plain-language drivers only — this is the narrative Step 1 requires in
    place of a feature-importance bar chart. No raw probability, no policy
    thresholds, no model vocabulary."""
    lines: list[str] = []

    engagement = _get(opportunity, "engagement_score")
    if engagement is not None:
        try:
            score = float(engagement)
            qualifier = "High" if score >= 0.7 else "Moderate" if score >= 0.4 else "Low"
            lines.append(f"{qualifier} engagement based on recent activity")
        except (TypeError, ValueError):
            pass

    days = _get(opportunity, "last_contact_days_ago")
    if days is not None:
        try:
            days_i = int(days)
            if days_i <= 7:
                lines.append("Contacted recently, within the last week")
            else:
                lines.append("Has not been contacted in a while")
        except (TypeError, ValueError):
            pass

    stage = _get(opportunity, "stage")
    if stage:
        lines.append(f"Currently in the {stage} stage")

    available = _get(opportunity, "sales_rep_available")
    if available is not None:
        lines.append(
            "A sales rep is available to act on this today"
            if available
            else "No sales rep is currently available"
        )

    if incremental_value > 0:
        lines.append(f"Expected to add {currency} {incremental_value:,.0f} in value compared to no action")

    return lines[:4] if lines else ["Not enough activity yet to explain this recommendation."]


def translate_opportunity_decision(
    opportunity: Any,
    *,
    conversion_probability: float,
    decision_result: dict[str, Any],
    generated_at: datetime | None = None,
) -> ClientFacingInsight:
    """`decision_result` is the dict returned by `decision_service.decide()`. This
    is the one place its raw probability and internal reasoning are allowed to
    exist before being thrown away — nothing downstream of this function ever
    sees them again."""
    currency = _get(opportunity, "currency") or "AED"
    incremental_value = float(decision_result.get("incremental_value") or 0.0)
    action_api_name = str(decision_result.get("recommended_action") or "NO_ACTION")
    action_key = str(
        decision_result.get("action_key")
        or _ACTION_KEY_BY_API_NAME.get(action_api_name, "no_action")
    )

    return ClientFacingInsight(
        subject_id=str(_get(opportunity, "external_id") or _get(opportunity, "id")),
        category=InsightCategory.SALES,
        headline=_headline(action_key),
        confidence_band=probability_to_band(conversion_probability),
        recommended_action=ACTION_LABELS.get(action_api_name, action_api_name.replace("_", " ").title()),
        expected_value=round(float(decision_result.get("expected_revenue") or 0.0), 2),
        currency=currency,
        reasoning=_reasoning(opportunity, incremental_value, currency),
        generated_at=generated_at or datetime.now(UTC),
    )
