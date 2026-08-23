"""Generate data/sample/opportunities.csv — run from repo root if you need to regenerate."""

from __future__ import annotations

import csv
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

from app.config import REPO_ROOT

OUT = REPO_ROOT / "data" / "sample" / "opportunities.csv"

STAGES = ["prospecting", "qualification", "proposal", "negotiation", "closed_won", "closed_lost"]
SOURCES = ["inbound", "outbound", "referral", "partner", "website"]
INDUSTRIES = ["telecom", "retail", "saas", "finance", "healthcare", "logistics"]
CURRENCIES = ["AED"]


def main() -> None:
    rng = random.Random(42)
    start = datetime(2025, 1, 1)
    rows: list[dict] = []

    for i in range(1, 521):
        stage = rng.choices(STAGES, weights=[22, 22, 20, 16, 12, 8], k=1)[0]
        source = rng.choice(SOURCES)
        engagement = round(min(1.0, max(0.0, rng.gauss(0.55, 0.22))), 2)
        last_contact = max(0, int(rng.gauss(12, 10)))
        interactions = max(0, int(rng.gauss(8, 5)))
        amount = round(max(1500, rng.lognormvariate(9.2, 0.7)), 2)
        age_days = rng.randint(5, 400)
        created = start + timedelta(days=age_days)
        close = (created + timedelta(days=rng.randint(14, 90))).date()
        stage_ord = STAGES.index(stage)
        logit = (
            -2.2
            + 2.8 * engagement
            + 0.35 * stage_ord
            - 0.035 * last_contact
            + 0.06 * interactions
            + (0.4 if source in {"inbound", "referral"} else 0.0)
            - 0.000002 * amount
        )
        prob = 1 / (1 + math.exp(-logit))
        converted = 1 if rng.random() < prob else 0
        if stage == "closed_won":
            converted = 1
        if stage == "closed_lost":
            converted = 0

        rows.append(
            {
                "external_id": f"opp_{i}",
                "customer_id": f"cust_{rng.randint(1, 180)}",
                "amount": amount,
                "currency": "AED",
                "stage": stage,
                "source": source,
                "owner_id": f"rep_{rng.randint(1, 12)}",
                "created_at": created.strftime("%Y-%m-%d"),
                "close_date": close.isoformat(),
                "last_contact_days_ago": last_contact,
                "engagement_score": engagement,
                "sales_rep_available": rng.random() > 0.18,
                "industry": rng.choice(INDUSTRIES),
                "num_interactions": interactions,
                "converted": converted,
            }
        )

    # Intentionally messy rows so ingestion must report specific errors.
    rows.append(
        {
            "external_id": "opp_bad_amount",
            "customer_id": "cust_bad",
            "amount": -500,
            "currency": "AED",
            "stage": "proposal",
            "source": "inbound",
            "owner_id": "rep_1",
            "created_at": "2026-01-15",
            "close_date": "2026-03-01",
            "last_contact_days_ago": 3,
            "engagement_score": 0.5,
            "sales_rep_available": True,
            "industry": "retail",
            "num_interactions": 4,
            "converted": 0,
        }
    )
    rows.append(
        {
            "external_id": "opp_bad_date",
            "customer_id": "cust_bad",
            "amount": 9000,
            "currency": "AED",
            "stage": "proposal",
            "source": "inbound",
            "owner_id": "rep_1",
            "created_at": "15/01/2026",
            "close_date": "2026-03-01",
            "last_contact_days_ago": 3,
            "engagement_score": 0.5,
            "sales_rep_available": True,
            "industry": "retail",
            "num_interactions": 4,
            "converted": 0,
        }
    )
    rows.append(
        {
            "external_id": "opp_missing_stage",
            "customer_id": "cust_bad",
            "amount": 9000,
            "currency": "AED",
            "stage": "",
            "source": "inbound",
            "owner_id": "rep_1",
            "created_at": "2026-01-15",
            "close_date": "2026-03-01",
            "last_contact_days_ago": 3,
            "engagement_score": 0.5,
            "sales_rep_available": True,
            "industry": "retail",
            "num_interactions": 4,
            "converted": 0,
        }
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
