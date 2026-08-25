"""Load the trained conversion layer and score opportunities.

Supports the factory artifact (member models + fusion recipe) and still fails
loudly if nothing has been trained. Never silently returns 0.5.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app.config import get_settings
from app.ml.ensemble import blend_probabilities
from app.ml.features import build_features, feature_vector

_cache: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}


class ModelNotTrainedError(RuntimeError):
    """Raised when the conversion artifact is missing. Never silently default."""


def reset_model_cache() -> None:
    _cache.clear()


def _artifact_paths(model_dir: Path | None = None) -> tuple[Path, Path]:
    directory = Path(model_dir or get_settings().model_dir)
    return directory / "model.joblib", directory / "metadata.json"


def load_model(model_dir: Path | None = None):
    directory = Path(model_dir or get_settings().model_dir)
    key = str(directory.resolve())
    cached = _cache.get(key)
    if cached is not None:
        return cached

    artifact, meta_path = _artifact_paths(directory)
    if not artifact.exists() or not meta_path.exists():
        raise ModelNotTrainedError(
            f"No trained model found at {artifact}. Run `python -m app.ml.train` first."
        )
    recipe = joblib.load(artifact)
    metadata = json.loads(meta_path.read_text())
    members = {}
    member_meta = metadata.get("members") or recipe.get("members") or []
    if member_meta:
        for row in member_meta:
            members[row["id"]] = joblib.load(directory / row["artifact"])
        bundle = {
            "kind": "factory",
            "fusion": metadata.get("fusion") or recipe.get("fusion"),
            "weights": metadata.get("weights") or recipe.get("weights") or {},
            "members": members,
            "member_meta": member_meta,
        }
    else:
        bundle = {"kind": "sklearn", "model": recipe, "members": {}, "member_meta": []}
    _cache[key] = (bundle, metadata)
    return bundle, metadata


def _member_probability(model, opportunity: Any, features: list[str]) -> float:
    feats = build_features(opportunity)
    vector = [feature_vector(feats, features)]
    return float(np.clip(model.predict_proba(vector)[0, 1], 0.0, 1.0))


def predict_with_evidence(opportunity: Any, model_dir: Path | None = None) -> dict[str, Any]:
    bundle, metadata = load_model(model_dir)
    version = str(metadata["model_version"])
    if bundle["kind"] != "factory":
        feats = build_features(opportunity)
        from app.ml.features import FEATURE_NAMES

        probability = float(np.clip(bundle["model"].predict_proba([feature_vector(feats, FEATURE_NAMES)])[0, 1], 0, 1))
        return {
            "probability": probability,
            "model_version": version,
            "evidence": {
                "models_used": 1,
                "feature_groups_used": [],
                "member_probabilities": {},
                "fusion": "sklearn_single",
            },
        }

    member_probs: dict[str, float] = {}
    groups_used: list[str] = []
    arrays: dict[str, np.ndarray] = {}
    for row in bundle["member_meta"]:
        model = bundle["members"][row["id"]]
        proba = _member_probability(model, opportunity, row["features"])
        member_probs[row["id"]] = proba
        arrays[row["id"]] = np.array([proba], dtype=float)
        groups_used.extend(row.get("groups") or [])

    fusion = bundle["fusion"]
    weights = bundle["weights"]
    if str(fusion).startswith("single:"):
        winner = fusion.split(":", 1)[1]
        probability = member_probs[winner]
    else:
        probability = float(blend_probabilities(arrays, weights)[0])

    probability = min(1.0, max(0.0, probability))
    return {
        "probability": probability,
        "model_version": version,
        "evidence": {
            "models_used": len(member_probs),
            "feature_groups_used": sorted(set(groups_used)),
            "member_probabilities": member_probs,
            "fusion": fusion,
        },
    }


def predict_conversion(opportunity: Any, model_dir: Path | None = None) -> tuple[float, str]:
    """Return (probability in [0, 1], exact model_version string)."""
    result = predict_with_evidence(opportunity, model_dir)
    return result["probability"], result["model_version"]
