"""Fuse retained member probabilities into one conversion score.

Weighted blend is compared to the best single member. The blend is kept only
if it improves the primary metric; otherwise the best single model wins.
This is not a causal / uplift estimator.
"""

from __future__ import annotations

import numpy as np


def blend_weights(scores: dict[str, float], member_ids: list[str]) -> dict[str, float]:
    raw = np.array([max(float(scores[mid]), 1e-6) for mid in member_ids], dtype=float)
    raw = raw / raw.sum()
    return {mid: float(weight) for mid, weight in zip(member_ids, raw)}


def blend_probabilities(
    member_probas: dict[str, np.ndarray],
    weights: dict[str, float],
) -> np.ndarray:
    stacked = None
    for model_id, weight in weights.items():
        vector = np.asarray(member_probas[model_id], dtype=float)
        contrib = vector * weight
        stacked = contrib if stacked is None else stacked + contrib
    if stacked is None:
        raise ValueError("No member probabilities to blend")
    return np.clip(stacked, 0.0, 1.0)


def choose_fusion(
    *,
    blend_metric: float,
    best_single_metric: float,
    best_single_id: str,
) -> str:
    if blend_metric > best_single_metric:
        return "weighted_blend"
    return f"single:{best_single_id}"
