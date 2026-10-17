"""P5.5-B-read: the pure §5 verdict for one comparison (ADR 0007): hand-computed cases, the
null false-positive rate, and mutation-style tests (a broken rule must make a test fail)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import t as student_t

from app.engine.evaluation import paired_verdict as pv
from app.engine.evaluation.paired_verdict import FoldScore, paired_verdict

PARENT = [0.80, 0.82, 0.78, 0.81, 0.79]
Q_K5_T1 = float(student_t.ppf(0.95, 4))  # 2.131847...
NB_K5 = math.sqrt(1 / 5 + 1 / 4)  # 0.670820...


def _folds(values, rows=None):
    return {i + 1: FoldScore(float(v), rows) for i, v in enumerate(values)}


def _pair(metric, parent, deltas, rows=None, **kwargs):
    child = [p + d for p, d in zip(parent, deltas)]
    return paired_verdict(metric, _folds(child, rows), _folds(parent, rows), **kwargs)


# --- hand-computed cases ------------------------------------------------------------------


def test_clear_win_matches_the_hand_computation():
    out = _pair("roc_auc", PARENT, [0.05, 0.06, 0.04, 0.05, 0.05])
    sd = math.sqrt(0.0002 / 4)  # deviations 0, .01, -.01, 0, 0
    assert out.verdict == "beats_the_noise" and out.reason is None and out.folds == 5
    assert out.mean_difference == pytest.approx(0.05)
    assert out.standard_error == pytest.approx(sd * NB_K5)
    assert out.t_quantile == pytest.approx(Q_K5_T1) and out.margin == pytest.approx(Q_K5_T1 * sd * NB_K5)
    assert out.margin == pytest.approx(0.0101119, abs=1e-6)
    assert out.minimum_effect == pytest.approx(0.001 * np.mean(PARENT))
    assert [f.fold_number for f in out.paired] == [1, 2, 3, 4, 5]
    assert out.paired[1].difference == pytest.approx(0.06)


def test_loss_is_worse_and_noise_is_within():
    worse = _pair("roc_auc", PARENT, [-0.05, -0.06, -0.04, -0.05, -0.05])
    assert worse.verdict == "worse" and worse.mean_difference == pytest.approx(-0.05)
    noise = _pair("roc_auc", PARENT, [0.01, -0.01, 0.02, -0.02, 0.0])
    assert (noise.verdict, noise.reason) == ("within_the_noise", "inside_margin")
    assert noise.mean_difference == pytest.approx(0.0, abs=1e-12)


def test_nb_factor_decides_a_case_the_naive_t_test_would_accept():
    # d = m + [0, s, -s, 0, 0]: sd = s * sqrt(1/2). Naive margin q*sd/sqrt(5) = 0.01348; NB margin 0.02022.
    s, m = 0.02, 0.017
    out = _pair("roc_auc", PARENT, [m, m + s, m - s, m, m])
    sd = s * math.sqrt(0.5)
    assert Q_K5_T1 * sd / math.sqrt(5) < m < Q_K5_T1 * sd * NB_K5
    assert out.margin == pytest.approx(0.0202237, abs=1e-6)
    assert (out.verdict, out.reason) == ("within_the_noise", "inside_margin")


@pytest.mark.parametrize("scale, verdict", [(1.001, "beats_the_noise"), (0.999, "within_the_noise")])
def test_k5_boundary_sits_at_the_t_quantile(scale, verdict):
    s = 0.02
    margin = Q_K5_T1 * s * math.sqrt(0.5) * NB_K5
    m = margin * scale
    out = _pair("roc_auc", PARENT, [m, m + s, m - s, m, m])
    assert out.margin == pytest.approx(margin, rel=1e-9)
    assert out.verdict == verdict


def test_zero_tolerance_is_relative_to_the_scale():
    # Fold errors of very different size: adding 0.1 leaves float noise of ~2e-7 in the differences, far
    # above an absolute 1e-12 but far below 1e-12 x the largest score.
    big = [3.1e8 + 0.123, 7.7e8 + 0.456, 1.3e9 + 0.789, 2.6e9 + 0.321, 5.2e9 + 0.654]
    shifted = paired_verdict("rmse", _folds([v + 0.1 for v in big]), _folds(big))
    assert np.std([(v + 0.1) - v for v in big], ddof=1) > 1e-12
    assert (shifted.verdict, shifted.reason) == ("not_comparable", "constant_difference")


def test_same_winner_is_no_change_and_constant_shift_is_not_comparable():
    same = paired_verdict("roc_auc", _folds(PARENT), _folds(PARENT))
    assert (same.verdict, same.reason, same.mean_difference, same.margin) == ("within_the_noise", "no_change", 0.0, 0.0)
    shifted = _pair("roc_auc", PARENT, [0.01] * 5)
    assert (shifted.verdict, shifted.reason) == ("not_comparable", "constant_difference")
    assert shifted.margin is None


def test_fold_problems_are_not_comparable_with_a_reason():
    four = paired_verdict("roc_auc", _folds(PARENT[:4]), _folds(PARENT[:4]))
    assert (four.verdict, four.reason, four.folds) == ("not_comparable", "too_few_folds", 4)
    other = dict(_folds(PARENT))
    other[6] = other.pop(5)
    assert paired_verdict("roc_auc", other, _folds(PARENT)).reason == "different_folds"
    rows = _folds(PARENT, 100)
    rows[3] = FoldScore(PARENT[2], 99)
    assert paired_verdict("roc_auc", rows, _folds(PARENT, 100)).reason == "different_folds"
    broken = dict(_folds(PARENT))
    broken[2] = FoldScore(float("nan"))
    assert paired_verdict("roc_auc", broken, _folds(PARENT)).reason == "missing_locked_folds"
    with pytest.raises(ValueError):
        paired_verdict("roc_auc", _folds(PARENT), _folds(PARENT), comparisons=0)


def test_error_metrics_are_judged_negated_and_reported_in_natural_units():
    rmse = [10.0, 11.0, 9.0, 10.5, 9.5]
    better = _pair("rmse", rmse, [-1.0, -1.2, -0.8, -1.0, -1.0])
    assert better.direction == "lower_is_better" and better.verdict == "beats_the_noise"
    assert better.mean_difference == pytest.approx(-1.0) and better.margin > 0
    assert better.margin == pytest.approx(Q_K5_T1 * math.sqrt(0.08 / 4) * NB_K5)
    assert better.paired[0].difference == pytest.approx(-1.0)
    assert better.minimum_effect == pytest.approx(0.001 * 10.0)
    worse = _pair("rmse", rmse, [1.0, 1.2, 0.8, 1.0, 1.0])
    assert worse.verdict == "worse" and worse.mean_difference == pytest.approx(1.0)


def test_count_based_minimum_effect_uses_the_smallest_validation_fold():
    out = _pair("accuracy", PARENT, [0.004, 0.00401, 0.00399, 0.004, 0.004], rows=200)
    assert out.minimum_effect == pytest.approx(1 / 200)
    assert out.mean_difference > out.margin
    assert (out.verdict, out.reason) == ("within_the_noise", "below_minimum_effect")
    assert out.minimum_effect_basis == "validation_rows"
    assert pv.minimum_effect("recall", PARENT, 5000) == 0.001  # floor
    assert pv.minimum_effect("pr_auc", [0.5] * 5, 10) == pytest.approx(0.0005)  # not count-based


def test_class_sensitive_minimum_effect_uses_the_smallest_class():
    def folds(values, smallest):
        return {i + 1: FoldScore(float(v), 200, smallest) for i, v in enumerate(values)}

    deltas = [0.03, 0.03001, 0.02999, 0.03, 0.03]
    child = [p + d for p, d in zip(PARENT, deltas)]
    out = paired_verdict("f1", folds(child, 25), folds(PARENT, 25))
    assert (out.minimum_effect, out.minimum_effect_basis) == (pytest.approx(1 / 25), "smallest_class_rows")
    assert (out.verdict, out.reason) == ("within_the_noise", "below_minimum_effect")  # 0.03 < 1/25
    accuracy = paired_verdict("accuracy", folds(child, 25), folds(PARENT, 25))
    assert (accuracy.minimum_effect_basis, accuracy.verdict) == ("validation_rows", "beats_the_noise")
    partial = folds(PARENT, 25)
    partial[3] = FoldScore(PARENT[2], 200, None)  # a class count on every fold of both runs, or none is used
    assert paired_verdict("macro_f1", folds(child, 25), partial).minimum_effect_basis == "validation_rows"


def test_more_comparisons_widen_the_margin():
    deltas = [0.05, 0.06, 0.04, 0.05, 0.05]
    one, six = _pair("roc_auc", PARENT, deltas), _pair("roc_auc", PARENT, deltas, comparisons=6)
    assert six.t_quantile == pytest.approx(float(student_t.ppf(1 - 0.05 / 6, 4)))
    assert six.margin > one.margin


# --- pairing by fold number (mutation: positional pairing must fail this) -----------------


def test_pairing_is_by_fold_number_not_by_position():
    parent = [0.60, 0.90, 0.70, 0.85, 0.65]  # wide spread: any mispairing drowns the gain in noise
    gain = [0.03, 0.031, 0.029, 0.03, 0.03]
    child = {n: FoldScore(parent[n - 1] + gain[n - 1]) for n in (5, 4, 3, 2, 1)}  # reversed insertion
    out = paired_verdict("roc_auc", child, _folds(parent))
    assert out.verdict == "beats_the_noise" and out.mean_difference == pytest.approx(0.03)
    positional = {i + 1: score for i, score in enumerate(child.values())}  # what zip() pairing would judge
    assert paired_verdict("roc_auc", positional, _folds(parent)).verdict == "within_the_noise"


# --- null false-positive rate -------------------------------------------------------------


def _null_rate(k: int, rho: float, sims: int, seed: int) -> float:
    """Equal models: fold deltas with correlation ``rho`` (rho = 0: independent folds)."""

    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(sims):
        parent = 0.8 + 0.02 * rng.standard_normal(k)
        d = 0.02 * (math.sqrt(rho) * rng.standard_normal() + math.sqrt(1 - rho) * rng.standard_normal(k))
        hits += paired_verdict("roc_auc", _folds(parent + d), _folds(parent)).verdict == "beats_the_noise"
    return hits / sims


def test_null_iid_equal_models_rarely_beat_the_noise():
    rate = _null_rate(5, 0.0, 2000, seed=11)
    assert rate <= 0.03  # independent folds: the NB rule is conservative (about 1.75 %)


@pytest.mark.parametrize("k", [5, 10])
def test_null_per_comparison_rate_is_alpha_under_the_nb_model(k):
    rate = _null_rate(k, 1.0 / k, 4000, seed=20 + k)
    assert 0.035 <= rate <= 0.065, rate  # ρ = 1/K: the statistic is exactly t_{K-1}, rate α = 5 %


def test_mutation_without_the_nb_factor_fails_the_null_band(monkeypatch):
    monkeypatch.setattr(pv, "nb_factor", lambda k: 1.0 / math.sqrt(k))
    rate = _null_rate(5, 0.2, 4000, seed=25)
    assert rate > 0.065, rate  # the band test above would catch an implementation without NB


def _best_of_k_rate(k_new: int, comparisons: int, sims: int, seed: int, k: int = 5) -> float:
    """A try adds ``k_new`` null candidates (equal to the incumbent, fold deltas correlated rho = 1/K);
    its winner is the best of them by CV mean, else the incumbent stays (d = 0)."""

    rng = np.random.default_rng(seed)
    rho, hits = 1.0 / k, 0
    for _ in range(sims):
        parent = 0.8 + 0.02 * rng.standard_normal(k)
        deltas = [0.02 * (math.sqrt(rho) * rng.standard_normal() + math.sqrt(1 - rho) * rng.standard_normal(k))
                  for _ in range(k_new)]
        best = max(deltas, key=lambda d: d.mean())
        if best.mean() <= 0:
            continue  # the incumbent stays the winner: no_change
        out = paired_verdict("roc_auc", _folds(parent + best), _folds(parent), comparisons=comparisons)
        hits += out.verdict == "beats_the_noise"
    return hits / sims


def test_null_best_of_k_new_candidates_is_alpha_with_t_equal_k():
    rate = _best_of_k_rate(3, comparisons=3, sims=6000, seed=41)
    assert 0.035 <= rate <= 0.065, rate  # T = k restores the per-click rate (4.3 % here)


def test_mutation_t_equal_one_fails_the_best_of_k_band():
    rate = _best_of_k_rate(3, comparisons=1, sims=6000, seed=41)
    assert rate > 0.065, rate  # best of 3 judged as one comparison: about 13 %
