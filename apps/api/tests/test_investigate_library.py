"""P5.1-A: the ten added trust checks, pure (no database).

Per check: a synthetic positive (fires with the right status, severity, recommendation
and message), a synthetic negative (clean data passes), ``not_evaluated`` for missing
evidence and a raising check that is reported ``not_evaluated`` (guarded). Null
false-positive tests (no effect -> at most 5% warnings) guard the noise gates, and named
mutation guards make sure each gate is load-bearing. Plus: the holdout-blind test, the
agent view of holdout-scoped evidence, determinism, old 5-check results, the runner's
out-of-fold hand-off and the finding-type mapping pinned to the 0070 CHECK constraint.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

from app.agents.tools.shaping import HOLDOUT_KEY, names_holdout
from app.domain.findings import (
    AGENT_MESSAGES,
    CHECK_FINDING_TYPES,
    FINDING_CHECKS,
    HOLDOUT_FEATURE_CHECKS,
    findings_read,
)
from app.domain.scientific_plane import DATA_QUALITY_FINDING_TYPES
from app.engine.investigate import (
    CHECK_ORDER,
    ParentEvidence,
    RunEvidence,
    SplitFeatures,
    TimeTravelProbe,
    check_calibration,
    check_contamination,
    check_feature_drift,
    check_fold_instability,
    check_missingness_shift,
    check_multicollinearity,
    check_new_feature,
    check_subgroup_gap,
    check_temporal_shift,
    check_time_travel,
    investigate,
    investigate_result,
    investigation_payload,
)
from app.engine.investigate import split_checks
from app.engine.investigate.oof import oof_evidence
from app.engine.validation.splits import SOURCE_ROW_COLUMN

NEW_CHECKS = FINDING_CHECKS[5:]
BASE = RunEvidence(task_type="binary", primary_metric="roc_auc", winner_family="xgboost",
                   winner_features=("a", "b", "c"), winner_cv={"roc_auc": 0.80}, winner_cv_std={"roc_auc": 0.02},
                   baseline_cv={"roc_auc": 0.5}, n_folds=5)
RNG_SEED = 7


def _folds(n: int, k: int = 5) -> list[np.ndarray]:
    return [np.arange(n)[i::k] for i in range(k)]


def _binary_oof(n: int = 3000, *, distort: bool = False, weak_level: str | None = None, seed: int = RNG_SEED):
    """Labels drawn from p; predictions p (calibrated) or p ** 3 (miscalibrated); one weak segment."""

    rng = np.random.default_rng(seed)
    segment = rng.choice(["s1", "s2", "s3"], n)
    p = rng.uniform(0.02, 0.98, n)
    y = (rng.uniform(size=n) < p).astype(int)
    pred = p ** 3 if distort else p.copy()
    if weak_level:
        weak = segment == weak_level
        pred[weak] = rng.uniform(0.02, 0.98, int(weak.sum()))  # no signal in that group
    frame = pd.DataFrame({"segment": segment, "x": rng.normal(size=n)})
    folds = [(ix, pred[ix]) for ix in _folds(n)]
    return oof_evidence("binary", y, folds, frame, ["segment"], primary_metric="roc_auc", candidate_id="w")


# --- guards -----------------------------------------------------------------------------------


def test_library_has_fifteen_checks_in_one_order_and_the_mapping_matches_the_0070_constraint():
    assert len(FINDING_CHECKS) == 15 and tuple(CHECK_ORDER) == FINDING_CHECKS
    # Rows only for types ck_data_quality_findings_type_valid admits; the P5.1-A checks are result-only.
    assert set(CHECK_FINDING_TYPES) == set(FINDING_CHECKS[:5])
    assert set(CHECK_FINDING_TYPES.values()) <= set(DATA_QUALITY_FINDING_TYPES)
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0070_investigation_findings.py"
    spec = importlib.util.spec_from_file_location("m0070", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(CHECK_FINDING_TYPES.values()) <= set(module._OLD_TYPES + module._ADDED_TYPES)
    assert set(DATA_QUALITY_FINDING_TYPES) == set(module._OLD_TYPES + module._ADDED_TYPES)
    # Holdout scope: the four train -> test checks; each (and duplicate_rows) has a fixed agent text.
    assert HOLDOUT_FEATURE_CHECKS == {"feature_drift", "temporal_shift", "missingness_shift", "contamination"}
    assert HOLDOUT_FEATURE_CHECKS | {"duplicate_rows"} == set(AGENT_MESSAGES)
    assert not any(names_holdout(text) or any(ch.isdigit() for ch in text) for text in AGENT_MESSAGES.values())


# --- 6. fold instability ------------------------------------------------------------------------


def _fold_ev(scores, metric="roc_auc", **kw):
    return replace(BASE, primary_metric=metric, winner_fold_metrics=tuple({metric: s} for s in scores), **kw)


def test_fold_instability_negative_positive_and_not_evaluated():
    clean = check_fold_instability(_fold_ev([0.80, 0.81, 0.79, 0.80, 0.82]))
    assert (clean.status, clean.message_keys) == ("pass", ("fold_instability.pass",))
    swing = check_fold_instability(_fold_ev([0.62, 0.92, 0.71, 0.86, 0.45]))
    assert (swing.status, swing.severity, swing.recommendation_kind) == ("warning", "warning", "collect_more_data")
    assert swing.message_keys == ("fold_instability.unstable", "fold_instability.at_baseline")
    assert "from 0.45 to 0.92" in swing.message and swing.evidence["worst_fold_at_baseline"] is True
    error = check_fold_instability(_fold_ev([10.0, 14.0, 9.0, 13.0, 8.0], metric="rmse", baseline_cv={"rmse": 20.0}))
    assert error.status == "warning" and error.evidence["direction"] == "lower_is_better"
    assert check_fold_instability(_fold_ev([10.0, 10.2, 9.9], metric="rmse")).status == "pass"
    assert check_fold_instability(BASE).evidence["not_evaluated_reason"] == "fold_scores_missing"
    assert check_fold_instability(_fold_ev([0.8, 0.9])).evidence["not_evaluated_reason"] == "too_few_folds"


def test_fold_instability_has_a_noise_floor_for_small_folds_and_near_zero_means():
    # 200 training rows, 70 in the minority class: 14 per fold, so AUC varies ~0.12 by chance.
    scores = [0.66, 0.74, 0.70, 0.79, 0.64]
    small = check_fold_instability(_fold_ev(scores, class_distribution={"0": 130, "1": 70}))
    assert small.status == "pass"
    assert small.message_keys == ("fold_instability.pass", "fold_instability.within_noise")
    assert small.evidence["fold_noise_std"] > 0.1 and small.evidence["fold_rows"] == 40
    big = check_fold_instability(_fold_ev(scores, class_distribution={"0": 13000, "1": 7000}))
    assert big.status == "warning"  # 1,400 minority rows per fold: real instability
    assert check_fold_instability(_fold_ev([0.0, 1e-13, 2e-13], metric="mae")).status == "pass"


# --- 7. calibration ----------------------------------------------------------------------------


def test_calibration_negative_positive_and_not_evaluated():
    good = check_calibration(replace(BASE, oof=_binary_oof()))
    assert good.status == "pass" and good.message_keys[0] == "calibration.pass"
    assert good.evidence["ece"] < 0.05 and good.evidence["brier"] < good.evidence["base_rate_brier"]
    bad = check_calibration(replace(BASE, oof=_binary_oof(distort=True), winner_class_weighted=True))
    assert (bad.status, bad.recommendation_kind) == ("warning", "calibrate")
    assert bad.message_keys == ("calibration.miscalibrated", "calibration.class_weighted")
    assert "Recalibrate" in bad.message and bad.evidence["ece"] >= 0.05
    assert check_calibration(replace(BASE, task_type="regression")).evidence["not_evaluated_reason"] == "not_applicable"
    assert check_calibration(BASE).evidence["not_evaluated_reason"] == "oof_missing"
    hard = oof_evidence("binary", np.array([0, 1] * 100), [(np.arange(200), np.array([0.0, 1.0] * 100))],
                        pd.DataFrame(index=range(200)), [], primary_metric="roc_auc")
    assert check_calibration(replace(BASE, oof=hard)).evidence["not_evaluated_reason"] == "no_probabilities"
    few = _binary_oof(n=60)
    assert check_calibration(replace(BASE, oof=few)).evidence["not_evaluated_reason"] == "too_few_rows"


def test_calibration_multiclass_uses_top_label_confidence():
    rng = np.random.default_rng(3)
    n = 1500
    proba = rng.dirichlet([2, 2, 2], n)
    y = np.array([rng.choice(3, p=row) for row in proba])
    summary = oof_evidence("multiclass", y, [(ix, proba[ix]) for ix in _folds(n)], pd.DataFrame(index=range(n)), [],
                           primary_metric="macro_f1", n_classes=3)
    finding = check_calibration(replace(BASE, task_type="multiclass", oof=summary))
    assert finding.status == "pass" and "calibration.top_label" in finding.message_keys
    flat = np.full_like(proba, 1 / 3) * 0.1 + np.eye(3)[proba.argmax(axis=1)] * 0.9  # overconfident
    summary = oof_evidence("multiclass", y, [(ix, flat[ix]) for ix in _folds(n)], pd.DataFrame(index=range(n)), [],
                           primary_metric="macro_f1", n_classes=3)
    assert check_calibration(replace(BASE, task_type="multiclass", oof=summary)).status == "warning"


# --- 8. subgroup gap ---------------------------------------------------------------------------


def test_subgroup_gap_negative_positive_and_not_evaluated():
    even = check_subgroup_gap(replace(BASE, oof=_binary_oof()))
    assert even.status == "pass" and even.evidence["columns_checked"] == 1
    weak = check_subgroup_gap(replace(BASE, oof=_binary_oof(weak_level="s2")))
    assert (weak.status, weak.recommendation_kind) == ("warning", "review_subgroups")
    assert weak.evidence["flagged_columns"] == ["segment"] and weak.evidence["worst_group_score"] < 0.6
    assert weak.evidence["columns"][0]["z"] >= weak.evidence["z_critical"] and weak.evidence["groups_tested"] == 3
    assert "in segment the weakest of 3 groups" in weak.message
    assert "s2" not in json.dumps(weak.evidence)  # group values never leave the worker
    assert check_subgroup_gap(BASE).evidence["not_evaluated_reason"] == "oof_missing"
    no_columns = oof_evidence("binary", np.array([0, 1] * 100), [(np.arange(200), np.linspace(0.1, 0.9, 200))],
                              pd.DataFrame({"x": range(200)}), [], primary_metric="roc_auc")
    assert check_subgroup_gap(replace(BASE, oof=no_columns)).evidence["not_evaluated_reason"] == \
        "no_low_cardinality_column"


def _subgroup_null(seed: int, n: int = 1500) -> dict:
    """Several categorical columns with 2..6 levels, independent of labels and predictions."""

    rng = np.random.default_rng(seed)
    p = rng.uniform(0.05, 0.95, n)
    y = (rng.uniform(size=n) < p).astype(int)
    pred = np.clip(p + rng.normal(0, 0.15, n), 0.01, 0.99)
    frame = pd.DataFrame({"two": rng.choice(["a", "b"], n), "four": rng.choice(list("abcd"), n),
                          "six": rng.choice(list("abcdef"), n)})
    folds = [(ix, pred[ix]) for ix in _folds(n)]
    return oof_evidence("binary", y, folds, frame, list(frame.columns), primary_metric="roc_auc", candidate_id="w")


def test_subgroup_gap_null_false_positive_rate_is_at_most_five_percent(monkeypatch):
    statuses = [check_subgroup_gap(replace(BASE, oof=_subgroup_null(seed))).status for seed in range(40)]
    assert statuses.count("warning") <= 2, statuses
    # Mutation guard: without the per-group standard-error gate the same null data warns far more.
    from app.engine.investigate import cv_checks

    monkeypatch.setattr(cv_checks, "SUBGROUP_ALPHA", 1.0)  # z critical ~1.38 (12 groups): far looser
    monkeypatch.setattr(cv_checks, "SUBGROUP_GAP_WARN", 0.02)
    loose = [check_subgroup_gap(replace(BASE, oof=_subgroup_null(seed))).status for seed in range(40)]
    assert loose.count("warning") > 2, loose


# --- 9. multicollinearity ---------------------------------------------------------------------------


def test_multicollinearity_negative_positive_and_not_evaluated():
    rng = np.random.default_rng(1)
    base = pd.DataFrame({"a": rng.normal(size=500), "b": rng.normal(size=500), "c": rng.choice(["x", "y"], 500)})
    clean = check_multicollinearity(base, "logistic_regression")
    assert clean.status == "pass" and clean.evidence["numeric_feature_count"] == 2
    twin = base.assign(a_copy=base["a"] * 2 + rng.normal(scale=0.01, size=500))
    finding = check_multicollinearity(twin, "logistic_regression")
    assert (finding.status, finding.recommendation_kind) == ("warning", "drop_correlated")
    assert finding.evidence["correlated_pairs"][0]["columns"] == ["a", "a_copy"]
    assert finding.message_keys[-1] == "multicollinearity.linear_model" and "a_copy" in finding.message
    assert check_multicollinearity(twin, "xgboost").message_keys[-1] == "multicollinearity.other_model"
    assert check_multicollinearity(base[["a", "c"]]).evidence["not_evaluated_reason"] == "too_few_numeric_columns"
    assert check_multicollinearity(None).evidence["not_evaluated_reason"] == "partition_unavailable"


def test_multicollinearity_flags_exact_linear_dependence_a_pseudo_inverse_would_hide():
    rng = np.random.default_rng(2)
    frame = pd.DataFrame({"a": rng.normal(size=400), "b": rng.normal(size=400)})
    exact = check_multicollinearity(frame.assign(c=frame["a"] + frame["b"]), "logistic_regression")
    assert (exact.status, exact.message_keys[0]) == ("warning", "multicollinearity.exact")
    assert exact.evidence["rank_deficient"] and exact.evidence["dependent_columns"] == ["a", "b", "c"]
    assert exact.evidence["max_vif"] == 1e6 and exact.evidence["max_vif_capped"]
    assert exact.evidence["drop_candidates"] == ["c"] and "carries no information" in exact.message
    level = rng.choice(3, 400)
    dummies = pd.DataFrame({f"is_{k}": (level == k).astype(int) for k in range(3)})
    assert check_multicollinearity(dummies).evidence["rank_deficient"]  # three 0/1 dummies summing to 1
    wide = pd.DataFrame(rng.normal(size=(60, 205)), columns=[f"x{i:03d}" for i in range(205)])
    truncated = check_multicollinearity(wide)
    assert truncated.evidence["columns_truncated"] and truncated.evidence["columns_checked"] == 200
    assert truncated.evidence["numeric_feature_count"] == 205
    assert "multicollinearity.truncated" in truncated.message_keys


# --- 10-13. train -> test feature checks ----------------------------------------------------------


def _split(n_train=800, n_test=200, *, shift=0.0, test_missing=0.0, seed=2, **kw) -> SplitFeatures:
    rng = np.random.default_rng(seed)

    def rows(n, loc):
        return pd.DataFrame({"amount": rng.normal(100 + loc * 30, 30, n), "plan": rng.choice(["a", "b", "c"], n),
                             "visits": rng.integers(0, 9, n).astype(float)})

    train, test = rows(n_train, 0.0), rows(n_test, shift)
    if test_missing:
        test.loc[test.sample(frac=test_missing, random_state=0).index, "visits"] = np.nan
    return SplitFeatures(train=train, test=test, **kw)


def test_feature_drift_negative_positive_and_not_evaluated():
    clean = check_feature_drift(_split(), "stratified_random")
    assert clean.status == "pass" and clean.evidence["columns_checked"] == 3
    drift = check_feature_drift(_split(shift=1.0), "stratified_random")
    assert (drift.status, drift.recommendation_kind) == ("warning", "review_split")
    comparison = drift.evidence["holdout_comparison"]
    assert comparison["drifted_columns"] == ["amount"] and comparison["max_psi"] > 0.25
    assert drift.message_keys == ("feature_drift.drift", "feature_drift.random_split")
    assert "significant after correcting for the 3 columns" in drift.message
    assert check_feature_drift(_split(shift=1.0), "temporal_future").message_keys[-1] == "feature_drift.temporal_split"
    categorical = _split()
    categorical.test["plan"] = "c"
    assert check_feature_drift(categorical).evidence["holdout_comparison"]["drifted_columns"] == ["plan"]
    assert check_feature_drift(_split(n_test=10)).evidence["not_evaluated_reason"] == "too_few_rows"
    assert check_feature_drift(None).evidence["not_evaluated_reason"] == "partition_unavailable"


def test_feature_drift_bins_come_from_the_training_rows():
    # Mutation guard: the PSI equals the reference computed on TRAINING-quantile bins (10 bins for
    # 100 test rows, 0.5 added per bin); bins fitted on test quantiles give another number.
    rng = np.random.default_rng(4)
    train, test = rng.normal(0, 1, 500), rng.normal(0.5, 2, 100)
    edges = np.unique(np.quantile(train, np.linspace(0, 1, 11))[1:-1])
    e, a = (np.bincount(np.searchsorted(edges, v, side="right"), minlength=len(edges) + 1) + 0.5 for v in (train, test))
    e, a = e / e.sum(), a / a.sum()
    split = SplitFeatures(train=pd.DataFrame({"x": train}), test=pd.DataFrame({"x": test}))
    finding = check_feature_drift(split, "random")
    assert finding.evidence["holdout_comparison"]["columns"][0]["psi"] == pytest.approx(
        float(((a - e) * np.log(a / e)).sum()), abs=1e-6)
    assert finding.evidence["holdout_comparison"]["bins"] == 10


def _null_split(n_test: int, n_columns: int, seed: int) -> SplitFeatures:
    rng = np.random.default_rng(seed)

    def rows(n: int) -> pd.DataFrame:
        data: dict[str, object] = {}
        for j in range(n_columns):
            kind = j % 3
            data[f"c{j:03d}"] = (rng.normal(0, 1, n) if kind == 0 else rng.integers(0, 5, n).astype(float)
                                 if kind == 1 else rng.choice(list("abcd"), n))
        return pd.DataFrame(data)

    return SplitFeatures(train=rows(5 * n_test), test=rows(n_test))


@pytest.mark.parametrize("n_test, n_columns", [(30, 20), (60, 50), (200, 100)])
def test_feature_drift_null_false_positive_rate_is_at_most_five_percent(n_test, n_columns):
    statuses = [check_feature_drift(_null_split(n_test, n_columns, seed), "random").status for seed in range(20)]
    assert statuses.count("warning") <= 1, statuses


def _timed(train_days, test_days, **kw) -> SplitFeatures:
    start = pd.Timestamp("2024-01-01")
    return _split(n_train=len(train_days), n_test=len(test_days), time_column="as_of",
                  train_times=pd.Series(start + pd.to_timedelta(train_days, unit="D")),
                  test_times=pd.Series(start + pd.to_timedelta(test_days, unit="D")), **kw)


def test_temporal_shift_negative_positive_and_not_evaluated():
    ordered = check_temporal_shift(_timed(np.arange(0, 300), np.arange(300, 380)), "temporal_future")
    assert ordered.status == "pass" and ordered.evidence["holdout_comparison"]["holdout_gap_days"] == 1.0
    assert "so the test score measures the future" in ordered.message
    leaky = check_temporal_shift(_timed(np.arange(0, 300), np.arange(250, 330)), "temporal_future")
    assert (leaky.status, leaky.severity, leaky.recommendation_kind) == ("fail", "error", "review_split")
    rng = np.random.default_rng(0)
    mixed = check_temporal_shift(_timed(rng.integers(0, 365, 300), rng.integers(0, 365, 80)), "stratified_random")
    assert mixed.status == "warning" and "The split ignored time" in mixed.message
    assert check_temporal_shift(_split(), "random").evidence["not_evaluated_reason"] == "no_time_column"
    garbled = replace(_timed(np.arange(10), np.arange(10, 20)), train_times=pd.Series(["x"] * 10))
    assert check_temporal_shift(garbled).evidence["not_evaluated_reason"] == "time_unparsable"
    assert check_temporal_shift(None).evidence["not_evaluated_reason"] == "partition_unavailable"


def test_missingness_shift_negative_positive_and_not_evaluated():
    assert check_missingness_shift(_split()).status == "pass"
    shifted = check_missingness_shift(_split(test_missing=0.3))
    assert (shifted.status, shifted.recommendation_kind) == ("warning", "review_split")
    comparison = shifted.evidence["holdout_comparison"]
    assert comparison["shifted_columns"] == ["visits"] and comparison["new_missing_columns"] == ["visits"]
    assert "visits" in shifted.message and "30.0%" in shifted.message
    assert check_missingness_shift(None).evidence["not_evaluated_reason"] == "partition_unavailable"


def test_missingness_shift_needs_significance_and_one_missing_cell_is_not_new():
    # 10% missing in training, 5 of 30 (16.7%) in test: a 6.7-point change that is chance (z ~ 1.2).
    train = pd.DataFrame({"x": [np.nan] * 40 + [1.0] * 360})
    test = pd.DataFrame({"x": [np.nan] * 5 + [1.0] * 25})
    assert check_missingness_shift(SplitFeatures(train=train, test=test)).status == "pass"  # MISSING_Z = 0 fails
    one = SplitFeatures(train=pd.DataFrame({"x": [1.0] * 1000}), test=pd.DataFrame({"x": [np.nan] + [1.0] * 199}))
    assert check_missingness_shift(one).status == "pass"
    rare = []
    for seed in range(20):  # 0.3% missing in both partitions
        rng = np.random.default_rng(seed)
        frames = [pd.DataFrame({f"x{j}": np.where(rng.uniform(size=n) < 0.003, np.nan, 1.0) for j in range(10)})
                  for n in (2000, 500)]
        rare.append(check_missingness_shift(SplitFeatures(train=frames[0], test=frames[1])).status)
    assert rare.count("warning") <= 1, rare


def _correlated(n: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    x = rng.normal(100, 30, n)
    frame = pd.DataFrame({"x": x, "x_twin": x + rng.normal(0, 3, n), "plan": rng.choice(["a", "b", "c"], n)})
    cut = int(n * 0.8)
    return frame.iloc[:cut].reset_index(drop=True), frame.iloc[cut:].reset_index(drop=True)


def test_contamination_near_duplicates_use_an_empirical_baseline_and_never_fail():
    # iid rows with strongly correlated numeric columns (r ~ 0.99) repeat after rounding by nature; the
    # repeats vanish at 5 significant digits, so they are natural repeats: a pass, never a warning.
    for seed in range(5):
        train, test = _correlated(2000, seed)
        finding = check_contamination(SplitFeatures(train=train, test=test), "random")
        assert finding.status == "pass", (seed, finding.message_keys, finding.evidence)
    near = _split()
    near = replace(near, train=near.train.assign(email=[f"user{i}@example.com" for i in range(800)]),
                   test=near.test.assign(email=[f"other{i}@example.com" for i in range(200)]))
    assert check_contamination(near, "random").status == "pass"
    # Re-keyed copies: case/spacing changed and a 7th-digit difference -- not exact duplicates.
    copies = near.train.iloc[:60].assign(amount=lambda f: f["amount"] * (1 + 1e-6),
                                         email=lambda f: " " + f["email"].str.upper())
    near = replace(near, test=pd.concat([near.test.iloc[:140], copies], ignore_index=True))
    found = check_contamination(near, "random")
    assert (found.status, found.recommendation_kind) == ("warning", "deduplicate")
    comparison = found.evidence["holdout_comparison"]
    # Matched against the 80% training reference: copies of rows in the 20% baseline slice are not seen.
    assert 40 <= comparison["near_duplicate_holdout_rows"] <= 60 and comparison["near_duplicate_holdout_z"] > 3
    assert "test rows" in found.message and "match a training row" in found.message
    tiny = SplitFeatures(train=near.train.iloc[:40], test=near.test)
    assert check_contamination(tiny).evidence["not_evaluated_reason"] == "no_baseline"
    assert check_contamination(None).evidence["not_evaluated_reason"] == "partition_unavailable"


def _grouped(train_ids, test_ids, **kw) -> SplitFeatures:
    rng = np.random.default_rng(5)
    return _split(n_train=len(train_ids), n_test=len(test_ids), group_column="customer",
                  train_groups=pd.Series(train_ids), test_groups=pd.Series(test_ids),
                  train_labels=pd.Series(rng.integers(0, 2, len(train_ids))), **kw)


def test_contamination_shared_groups_follow_the_split_plan():
    disjoint = _grouped([f"c{i}" for i in range(800)], [f"d{i}" for i in range(200)])
    finding = check_contamination(disjoint, "group_disjoint")
    # Three coarse columns repeat naturally after rounding: an honest pass that does not claim "no contamination".
    assert finding.status == "pass" and finding.message_keys == ("contamination.natural_repeats",
                                                                  "contamination.groups_disjoint")
    assert "No contamination" not in finding.message
    shared = _grouped([f"c{i}" for i in range(800)], [f"c{i}" for i in range(30)] + [f"d{i}" for i in range(170)])
    planned = check_contamination(shared, "group_disjoint")  # a group split was planned and still shares
    assert (planned.status, planned.recommendation_kind) == ("fail", "review_split")
    assert planned.evidence["holdout_comparison"]["holdout_shared_group_count"] == 30 and "c1" not in json.dumps(planned.evidence)
    # Random split where the planner judged repetition too low to group: ~27% shared -> warning only.
    rng = np.random.default_rng(9)
    ids = [f"e{i}" for i in rng.integers(0, 2000, 1000)]
    random_split = check_contamination(_grouped(ids[:800], ids[800:]), "stratified_random")
    assert random_split.status == "warning" and random_split.recommendation_kind == "review_split"
    assert 0.15 < random_split.evidence["holdout_comparison"]["holdout_rows_sharing_group_fraction"] < 0.4
    # Customers observed repeatedly and split forward in time: the intended design, informational.
    repeated = [f"k{i % 150}" for i in range(1000)]
    temporal = check_contamination(_grouped(repeated[:800], repeated[800:]), "temporal_future")
    assert temporal.status == "pass" and temporal.recommendation_kind is None
    assert "contamination.shared_groups_temporal" in temporal.message_keys
    conflict = replace(disjoint, train=pd.concat([disjoint.train.iloc[:780], disjoint.train.iloc[:20]],
                                                 ignore_index=True),
                       train_labels=pd.Series([0] * 780 + [1] * 20))
    assert "contamination.conflicting_labels" in check_contamination(conflict, "group_disjoint").message_keys


def test_split_checks_are_bounded(monkeypatch):
    monkeypatch.setattr(split_checks, "SPLIT_MAX_ROWS", 300)
    monkeypatch.setattr(split_checks, "SPLIT_MAX_COLUMNS", 2)
    split = _split(n_train=900, n_test=400)
    drift = check_feature_drift(split)
    assert drift.evidence["columns_checked"] == 2 and drift.evidence["columns_truncated"]
    contamination = check_contamination(split)
    assert contamination.evidence["train_rows"] == 300 and contamination.evidence["holdout_rows"] == 300


# --- 14-15. time travel and new feature -------------------------------------------------------------


def _history(n=120, seed=4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"customer": rng.choice([f"c{i}" for i in range(12)], n),
                         "as_of": pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 90, n), unit="D"),
                         "amount": rng.normal(50, 10, n)})


def _strict(frame: pd.DataFrame) -> pd.DataFrame:  # count of the customer's strictly earlier rows
    t = pd.to_datetime(frame["as_of"])
    return pd.DataFrame({"prior_orders": [int(((frame["customer"] == c) & (t < ti)).sum())
                                          for c, ti in zip(frame["customer"], t)]})


def _whole_table(frame: pd.DataFrame) -> pd.DataFrame:  # every row of the customer, future included
    return pd.DataFrame({"prior_orders": frame.groupby("customer")["amount"].transform("size").to_numpy()})


def test_time_travel_negative_positive_and_not_evaluated():
    ev = replace(BASE, time_column="as_of", group_column="customer", as_of_features=("prior_orders",))
    clean = check_time_travel(ev, TimeTravelProbe(produce=_strict, frame=_history()))
    assert clean.status == "pass" and clean.evidence["probed_rows"] == 120
    leak = check_time_travel(ev, TimeTravelProbe(produce=_whole_table, frame=_history()))
    assert (leak.status, leak.severity, leak.recommendation_kind) == ("fail", "critical", "review_columns")
    assert leak.evidence["moved_columns"] == ["prior_orders"] and "read rows from after" in leak.message
    big = check_time_travel(ev, TimeTravelProbe(produce=_whole_table, frame=_history(n=900)))
    assert big.status == "fail" and big.evidence["rows_used"] <= 500  # bounded replay
    assert check_time_travel(BASE, None).evidence["not_evaluated_reason"] == "no_time_column"
    assert check_time_travel(replace(ev, as_of_features=()), None).evidence["not_evaluated_reason"] == \
        "no_as_of_features"
    assert check_time_travel(ev, None).evidence["not_evaluated_reason"] == "probe_unavailable"


def _branch(child_scores, parent_scores, child_family="xgboost", parent_family="xgboost"):
    child = replace(BASE, winner_family=child_family, winner_features=("a", "b", "c"),
                    winner_cv={"roc_auc": float(np.mean(child_scores))}, winner_cv_std={"roc_auc": 0.01},
                    winner_fold_metrics=tuple({"roc_auc": v} for v in child_scores))
    parent = ParentEvidence(experiment_id="p1", same_split_plan=True, primary_metric="roc_auc", features=("a", "b"),
                            cv={"roc_auc": float(np.mean(parent_scores))}, cv_std={"roc_auc": 0.02},
                            winner_family=parent_family,
                            fold_metrics=tuple({"roc_auc": v} for v in parent_scores))
    return child, parent


def test_new_feature_negative_positive_and_not_evaluated():
    parent_folds = [0.79, 0.81, 0.80, 0.78, 0.82]
    child, parent = _branch([0.97, 0.98, 0.96, 0.97, 0.97], parent_folds)
    jump = check_new_feature(child, parent)
    assert (jump.status, jump.recommendation_kind) == ("fail", "investigate_leakage")
    assert jump.message_keys == ("new_feature.outsized", "new_feature.same_family", "new_feature.paired")
    assert jump.evidence["added_feature"] == "c" and jump.evidence["residual_closed_fraction"] == pytest.approx(0.85)
    assert jump.evidence["paired_t"] is None or jump.evidence["paired_t"] > jump.evidence["t_critical"]
    assert "single column c moved" in jump.message and "Both winners are xgboost models" in jump.message
    unknown = check_new_feature(child, replace(parent, winner_family=None))  # parent family unknown
    assert unknown.status == "warning" and "Both winners" not in unknown.message
    assert check_new_feature(*_branch([0.91, 0.90, 0.92, 0.91, 0.91], parent_folds)).status == "warning"
    assert check_new_feature(*_branch([0.82, 0.80, 0.81, 0.80, 0.82], parent_folds)).status == "pass"
    # Fold deltas that do not hold fold by fold: not significant -> pass.
    assert check_new_feature(*_branch([0.99, 0.80, 0.99, 0.78, 0.99], parent_folds)).status == "pass"
    changed = check_new_feature(*_branch([0.97, 0.98, 0.96, 0.97, 0.97], parent_folds, child_family="random_forest"))
    assert changed.evidence["not_evaluated_reason"] == "different_winner_family"
    unpaired = check_new_feature(replace(child, winner_fold_metrics=()), parent)  # at most a warning
    assert unpaired.status == "warning" and unpaired.message_keys[-1] == "new_feature.unpaired"
    assert check_new_feature(child, None).evidence["not_evaluated_reason"] == "no_parent"
    assert check_new_feature(child, replace(parent, same_split_plan=False)).evidence["not_evaluated_reason"] == \
        "different_split_plan"
    assert check_new_feature(child, replace(parent, features=("a",))).evidence["not_evaluated_reason"] == \
        "not_single_feature_change"
    assert check_new_feature(child, replace(parent, cv={})).evidence["not_evaluated_reason"] == "cv_missing"


# --- orchestration: guarded, holdout-blind, deterministic, old results -------------------------------


@pytest.mark.parametrize("module, name", [
    ("cv_checks", "check_fold_instability"), ("cv_checks", "check_calibration"), ("cv_checks", "check_subgroup_gap"),
    ("cv_checks", "check_multicollinearity"), ("split_checks", "check_feature_drift"),
    ("split_checks", "check_temporal_shift"), ("split_checks", "check_missingness_shift"),
    ("split_checks", "check_contamination"), ("probe_checks", "check_time_travel"),
    ("probe_checks", "check_new_feature"),
])
def test_a_raising_new_check_is_not_evaluated_and_the_others_still_run(monkeypatch, module, name):
    import importlib

    def boom(*_args):
        raise RuntimeError("broken")

    monkeypatch.setattr(importlib.import_module(f"app.engine.investigate.{module}"), name, boom)
    findings = investigate(replace(BASE, oof=_binary_oof()), split=_split)
    check = name.removeprefix("check_")
    broken = [f for f in findings if f.check == check]
    assert broken[0].evidence == {"error_type": "RuntimeError", "not_evaluated_reason": "check_error"}
    assert not [f for f in findings if f.check != check and f.evidence.get("not_evaluated_reason") == "check_error"]


def test_a_raising_split_supplier_only_affects_the_split_checks():
    def boom():
        raise ValueError("no frame")

    findings = {f.check: f for f in investigate(replace(BASE, oof=_binary_oof()), split=boom)}
    split_based = {"duplicate_rows", "multicollinearity", "feature_drift", "temporal_shift", "missingness_shift",
                   "contamination"}
    assert {c for c, f in findings.items() if f.evidence.get("not_evaluated_reason") == "check_error"} == split_based
    assert findings["calibration"].status == "pass"


def _run_inputs(seed: int = 11):
    """A frame with model columns (one a LABEL PROXY: missing for every negative row), a time
    column, a group column and the target, split into training and test source rows, and a run
    result whose winner is the 'xgboost' candidate."""

    rng = np.random.default_rng(seed)
    n = 600
    label = rng.integers(0, 2, n)
    frame = pd.DataFrame({
        SOURCE_ROW_COLUMN: np.arange(n), "amount": rng.normal(100, 30, n), "plan": rng.choice(["a", "b", "c"], n),
        "visits": rng.integers(0, 9, n).astype(float), "customer": [f"c{i}" for i in rng.integers(0, 400, n)],
        "proxy": np.where(label == 1, rng.normal(5, 1, n), np.nan),
        "as_of": pd.Timestamp("2024-01-01") + pd.to_timedelta(np.sort(rng.integers(0, 365, n)), unit="D"),
        "label": label,
    })
    features = ["amount", "plan", "visits", "proxy"]
    train_rows, test_rows = list(range(480)), list(range(480, n))
    result = {
        "task": {"target": "label", "task_type": "binary"},
        "metric_plan": {"primary_metric": "roc_auc"},
        "problem_profile": {"task_type": "binary", "class_distribution": {"0": 240, "1": 240}},
        "holdout_plan": {"strategy": "temporal_future", "time_column": "as_of"},
        "model_development_plan": {"allowed_features": features, "excluded_features": [],
                                   "leakage_assessment": {"findings": []}, "time_column": "as_of",
                                   "group_column": "customer"},
        "split": {"train_source_rows": train_rows, "test_source_rows": test_rows},
        "candidates": [{"candidate_id": "base", "model_family": "majority", "status": "trained",
                        "cv_mean": {"roc_auc": 0.5}}],
        "best_single": {"candidate_id": "w", "model_family": "xgboost", "features": features,
                        "n_folds": 5, "cv_mean": {"roc_auc": 0.71}, "cv_std": {"roc_auc": 0.02},
                        "fold_metrics": [{"roc_auc": v} for v in (0.70, 0.72, 0.69, 0.73, 0.71)],
                        "test_metrics": {"roc_auc": 0.64}},
        "baseline_comparison": {"metric": "roc_auc", "baseline_candidate_id": "base"},
        "train_metrics": {"roc_auc": 0.74},
        "oof_evidence": _binary_oof(n=480),
        "test_metrics": {"roc_auc": 0.64}, "final_test_evaluation": {"metrics": {"roc_auc": 0.64}},
        "test_predictions": [{"row": 500, "prediction": 1, "score": 0.9}],
    }
    inp = SimpleNamespace(frame=frame, split_assignment=None, modeled_cols=features,
                          transformed_datetime=set(), entity_column=None, experiment=None)
    return inp, result


def holdout_scope_values(investigation: dict) -> tuple[set[float], set[str]]:
    """Distinctive numbers and timestamps under holdout-scoped keys of a stored investigation
    (the human view), minus any value the training-side evidence also carries."""

    numbers: set[float] = set()
    texts: set[str] = set()
    allowed: set[float] = set()

    def walk(value, scoped):
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, scoped or bool(HOLDOUT_KEY.search(str(key))))
        elif isinstance(value, list):
            for item in value:
                walk(item, scoped)
        elif isinstance(value, float) and value == value and abs(value) not in (0.0, 1.0) and value != int(value):
            (numbers if scoped else allowed).add(round(value, 6))
        elif isinstance(value, str) and scoped and value[:4].isdigit() and "T" in value:
            texts.add(value)

    for row in investigation.get("checks") or []:
        walk(row.get("evidence") or {}, False)
    return numbers - allowed, texts


def test_holdout_blind_every_finding_is_byte_identical_when_test_labels_and_results_change():
    from app.services.auto_train.persistence import _investigation

    inp, result = _run_inputs()
    before = json.dumps(_investigation(inp, result), sort_keys=True)
    statuses = {row["check"]: row["status"] for row in json.loads(before)["checks"]}
    for check in ("feature_drift", "temporal_shift", "missingness_shift", "contamination", "multicollinearity",
                  "calibration", "subgroup_gap", "fold_instability", "duplicate_rows"):
        assert statuses[check] != "not_evaluated", check
    test_mask = inp.frame[SOURCE_ROW_COLUMN] >= 480
    inp.frame.loc[test_mask, "label"] = 1 - inp.frame.loc[test_mask, "label"]  # every test label flipped
    for key in ("test_metrics", "final_test_evaluation"):
        result[key] = {"roc_auc": 0.999, "metrics": {"roc_auc": 0.999}}
    result["best_single"]["test_metrics"] = {"roc_auc": 0.999}
    result["test_predictions"] = [{"row": 500, "prediction": 0, "score": 0.01}]
    assert json.dumps(_investigation(inp, result), sort_keys=True) == before
    assert "0.64" not in before and "0.999" not in before


def test_agents_get_no_holdout_feature_statistic_even_from_a_label_proxy():
    from app.agents.tools.shaping import withhold_findings
    from app.services.auto_train.persistence import _investigation

    inp, result = _run_inputs()
    stored = _investigation(inp, result)
    test_rows = inp.frame[inp.frame[SOURCE_ROW_COLUMN] >= 480]
    negatives = round(float(test_rows["proxy"].isna().mean()), 6)  # = the test negative rate
    human = findings_read(uuid4(), stored)
    rows = {row["column"]: row for row in human.model_dump()["checks"][FINDING_CHECKS.index("missingness_shift")]
            ["evidence"]["holdout_comparison"]["columns"]}
    assert rows["proxy"]["holdout_missing_fraction"] == negatives  # people see it ...
    numbers, texts = holdout_scope_values(stored)
    assert negatives in numbers and texts  # ... and it is a holdout-scoped canary
    for body in (findings_read(uuid4(), stored, agent=True), withhold_findings(human)):
        text = json.dumps(body.model_dump(mode="json"))
        found = {float(v) for v in __import__("re").findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)}
        assert not {round(v, 6) for v in found} & numbers, sorted({round(v, 6) for v in found} & numbers)
        assert not [t for t in texts if t in text]
        assert not [k for k in _keys(body.model_dump()) if HOLDOUT_KEY.search(k)]
        by_check = {item.check: item for item in body.checks}
        for check in HOLDOUT_FEATURE_CHECKS:  # status, severity, recommendation kind stay; a fixed message
            assert by_check[check].message == AGENT_MESSAGES[check]
            assert by_check[check].status == next(c.status for c in human.checks if c.check == check)
        assert by_check["calibration"].message == next(c.message for c in human.checks if c.check == "calibration")


def _keys(value) -> list[str]:
    if isinstance(value, dict):
        return [str(k) for k in value] + [x for v in value.values() for x in _keys(v)]
    return [x for v in value for x in _keys(v)] if isinstance(value, list) else []


def test_legacy_duplicate_evidence_reads_with_holdout_names_and_is_stripped_for_agents():
    legacy = {"version": "investigate.v1", "checks": [{
        "check": "duplicate_rows", "status": "warning", "severity": "warning", "recommendation_kind": "deduplicate",
        "message_keys": ["duplicate_rows.across_split"],
        "evidence": {"feature_count": 3, "train_rows": 400, "test_rows": 100, "train_duplicate_rows": 0,
                     "train_test_duplicate_rows": 9, "train_test_duplicate_fraction": 0.09,
                     "expected_train_test_duplicate_rows": 0.4}}]}
    human = findings_read(uuid4(), legacy)
    assert human.checks[0].message.startswith("9 test rows (9.0%) are identical to a training row")
    agent = findings_read(uuid4(), legacy, agent=True).checks[0]
    assert agent.evidence == {"feature_count": 3, "train_rows": 400, "train_duplicate_rows": 0}
    assert agent.message == AGENT_MESSAGES["duplicate_rows"] and agent.status == "warning"


def test_same_inputs_give_the_same_findings_and_oof_evidence():
    first, second = _run_inputs(), _run_inputs()
    from app.services.auto_train.persistence import _investigation

    assert _investigation(*first) == _investigation(*second)
    assert _binary_oof(weak_level="s1") == _binary_oof(weak_level="s1")


def test_old_five_check_results_still_read_and_unknown_checks_are_ignored():
    stored = {"version": "investigate.v1", "checks": [
        {"check": check, "status": "pass", "severity": "info", "evidence": {}, "recommendation_kind": None,
         "message_keys": [f"{check}.pass"]} for check in FINDING_CHECKS[:5]]}
    body = findings_read(uuid4(), {**stored, "checks": [*stored["checks"], {"check": "future_check", "status": "fail",
                                                                              "severity": "error"}]})
    assert [item.check for item in body.checks] == list(FINDING_CHECKS[:5]) and body.summary.passed == 5
    full = findings_read(uuid4(), investigation_payload(investigate_result(_run_inputs()[1])))
    assert [item.check for item in full.checks] == list(FINDING_CHECKS) and all(item.message for item in full.checks)


def test_oof_summary_is_ignored_when_it_belongs_to_another_candidate():
    _inp, result = _run_inputs()
    result["oof_evidence"] = {**result["oof_evidence"], "candidate_id": "someone_else"}
    findings = {f.check: f for f in investigate_result(result)}
    assert findings["calibration"].evidence["not_evaluated_reason"] == "oof_missing"
    assert set(NEW_CHECKS) <= {f.check for f in findings.values()}


# --- the runner's out-of-fold hand-off --------------------------------------------------------------


def test_runner_hands_oof_evidence_exactly_the_winners_cv_validation_predictions(monkeypatch):
    """Mutation guard: evidence built from in-sample predictions (or other rows) fails here."""

    from app.engine.experiments import runner
    from app.engine.investigate import oof as oof_module
    from app.engine.types import SearchConfig, TaskSpec

    rng = np.random.default_rng(3)
    n = 260
    contract = rng.choice(["monthly", "yearly", "two_year"], n)
    frame = pd.DataFrame({"tenure": rng.integers(1, 72, n), "monthly": rng.uniform(20, 120, n), "contract": contract,
                          "churn": np.where(rng.binomial(1, np.where(contract == "monthly", 0.6, 0.15)) == 1,
                                            "Yes", "No")})
    seen: list[dict] = []
    original = oof_module.oof_evidence

    def spy(task_type, y, folds, pool, columns, **kwargs):
        seen.append({"y": np.asarray(y), "folds": [(np.asarray(ix), np.asarray(p)) for ix, p in folds],
                     "pool": pool.copy()})
        return original(task_type, y, folds, pool, columns, **kwargs)

    monkeypatch.setattr(oof_module, "oof_evidence", spy)
    task = TaskSpec(id="oof_handoff", name="t", task_type="binary", target="churn", entity_id=None,
                    prediction_time_column=None, evaluation_metric="roc_auc",
                    feature_groups={"features": ["tenure", "monthly", "contract"]}, validation_strategy="stratified",
                    column_roles={"numerical": ["tenure", "monthly"], "categorical": ["contract"]})
    config = SearchConfig(strategy="open_ingest", max_candidates=2, seed=42, families=["random_forest"])
    with tempfile.TemporaryDirectory() as tmp:
        result = runner.run_experiment(frame, task, config, artifact_dir=Path(tmp), dataset_version="v1")
    [call] = seen
    winner = result["best_single"]
    positions = np.concatenate([ix for ix, _p in call["folds"]])
    assert sorted(positions.tolist()) == list(range(len(call["pool"])))  # every pool row exactly once
    source = call["pool"][SOURCE_ROW_COLUMN].to_numpy()
    for (ix, pred), fold in zip(call["folds"], winner["folds"]):  # each fold's validation rows, in order
        assert sorted(source[ix].tolist()) == sorted(fold["validation_provenance"]) and len(pred) == len(ix)
    assert not set(source.tolist()) & set(result["split"]["test_source_rows"])  # no holdout row
    pooled = roc_auc_score(call["y"][positions], np.concatenate([p for _ix, p in call["folds"]]))
    assert abs(pooled - winner["cv_mean"]["roc_auc"]) < 0.05  # out-of-fold, like the CV mean ...
    assert result["train_metrics"]["roc_auc"] - pooled > 0.1  # ... not the in-sample fit
    assert result["oof_evidence"]["rows"] == len(call["pool"]) and result["oof_evidence"]["version"] == 3


# --- round 3 (review): invariants, null rates and survivors -------------------------------------


def test_holdout_marker_equals_the_checks_with_a_fixed_agent_message_and_holdout_comparison():
    from app.services.auto_train.persistence import _investigation

    stored = _investigation(*_run_inputs())
    scoped = {row["check"] for row in stored["checks"]
              if row["check"] in AGENT_MESSAGES and "holdout_comparison" in row["evidence"]}
    assert scoped == HOLDOUT_FEATURE_CHECKS


def test_every_template_naming_test_row_numbers_belongs_to_a_check_with_a_fixed_agent_message():
    import string

    from app.domain.findings import MESSAGE_TEMPLATES
    from app.services.auto_train.persistence import _investigation

    stored = _investigation(*_run_inputs())
    comparison = {key for row in stored["checks"] for key in row["evidence"].get("holdout_comparison") or {}}
    assert {"max_psi", "shifted_columns", "holdout_start", "near_duplicate_holdout_rows"} <= comparison
    offenders = []
    for key, template in MESSAGE_TEMPLATES.items():
        fields = {name.removesuffix("_pct") for _text, name, _spec, _conv in string.Formatter().parse(template) if name}
        scoped = {name for name in fields if HOLDOUT_KEY.search(name) or name in comparison}
        if scoped and key.split(".")[0] not in AGENT_MESSAGES:
            offenders.append((key, sorted(scoped)))
    assert not offenders, offenders


def _predictive_segment(seed: int, n: int = 3000) -> dict:
    """A categorical column that predicts the outcome (its own base rate per level) and a model
    that is equally good inside every level: the pooled AUC is higher than any within-level AUC."""

    rng = np.random.default_rng(seed)
    segment = rng.choice(["low", "mid", "high"], n)
    offset = np.select([segment == "low", segment == "mid"], [-1.5, 0.0], 1.5)
    p = 1 / (1 + np.exp(-(offset + 1.2 * rng.normal(size=n))))
    y = (rng.uniform(size=n) < p).astype(int)
    folds = [(ix, p[ix]) for ix in _folds(n)]
    return oof_evidence("binary", y, folds, pd.DataFrame({"segment": segment}), ["segment"], primary_metric="roc_auc",
                        candidate_id="w")


def test_subgroup_gap_compares_groups_with_their_column_average_not_the_pooled_auc():
    findings = [check_subgroup_gap(replace(BASE, oof=_predictive_segment(seed))) for seed in range(40)]
    assert [f.status for f in findings].count("warning") == 0
    first = findings[0].evidence
    assert first["overall_score"] - first["reference_score"] > 0.05  # pooled AUC > every group's own


def _rare_positives(seed: int, n: int = 3000) -> dict:
    rng = np.random.default_rng(seed)
    p = rng.uniform(0.0, 0.08, n)
    y = (rng.uniform(size=n) < p).astype(int)
    pred = np.clip(p + rng.normal(0, 0.02, n), 0.001, 0.999)
    frame = pd.DataFrame({"a": rng.choice(list("abcdefgh"), n, p=[0.3, 0.3, 0.2, 0.08, 0.04, 0.03, 0.03, 0.02]),
                          "b": rng.choice(list("abcdefghij"), n)})
    return oof_evidence("binary", y, [(ix, pred[ix]) for ix in _folds(n)], frame, ["a", "b"], primary_metric="roc_auc",
                        candidate_id="w")


def test_subgroup_gap_null_rate_with_rare_positives_needs_the_class_row_floor():
    # Mutation guard: SUBGROUP_MIN_CLASS_ROWS = 1 scores groups with one or two positives -> ~17%.
    statuses = [check_subgroup_gap(replace(BASE, oof=_rare_positives(seed))).status for seed in range(100, 200)]
    assert statuses.count("warning") <= 5, statuses.count("warning")


def test_subgroup_gap_counts_skipped_groups_and_names_too_few_positives():
    finding = check_subgroup_gap(replace(BASE, oof=_rare_positives(1)))
    assert finding.evidence["groups_skipped"] >= 1 and "could not be scored (too few rows" in finding.message
    rng = np.random.default_rng(2)
    n = 400
    y = (rng.uniform(size=n) < 0.01).astype(int)  # about 4 positives in all: no group can rank
    summary = oof_evidence("binary", y, [(ix, rng.uniform(size=len(ix))) for ix in _folds(n)],
                           pd.DataFrame({"seg": rng.choice(["a", "b", "c"], n)}), ["seg"], primary_metric="roc_auc")
    assert check_subgroup_gap(replace(BASE, oof=summary)).evidence["not_evaluated_reason"] == "too_few_class_rows"


def _regression_null(seed: int, n: int = 400) -> dict:
    """Small groups (~65-100 rows) and heavy-tailed errors: chance MAE gaps often exceed 25%."""

    rng = np.random.default_rng(seed)
    y = rng.normal(50, 10, n)
    pred = y + rng.lognormal(0, 1.5, n) * rng.choice([-1, 1], n)
    frame = pd.DataFrame({"four": rng.choice(list("abcd"), n), "six": rng.choice(list("abcdef"), n)})
    return oof_evidence("regression", y, [(ix, pred[ix]) for ix in _folds(n)], frame, list(frame.columns),
                        primary_metric="mae", candidate_id="w")


def test_subgroup_gap_regression_null_rate_is_at_most_five_percent():
    # Mutation guard: a regression group standard error 10x too small makes chance gaps significant.
    ev = replace(BASE, task_type="regression", primary_metric="mae")
    statuses = [check_subgroup_gap(replace(ev, oof=_regression_null(seed))).status for seed in range(60)]
    assert statuses.count("warning") <= 3, statuses.count("warning")


def _calibrated(seed: int) -> dict:
    rng = np.random.default_rng(seed)
    n = int(rng.integers(100, 401))
    p = rng.uniform(0.02, 0.98, n)
    y = (rng.uniform(size=n) < p).astype(int)
    return oof_evidence("binary", y, [(ix, p[ix]) for ix in _folds(n)], pd.DataFrame(index=range(n)), [],
                        primary_metric="roc_auc", candidate_id="w")


def test_calibration_null_rate_at_small_sizes_needs_the_noise_bound():
    # Mutation guard: ECE_NOISE_MULTIPLE = 0 warns on most perfectly calibrated runs of 100-400 rows.
    statuses = [check_calibration(replace(BASE, oof=_calibrated(seed))).status for seed in range(60)]
    assert statuses.count("warning") <= 3, statuses.count("warning")


def _fold_regression(seed: int, metric: str, n: int = 200) -> RunEvidence:
    from app.engine.evaluation.metrics import selection_metric_value

    rng = np.random.default_rng(seed)
    y = rng.normal(50, 10, n)
    pred = y + rng.lognormal(0, 1, n) * rng.choice([-1, 1], n)  # heavy-tailed errors
    folds = [(ix, pred[ix]) for ix in _folds(n)]
    summary = oof_evidence("regression", y, folds, pd.DataFrame(index=range(n)), [], primary_metric=metric)
    scores = tuple({metric: selection_metric_value(metric, y[ix], p, task_type="regression")} for ix, p in folds)
    return RunEvidence(task_type="regression", primary_metric=metric, winner_fold_metrics=scores, oof=summary)


@pytest.mark.parametrize("metric", ["mae", "rmse", "r2"])
def test_fold_instability_regression_null_rate_is_at_most_five_percent(metric):
    findings = [check_fold_instability(_fold_regression(seed, metric)) for seed in range(60)]
    assert [f.status for f in findings].count("warning") <= 3
    assert findings[0].evidence["fold_noise_std"] and findings[0].evidence["fold_rows"] == 40


def test_temporal_boundary_ties_are_not_time_travel():
    # The latest training day is also the first test day: tied rows are not earlier (te < end).
    tied = check_temporal_shift(_timed(np.arange(0, 300), np.arange(299, 379)), "temporal_future")
    assert tied.status == "pass" and tied.evidence["holdout_comparison"]["holdout_rows_before_train_end_fraction"] == 0


def test_temporal_shift_reads_day_first_dates():
    days = pd.Timestamp("2024-01-01") + pd.to_timedelta(np.arange(120), unit="D")
    text = pd.Series(days.strftime("%d/%m/%Y"))  # 01/01/2024 ... 29/04/2024: month-first fails after the 12th
    split = _split(n_train=90, n_test=30, time_column="as_of", train_times=text[:90], test_times=text[90:])
    finding = check_temporal_shift(split, "temporal_future")
    assert finding.status == "pass" and finding.evidence["train_end"].startswith("2024-03-30")


def test_contamination_cannot_call_reentered_records_clean_on_a_random_split():
    rng = np.random.default_rng(12)
    base = pd.DataFrame({"amount": rng.normal(100, 30, 1700), "plan": rng.choice(list("abc"), 1700),
                         "email": [f"user{i}@example.com" for i in range(1700)]})
    again = base.sample(n=300, random_state=1).assign(amount=lambda f: f["amount"] * (1 + 1e-5))
    rows = pd.concat([base, again], ignore_index=True).sample(frac=1.0, random_state=2).reset_index(drop=True)
    finding = check_contamination(SplitFeatures(train=rows.iloc[:1600], test=rows.iloc[1600:]), "random")
    # The rounded columns identify rows (an e-mail per record): re-entered records, a warning.
    assert (finding.status, finding.recommendation_kind) == ("warning", "deduplicate")
    assert finding.message_keys[0] == "contamination.near_copies_dataset" and finding.evidence[
        "normalized_columns_identify_rows"]
    assert "No contamination" not in finding.message and "assuming they vary independently" in finding.message
    # Coarse columns (combinations ~ rows) repeat naturally: a pass that says it cannot rule copies out.
    coarse_rng = np.random.default_rng(3)
    coarse = pd.DataFrame({"age": coarse_rng.normal(40, 3, 2000), "plan": coarse_rng.choice(list("abc"), 2000),
                           "region": coarse_rng.choice(list("abcd"), 2000)})
    natural = check_contamination(SplitFeatures(train=coarse.iloc[:1600], test=coarse.iloc[1600:]), "temporal_future")
    assert natural.status == "pass" and natural.message_keys[0] == "contamination.natural_repeats"
    assert "No contamination" not in natural.message and "random split" not in natural.message


def test_split_checks_say_when_counts_come_from_a_sample_and_name_unreadable_columns(monkeypatch):
    monkeypatch.setattr(split_checks, "SPLIT_MAX_ROWS", 300)
    finding = check_missingness_shift(_split(n_train=900, n_test=400))
    assert finding.evidence["sampled"] and "split_checks.sampled" in finding.message_keys
    assert "seeded sample of 300 rows" in finding.message
    empty = SplitFeatures(train=pd.DataFrame({"x": [1.0] * 100}), test=pd.DataFrame({"x": [np.nan] * 40}))
    assert check_feature_drift(empty).evidence["not_evaluated_reason"] == "no_readable_columns"


def test_multicollinearity_on_wide_data_compares_pairs_only_and_caps_column_lists():
    rng = np.random.default_rng(3)
    wide = check_multicollinearity(pd.DataFrame(rng.normal(size=(80, 120)), columns=[f"x{i:03d}" for i in range(120)]))
    assert wide.evidence["pairs_only"] and not wide.evidence["rank_deficient"]
    assert "multicollinearity.exact" not in wide.message_keys and "multicollinearity.pairs_only" in wide.message_keys
    base = pd.DataFrame(rng.normal(size=(500, 3)), columns=["a", "b", "c"])
    many = base.assign(**{f"s{i:02d}": base["a"] + base["b"] * i for i in range(30)})  # 30 exact combinations
    finding = check_multicollinearity(many)
    assert finding.evidence["dependent_count"] > 20 and len(finding.evidence["dependent_columns"]) == 20


def test_new_feature_pairs_folds_by_index_when_one_score_is_missing():
    child, parent = _branch([0.97, 0.98, 0.96, 0.97, 0.97], [0.79, 0.81, 0.80, 0.78, 0.82])
    holed = replace(child, winner_fold_metrics=({"roc_auc": float("nan")}, *child.winner_fold_metrics[1:]))
    finding = check_new_feature(holed, parent)
    assert finding.evidence["paired_folds"] == 4 and finding.status == "fail"


def test_a_group_disjoint_holdout_is_checked_on_its_own_group_column():
    from app.services.auto_train.persistence import _investigation

    inp, result = _run_inputs()
    inp.frame["account"] = [f"a{i % 50}" for i in range(len(inp.frame))]
    result["holdout_plan"] = {"strategy": "group_disjoint", "group_column": "account"}
    stored = _investigation(inp, result)
    contamination = next(row for row in stored["checks"] if row["check"] == "contamination")
    assert contamination["evidence"]["group_column"] == "account"  # not the CV plan's "customer"
    assert contamination["status"] == "fail"  # this toy split shares accounts across the planned group split


def test_agent_messages_render_from_stripped_evidence_even_for_a_future_template(monkeypatch):
    """SF2 guard: a template outside AGENT_MESSAGES that someday names a holdout number renders
    "n/a" for agents (the REST route and the tools render from stripped evidence)."""

    from app.domain import findings as domain

    monkeypatch.setitem(domain.MESSAGE_TEMPLATES, "calibration.pass", "Leaky future template: {holdout_rows} test rows.")
    stored = {"version": "investigate.v1", "checks": [{
        "check": "calibration", "status": "pass", "severity": "info", "recommendation_kind": None,
        "message_keys": ["calibration.pass"], "evidence": {"rows": 400, "holdout_rows": 777}}]}
    assert "777" in findings_read(uuid4(), stored).checks[0].message
    agent = findings_read(uuid4(), stored, agent=True).checks[0]
    assert "777" not in agent.message and "n/a test rows" in agent.message and "holdout_rows" not in agent.evidence


# --- round 4 (review): single time reading, re-entered records, fold spread, wide data --------------


def _dayfirst_hourly() -> tuple[pd.Series, np.ndarray]:
    true = pd.date_range("2023-11-15", "2023-12-05 23:00", freq="h")
    text = pd.Series(true.strftime("%d/%m/%Y %H:%M"))
    train = np.zeros(len(text), dtype=bool)
    train[: int(0.8 * len(text))] = True  # sorted file, the last 20% is the test period (1-5 Dec)
    return text, train


def test_temporal_shift_reads_both_sides_with_one_date_format():
    # Read separately, the test side (01/12 .. 05/12) parses month-first and invents an overlap.
    text, train = _dayfirst_hourly()
    dummy = pd.DataFrame({"x": np.zeros(len(text))})
    whole = SplitFeatures(train=dummy[train], test=dummy[~train], time_column="ts", train_times=text[train],
                          test_times=text[~train], all_times=text, time_masks=(train, ~train))
    sides = replace(whole, all_times=None, time_masks=None)
    for split in (whole, sides):
        finding = check_temporal_shift(split, "temporal_future")
        assert finding.status == "pass", finding.evidence
        assert finding.evidence["holdout_comparison"]["holdout_start"].startswith("2023-12-01")
    garbled = replace(whole, all_times=pd.Series(list(text[train]) + ["not a date"] * int((~train).sum())))
    assert check_temporal_shift(garbled, "temporal_future").evidence["not_evaluated_reason"] == "time_unparsable"


def test_contamination_names_a_small_significant_excess_neutrally():
    rng = np.random.default_rng(21)
    rows = pd.DataFrame({"amount": rng.normal(100, 30, 20000), "email": [f"u{i}@example.com" for i in range(20000)]})
    train, test = rows.iloc[:16000].reset_index(drop=True), rows.iloc[16000:].reset_index(drop=True)
    copies = train.sample(n=25, random_state=4).assign(email=lambda f: f["email"].str.upper())
    test = pd.concat([test.iloc[:-25], copies], ignore_index=True)  # ~0.5% near copies (80% inside the reference)
    finding = check_contamination(SplitFeatures(train=train, test=test), "random")
    comparison = finding.evidence["holdout_comparison"]
    assert 0 < comparison["excess_near_duplicate_holdout_fraction"] < 0.01 and comparison["near_duplicate_holdout_z"] >= 3
    assert (finding.status, finding.message_keys[0]) == ("pass", "contamination.small_excess")
    assert "No contamination" not in finding.message and "under the 1-point threshold" in finding.message


def _legitimate(shape: str, rng: np.random.Generator, n: int) -> pd.DataFrame:
    """The review's legitimate data shapes: no copies anywhere, many natural near-repeats."""

    if shape == "sensors":
        t = rng.normal(20, 5, n)
        return pd.DataFrame({"s1": t + rng.normal(0, .5, n), "s2": t + rng.normal(0, .5, n),
                             "s3": t + rng.normal(0, .5, n), "site": rng.choice(list("abcd"), n)})
    if shape == "price_tax":
        price = np.round(rng.lognormal(3, 1, n), 2)
        return pd.DataFrame({"price": price, "price_with_tax": np.round(price * 1.2, 2),
                             "category": rng.choice([f"c{i}" for i in range(10)], n), "qty": rng.integers(1, 11, n)})
    if shape == "latlon":
        city = rng.integers(0, 50, n)
        lat, lon = rng.uniform(25, 48, 50), rng.uniform(-120, -70, 50)
        return pd.DataFrame({"lat": lat[city] + rng.normal(0, .05, n), "lon": lon[city] + rng.normal(0, .05, n),
                             "age": rng.integers(18, 81, n), "plan": rng.choice(list("abc"), n)})
    if shape == "onehot":
        data: dict[str, object] = {"amount": rng.lognormal(4, .6, n)}
        for name, k in (("a", 5), ("b", 4), ("c", 3)):
            level = rng.integers(0, k, n)
            data.update({f"{name}_{i}": (level == i).astype(int) for i in range(k)})
        return pd.DataFrame(data)
    region = rng.integers(0, 4, n)
    state = region * 3 + rng.integers(0, 3, n)
    return pd.DataFrame({"region": region.astype(str), "state": state.astype(str),
                         "city": (state * 2 + rng.integers(0, 2, n)).astype(str),
                         "segment": rng.choice(list("abcde"), n), "channel": rng.choice(list("xyz"), n),
                         "tier": rng.choice(list("pq"), n), "device": rng.choice(list("mdt"), n),
                         "age": rng.normal(40, 12, n)})


