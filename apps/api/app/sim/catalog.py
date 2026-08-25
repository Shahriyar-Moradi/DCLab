"""Registry of simulation use cases wired to the live factory + policy engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import REPO_ROOT
from app.sim import (
    HERO_CAMP_A,
    HERO_CAMP_B,
    HERO_CAMP_C,
    HERO_CHURN_ID,
    HERO_LEAD_A,
    HERO_PURCHASE_ID,
    HERO_SUPPORT_ID,
    HERO_TRAVEL_ID,
    HERO_UPSELL_ID,
    HERO_VALUE_ID,
    USE_CASES,
)

LAYERS = REPO_ROOT / "configs" / "layers" / "sim"
POLICIES = REPO_ROOT / "configs" / "policies" / "sim"
DATA = REPO_ROOT / "data" / "sim"
MODELS = REPO_ROOT / "models" / "sim"


@dataclass(frozen=True)
class UseCase:
    name: str
    company: str
    csv_path: Path
    layer_path: Path
    policy_path: Path
    model_dir: Path
    target: str
    value_column: str
    true_p0_column: str
    hero_ids: tuple[str, ...]
    question: str


def use_case(name: str) -> UseCase:
    specs = {item.name: item for item in all_use_cases()}
    if name not in specs:
        raise KeyError(f"Unknown use case {name!r}. Choose from {USE_CASES}")
    return specs[name]


def all_use_cases() -> list[UseCase]:
    return [
        UseCase(
            name="churn",
            company="Northstar SaaS",
            csv_path=DATA / "northstar" / "customers.csv",
            layer_path=LAYERS / "churn.yaml",
            policy_path=POLICIES / "churn.yaml",
            model_dir=MODELS / "churn",
            target="churned",
            value_column="remaining_arr",
            true_p0_column="true_p0_churn",
            hero_ids=(HERO_CHURN_ID,),
            question="Which customers are likely to churn, and what should we do?",
        ),
        UseCase(
            name="purchase",
            company="Shoppe",
            csv_path=DATA / "shoppe" / "shoppers.csv",
            layer_path=LAYERS / "purchase.yaml",
            policy_path=POLICIES / "purchase.yaml",
            model_dir=MODELS / "purchase",
            target="purchased_7d",
            value_column="expected_margin",
            true_p0_column="true_p0_purchase",
            hero_ids=(HERO_PURCHASE_ID,),
            question="Who will purchase in 7 days, and is intervening worth the margin?",
        ),
        UseCase(
            name="lead_conversion",
            company="Northstar SaaS",
            csv_path=DATA / "northstar" / "leads.csv",
            layer_path=LAYERS / "lead_conversion.yaml",
            policy_path=POLICIES / "lead_conversion.yaml",
            model_dir=MODELS / "lead_conversion",
            target="converted",
            value_column="deal_amount",
            true_p0_column="true_p0_converted",
            hero_ids=(HERO_LEAD_A,),
            question="Where should sales spend limited time?",
        ),
        UseCase(
            name="upsell",
            company="Northstar SaaS",
            csv_path=DATA / "northstar" / "customers.csv",
            layer_path=LAYERS / "upsell.yaml",
            policy_path=POLICIES / "upsell.yaml",
            model_dir=MODELS / "upsell",
            target="upgraded",
            value_column="upgrade_arpu",
            true_p0_column="true_p0_upsell",
            hero_ids=(HERO_UPSELL_ID,),
            question="Which customers will upgrade without inflating churn risk?",
        ),
        UseCase(
            name="cross_sell",
            company="Atlas Air",
            csv_path=DATA / "atlas" / "travelers.csv",
            layer_path=LAYERS / "cross_sell.yaml",
            policy_path=POLICIES / "cross_sell.yaml",
            model_dir=MODELS / "cross_sell",
            target="bought_ancillary",
            value_column="offer_value",
            true_p0_column="true_p0_ancillary",
            hero_ids=(HERO_TRAVEL_ID,),
            question="Which ancillary offer has the highest expected value?",
        ),
        UseCase(
            name="campaign_response",
            company="Northstar SaaS",
            csv_path=DATA / "northstar" / "customers.csv",
            layer_path=LAYERS / "campaign_response.yaml",
            policy_path=POLICIES / "campaign_response.yaml",
            model_dir=MODELS / "campaign_response",
            target="responded",
            value_column="incremental_margin",
            true_p0_column="true_p0_response",
            hero_ids=(HERO_CAMP_A, HERO_CAMP_B, HERO_CAMP_C),
            question="Who will respond, and when is do_nothing the right send?",
        ),
        UseCase(
            name="customer_value",
            company="Northstar SaaS",
            csv_path=DATA / "northstar" / "customers.csv",
            layer_path=LAYERS / "customer_value.yaml",
            policy_path=POLICIES / "customer_value.yaml",
            model_dir=MODELS / "customer_value",
            target="high_ltv",
            value_column="expected_contribution",
            true_p0_column="true_p0_high_ltv",
            hero_ids=(HERO_VALUE_ID,),
            question="How much should we spend to retain a high-value customer?",
        ),
        UseCase(
            name="custom_support",
            company="Northstar SaaS",
            csv_path=DATA / "northstar" / "customers.csv",
            layer_path=LAYERS / "custom_support.yaml",
            policy_path=POLICIES / "custom_support.yaml",
            model_dir=MODELS / "custom_support",
            target="support_30d",
            value_column="avoided_support_cost",
            true_p0_column="true_p0_support",
            hero_ids=(HERO_SUPPORT_ID,),
            question="Who will need support in 30 days, and is a proactive CSM worth it?",
        ),
    ]
