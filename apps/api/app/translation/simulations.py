"""Translator for the eight simulation use cases (churn, purchase, lead_conversion,
upsell, cross_sell, campaign_response, customer_value, custom_support).

Each use case runs through the same DCLab factory + policy engine internally, but
the eight are different enough in what a business user needs to hear that each
gets its own reasoning builder — a single generic sentence would either be too
vague to be useful or would have to fall back to describing the raw features.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from app.translation.bands import agreement_to_band
from app.translation.models import ClientFacingInsight, InsightCategory

CATEGORY_BY_USE_CASE: dict[str, InsightCategory] = {
    "churn": InsightCategory.CHURN_RETENTION,
    "purchase": InsightCategory.SALES,
    "lead_conversion": InsightCategory.SALES,
    "upsell": InsightCategory.REVENUE,
    "cross_sell": InsightCategory.REVENUE,
    "campaign_response": InsightCategory.MARKETING,
    "customer_value": InsightCategory.CUSTOMER_VALUE,
    "custom_support": InsightCategory.CUSTOM,
}

HEADLINE_BY_USE_CASE: dict[str, str] = {
    "churn": "Retention risk",
    "purchase": "Purchase likelihood",
    "lead_conversion": "Lead priority",
    "upsell": "Upsell opportunity",
    "cross_sell": "Cross-sell opportunity",
    "campaign_response": "Campaign responsiveness",
    "customer_value": "Customer value",
    "custom_support": "Support need",
}

ACTION_OVERRIDES = {
    "do_nothing": "No action",
    "email": "Send an email",
    "discount": "Offer a discount",
    "call": "Call the customer",
    "csm_call": "Schedule a customer success call",
    "priority_call": "Prioritize a sales call",
    # Generic humanization (action_key.replace("_", " ").capitalize()) would
    # otherwise render this as "Offer training" -- a legitimate churn-retention
    # action that happens to collide with banned ML vocabulary ("training").
    # Caught live by scripts/audit_client_surface.py, not by static scanning,
    # since the schema only declares this field as `str`.
    "offer_training": "Offer a coaching session",
}


def _humanize_action(action_key: str) -> str:
    if action_key in ACTION_OVERRIDES:
        return ACTION_OVERRIDES[action_key]
    return action_key.replace("_", " ").strip().capitalize()


def _num(features: dict[str, Any], key: str) -> float | None:
    value = features.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _churn_reasoning(features: dict[str, Any]) -> list[str]:
    lines = []
    change = _num(features, "login_frequency_change")
    if change is not None and change < -0.1:
        lines.append("Product usage has dropped over the last month")
    negative_support = _num(features, "negative_support")
    if negative_support and negative_support > 0:
        lines.append("Recent support interactions were negative")
    renewal = _num(features, "days_until_renewal")
    if renewal is not None and renewal < 30:
        lines.append("Contract renewal is coming up soon")
    engagement = _num(features, "email_engagement")
    if engagement is not None and engagement < 0.3:
        lines.append("Low engagement with recent emails")
    return lines


def _purchase_reasoning(features: dict[str, Any]) -> list[str]:
    lines = []
    abandon = _num(features, "cart_abandonments")
    if abandon and abandon > 0:
        lines.append("Has abandoned a cart recently")
    since_last = _num(features, "days_since_last_purchase")
    if since_last is not None and since_last <= 14:
        lines.append("Has purchased recently and remains active")
    views = _num(features, "product_views_7d")
    if views is not None and views >= 15:
        lines.append("Has been browsing frequently this week")
    return lines


def _lead_conversion_reasoning(features: dict[str, Any]) -> list[str]:
    lines = []
    if _num(features, "demo_request") == 1.0:
        lines.append("Requested a product demo")
    pricing_visits = _num(features, "pricing_page_visits")
    if pricing_visits and pricing_visits > 0:
        lines.append("Visited the pricing page")
    engagement = _num(features, "campaign_engagement")
    if engagement is not None and engagement >= 0.6:
        lines.append("Highly engaged with recent marketing")
    return lines


def _upsell_reasoning(features: dict[str, Any]) -> list[str]:
    lines = []
    change = _num(features, "feature_usage_change")
    if change is not None and change > 0.1:
        lines.append("Product usage has been growing")
    features_used = _num(features, "features_used")
    if features_used is not None and features_used >= 8:
        lines.append("Actively using most of the product's features")
    return lines


def _cross_sell_reasoning(features: dict[str, Any]) -> list[str]:
    lines = []
    if _num(features, "past_hotels"):
        lines.append("Has booked hotels on previous trips")
    if _num(features, "past_activities"):
        lines.append("Has purchased activities on previous trips")
    lead_days = _num(features, "booking_lead_days")
    if lead_days is not None and lead_days <= 21:
        lines.append("Trip is coming up soon")
    return lines


def _campaign_response_reasoning(features: dict[str, Any]) -> list[str]:
    lines = []
    engagement = _num(features, "email_engagement")
    if engagement is not None:
        lines.append("Strong recent email engagement" if engagement >= 0.5 else "Limited recent email engagement")
    last_login = _num(features, "last_login_days")
    if last_login is not None and last_login <= 7:
        lines.append("Has been active recently")
    return lines


def _customer_value_reasoning(features: dict[str, Any]) -> list[str]:
    lines = []
    revenue = _num(features, "monthly_revenue")
    if revenue is not None and revenue >= 249:
        lines.append("On a higher-tier plan")
    renewal = _num(features, "days_until_renewal")
    if renewal is not None and renewal >= 60:
        lines.append("Not at immediate renewal risk")
    return lines


def _custom_support_reasoning(features: dict[str, Any]) -> list[str]:
    lines = []
    tickets = _num(features, "support_tickets")
    if tickets and tickets >= 2:
        lines.append("Has raised several support tickets recently")
    negative = _num(features, "negative_support")
    if negative and negative > 0:
        lines.append("Recent support experience has been negative")
    return lines


REASONING_BUILDERS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "churn": _churn_reasoning,
    "purchase": _purchase_reasoning,
    "lead_conversion": _lead_conversion_reasoning,
    "upsell": _upsell_reasoning,
    "cross_sell": _cross_sell_reasoning,
    "campaign_response": _campaign_response_reasoning,
    "customer_value": _customer_value_reasoning,
    "custom_support": _custom_support_reasoning,
}


def translate_simulation_outcome(
    use_case_name: str,
    *,
    external_id: str,
    features: dict[str, Any],
    agreement: float,
    recommended_action_key: str,
    expected_value: float,
    incremental_value: float,
    currency: str = "AED",
    generated_at: datetime | None = None,
) -> ClientFacingInsight:
    if use_case_name not in CATEGORY_BY_USE_CASE:
        raise ValueError(f"no translator registered for use case {use_case_name!r}")

    builder = REASONING_BUILDERS[use_case_name]
    lines = builder(features)
    if incremental_value > 0:
        lines.append(f"Expected to add {currency} {incremental_value:,.0f} in value compared to no action")
    if not lines:
        lines = ["Not enough recent activity to explain this recommendation."]

    return ClientFacingInsight(
        subject_id=external_id,
        category=CATEGORY_BY_USE_CASE[use_case_name],
        headline=HEADLINE_BY_USE_CASE[use_case_name],
        confidence_band=agreement_to_band(agreement),
        recommended_action=_humanize_action(recommended_action_key),
        expected_value=round(float(expected_value), 2),
        currency=currency,
        reasoning=lines[:4],
        generated_at=generated_at or datetime.now(UTC),
    )
