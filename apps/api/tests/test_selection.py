import pandas as pd

from app.ml.ensemble import blend_probabilities, blend_weights, choose_fusion
from app.ml.selection import greedy_diverse_selection


def test_greedy_selection_skips_near_duplicates():
    n = 20
    a = [0.1 + 0.04 * i for i in range(n)]
    b = [x + 0.001 for x in a]
    c = [0.3 if i % 2 == 0 else 0.7 for i in range(n)]
    matrix = pd.DataFrame({"a": a, "b": b, "c": c})
    selected = greedy_diverse_selection(
        matrix,
        {"a": 0.82, "b": 0.81, "c": 0.78},
        retain_max=3,
        retain_min=1,
        max_abs_correlation=0.95,
    )
    assert selected[0] == "a"
    assert "b" not in selected
    assert "c" in selected


def test_greedy_selection_fills_retain_min():
    n = 10
    matrix = pd.DataFrame({"x": list(range(n)), "y": list(range(n))})
    selected = greedy_diverse_selection(
        matrix,
        {"x": 0.9, "y": 0.89},
        retain_max=2,
        retain_min=2,
        max_abs_correlation=0.01,
    )
    assert selected == ["x", "y"]


def test_blend_beats_or_falls_back_to_single():
    weights = blend_weights({"m1": 0.8, "m2": 0.4}, ["m1", "m2"])
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    blended = blend_probabilities(
        {"m1": [0.2, 0.8], "m2": [0.4, 0.6]},
        weights,
    )
    assert list(blended)[0] >= 0
    assert choose_fusion(blend_metric=0.84, best_single_metric=0.80, best_single_id="m1") == "weighted_blend"
    assert choose_fusion(blend_metric=0.79, best_single_metric=0.80, best_single_id="m1") == "single:m1"
