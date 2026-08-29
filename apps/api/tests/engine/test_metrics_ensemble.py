import numpy as np
import pandas as pd
import pytest

from app.engine.ensemble import blend_probabilities, blend_weights, choose_fusion
from app.engine.evaluation.metrics import classification_metrics, primary_score, regression_metrics
from app.engine.selection import greedy_diverse_selection


def test_classification_metrics_and_primary_score():
    y = np.array([0, 0, 1, 1, 1, 0, 1, 0])
    p = np.array([0.1, 0.2, 0.9, 0.8, 0.7, 0.3, 0.6, 0.4])
    metrics = classification_metrics(y, p)
    assert metrics["roc_auc"] > 0.8
    assert metrics["pr_auc"] > 0.7
    assert metrics["accuracy"] > 0.7
    assert primary_score(metrics, "pr_auc", "binary") == metrics["pr_auc"]


def test_regression_mae_is_inverted_for_ranking():
    metrics = regression_metrics([1.0, 2.0, 3.0], [1.1, 2.2, 2.7])
    assert metrics["mae"] > 0
    score = primary_score(metrics, "mae", "regression")
    assert score == pytest.approx(-metrics["mae"])


def test_diversity_and_blend_prefer_best_single_when_blend_loses():
    preds = pd.DataFrame(
        {
            "a": [0.9, 0.8, 0.1, 0.2],
            "b": [0.85, 0.75, 0.15, 0.25],
            "c": [0.2, 0.1, 0.9, 0.8],
        }
    )
    scores = {"a": 0.82, "b": 0.80, "c": 0.70}
    selected = greedy_diverse_selection(preds, scores, retain_max=3, retain_min=2, max_abs_correlation=0.99)
    assert "a" in selected
    weights = blend_weights({mid: scores[mid] for mid in selected}, selected)
    blended = blend_probabilities({mid: preds[mid].to_numpy() for mid in selected}, weights)
    assert len(blended) == 4
    fusion = choose_fusion(blend_metric=0.70, best_single_metric=0.82, best_single_id="a")
    assert fusion.startswith("single:")
