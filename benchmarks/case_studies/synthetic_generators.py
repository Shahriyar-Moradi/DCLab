"""Documented synthetic generators with a sidecar ground truth for CS4-CS6.

Each generator follows the pattern already used for the Lab environment's
synthetic company data: a documented true latent probability function,
labels drawn from that probability with realistic sampling noise, and a
ground-truth sidecar frame that is kept completely separate from the
observed/training frame. Nothing in the observed frame ever contains the
true probability — only the noisy drawn label. The sidecar exists purely for
Step 4's calibration-against-truth comparison.

Every generator has signature ``(*, n: int, seed: int) -> (observed, ground_truth)``:

- ``observed``:     entity_id, as_of_date, declared feature columns, and the
                     one observed (noisy) target column — this is what both
                     the baseline and the DCLab engine ever see.
- ``ground_truth``:  entity_id, as_of_date, true_probability, observed label
                      (for convenience joins). Never merged into ``observed``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _as_of_dates(rng: np.random.Generator, n: int, start: str = "2023-01-01", span_days: int = 400) -> pd.Series:
    start_ts = pd.Timestamp(start)
    return start_ts + pd.to_timedelta(rng.integers(0, span_days, size=n), unit="D")


def lead_conversion_v1(*, n: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """True latent function (documented, never exposed to training):

    logit = -1.2 + 1.8*engagement_score + 0.15*num_interactions
            - 0.03*last_contact_days_ago + 0.35*sales_rep_available
            + 0.00003*(amount - 5000) + source_effect + industry_effect

    ``amount`` (deal size) is deliberately given a near-zero coefficient —
    in this generator, deal size does not predict whether a lead converts.
    """
    rng = np.random.default_rng(seed)
    entity_id = [f"L-{i:05d}" for i in range(n)]
    as_of_date = _as_of_dates(rng, n)
    engagement_score = rng.uniform(0, 1, size=n)
    num_interactions = rng.poisson(3, size=n)
    last_contact_days_ago = rng.integers(0, 60, size=n)
    amount = rng.lognormal(mean=8.5, sigma=0.7, size=n)
    # int, not bool: app.engine.schema.profiler.profile_frame computes
    # quantiles on every column and numpy no longer supports subtraction on
    # boolean arrays (a pre-existing engine/numpy-version limitation never
    # exercised before since no existing DCLab dataset had a boolean column).
    sales_rep_available = rng.integers(0, 2, size=n)

    sources = np.array(["organic", "paid_search", "referral", "outbound", "event"])
    source_weight = {"organic": 0.1, "paid_search": 0.0, "referral": 0.6, "outbound": -0.3, "event": 0.4}
    source = rng.choice(sources, size=n, p=[0.25, 0.20, 0.20, 0.25, 0.10])
    source_effect = np.array([source_weight[s] for s in source])

    industries = np.array(["retail", "saas", "manufacturing", "healthcare", "finance"])
    industry_weight = {"retail": 0.0, "saas": 0.2, "manufacturing": -0.2, "healthcare": -0.1, "finance": 0.1}
    industry = rng.choice(industries, size=n)
    industry_effect = np.array([industry_weight[i] for i in industry])

    logit = (
        -1.2
        + 1.8 * engagement_score
        + 0.15 * num_interactions
        - 0.03 * last_contact_days_ago
        + 0.35 * sales_rep_available.astype(float)
        + 0.00003 * (amount - 5000)
        + source_effect
        + industry_effect
    )
    true_probability = _sigmoid(logit)
    converted = rng.binomial(1, np.clip(true_probability, 0.02, 0.95))
    deal_size_band = pd.cut(amount, bins=[0, 3000, 8000, np.inf], labels=["small", "medium", "large"]).astype(str)

    observed = pd.DataFrame(
        {
            "entity_id": entity_id,
            "as_of_date": as_of_date,
            "engagement_score": engagement_score,
            "num_interactions": num_interactions,
            "last_contact_days_ago": last_contact_days_ago,
            "amount": amount,
            "source": source,
            "sales_rep_available": sales_rep_available,
            "industry": industry,
            "deal_size_band": deal_size_band,
            "converted": converted,
        }
    ).sort_values("as_of_date").reset_index(drop=True)

    ground_truth = pd.DataFrame(
        {
            "entity_id": entity_id,
            "as_of_date": as_of_date,
            "true_probability": true_probability,
            "observed_label": converted,
        }
    )
    return observed, ground_truth


def upsell_crosssell_v1(*, n: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """True latent function (documented, never exposed to training):

    logit = -1.0 + 1.5*usage_score + 0.18*product_breadth
            - 0.01*last_purchase_days_ago + 0.0006*tenure_days
            - 0.25*log1p(support_ticket_count) + 0.00008*(current_spend - 500)
    """
    rng = np.random.default_rng(seed)
    entity_id = [f"U-{i:05d}" for i in range(n)]
    as_of_date = _as_of_dates(rng, n)
    tenure_days = rng.integers(30, 2000, size=n)
    usage_score = rng.uniform(0, 1, size=n)
    product_breadth = rng.integers(1, 10, size=n)
    support_ticket_count = rng.poisson(1.5, size=n)
    last_purchase_days_ago = rng.integers(0, 180, size=n)
    current_spend = rng.lognormal(mean=6.5, sigma=0.6, size=n)

    logit = (
        -1.0
        + 1.5 * usage_score
        + 0.18 * product_breadth
        - 0.01 * last_purchase_days_ago
        + 0.0006 * tenure_days
        - 0.25 * np.log1p(support_ticket_count)
        + 0.00008 * (current_spend - 500)
    )
    true_probability = _sigmoid(logit)
    upsell_converted = rng.binomial(1, np.clip(true_probability, 0.02, 0.95))
    tenure_band = pd.cut(
        tenure_days, bins=[0, 180, 730, np.inf], labels=["new", "established", "veteran"]
    ).astype(str)

    observed = pd.DataFrame(
        {
            "entity_id": entity_id,
            "as_of_date": as_of_date,
            "tenure_days": tenure_days,
            "usage_score": usage_score,
            "product_breadth": product_breadth,
            "support_ticket_count": support_ticket_count,
            "last_purchase_days_ago": last_purchase_days_ago,
            "current_spend": current_spend,
            "tenure_band": tenure_band,
            "upsell_converted": upsell_converted,
        }
    ).sort_values("as_of_date").reset_index(drop=True)

    ground_truth = pd.DataFrame(
        {
            "entity_id": entity_id,
            "as_of_date": as_of_date,
            "true_probability": true_probability,
            "observed_label": upsell_converted,
        }
    )
    return observed, ground_truth


def campaign_response_v1(*, n: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """True latent function (documented, never exposed to training):

    logit = -1.5 + 3.0*past_response_rate + 0.2*prior_response_count
            + 0.01*days_since_last_campaign + channel_effect + segment_effect
    """
    rng = np.random.default_rng(seed)
    entity_id = [f"M-{i:05d}" for i in range(n)]
    as_of_date = _as_of_dates(rng, n)
    past_response_rate = rng.beta(2, 5, size=n)
    prior_response_count = rng.poisson(1.2, size=n)
    days_since_last_campaign = rng.integers(1, 120, size=n)

    channels = np.array(["email", "sms", "push", "direct_mail"])
    channel_weight = {"email": 0.3, "sms": 0.1, "push": -0.1, "direct_mail": -0.3}
    channel = rng.choice(channels, size=n, p=[0.40, 0.25, 0.25, 0.10])
    channel_effect = np.array([channel_weight[c] for c in channel])

    segments = np.array(["high_value", "standard", "new", "at_risk"])
    segment_weight = {"high_value": 0.5, "standard": 0.0, "new": -0.2, "at_risk": -0.4}
    customer_segment = rng.choice(segments, size=n, p=[0.15, 0.50, 0.20, 0.15])
    segment_effect = np.array([segment_weight[s] for s in customer_segment])

    logit = (
        -1.5
        + 3.0 * past_response_rate
        + 0.2 * prior_response_count
        + 0.01 * days_since_last_campaign
        + channel_effect
        + segment_effect
    )
    true_probability = _sigmoid(logit)
    responded = rng.binomial(1, np.clip(true_probability, 0.02, 0.95))

    observed = pd.DataFrame(
        {
            "entity_id": entity_id,
            "as_of_date": as_of_date,
            "channel": channel,
            "past_response_rate": past_response_rate,
            "prior_response_count": prior_response_count,
            "days_since_last_campaign": days_since_last_campaign,
            "customer_segment": customer_segment,
            "responded": responded,
        }
    ).sort_values("as_of_date").reset_index(drop=True)

    ground_truth = pd.DataFrame(
        {
            "entity_id": entity_id,
            "as_of_date": as_of_date,
            "true_probability": true_probability,
            "observed_label": responded,
        }
    )
    return observed, ground_truth


GENERATORS = {
    "lead_conversion_v1": lead_conversion_v1,
    "upsell_crosssell_v1": upsell_crosssell_v1,
    "campaign_response_v1": campaign_response_v1,
}
