"""Run a simulation use case through the live factory and policy engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from app.ml.feature_groups import load_layer_config
from app.ml.predict import reset_model_cache
from app.ml.train import _load_labeled_frame, _split, train_and_save
from app.services.decision_service import load_policy
from app.sim.catalog import all_use_cases, use_case
from app.sim.compare import compare_holdout
from app.sim.decide import decide_simulated
from app.sim.generate import write_all
from app.sim.predict import predict_entity


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if hasattr(value, "item") and not isinstance(value, (bytes, str)):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def ensure_data(root: Path | None = None) -> None:
    from app.sim.catalog import DATA

    base = root or DATA
    needed = [
        base / "northstar" / "customers.csv",
        base / "northstar" / "leads.csv",
        base / "shoppe" / "shoppers.csv",
        base / "atlas" / "travelers.csv",
    ]
    if all(path.exists() for path in needed):
        return
    write_all(base)


def _entity_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return frame.where(pd.notnull(frame), None).to_dict(orient="records")


def _walkthrough(entity: dict[str, Any], scored: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    decision = decide_simulated(entity, scored["probability"], policy)
    return {
        "external_id": entity.get("external_id"),
        "probability": round(float(scored["probability"]), 4),
        "best_single_probability": round(float(scored["best_single_probability"]), 4),
        "agreement": scored["agreement"],
        "evidence": scored["evidence"],
        "recommended_action": decision["recommended_action"],
        "action_key": decision["action_key"],
        "expected_value": decision["expected_value"],
        "incremental_value": decision["incremental_value"],
        "action_table": decision["action_table"],
        "uplift_is_simulated": True,
        "policy_version": decision["policy_version"],
        "model_version": scored["model_version"],
        "features": {
            key: entity.get(key)
            for key in entity
            if key not in {"created_at"} and not str(key).startswith("true_")
        },
    }


def run_use_case(
    name: str,
    *,
    csv_path: Path | None = None,
    model_dir: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    spec = use_case(name)
    if data_root is not None:
        ensure_data(data_root)
    else:
        ensure_data()
    csv = Path(csv_path or spec.csv_path)
    destination = Path(model_dir or spec.model_dir)
    if not csv.exists():
        raise FileNotFoundError(csv)

    reset_model_cache()
    metadata = train_and_save(
        csv_path=csv,
        model_dir=destination,
        layer_path=spec.layer_path,
        target_col=spec.target,
    )
    policy = load_policy(spec.policy_path)
    layer = load_layer_config(spec.layer_path)
    frame = _load_labeled_frame(csv, spec.target)
    _train_df, test_df, _split_kind = _split(frame, spec.target)

    holdout_ids = set(metadata.get("test_external_ids") or [])
    if holdout_ids:
        test_df = frame[frame["external_id"].astype(str).isin(holdout_ids)].copy()

    scored_holdout: list[dict[str, Any]] = []
    for entity in _entity_rows(test_df):
        scored = predict_entity(entity, destination)
        scored_holdout.append(
            {
                "entity": entity,
                "probability": scored["probability"],
                "best_single_probability": scored["best_single_probability"],
            }
        )
    comparison = compare_holdout(test_df, scored_holdout, policy)

    heroes = []
    for hero_id in spec.hero_ids:
        match = frame[frame["external_id"].astype(str) == hero_id]
        if match.empty:
            continue
        entity = _entity_rows(match)[0]
        scored = predict_entity(entity, destination)
        heroes.append(_walkthrough(entity, scored, policy))

    sample = []
    for row in scored_holdout[:8]:
        scored = predict_entity(row["entity"], destination)
        sample.append(_walkthrough(row["entity"], scored, policy))

    return json_ready(
        {
            "use_case": spec.name,
            "company": spec.company,
            "question": spec.question,
            "model_version": metadata["model_version"],
            "policy_version": policy["version"],
            "fusion": metadata["fusion"],
            "n_candidates_evaluated": metadata["n_candidates_evaluated"],
            "members": metadata["members"],
            "metrics": metadata["metrics"],
            "all_metrics": metadata["all_metrics"],
            "feature_groups_used": metadata.get("feature_groups_used"),
            "comparison": comparison,
            "heroes": heroes,
            "sample_decisions": sample,
            "uplift_is_simulated": True,
            "layer": layer.get("layer"),
            "target": spec.target,
            "n_train": metadata["n_train"],
            "n_test": metadata["n_test"],
        }
    )


def run_all() -> list[dict[str, Any]]:
    ensure_data()
    return [run_use_case(spec.name) for spec in all_use_cases()]
