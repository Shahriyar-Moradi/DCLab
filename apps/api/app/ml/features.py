"""Turn a raw Opportunity into a numeric feature vector.

Every feature documents what it means and where it comes from. Missing values
are filled with documented defaults rather than raising.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Fixed vocabularies so encoding is deterministic across train and serve.
STAGE_ENCODING: dict[str, int] = {
    "prospecting": 0,
    "qualification": 1,
    "proposal": 2,
    "negotiation": 3,
    "closed_won": 4,
    "closed_lost": 5,
}

SOURCE_ENCODING: dict[str, int] = {
    "inbound": 0,
    "outbound": 1,
    "referral": 2,
    "partner": 3,
    "website": 4,
}

FEATURE_NAMES: tuple[str, ...] = (
    "deal_size",
    "stage_encoded",
    "source_encoded",
    "engagement_score",
    "last_contact_days_ago",
    "num_interactions",
    "sales_rep_available",
    "opportunity_age_days",
)

DEFAULT_STAGE_CODE = -1
DEFAULT_SOURCE_CODE = -1
DEFAULT_ENGAGEMENT = 0.0
DEFAULT_LAST_CONTACT_DAYS = 30
DEFAULT_NUM_INTERACTIONS = 0
DEFAULT_SALES_REP_AVAILABLE = 0
DEFAULT_AGE_DAYS = 0


def _get(opportunity: Any, name: str) -> Any:
    if isinstance(opportunity, dict):
        return opportunity.get(name)
    return getattr(opportunity, name, None)


def _as_float(value: Any, default: float) -> tuple[float, bool]:
    if value is None or value == "":
        return default, True
    try:
        return float(value), False
    except (TypeError, ValueError):
        return default, True


def _as_int(value: Any, default: int) -> tuple[int, bool]:
    if value is None or value == "":
        return default, True
    if isinstance(value, bool):
        return int(value), False
    try:
        return int(value), False
    except (TypeError, ValueError):
        return default, True


def _as_bool01(value: Any, default: int) -> tuple[int, bool]:
    if value is None or value == "":
        return default, True
    if isinstance(value, bool):
        return int(value), False
    if isinstance(value, (int, float)):
        return int(bool(value)), False
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return 1, False
        if lowered in {"false", "0", "no"}:
            return 0, False
    return default, True


def build_features(opportunity: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Build a deterministic numeric feature dict from an opportunity-like object.

    deal_size: opportunity amount in the deal currency (source: Opportunity.amount).
    stage_encoded: ordinal stage index from STAGE_ENCODING (source: Opportunity.stage).
    source_encoded: categorical source index from SOURCE_ENCODING (source: Opportunity.source).
    engagement_score: 0–1 engagement intensity (source: Opportunity.engagement_score).
    last_contact_days_ago: days since last sales contact (source: Opportunity.last_contact_days_ago).
    num_interactions: count of recorded interactions (source: Opportunity.num_interactions).
    sales_rep_available: 1 if a rep can act now, else 0 (source: Opportunity.sales_rep_available).
    opportunity_age_days: whole days between created_at and `now` (source: Opportunity.created_at).
    """
    defaulted: list[str] = []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    deal_size, missing = _as_float(_get(opportunity, "amount"), 0.0)
    if missing:
        defaulted.append("deal_size")

    stage_raw = _get(opportunity, "stage")
    stage_key = str(stage_raw).strip().lower() if stage_raw else ""
    if stage_key in STAGE_ENCODING:
        stage_encoded = STAGE_ENCODING[stage_key]
    else:
        stage_encoded = DEFAULT_STAGE_CODE
        defaulted.append("stage_encoded")

    source_raw = _get(opportunity, "source")
    source_key = str(source_raw).strip().lower() if source_raw else ""
    if source_key in SOURCE_ENCODING:
        source_encoded = SOURCE_ENCODING[source_key]
    else:
        source_encoded = DEFAULT_SOURCE_CODE
        defaulted.append("source_encoded")

    engagement_score, missing = _as_float(_get(opportunity, "engagement_score"), DEFAULT_ENGAGEMENT)
    if missing:
        defaulted.append("engagement_score")

    last_contact_days_ago, missing = _as_int(
        _get(opportunity, "last_contact_days_ago"), DEFAULT_LAST_CONTACT_DAYS
    )
    if missing:
        defaulted.append("last_contact_days_ago")

    num_interactions, missing = _as_int(_get(opportunity, "num_interactions"), DEFAULT_NUM_INTERACTIONS)
    if missing:
        defaulted.append("num_interactions")

    sales_rep_available, missing = _as_bool01(
        _get(opportunity, "sales_rep_available"), DEFAULT_SALES_REP_AVAILABLE
    )
    if missing:
        defaulted.append("sales_rep_available")

    created_at = _get(opportunity, "created_at")
    if created_at is None:
        opportunity_age_days = DEFAULT_AGE_DAYS
        defaulted.append("opportunity_age_days")
    else:
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = None
        if created_at is None:
            opportunity_age_days = DEFAULT_AGE_DAYS
            defaulted.append("opportunity_age_days")
        else:
            if getattr(created_at, "tzinfo", None) is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            opportunity_age_days = max(0, int((now - created_at).total_seconds() // 86400))

    return {
        "deal_size": float(deal_size),
        "stage_encoded": int(stage_encoded),
        "source_encoded": int(source_encoded),
        "engagement_score": float(engagement_score),
        "last_contact_days_ago": int(last_contact_days_ago),
        "num_interactions": int(num_interactions),
        "sales_rep_available": int(sales_rep_available),
        "opportunity_age_days": int(opportunity_age_days),
        "defaulted": defaulted,
    }


def feature_vector(feature_dict: dict[str, Any], names: tuple[str, ...] | list[str] = FEATURE_NAMES) -> list[float]:
    return [float(feature_dict[name]) for name in names]
