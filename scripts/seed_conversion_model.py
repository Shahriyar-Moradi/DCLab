"""Seed the conversion serving artifact used by POST /app/decisions/generate.

CI boots a fresh checkout: `models/revenue_prediction/` is empty, and pytest
trains only into tmp dirs. Generate refuses to invent a score when the
artifact is missing (HTTP 503, ModelNotTrainedError), so the live client-surface
audit cannot create a decision or hit GET /app/decisions/{decision_id}.

This writes the same factory artifact `app.ml.train.train_and_save` already
produces in API tests: a small labeled CSV, expanded enough to split, fitted
with sklearn `random_state=42`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.config import get_settings
from app.ml.train import train_and_save

TRAINING_CSV = (
    "external_id,customer_id,amount,currency,stage,source,owner_id,created_at,"
    "close_date,last_contact_days_ago,engagement_score,sales_rep_available,"
    "industry,num_interactions,converted\n"
    "opp_1,cust_1,100000,AED,proposal,inbound,rep_1,2026-01-15,2026-09-01,5,0.88,true,telecom,14,1\n"
    "opp_2,cust_2,8000,AED,prospecting,outbound,rep_2,2026-03-01,2026-10-01,40,0.21,true,retail,2,0\n"
    "opp_3,cust_3,45000,AED,negotiation,referral,rep_1,2026-02-10,2026-08-01,4,0.91,true,saas,18,1\n"
)

_COPIES = 40


def expanded_training_csv(copies: int = _COPIES) -> str:
    header, *rows = TRAINING_CSV.strip().split("\n")
    lines = [header]
    for index in range(copies):
        for row in rows:
            parts = row.split(",")
            parts[0] = f"{parts[0]}_{index}"
            lines.append(",".join(parts))
    return "\n".join(lines) + "\n"


def conversion_artifact_ready(model_dir: Path) -> bool:
    return (model_dir / "model.joblib").is_file() and (model_dir / "metadata.json").is_file()


def seed_conversion_artifact(model_dir: Path | None = None) -> Path:
    destination = Path(model_dir or get_settings().model_dir)
    if conversion_artifact_ready(destination):
        return destination
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dclab-conversion-seed-") as tmp:
        csv_path = Path(tmp) / "train.csv"
        csv_path.write_text(expanded_training_csv(), encoding="utf-8")
        train_and_save(csv_path, destination)
    if not conversion_artifact_ready(destination):
        raise RuntimeError(f"Training did not write a conversion artifact at {destination}")
    return destination


def main() -> int:
    path = seed_conversion_artifact()
    print(f"conversion artifact ready at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