@pytest.mark.parametrize("shape", ["sensors", "price_tax", "latlon", "onehot", "hierarchy"])
def test_contamination_never_calls_legitimate_repeats_dataset_copies(shape):
    for seed in range(3):
        rng = np.random.default_rng(seed)
        rows = _legitimate(shape, rng, 5000)
        order = rng.permutation(len(rows))
        train, test = rows.iloc[order[:4000]].reset_index(drop=True), rows.iloc[order[4000:]].reset_index(drop=True)
        finding = check_contamination(SplitFeatures(train=train, test=test), "random")
        assert "contamination.near_copies_dataset" not in finding.message_keys, (shape, seed, finding.evidence)
        assert finding.status == "pass", (shape, seed, finding.message_keys)


@pytest.mark.parametrize("n, share", [(2000, 0.1), (5000, 0.2)])
def test_contamination_still_warns_on_reentered_records(n, share):
    warned = 0
    for seed in range(20):
        rng = np.random.default_rng(seed)
        rows = pd.DataFrame({"amount": rng.lognormal(5, 1, n), "score": rng.normal(600, 80, n),
                             "ratio": rng.uniform(0, 1, n), "plan": rng.choice(["a", "b", "c"], n)})
        pick = rng.choice(n, int(share * n), replace=False)
        again = rows.iloc[pick].copy()
        for column in ("amount", "score", "ratio"):
            again[column] = again[column] * (1 + rng.normal(0, 1e-5, len(again)))
        rows = pd.concat([rows, again], ignore_index=True)
        order = rng.permutation(len(rows))
        cut = int(0.8 * len(rows))
        finding = check_contamination(SplitFeatures(train=rows.iloc[order[:cut]].reset_index(drop=True),
                                                    test=rows.iloc[order[cut:]].reset_index(drop=True)), "random")
        warned += finding.status == "warning" and finding.recommendation_kind == "deduplicate"
    assert warned == 20


