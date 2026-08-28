"""One CSV with labels for all five admin Lab use cases — planted signal, deterministic."""

from __future__ import annotations

import numpy as np
import pandas as pd

STAGES = np.array(["prospecting", "qualification", "proposal", "negotiation"])
SOURCES = np.array(["inbound", "outbound", "referral", "partner"])
INDUSTRIES = np.array(["saas", "retail", "healthcare", "telecom", "finance"])


def make_lab_workbook(n: int = 240, seed: int = 42) -> pd.DataFrame:
    """Customer + deal attributes plus churn / conversion / lead / purchase / value labels."""
    rng = np.random.default_rng(seed)
    days_since = rng.integers(1, 180, size=n)
    logins = rng.integers(0, 40, size=n)
    spend = rng.lognormal(mean=5.6, sigma=0.55, size=n)
    amount = rng.lognormal(mean=8.8, sigma=0.7, size=n)
    engagement = np.clip(rng.normal(0.55, 0.22, size=n), 0.0, 1.0)
    interactions = rng.integers(0, 24, size=n)
    tickets = rng.integers(0, 8, size=n)
    orders = np.clip(rng.poisson(4, size=n), 0, None)
    stage = STAGES[rng.integers(0, len(STAGES), size=n)]
    source = SOURCES[rng.integers(0, len(SOURCES), size=n)]
    industry = INDUSTRIES[rng.integers(0, len(INDUSTRIES), size=n)]
    stage_rank = pd.Series(stage).map(
        {"prospecting": 0, "qualification": 1, "proposal": 2, "negotiation": 3}
    ).to_numpy()
    inbound = (source == "inbound").astype(float)
    saas = (industry == "saas").astype(float)

    churn_p = np.clip(
        0.12 + 0.004 * days_since - 0.012 * logins + 0.05 * tickets - 0.25 * engagement,
        0.03,
        0.92,
    )
    convert_p = np.clip(
        0.08 + 0.12 * stage_rank + 0.35 * engagement + 0.000008 * amount + 0.02 * interactions,
        0.04,
        0.92,
    )
    lead_p = np.clip(0.15 + 0.28 * inbound + 0.18 * saas + 0.25 * engagement, 0.05, 0.9)
    purchase_p = np.clip(
        0.2 + 0.06 * orders - 0.003 * days_since + 0.0004 * spend + 0.2 * engagement,
        0.04,
        0.92,
    )

    start = pd.Timestamp("2024-01-01")
    as_of = start + pd.to_timedelta(rng.integers(0, 400, size=n), unit="D")
    purchased = rng.binomial(1, purchase_p)
    return pd.DataFrame(
        {
            "entity_id": [f"C-{i:05d}" for i in range(n)],
            "as_of_date": as_of.strftime("%Y-%m-%d"),
            "customer_age": rng.integers(22, 68, size=n),
            "order_count": orders,
            "total_spend": np.round(spend, 2),
            "days_since_last_order": days_since,
            "logins_30d": logins,
            "last_login_days": np.clip(days_since - rng.integers(0, 10, size=n), 0, None),
            "marketing_interactions": rng.integers(0, 14, size=n),
            "support_tickets": tickets,
            "stage": stage,
            "source": source,
            "industry": industry,
            "amount": np.round(amount, 2),
            "engagement_score": np.round(engagement, 3),
            "num_interactions": interactions,
            "last_contact_days_ago": rng.integers(0, 60, size=n),
            "sales_rep_available": rng.integers(0, 2, size=n),
            "unique_products": rng.integers(1, 7, size=n),
            "avg_order_value": np.round(spend / np.clip(orders, 1, None), 2),
            "churned": rng.binomial(1, churn_p),
            "converted": rng.binomial(1, convert_p),
            "lead_converted": rng.binomial(1, lead_p),
            "purchase_within_60d": purchased,
            "customer_value_90d": np.round(spend * (0.25 + 0.9 * purchased), 2),
        }
    ).sort_values("as_of_date").reset_index(drop=True)
