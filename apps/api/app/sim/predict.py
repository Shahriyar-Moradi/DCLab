"""Score a simulation entity with a factory artifact.

Uses the same member/fusion recipe as the live conversion layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from app.ml.ensemble import blend_probabilities
from app.ml.features import feature_vector
from app.ml.predict import load_model
from app.sim.decide import member_agreement


def _row_values(entity: Any) -> dict[str, Any]:
    if isinstance(entity, dict):
        return entity
    return {column: getattr(entity, column) for column in dir(entity) if not column.startswith("_")}


def predict_entity(entity: Any, model_dir: Path) -> dict[str, Any]:
    bundle, metadata = load_model(model_dir)
    row = _row_values(entity)
    clean = {}
    for key, value in row.items():
        if value is None or (isinstance(value, float) and value != value):
            clean[key] = 0.0
        else:
            clean[key] = value
    version = str(metadata["model_version"])
    member_probs: dict[str, float] = {}
    groups_used: list[str] = []
    arrays: dict[str, np.ndarray] = {}
    for spec in bundle["member_meta"]:
        model = bundle["members"][spec["id"]]
        vector = [feature_vector(clean, spec["features"])]
        proba = float(np.clip(model.predict_proba(vector)[0, 1], 0.0, 1.0))
        member_probs[spec["id"]] = proba
        arrays[spec["id"]] = np.array([proba], dtype=float)
        groups_used.extend(spec.get("groups") or [])

    fusion = bundle["fusion"]
    weights = bundle["weights"]
    if str(fusion).startswith("single:"):
        winner = fusion.split(":", 1)[1]
        probability = member_probs[winner]
    else:
        probability = float(blend_probabilities(arrays, weights)[0])
    probability = min(1.0, max(0.0, probability))

    best_single_id = None
    if str(fusion).startswith("single:"):
        best_single_id = fusion.split(":", 1)[1]
    elif member_probs:
        best_single_id = max(member_probs, key=member_probs.get)
    best_single_probability = member_probs.get(best_single_id or "", probability)

    return {
        "probability": probability,
        "best_single_probability": best_single_probability,
        "best_single_id": best_single_id,
        "model_version": version,
        "agreement": member_agreement(member_probs),
        "evidence": {
            "models_used": len(member_probs),
            "feature_groups_used": sorted(set(groups_used)),
            "member_probabilities": member_probs,
            "fusion": fusion,
        },
    }