def test_temporal_shift_reads_mixed_utc_offsets():
    stamps = pd.date_range("2024-01-01", periods=120, freq="D", tz="UTC")
    text = pd.Series([ts.tz_convert("Europe/Paris" if i % 2 else "America/New_York").isoformat()
                      for i, ts in enumerate(stamps)])  # alternating offsets
    split = _split(n_train=90, n_test=30, time_column="as_of", train_times=text[:90], test_times=text[90:])
    finding = check_temporal_shift(split, "temporal_future")
    assert finding.status == "pass", finding.evidence


def test_multicollinearity_tests_exact_dependence_between_p_plus_1_and_2p_rows():
    rng = np.random.default_rng(5)
    frame = pd.DataFrame(rng.normal(size=(50, 40)), columns=[f"x{i:02d}" for i in range(40)])
    frame["total"] = frame["x00"] + frame["x01"] + frame["x02"]  # 41 columns, 50 rows
    finding = check_multicollinearity(frame)
    assert finding.evidence["pairs_only"] and finding.evidence["rank_tested"]
    assert finding.status == "warning" and finding.message_keys[0] == "multicollinearity.exact"
    assert set(finding.evidence["dependent_columns"]) == {"x00", "x01", "x02", "total"}


def _r2_null(seed: int, n: int = 200, signal: float = 2.0, layout: str = "equal") -> RunEvidence:
    from app.engine.evaluation.metrics import selection_metric_value

    rng = np.random.default_rng(seed)
    x = rng.normal(size=n)
    y, p = signal * x + rng.normal(size=n), signal * x  # R2 ~ 0.8
    order = rng.permutation(n)
    folds = ([order[i::5] for i in range(5)] if layout == "equal"
             else np.split(order, (np.array([0.24, 0.48, 0.72, 0.96]) * n).astype(int)))  # one fold at 4%
    summary = oof_evidence("regression", y, [(ix, p[ix]) for ix in folds], pd.DataFrame(index=range(n)), [],
                           primary_metric="r2")
    scores = tuple({"r2": selection_metric_value("r2", y[ix], p[ix], task_type="regression")} for ix in folds)
    return RunEvidence(task_type="regression", primary_metric="r2", winner_fold_metrics=scores, oof=summary)


