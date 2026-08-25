"""Deterministic synthetic customers for tests — planted signal, no Olist."""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_synthetic_customers(n: int = 2000, seed: int = 42, leak: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    age = rng.integers(18, 70, size=n)
    order_count = rng.poisson(4, size=n)
    total_spend = rng.lognormal(mean=5.5, sigma=0.6, size=n)
    days_since = rng.integers(1, 180, size=n)
    marketing = rng.integers(0, 12, size=n)
    review = rng.uniform(1, 5, size=n)
    logit = (
        -0.35
        + 0.42 * (order_count - 3)
        + 0.0015 * (total_spend - 200)
        - 0.028 * days_since
        + 0.38 * marketing
        + 0.55 * (review - 3)
    )
    prob = 1 / (1 + np.exp(-logit))
    target = rng.binomial(1, np.clip(prob, 0.02, 0.95))
    start = pd.Timestamp("2023-01-01")
    as_of = start + pd.to_timedelta(rng.integers(0, 400, size=n), unit="D")
    frame = pd.DataFrame(
        {
            "entity_id": [f"C-{i:05d}" for i in range(n)],
            "as_of_date": as_of,
            "customer_age": age,
            "order_count": order_count,
            "total_spend": total_spend,
            "days_since_last_order": days_since,
            "marketing_interactions": marketing,
            "review_score": review,
            "category_count": rng.integers(1, 8, size=n),
            "avg_order_value": total_spend / np.clip(order_count, 1, None),
            "lifetime_days": rng.integers(10, 800, size=n),
            "item_count": np.clip(order_count * rng.integers(1, 3, size=n), 1, None),
            "unique_products": rng.integers(1, 6, size=n),
            "unique_sellers": rng.integers(1, 4, size=n),
            "avg_price": total_spend / np.clip(order_count, 1, None),
            "review_count": rng.integers(0, 8, size=n),
            "avg_review_score": review,
            "latest_review_score": review,
            "target": target,
            "purchase_within_60d": target,
            "revenue_60d": total_spend * (0.15 + 0.7 * target),
            "customer_value_90d": total_spend * (0.2 + 0.9 * target),
            "days_to_next_purchase": np.clip(25 + (1 - target) * 90 + rng.normal(0, 8, size=n), 1, 365),
        }
    )
    if leak:
        frame["future_purchase_amount"] = target * rng.uniform(50, 400, size=n)
    return frame.sort_values("as_of_date").reset_index(drop=True)


def synthetic_events(n_customers: int = 200, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_customers):
        n_orders = int(rng.integers(1, 8))
        t = pd.Timestamp("2022-01-01")
        for _ in range(n_orders):
            t = t + pd.to_timedelta(int(rng.integers(10, 80)), unit="D")
            rows.append(
                {
                    "entity_id": f"C-{i:05d}",
                    "event_time": t,
                    "value": float(rng.uniform(20, 300)),
                }
            )
    return pd.DataFrame(rows)


SYNTHETIC_GROUPS = {
    "customer": ["customer_age", "order_count", "total_spend", "lifetime_days"],
    "transaction": ["avg_order_value", "order_count", "item_count"],
    "behavior": ["marketing_interactions", "review_score", "review_count"],
    "temporal": ["days_since_last_order"],
    "product": ["category_count", "unique_products", "unique_sellers", "avg_price"],
    "reviews": ["avg_review_score", "latest_review_score", "review_count"],
    "marketing": ["marketing_interactions"],
}
