"""Turn a conversion probability into a recommended action.

This is a pure function of (opportunity, probability, policy). Every numeric
threshold comes from the policy YAML — nothing is hardcoded here.

Uplift values in the YAML are PLACEHOLDERS until real treatment-effect data
exists in a later milestone. They are not causal estimates.

Confidence is a PLACEHOLDER for the later multi-model agreement score: it is
how far the probability sits from the policy decision boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings

ACTION_API_NAME = {
    "contact_today": "CONTACT_TODAY",
    "schedule_followup": "SCHEDULE_FOLLOWUP",
    "send_email": "SEND_EMAIL",
    "no_action": "NO_ACTION",
}


def load_policy(path: Path | None = None) -> dict[str, Any]:
    policy_path = Path(path or get_settings().policy_path)
    with policy_path.open() as handle:
        policy = yaml.safe_load(handle)
    if not policy or "version" not in policy:
        raise ValueError(f"Policy at {policy_path} is missing a version field")
    return policy


def _get(opportunity: Any, name: str, default: Any = None) -> Any:
    if isinstance(opportunity, dict):
        return opportunity.get(name, default)
    return getattr(opportunity, name, default)


def _confidence(probability: float, minimum_probability: float) -> float:
    """Placeholder: distance from the decision boundary, scaled into (0, 1]."""
    span = max(minimum_probability, 1.0 - minimum_probability)
    distance = abs(probability - minimum_probability)
    return round(min(1.0, 0.5 + distance / (2.0 * span)), 4)


def _contacts_this_week(opportunity: Any) -> int:
    """Placeholder contact-frequency estimate from last_contact_days_ago.

    A contact in the last 7 days counts as 1 this week; a contact in the last
    2 days counts as 2. Replace with a real activity feed later.
    """
    days = _get(opportunity, "last_contact_days_ago")
    if days is None:
        return 0
    try:
        days = int(days)
    except (TypeError, ValueError):
        return 0
    if days < 0:
        return 0
    if days == 0:
        return 3
    if days <= 2:
        return 2
    if days < 7:
        return 1
    return 0


def _build_reasoning(
    opportunity: Any,
    probability: float,
    expected_revenue: float,
    incremental: float,
    action: str,
    policy: dict[str, Any],
) -> list[str]:
    currency = _get(opportunity, "currency") or "AED"
    engagement = _get(opportunity, "engagement_score")
    days = _get(opportunity, "last_contact_days_ago")
    amount = _get(opportunity, "amount")
    stage = _get(opportunity, "stage")
    industry = _get(opportunity, "industry")
    available = _get(opportunity, "sales_rep_available")
    min_p = float(policy["constraints"]["minimum_probability"])

    lines: list[str] = []
    if engagement is not None:
        try:
            score = float(engagement)
            qualifier = "High" if score >= 0.7 else "Moderate" if score >= 0.4 else "Low"
            lines.append(f"{qualifier} engagement score ({score:.2f})")
        except (TypeError, ValueError):
            pass
    if days is not None:
        try:
            days_i = int(days)
            if days_i <= 7:
                lines.append(f"Recent activity ({days_i} days since contact)")
            else:
                lines.append(f"Stale activity ({days_i} days since contact)")
        except (TypeError, ValueError):
            pass
    if stage:
        lines.append(f"Pipeline stage is {stage}")
    if industry:
        lines.append(f"Industry is {industry}")
    if available is not None:
        lines.append(
            "Sales rep is available" if available else "Sales rep is not available"
        )
    lines.append(f"Conversion probability {probability:.2f} vs policy floor {min_p:.2f}")
    if amount is not None:
        try:
            lines.append(
                f"Expected value {currency} {float(expected_revenue):,.0f} "
                f"on a {currency} {float(amount):,.0f} deal"
            )
        except (TypeError, ValueError):
            pass
    lines.append(
        f"Selected {ACTION_API_NAME.get(action, action)} "
        f"with expected incremental value {currency} {incremental:,.0f} vs {currency} 0 for no action"
    )
    return lines


def decide(
    opportunity: Any,
    conversion_probability: float,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Pure function: opportunity + probability + policy → decision dict."""
    constraints = policy["constraints"]
    minimum_probability = float(constraints["minimum_probability"])
    max_contacts = int(constraints["max_contacts_per_customer_per_week"])
    uplifts: dict[str, float] = dict(policy.get("action_uplift") or {})
    actions: list[str] = list(policy.get("actions") or [])

    amount = float(_get(opportunity, "amount") or 0)
    expected_revenue = amount * conversion_probability
    contacts = _contacts_this_week(opportunity)
    rep_available = _get(opportunity, "sales_rep_available")
    if rep_available is None:
        rep_available = True

    chosen = "no_action"
    best_incremental = 0.0

    if conversion_probability >= minimum_probability:
        for action in actions:
            if action == "no_action":
                continue
            # Placeholder constraint: do not pile more live contacts onto a customer
            # already at the weekly cap. contact_today is the only action that
            # consumes a contact slot in this milestone.
            if action == "contact_today" and contacts >= max_contacts:
                continue
            if action == "contact_today" and not rep_available:
                continue
            uplift = float(uplifts.get(action, 0.0))
            incremental = expected_revenue * uplift
            if incremental > best_incremental:
                best_incremental = incremental
                chosen = action

    reasoning = _build_reasoning(
        opportunity, conversion_probability, expected_revenue, best_incremental, chosen, policy
    )
    return {
        "recommended_action": ACTION_API_NAME[chosen],
        "expected_revenue": round(expected_revenue, 2),
        "confidence": _confidence(conversion_probability, minimum_probability),
        "reasoning": reasoning,
        "policy_version": str(policy["version"]),
        "action_key": chosen,
        "incremental_value": round(best_incremental, 2),
    }
