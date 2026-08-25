"""Generate synthetic CRM + product + marketing tables with planted outcomes.

Labels come from a known outcome function plus historical confounding.
Action deltas are planted so the oracle comparison is well-defined.
They are not causal estimates from real logs.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from app.config import REPO_ROOT
from app.sim import (
    HERO_CAMP_A,
    HERO_CAMP_B,
    HERO_CAMP_C,
    HERO_CHURN_ID,
    HERO_LEAD_A,
    HERO_LEAD_B,
    HERO_LEAD_C,
    HERO_LEAD_D,
    HERO_PURCHASE_ID,
    HERO_SUPPORT_ID,
    HERO_TRAVEL_ID,
    HERO_UPSELL_ID,
    HERO_VALUE_ID,
)

SIM_DATA_DIR = REPO_ROOT / "data" / "sim"
SEED = 42


def _sigmoid(x: np.ndarray | float) -> np.ndarray | float:
    x = np.clip(x, -20, 20)
    return 1.0 / (1.0 + np.exp(-x))


def _dates(n: int, rng: np.random.Generator, start: str = "2024-01-01", end: str = "2026-06-01") -> np.ndarray:
    start_ts = np.datetime64(start)
    end_ts = np.datetime64(end)
    span = int((end_ts - start_ts) / np.timedelta64(1, "D"))
    offsets = rng.integers(0, max(span, 1), size=n)
    return (start_ts + offsets.astype("timedelta64[D]")).astype(str)


def generate_northstar_customers(n: int = 1600, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = max(n, 20)
    account_age = rng.integers(2, 60, size=n).astype(float)
    monthly_revenue = rng.choice([49, 99, 149, 249, 399, 799], size=n).astype(float)
    logins_prev = rng.integers(1, 28, size=n).astype(float)
    logins_30d = np.clip(logins_prev + rng.normal(0, 6, size=n), 0, 40).round()
    features_prev = rng.integers(1, 12, size=n).astype(float)
    features_used = np.clip(features_prev + rng.normal(-0.5, 2.5, size=n), 0, 14).round()
    support_tickets = rng.poisson(1.2, size=n).astype(float)
    negative_support = np.clip(rng.binomial(support_tickets.astype(int), 0.35), 0, None).astype(float)
    emails_opened = rng.integers(0, 12, size=n).astype(float)
    emails_clicked = np.clip(emails_opened - rng.integers(0, 4, size=n), 0, None).astype(float)
    last_login_days = rng.integers(0, 45, size=n).astype(float)
    payment_failures = rng.binomial(2, 0.12, size=n).astype(float)
    days_until_renewal = rng.integers(5, 180, size=n).astype(float)
    number_of_users = rng.integers(1, 40, size=n).astype(float)
    user_activity_variance = np.clip(rng.normal(0.4, 0.2, size=n), 0.05, 1.2)
    support_ticket_growth = np.clip(rng.normal(0.0, 0.8, size=n), -1.5, 3.0)

    login_frequency_change = (logins_30d - logins_prev) / np.clip(logins_prev, 1, None)
    feature_usage_change = (features_used - features_prev) / np.clip(features_prev, 1, None)
    email_engagement = np.clip(emails_opened / 12.0 * 0.6 + emails_clicked / 8.0 * 0.4, 0, 1)
    remaining_arr = monthly_revenue * 12.0

    usage_drop = np.clip(-login_frequency_change, 0, 3)
    logit_churn = (
        -1.8
        + 1.8 * usage_drop
        + 0.045 * last_login_days
        + 0.35 * negative_support
        + 0.55 * (days_until_renewal < 30).astype(float)
        + 0.45 * payment_failures
        - 0.9 * email_engagement
        - 0.015 * account_age
    )
    true_p0_churn = _sigmoid(logit_churn)
    treated = (rng.random(n) < _sigmoid(1.4 * (true_p0_churn - 0.4))).astype(float)
    historical_discount = treated
    true_p_obs_churn = np.clip(true_p0_churn - 0.14 * treated, 0.02, 0.98)
    churned = rng.binomial(1, true_p_obs_churn)

    logit_upsell = (
        -1.2
        + 0.08 * features_used
        + 0.03 * number_of_users
        + 0.4 * (monthly_revenue >= 249).astype(float)
        - 0.5 * true_p0_churn
        + 0.02 * account_age
    )
    true_p0_upsell = _sigmoid(logit_upsell)
    upgraded = rng.binomial(1, np.clip(true_p0_upsell + 0.04 * treated, 0.02, 0.98))
    upgrade_arpu = np.where(monthly_revenue < 249, 120.0, 360.0)

    logit_resp = -0.6 + 1.6 * email_engagement - 0.02 * last_login_days + 0.3 * historical_discount
    true_p0_response = _sigmoid(logit_resp)
    responded = rng.binomial(1, true_p0_response)
    incremental_margin = np.clip(monthly_revenue * 0.15, 8, 80)

    expected_contribution = remaining_arr * (1.0 - true_p0_churn) * 0.55
    true_p0_high_ltv = _sigmoid((expected_contribution - 1800) / 700)
    high_ltv = rng.binomial(1, true_p0_high_ltv)

    logit_support = (
        -1.4
        + 0.4 * support_tickets
        + 0.5 * negative_support
        + 0.03 * last_login_days
        - 0.05 * features_used
    )
    true_p0_support = _sigmoid(logit_support)
    support_30d = rng.binomial(1, true_p0_support)
    avoided_support_cost = 40.0 + 25.0 * support_tickets

    frame = pd.DataFrame(
        {
            "external_id": [f"C-{10000 + i}" for i in range(n)],
            "created_at": _dates(n, rng),
            "account_age_months": account_age,
            "monthly_revenue": monthly_revenue,
            "remaining_arr": remaining_arr,
            "logins_30d": logins_30d,
            "logins_prev_30d": logins_prev,
            "login_frequency_change": login_frequency_change.round(4),
            "features_used": features_used,
            "previous_features_used": features_prev,
            "feature_usage_change": feature_usage_change.round(4),
            "support_tickets": support_tickets,
            "negative_support": negative_support,
            "support_ticket_growth": support_ticket_growth.round(4),
            "emails_opened": emails_opened,
            "emails_clicked": emails_clicked,
            "email_engagement": email_engagement.round(4),
            "last_login_days": last_login_days,
            "payment_failures": payment_failures,
            "days_until_renewal": days_until_renewal,
            "number_of_users": number_of_users,
            "user_activity_variance": user_activity_variance.round(4),
            "historical_discount": historical_discount,
            "churned": churned,
            "upgraded": upgraded,
            "responded": responded,
            "high_ltv": high_ltv,
            "support_30d": support_30d,
            "upgrade_arpu": upgrade_arpu,
            "expected_contribution": expected_contribution.round(2),
            "avoided_support_cost": avoided_support_cost.round(2),
            "incremental_margin": incremental_margin.round(2),
            "true_p0_churn": true_p0_churn.round(4),
            "true_p0_upsell": true_p0_upsell.round(4),
            "true_p0_response": true_p0_response.round(4),
            "true_p0_high_ltv": true_p0_high_ltv.round(4),
            "true_p0_support": true_p0_support.round(4),
        }
    )
    heroes = [
        _hero_churn(),
        _hero_upsell(),
        _hero_campaign_a(),
        _hero_campaign_b(),
        _hero_campaign_c(),
        _hero_value(),
        _hero_support(),
    ]
    frame = pd.concat([frame, pd.DataFrame(heroes)], ignore_index=True)
    return frame


def _hero_churn() -> dict:
    logins_30d, logins_prev = 3.0, 11.0
    features_used, features_prev = 2.0, 7.0
    login_frequency_change = (logins_30d - logins_prev) / logins_prev
    feature_usage_change = (features_used - features_prev) / features_prev
    email_engagement = 1 / 12 * 0.6 + 0.0
    remaining_arr = 249.0 * 12.0
    true_p0 = 0.77
    return {
        "external_id": HERO_CHURN_ID,
        "created_at": "2026-05-20",
        "account_age_months": 14.0,
        "monthly_revenue": 249.0,
        "remaining_arr": remaining_arr,
        "logins_30d": logins_30d,
        "logins_prev_30d": logins_prev,
        "login_frequency_change": round(login_frequency_change, 4),
        "features_used": features_used,
        "previous_features_used": features_prev,
        "feature_usage_change": round(feature_usage_change, 4),
        "support_tickets": 4.0,
        "negative_support": 2.0,
        "support_ticket_growth": 1.4,
        "emails_opened": 1.0,
        "emails_clicked": 0.0,
        "email_engagement": round(email_engagement, 4),
        "last_login_days": 18.0,
        "payment_failures": 1.0,
        "days_until_renewal": 23.0,
        "number_of_users": 6.0,
        "user_activity_variance": 0.85,
        "historical_discount": 0.0,
        "churned": 1,
        "upgraded": 0,
        "responded": 0,
        "high_ltv": 0,
        "support_30d": 1,
        "upgrade_arpu": 360.0,
        "expected_contribution": round(remaining_arr * 0.23 * 0.55, 2),
        "avoided_support_cost": 140.0,
        "incremental_margin": 37.35,
        "true_p0_churn": true_p0,
        "true_p0_upsell": 0.22,
        "true_p0_response": 0.18,
        "true_p0_high_ltv": 0.28,
        "true_p0_support": 0.64,
    }


def _hero_upsell() -> dict:
    row = _hero_churn()
    row.update(
        {
            "external_id": HERO_UPSELL_ID,
            "created_at": "2026-05-18",
            "logins_30d": 18.0,
            "logins_prev_30d": 16.0,
            "login_frequency_change": 0.125,
            "features_used": 9.0,
            "previous_features_used": 7.0,
            "feature_usage_change": 0.2857,
            "support_tickets": 0.0,
            "negative_support": 0.0,
            "last_login_days": 2.0,
            "days_until_renewal": 80.0,
            "payment_failures": 0.0,
            "churned": 0,
            "upgraded": 1,
            "true_p0_churn": 0.11,
            "true_p0_upsell": 0.71,
            "remaining_arr": 2988.0,
            "upgrade_arpu": 1200.0,
            "monthly_revenue": 249.0,
        }
    )
    return row


def _hero_campaign_a() -> dict:
    row = _hero_churn()
    row.update(
        {
            "external_id": HERO_CAMP_A,
            "created_at": "2026-05-19",
            "email_engagement": 0.82,
            "emails_opened": 9.0,
            "emails_clicked": 5.0,
            "last_login_days": 4.0,
            "responded": 1,
            "true_p0_response": 0.41,
            "incremental_margin": 22.0,
            "churned": 0,
        }
    )
    return row


def _hero_campaign_b() -> dict:
    row = _hero_churn()
    row.update(
        {
            "external_id": HERO_CAMP_B,
            "created_at": "2026-05-19",
            "email_engagement": 0.12,
            "emails_opened": 1.0,
            "emails_clicked": 0.0,
            "last_login_days": 20.0,
            "number_of_users": 2.0,
            "responded": 1,
            "true_p0_response": 0.29,
            "incremental_margin": 18.0,
            "churned": 0,
        }
    )
    return row


def _hero_campaign_c() -> dict:
    row = _hero_churn()
    row.update(
        {
            "external_id": HERO_CAMP_C,
            "created_at": "2026-05-19",
            "email_engagement": 0.71,
            "emails_opened": 8.0,
            "emails_clicked": 3.0,
            "last_login_days": 3.0,
            "responded": 1,
            "true_p0_response": 0.60,
            "incremental_margin": 8.0,
            "churned": 0,
        }
    )
    return row


def _hero_value() -> dict:
    row = _hero_churn()
    row.update(
        {
            "external_id": HERO_VALUE_ID,
            "created_at": "2026-05-17",
            "monthly_revenue": 399.0,
            "remaining_arr": 4788.0,
            "expected_contribution": 1743.0,
            "true_p0_high_ltv": 0.84,
            "true_p0_churn": 0.17,
            "high_ltv": 1,
            "churned": 0,
            "logins_30d": 20.0,
            "features_used": 10.0,
        }
    )
    return row


def _hero_support() -> dict:
    row = _hero_churn()
    row.update(
        {
            "external_id": HERO_SUPPORT_ID,
            "created_at": "2026-05-16",
            "support_tickets": 5.0,
            "negative_support": 3.0,
            "true_p0_support": 0.72,
            "support_30d": 1,
            "avoided_support_cost": 165.0,
        }
    )
    return row


def generate_northstar_leads(n: int = 1000, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 1)
    n = max(n, 16)
    company_size = rng.choice([8, 25, 80, 200, 600, 2000], size=n).astype(float)
    industry = rng.integers(0, 6, size=n).astype(float)
    country = rng.integers(0, 8, size=n).astype(float)
    job_title = rng.integers(0, 5, size=n).astype(float)
    lead_source = rng.integers(0, 5, size=n).astype(float)
    website_visits = rng.integers(0, 30, size=n).astype(float)
    pricing_page_visits = rng.integers(0, 8, size=n).astype(float)
    demo_request = rng.binomial(1, 0.18, size=n).astype(float)
    email_opens = rng.integers(0, 14, size=n).astype(float)
    email_clicks = np.clip(email_opens - rng.integers(0, 5, size=n), 0, None).astype(float)
    sales_calls = rng.integers(0, 6, size=n).astype(float)
    days_since_contact = rng.integers(0, 40, size=n).astype(float)
    previous_interactions = rng.integers(0, 12, size=n).astype(float)
    campaign_engagement = np.clip(rng.beta(2, 3, size=n), 0, 1)
    deal_amount = rng.choice([4000, 8000, 15000, 28000, 60000], size=n).astype(float)

    logit = (
        -2.2
        + 0.004 * np.log1p(company_size) * 8
        + 0.08 * website_visits
        + 0.35 * pricing_page_visits
        + 1.4 * demo_request
        + 0.25 * sales_calls
        + 0.9 * campaign_engagement
        - 0.03 * days_since_contact
        + 0.12 * email_clicks
    )
    true_p0 = _sigmoid(logit)
    treated_call = (rng.random(n) < true_p0).astype(float)
    converted = rng.binomial(1, np.clip(true_p0 + 0.03 * treated_call, 0.02, 0.98))

    frame = pd.DataFrame(
        {
            "external_id": [f"L-{20000 + i}" for i in range(n)],
            "created_at": _dates(n, rng, start="2024-06-01"),
            "company_size": company_size,
            "industry": industry,
            "country": country,
            "job_title": job_title,
            "lead_source": lead_source,
            "website_visits": website_visits,
            "pricing_page_visits": pricing_page_visits,
            "demo_request": demo_request,
            "email_opens": email_opens,
            "email_clicks": email_clicks,
            "sales_calls": sales_calls,
            "days_since_contact": days_since_contact,
            "number_of_employees": company_size,
            "previous_interactions": previous_interactions,
            "campaign_engagement": campaign_engagement.round(4),
            "deal_amount": deal_amount,
            "historical_call": treated_call,
            "converted": converted,
            "true_p0_converted": true_p0.round(4),
        }
    )
    heroes = [
        {
            "external_id": HERO_LEAD_A,
            "created_at": "2026-05-21",
            "company_size": 600.0,
            "industry": 1.0,
            "country": 0.0,
            "job_title": 4.0,
            "lead_source": 0.0,
            "website_visits": 18.0,
            "pricing_page_visits": 5.0,
            "demo_request": 1.0,
            "email_opens": 10.0,
            "email_clicks": 6.0,
            "sales_calls": 3.0,
            "days_since_contact": 2.0,
            "number_of_employees": 600.0,
            "previous_interactions": 9.0,
            "campaign_engagement": 0.88,
            "deal_amount": 15000.0,
            "historical_call": 1.0,
            "converted": 1,
            "true_p0_converted": 0.91,
        },
        {
            "external_id": HERO_LEAD_B,
            "created_at": "2026-05-21",
            "company_size": 80.0,
            "industry": 2.0,
            "country": 1.0,
            "job_title": 2.0,
            "lead_source": 1.0,
            "website_visits": 9.0,
            "pricing_page_visits": 2.0,
            "demo_request": 0.0,
            "email_opens": 6.0,
            "email_clicks": 3.0,
            "sales_calls": 1.0,
            "days_since_contact": 6.0,
            "number_of_employees": 80.0,
            "previous_interactions": 4.0,
            "campaign_engagement": 0.55,
            "deal_amount": 8000.0,
            "historical_call": 0.0,
            "converted": 1,
            "true_p0_converted": 0.73,
        },
        {
            "external_id": HERO_LEAD_C,
            "created_at": "2026-05-21",
            "company_size": 8.0,
            "industry": 4.0,
            "country": 5.0,
            "job_title": 0.0,
            "lead_source": 3.0,
            "website_visits": 1.0,
            "pricing_page_visits": 0.0,
            "demo_request": 0.0,
            "email_opens": 1.0,
            "email_clicks": 0.0,
            "sales_calls": 0.0,
            "days_since_contact": 28.0,
            "number_of_employees": 8.0,
            "previous_interactions": 1.0,
            "campaign_engagement": 0.12,
            "deal_amount": 4000.0,
            "historical_call": 0.0,
            "converted": 0,
            "true_p0_converted": 0.22,
        },
        {
            "external_id": HERO_LEAD_D,
            "created_at": "2026-05-21",
            "company_size": 200.0,
            "industry": 1.0,
            "country": 0.0,
            "job_title": 3.0,
            "lead_source": 2.0,
            "website_visits": 7.0,
            "pricing_page_visits": 2.0,
            "demo_request": 1.0,
            "email_opens": 4.0,
            "email_clicks": 2.0,
            "sales_calls": 1.0,
            "days_since_contact": 9.0,
            "number_of_employees": 200.0,
            "previous_interactions": 5.0,
            "campaign_engagement": 0.48,
            "deal_amount": 15000.0,
            "historical_call": 0.0,
            "converted": 1,
            "true_p0_converted": 0.67,
        },
    ]
    return pd.concat([frame, pd.DataFrame(heroes)], ignore_index=True)


def generate_shoppe(n: int = 1200, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 2)
    n = max(n, 16)
    orders_30d = rng.integers(0, 6, size=n).astype(float)
    orders_90d = orders_30d + rng.integers(0, 8, size=n).astype(float)
    aov = rng.uniform(18, 140, size=n)
    days_since_last = rng.integers(0, 60, size=n).astype(float)
    product_views_7d = rng.integers(0, 40, size=n).astype(float)
    cart_additions = rng.integers(0, 8, size=n).astype(float)
    cart_abandonments = rng.integers(0, 5, size=n).astype(float)
    email_clicks = rng.integers(0, 10, size=n).astype(float)
    discount_usage = rng.integers(0, 4, size=n).astype(float)
    website_sessions = rng.integers(1, 20, size=n).astype(float)
    mobile_sessions = rng.integers(0, 15, size=n).astype(float)
    category_preferences = rng.integers(0, 6, size=n).astype(float)
    price_sensitivity = np.clip(rng.beta(2, 2, size=n), 0, 1)
    campaign_exposure = rng.integers(0, 8, size=n).astype(float)
    expected_margin = np.clip(aov * 0.42, 8, 70)

    logit = (
        -1.4
        + 0.35 * orders_30d
        + 0.04 * product_views_7d
        + 0.28 * cart_additions
        - 0.22 * cart_abandonments
        + 0.12 * email_clicks
        - 0.025 * days_since_last
        + 0.06 * website_sessions
    )
    true_p0 = _sigmoid(logit)
    historically_discounted = (rng.random(n) < _sigmoid(1.2 * (true_p0 - 0.3))).astype(float)
    purchased_7d = rng.binomial(1, np.clip(true_p0 + 0.08 * historically_discounted, 0.02, 0.98))

    frame = pd.DataFrame(
        {
            "external_id": [f"S-{30000 + i}" for i in range(n)],
            "created_at": _dates(n, rng, start="2025-01-01"),
            "orders_30d": orders_30d,
            "orders_90d": orders_90d,
            "average_order_value": aov.round(2),
            "days_since_last_purchase": days_since_last,
            "product_views_7d": product_views_7d,
            "cart_additions": cart_additions,
            "cart_abandonments": cart_abandonments,
            "email_clicks": email_clicks,
            "discount_usage": discount_usage,
            "website_sessions": website_sessions,
            "mobile_sessions": mobile_sessions,
            "category_preferences": category_preferences,
            "price_sensitivity": price_sensitivity.round(4),
            "campaign_exposure": campaign_exposure,
            "historical_discount": historically_discounted,
            "expected_margin": expected_margin.round(2),
            "purchased_7d": purchased_7d,
            "true_p0_purchase": true_p0.round(4),
        }
    )
    hero = {
        "external_id": HERO_PURCHASE_ID,
        "created_at": "2026-05-22",
        "orders_30d": 2.0,
        "orders_90d": 5.0,
        "average_order_value": 48.0,
        "days_since_last_purchase": 9.0,
        "product_views_7d": 22.0,
        "cart_additions": 3.0,
        "cart_abandonments": 1.0,
        "email_clicks": 4.0,
        "discount_usage": 1.0,
        "website_sessions": 8.0,
        "mobile_sessions": 6.0,
        "category_preferences": 2.0,
        "price_sensitivity": 0.35,
        "campaign_exposure": 3.0,
        "historical_discount": 0.0,
        "expected_margin": 40.0,
        "purchased_7d": 1,
        "true_p0_purchase": 0.82,
    }
    return pd.concat([frame, pd.DataFrame([hero])], ignore_index=True)


def generate_atlas(n: int = 900, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 3)
    n = max(n, 16)
    trip_length = rng.integers(2, 14, size=n).astype(float)
    party_size = rng.integers(1, 5, size=n).astype(float)
    booking_lead_days = rng.integers(1, 90, size=n).astype(float)
    past_hotels = rng.integers(0, 8, size=n).astype(float)
    past_cars = rng.integers(0, 5, size=n).astype(float)
    past_activities = rng.integers(0, 6, size=n).astype(float)
    search_hotel = rng.integers(0, 10, size=n).astype(float)
    search_activity = rng.integers(0, 8, size=n).astype(float)
    loyalty_tier = rng.integers(0, 4, size=n).astype(float)
    fare_class = rng.integers(0, 3, size=n).astype(float)
    email_clicks = rng.integers(0, 6, size=n).astype(float)

    p_hotel = _sigmoid(-0.8 + 0.35 * past_hotels + 0.18 * search_hotel + 0.08 * trip_length)
    p_activity = _sigmoid(-1.1 + 0.4 * past_activities + 0.2 * search_activity)
    p_car = _sigmoid(-1.6 + 0.5 * past_cars + 0.15 * party_size)
    p_ins = _sigmoid(-2.0 + 0.2 * booking_lead_days / 30 + 0.2 * fare_class)
    p_bundle = np.clip(0.55 * p_hotel + 0.45 * p_activity, 0.05, 0.9)
    p_any = np.clip(1 - (1 - p_hotel) * (1 - p_activity) * (1 - p_car), 0.05, 0.95)
    bought = rng.binomial(1, p_any)

    frame = pd.DataFrame(
        {
            "external_id": [f"T-{40000 + i}" for i in range(n)],
            "created_at": _dates(n, rng, start="2025-03-01"),
            "trip_length": trip_length,
            "party_size": party_size,
            "booking_lead_days": booking_lead_days,
            "past_hotels": past_hotels,
            "past_cars": past_cars,
            "past_activities": past_activities,
            "search_hotel": search_hotel,
            "search_activity": search_activity,
            "loyalty_tier": loyalty_tier,
            "fare_class": fare_class,
            "email_clicks": email_clicks,
            "bought_ancillary": bought,
            "true_p0_ancillary": p_any.round(4),
            "true_p_hotel": p_hotel.round(4),
            "true_p_activity": p_activity.round(4),
            "true_p_car": p_car.round(4),
            "true_p_insurance": p_ins.round(4),
            "true_p_bundle": p_bundle.round(4),
            "offer_value": 120.0,
        }
    )
    hero = {
        "external_id": HERO_TRAVEL_ID,
        "created_at": "2026-05-22",
        "trip_length": 6.0,
        "party_size": 2.0,
        "booking_lead_days": 21.0,
        "past_hotels": 3.0,
        "past_cars": 1.0,
        "past_activities": 2.0,
        "search_hotel": 5.0,
        "search_activity": 4.0,
        "loyalty_tier": 2.0,
        "fare_class": 1.0,
        "email_clicks": 2.0,
        "bought_ancillary": 1,
        "true_p0_ancillary": 0.62,
        "true_p_hotel": 0.62,
        "true_p_activity": 0.54,
        "true_p_car": 0.31,
        "true_p_insurance": 0.18,
        "true_p_bundle": 0.48,
        "offer_value": 120.0,
    }
    return pd.concat([frame, pd.DataFrame([hero])], ignore_index=True)


def write_all(root: Path | None = None, **sizes) -> dict[str, Path]:
    root = Path(root or SIM_DATA_DIR)
    northstar = root / "northstar"
    shoppe = root / "shoppe"
    atlas = root / "atlas"
    northstar.mkdir(parents=True, exist_ok=True)
    shoppe.mkdir(parents=True, exist_ok=True)
    atlas.mkdir(parents=True, exist_ok=True)

    customers = generate_northstar_customers(n=int(sizes.get("n_customers", 1600)))
    leads = generate_northstar_leads(n=int(sizes.get("n_leads", 1000)))
    shoppers = generate_shoppe(n=int(sizes.get("n_shoppers", 1200)))
    travelers = generate_atlas(n=int(sizes.get("n_travelers", 900)))

    paths = {
        "customers": northstar / "customers.csv",
        "leads": northstar / "leads.csv",
        "shoppers": shoppe / "shoppers.csv",
        "travelers": atlas / "travelers.csv",
    }
    customers.to_csv(paths["customers"], index=False)
    leads.to_csv(paths["leads"], index=False)
    shoppers.to_csv(paths["shoppers"], index=False)
    travelers.to_csv(paths["travelers"], index=False)
    return paths


def main() -> None:
    paths = write_all()
    for name, path in paths.items():
        print(f"wrote {name}: {path}")


if __name__ == "__main__":
    main()
