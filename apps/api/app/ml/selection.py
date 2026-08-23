"""Diversity-aware model selection.

Accuracy alone is not enough: two 0.81-AUC models that make the same predictions
add almost no new evidence. Keep strong models whose held-out predictions are
not highly correlated.
"""

from __future__ import annotations

import pandas as pd


def greedy_diverse_selection(
    prediction_matrix: pd.DataFrame,
    scores: dict[str, float],
    *,
    retain_max: int = 7,
    retain_min: int = 3,
    max_abs_correlation: float = 0.95,
) -> list[str]:
    """Return model ids ordered by score, skipping near-duplicates.

    prediction_matrix: columns are model ids, rows are held-out probabilities.
    """
    ordered = sorted(scores, key=scores.get, reverse=True)
    selected: list[str] = []
    for model_id in ordered:
        if model_id not in prediction_matrix.columns:
            continue
        if len(selected) >= retain_max:
            break
        if not selected:
            selected.append(model_id)
            continue
        corr = prediction_matrix[selected].corrwith(prediction_matrix[model_id]).abs()
        peak = float(corr.max())
        if pd.isna(peak) or peak <= max_abs_correlation:
            selected.append(model_id)

    if len(selected) < retain_min:
        for model_id in ordered:
            if model_id not in selected and model_id in prediction_matrix.columns:
                selected.append(model_id)
            if len(selected) >= retain_min:
                break
    return selected