@pytest.mark.parametrize("n, layout", [(200, "equal"), (1000, "one_small")])
def test_fold_instability_r2_null_rate_at_r2_08_and_with_a_tiny_fold(n, layout):
    statuses = [check_fold_instability(_r2_null(seed, n=n, layout=layout)).status for seed in range(100)]
    assert statuses.count("warning") <= 5, statuses.count("warning")
    # One fold of 8 rows (4% of 200): too small to judge, never a warning.
    tiny = check_fold_instability(_r2_null(0, n=200, layout="one_small"))
    assert tiny.evidence["not_evaluated_reason"] == "fold_too_small" and tiny.evidence["smallest_fold_rows"] == 8


def test_fold_instability_regression_without_a_stored_chance_spread_is_not_evaluated():
    ev = replace(_fold_regression(0, "mae"), oof={})
    assert check_fold_instability(ev).evidence["not_evaluated_reason"] == "chance_spread_missing"


@pytest.mark.parametrize("ratio", [1.05, 1.2, 1.5])
def test_multicollinearity_does_not_warn_on_independent_columns_just_above_rows_equal_columns(ratio):
    statuses = []
    for seed in range(10):
        rng = np.random.default_rng(seed)
        n = int(40 * ratio)
        frame = pd.DataFrame(rng.normal(size=(n, 40)), columns=[f"x{i:02d}" for i in range(40)])
        finding = check_multicollinearity(frame)
        statuses.append(finding.status)
        assert finding.evidence["pairs_only"]
    assert statuses.count("warning") == 0, statuses


def test_subgroup_gap_counts_a_perfectly_ranked_group_and_names_columns_it_cannot_compare():
    rng = np.random.default_rng(0)
    n = 3000
    segment = rng.choice(list("abc"), n)
    y = (rng.uniform(size=n) < 0.3).astype(int)
    p = np.where(segment == "a", 0.5 + 0.4 * y + rng.uniform(-0.05, 0.05, n),
                 1 / (1 + np.exp(-(rng.normal(size=n) + (2 * y - 1)))))  # level "a": AUC 1.0
    rare = np.where(rng.uniform(size=n) < 0.03, "rare", "common")
    rare[(rare == "rare") & (y == 1)] = "common"  # the rare level has no positives: column not comparable
    summary = oof_evidence("binary", y, [(ix, p[ix]) for ix in _folds(n)],
                           pd.DataFrame({"segment": segment, "flag": rare}), ["segment", "flag"], primary_metric="roc_auc")
    groups = summary["subgroups"]["columns"][1]["groups"]  # "segment" (sorted after "flag")
    assert all(g["se"] > 0 for g in groups) and len(groups) == 3  # AUC 1.0 keeps a floored error
    finding = check_subgroup_gap(replace(BASE, oof=summary))
    assert finding.evidence["groups_tested"] == 3 and finding.evidence["columns_not_compared"] == ["flag"]
    assert finding.evidence["groups_skipped"] >= 1 and "flag" in finding.message
