"""P7.4-A: monitoring windows, drift (PSI) and real performance when outcomes arrive.

Engine math is checked against hand-computed values (half-count smoothing, G-test); a seeded
no-drift simulation shows noise does not raise alarms; the end to end runs a real auto-train,
real scoring jobs and the real ``monitoring.compute_window`` worker job on a planted-drift file.
The reference is the training partition and out-of-fold CV scores only; outcomes are real labels
of scored rows, never the final test set.
"""

from __future__ import annotations

import ast
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
import pytest
from scipy.stats import chi2
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import get_settings
from app.db.models import (
    BatchPrediction,
    MlJob,
    ModelRelease,
    ModelVersion,
    MonitoringOutcomeSet,
    MonitoringWindow,
    ProjectDecisionRecord,
    WorkspaceRole,
)
from app.domain import monitoring as dm
from app.domain.errors import MonitoringError
from app.engine.serving import monitoring as mon
from app.services import monitoring_service as ms
from app.services.artifact_service import read_artifact_bytes
from app.services.ml_job_service import process_next_job
from app.services.service_token_service import create_service_token
from test_decision_record_service import _bootstrap, g  # noqa: F401 (fixture)
from test_v1_batch_predictions import _frame, _predict, _trained, _upload, _work
from test_v1_contract_conventions import _assert_envelope
from test_v1_resources_experiments import _h, _key, _member, setup  # noqa: F401 (fixture)

API = Path(__file__).resolve().parents[1] / "app"
MONITORING_MODULES = (API / "engine/serving/monitoring.py", API / "services/monitoring_service.py",
                      API / "domain/monitoring.py", API / "api/v1_monitoring.py")


def _smoothed(counts):
    counts = np.asarray(counts, dtype=float)
    return (counts + 0.5) / (counts.sum() + 0.5 * len(counts))


def _hand_psi(expected, actual) -> float:
    e, a = _smoothed(expected), _smoothed(actual)
    return float(sum((x - y) * math.log(x / y) for x, y in zip(a, e)))


def _hand_g_p(expected, actual) -> float:
    """G = 2 * sum(O ln(O / E)) over the 2 x k table (empty columns dropped), df = k - 1."""

    table = [[e, a] for e, a in zip(expected, actual) if e + a > 0]
    rows = [sum(col[0] for col in table), sum(col[1] for col in table)]
    total = sum(rows)
    g_stat = 0.0
    for col in table:
        for side in (0, 1):
            observed = col[side]
            if observed:
                g_stat += 2 * observed * math.log(observed / (rows[side] * sum(col) / total))
    return float(chi2.sf(g_stat, len(table) - 1))


# --- PSI and buckets (pure) -------------------------------------------------------------------


def test_psi_on_counts_matches_hand_computed_values_with_half_count_smoothing():
    assert mon.psi([50, 50], [25, 75]) == pytest.approx(_hand_psi([50, 50], [25, 75]))
    # An empty bucket gets half a count on both sides, never a fixed floor or a division by zero.
    assert mon.psi([100, 0], [90, 10]) == pytest.approx(
        (90.5 / 101 - 100.5 / 101) * math.log(90.5 / 100.5) + (10.5 / 101 - 0.5 / 101) * math.log(10.5 / 0.5))
    assert mon.psi([20, 30, 50], [20, 30, 50]) == 0.0 and mon.psi([2, 3, 5], [20, 30, 50]) == pytest.approx(
        _hand_psi([2, 3, 5], [20, 30, 50]))
    assert [mon.psi_status(v) for v in (0.05, 0.10, 0.19, 0.20)] == ["ok", "watch", "watch", "alert"]
    assert mon.gated_status(0.5, 0.2, 0.01) == "ok" and mon.gated_status(0.5, 0.001, 0.01) == "alert"
    assert mon.p_value([50, 50], [25, 75]) == pytest.approx(_hand_g_p([50, 50], [25, 75]))
    assert mon.p_value([10, 0], [10, 0]) == 1.0
    with pytest.raises(ValueError):
        mon.psi([5, 5], [10])


def test_compare_inputs_equals_hand_computed_psi_and_g_test_and_tiny_samples_never_count():
    training = pd.DataFrame({"x": np.arange(1.0, 11.0), "c": ["a"] * 5 + ["b"] * 3 + [None] * 2})
    ref = mon.build_reference(training, numeric=["x"], categorical=["c"], task_type="regression", oof_scores=[],
                              threshold=None, class_labels=None)
    x_ref = next(f for f in ref["features"] if f["name"] == "x")
    assert x_ref["counts"] == [1] * 10 + [0]  # ten one-value bins, no missing
    c_ref = next(f for f in ref["features"] if f["name"] == "c")
    # "b" (3 rows) is below the 5-row minimum for a named bucket: it is part of "other".
    assert c_ref["categories"] == ["a"] and c_ref["counts"] == [5, 3, 2]
    window = pd.DataFrame({"x": [1.0] * 5 + [10.0] * 5, "c": ["a"] * 4 + ["b"] * 4 + ["new"] * 2})
    drift = mon.compare_inputs(window, ref)
    rows = {row["name"]: row for row in drift["features"]}
    x_window = [5] + [0] * 8 + [5] + [0]
    assert rows["x"]["psi"] == pytest.approx(round(_hand_psi(x_ref["counts"], x_window), 4))
    assert rows["x"]["p_value"] == pytest.approx(round(_hand_g_p(x_ref["counts"], x_window), 6), abs=1e-6)
    assert rows["c"]["psi"] == pytest.approx(round(_hand_psi([5, 3, 2], [4, 6, 0]), 4))
    assert rows["c"]["p_value"] == pytest.approx(round(_hand_g_p([5, 3, 2], [4, 6, 0]), 6), abs=1e-6)
    assert rows["c"]["top_change"] == {"bucket": "other", "value": None, "share": 0.6, "share_reference": 0.3}
    assert rows["x"]["mean"] == 5.5 and rows["x"]["mean_reference"] == 5.5
    # A large PSI from 10 rows is not statistically clear (Bonferroni over 2 features): it never counts.
    assert rows["x"]["psi"] > 0.2 and not rows["x"]["significant"] and rows["x"]["status"] == "ok"
    assert drift["significance"]["per_feature_alpha"] == pytest.approx(0.005)
    assert drift["status_counts"] == {"alert": 0, "watch": 0, "ok": 2}


def test_numeric_reference_bins_constant_columns_and_missing_bucket():
    values = pd.Series(np.arange(1, 101, dtype=float))
    ref = mon.numeric_reference(values)
    assert len(ref["edges"]) == 9 and ref["counts"] == [10] * 10 + [0]
    assert mon.psi(ref["counts"], mon.numeric_counts(values.to_numpy(), ref["edges"])) == pytest.approx(0.0)
    with_missing = values.astype(object).where(values > 20, None)  # 20% missing
    counts = mon.numeric_counts(mon._numeric(with_missing), ref["edges"])
    assert counts[-1] == 20 and counts[0] == 0 and mon.psi(ref["counts"], counts) > 0.2
    # Ties collapse edges and a bin without training values is merged into its neighbour.
    tied = mon.numeric_reference(pd.Series([1.0] * 95 + [2.0] * 5))
    assert tied["edges"] == [1.0] and tied["counts"] == [95, 5, 0]
    # A constant column keeps an edge at its value, so a move above it shows.
    constant = mon.numeric_reference(pd.Series([3.0] * 100))
    assert constant["edges"] == [3.0] and constant["counts"] == [100, 0, 0]
    moved = mon.numeric_counts(np.full(100, 4.0), constant["edges"])
    assert moved == [0, 100, 0] and mon.psi(constant["counts"], moved) > 1.0
    assert mon.numeric_reference(pd.Series([np.nan, np.inf]))["counts"] == [0, 2]


def test_categorical_reference_minimum_count_other_unseen_missing_and_label_caps():
    long = "x" * 80
    series = pd.Series(["a"] * 50 + ["b"] * 30 + [f"c{i}" for i in range(12)] + [None] * 4 + [long] * 4 + [1.0, 1],
                       dtype=object)
    ref = mon.categorical_reference(series)
    # Named buckets need 5 rows and 1% of rows: a and b; the rest (incl. the 80-character value) is "other".
    assert ref["categories"] == ["a", "b"] and ref["counts"] == [50, 30, 18, 4]
    assert mon.categorical_counts(pd.Series(["a", "new", "new", None], dtype=object), ref["categories"]) == [1, 0, 2, 1]
    assert mon.category_label("a\x00b\n") == "ab" and mon.category_label(3.0) == "3"
    assert mon.category_label(float("nan")) is None and mon.category_label(np.int64(7)) == "7"
    assert len(mon.category_label(long)) == mon.LABEL_MAX
    counts, missing = mon._label_counts(pd.Series([1.0, 1, "1", None], dtype=object))  # one label "1"
    assert counts == {"1": 3} and missing == 1


def test_reference_keeps_the_most_important_features_when_there_are_too_many(monkeypatch):
    monkeypatch.setattr(mon, "REFERENCE_FEATURES_MAX", 2)
    frame = pd.DataFrame({name: np.arange(20.0) for name in ("a", "b", "c", "d")})
    ref = mon.build_reference(frame, numeric=list(frame.columns), categorical=[], task_type="regression",
                              oof_scores=[], threshold=None, class_labels=None, priority=["d", "b"])
    assert [f["name"] for f in ref["features"]] == ["b", "d"]  # by CV importance, kept in column order
    assert (ref["features_total"], ref["features_not_monitored"]) == (4, 2)
    assert mon.compare_inputs(frame, ref)["features_not_monitored"] == 2
    plain = mon.build_reference(frame, numeric=list(frame.columns), categorical=[], task_type="regression",
                                oof_scores=[], threshold=None, class_labels=None)
    assert [f["name"] for f in plain["features"]] == ["a", "b"]  # no importance: by order


def _population(rng, rows: int, *, numeric: int = 50, categorical: int = 50, shift: bool = False) -> pd.DataFrame:
    data = {}
    for i in range(numeric):
        values = rng.normal(i % 7, 1 + i % 3, rows) if i % 2 else rng.poisson(3 + i % 5, rows).astype(float)
        data[f"n{i}"] = values + (3.0 if shift and i == 0 else 0.0)
    levels = [f"l{j}" for j in range(8)]
    weights = np.array([0.3, 0.2, 0.15, 0.12, 0.1, 0.08, 0.04, 0.01])
    for i in range(categorical):
        data[f"c{i}"] = rng.choice(levels, rows, p=weights)
    frame = pd.DataFrame(data)
    frame.loc[rng.random(rows) < 0.05, "n1"] = np.nan
    if shift:
        frame["c0"] = rng.choice(["new"] * 3 + levels[:1], rows)
    return frame


def test_no_drift_windows_of_500_rows_stay_ok_and_never_alert():
    """Seeded simulation (B2): 50 numeric + 50 categorical features, 2,000 training rows, 20 windows
    of 500 rows from the same population. Raw PSI over many features would flag most windows; the
    smoothed PSI plus the Bonferroni G-test keeps them ok, and a planted shift still alerts."""

    rng = np.random.default_rng(7)
    training = _population(rng, 2000)
    names = list(training.columns)
    ref = mon.build_reference(training, numeric=[n for n in names if n.startswith("n")],
                              categorical=[n for n in names if n.startswith("c")], task_type="binary",
                              oof_scores=[rng.uniform(0, 1, 2000)], threshold=0.5, class_labels=None)
    statuses = []
    for seed in range(20):
        window_rng = np.random.default_rng(1000 + seed)
        drift = mon.compare_inputs(_population(window_rng, 500), ref)
        scores = mon.compare_predictions(pd.DataFrame({"probability": window_rng.uniform(0, 1, 500)}),
                                         ref["prediction"], min_rows=500)
        statuses.append(mon.window_verdict(500, drift, scores)[0])
    assert "alert" not in statuses and statuses.count("ok") >= 19, statuses
    planted = mon.compare_inputs(_population(np.random.default_rng(99), 500, shift=True), ref)
    flagged = {row["name"] for row in planted["features"] if row["status"] == "alert"}
    assert {"n0", "c0"} <= flagged and mon.window_verdict(500, planted, {"available": False})[0] == "alert"


def _reference(rows: int = 2000, seed: int = 0) -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame({"tenure": rng.normal(30, 8, rows), "plan": rng.choice(["basic", "plus", "pro"], rows)})
    oof = [rng.uniform(0, 1, rows // 2), rng.uniform(0, 1, rows - rows // 2)]
    ref = mon.build_reference(frame, numeric=["tenure"], categorical=["plan"], task_type="binary",
                              oof_scores=oof, threshold=0.6, class_labels=None)
    return frame, ref


def test_identical_inputs_are_ok_and_a_planted_shift_is_an_alert_deterministically():
    frame, ref = _reference()
    assert ref["format"] == mon.REFERENCE_FORMAT and ref["basis"] == "training_partition"
    assert ref["prediction"]["flagged_share"] == pytest.approx(0.4, abs=0.05)
    assert ref["prediction"]["n_rows"] == ref["n_rows"] == 2000  # every pool row once (k-fold)
    same = mon.compare_inputs(frame.sample(frac=1.0, random_state=3), ref)
    assert same["max_psi"] < 0.01 and same["status_counts"] == {"alert": 0, "watch": 0, "ok": 2}
    shifted = frame.assign(tenure=frame["tenure"] + 12, plan=np.where(np.arange(len(frame)) % 3 == 0, "family", "pro"))
    drift = mon.compare_inputs(shifted, ref)
    psis = [row["psi"] for row in drift["features"]]
    assert psis == sorted(psis, reverse=True)
    assert {row["name"]: row["status"] for row in drift["features"]} == {"plan": "alert", "tenure": "alert"}
    plan = next(row for row in drift["features"] if row["name"] == "plan")
    assert plan["top_change"]["bucket"] in {"category", "other"} and plan["significant"]
    assert drift == mon.compare_inputs(shifted, ref)  # deterministic
    uniform = pd.DataFrame({"probability": np.linspace(0.0, 1.0, 1000)})
    predictions = mon.compare_predictions(uniform, ref["prediction"], min_rows=500)
    assert predictions["available"] and predictions["status"] == "ok" and predictions["flagged_status"] == "ok"
    flagged = mon.compare_predictions(pd.DataFrame({"probability": [0.9] * 500}), ref["prediction"], min_rows=500)
    assert flagged["status"] == "alert" and flagged["flagged_share"] == 1.0 and flagged["flagged_status"] == "watch"
    # A flagged share 1 point away from cross-validation is not a change worth a watch (S2).
    near = np.r_[np.full(590, 0.1), np.full(410, 0.9)]
    assert mon.compare_predictions(pd.DataFrame({"probability": near}), ref["prediction"],
                                   min_rows=500)["flagged_status"] == "ok"
    assert mon.compare_predictions(pd.DataFrame({"probability": [0.9] * 100}), ref["prediction"], min_rows=500) == {
        "available": False, "reason_code": "too_few_scores", "n_scores": 100}
    assert mon.window_verdict(500, drift, predictions) == ("alert", "inputs_moved")
    assert mon.window_verdict(500, same, flagged) == ("alert", "scores_moved")
    assert mon.window_verdict(5000, drift, flagged) == ("not_enough_data", "too_few_rows")  # compared rows count
    assert mon.window_verdict(500, {"available": False, "reason_code": "no_reference"}, {}) == (
        "not_enough_data", "no_reference")
    assert mon.usable_reference({"reference_summary": ref}) is ref and mon.usable_reference({}) is None
    assert mon.usable_reference({"reference_summary": {**ref, "format": "dclab.monitoring.reference.v1"}}) is None


def test_date_inputs_are_shown_with_ranges_but_never_counted():
    rng = np.random.default_rng(3)
    day = 86400.0
    start_2023 = datetime(2023, 1, 1, tzinfo=UTC).timestamp()
    training = pd.DataFrame({"signup": start_2023 + rng.integers(0, 365, 2000) * day,
                             "spend": rng.normal(50, 10, 2000)})
    ref = mon.build_reference(training, numeric=["signup", "spend"], categorical=[], task_type="regression",
                              oof_scores=[], threshold=None, class_labels=None, date_columns=["signup"])
    assert [f["kind"] for f in ref["features"]] == ["date", "numeric"]
    window = pd.DataFrame({"signup": start_2023 + (400 + rng.integers(0, 30, 800)) * day,
                           "spend": rng.normal(50, 10, 800)})
    drift = mon.compare_inputs(window, ref)
    date = next(row for row in drift["features"] if row["name"] == "signup")
    assert date["kind"] == "date" and date["psi"] > 2 and date["counted"] is False and date["status"] == "ok"
    assert date["later_than_training"] and date["range"][0].startswith("2024-02")
    assert date["range_reference"][0].startswith("2023-01") and date["range_reference"][1].startswith("2023-12")
    assert drift["features_compared"] == 1 and drift["dates_compared"] == 1 and drift["max_psi"] < 0.1
    assert mon.window_verdict(500, drift, {"available": False})[0] == "ok"
    assert dm.DATE_SENTENCE in dm.feature_sentence(date) and " to 2023-12" in dm.feature_sentence(date)
    # The scoring plan's date columns count as dates too (a reference recorded before the kind existed).
    plain = mon.build_reference(training, numeric=["signup", "spend"], categorical=[], task_type="regression",
                                oof_scores=[], threshold=None, class_labels=None)
    assert mon.compare_inputs(window, plain, date_columns=["signup"])["status_counts"]["alert"] == 0
    assert dm.finding_for("alert", "s", drift, {}).code == "scores_look_different"  # dates never name a finding


def test_binary_outcomes_count_only_in_the_training_spellings():
    yes_no = {"positive": ["yes"], "negative": ["no"]}
    codes, unknown = mon.binary_outcome_codes(pd.Series(["yes", "pending", "no", "YES", " no ", None]), yes_no)
    assert codes.iloc[[0, 2, 3, 4]].tolist() == [1.0, 0.0, 1.0, 0.0]
    assert np.isnan(codes.iloc[1]) and np.isnan(codes.iloc[5]) and unknown == 1  # missing is not "unrecognised"
    ones = {"positive": ["1"], "negative": ["0"]}
    codes, unknown = mon.binary_outcome_codes(pd.Series(["1", "unknown", 0, 1.0]), ones)
    assert codes.iloc[[0, 2, 3]].tolist() == [1.0, 0.0, 1.0] and np.isnan(codes.iloc[1]) and unknown == 1
    bad_good = {"positive": ["bad"], "negative": ["good"]}  # never the sorted-order rule of the outcome file
    codes, _ = mon.binary_outcome_codes(pd.Series(["Good", "bad", "BAD"]), bad_good)
    assert codes.tolist() == [0.0, 1.0, 1.0]
    codes, _ = mon.binary_outcome_codes(pd.Series(["A", "a"]), {"positive": ["A"], "negative": ["a"]})
    assert codes.tolist() == [1.0, 0.0]  # casing is ambiguous here: exact spellings only
    # A package without recorded spellings: explicit yes/no tokens only.
    codes, unknown = mon.binary_outcome_codes(pd.Series(["yes", "no", "pending", "1", "0", "Good"]), None)
    assert codes.iloc[[0, 1, 3, 4]].tolist() == [1.0, 0.0, 1.0, 0.0] and unknown == 2
    spellings = mon.target_spellings(pd.Series(["yes", "no", "Yes ", "no", None]), pd.Series([1, 0, 1, 0, np.nan]))
    assert sorted(spellings["positive"]) == ["Yes", "yes"] and spellings["negative"] == ["no"]
    assert mon.target_spellings(pd.Series([], dtype=object), pd.Series([], dtype=float)) is None  # zero rows
    assert mon.informative_spellings({"positive": ["1"], "negative": ["0"]}) is None  # only 0 / 1: tokens
    from app.services.auto_train.cleaning import _target_labels

    assert _target_labels(pd.Series([1, 0, 1]), pd.Series([1, 0, 1])) is None
    assert _target_labels(pd.Series(["Good", "bad"]), pd.Series([0, 1])) == {"positive": ["bad"], "negative": ["Good"]}
    assert _target_labels(pd.Series([], dtype=object), pd.Series([1, 0])) is None  # advisory, never raises
    # Matching uses full text: labels sharing their first 60 characters stay apart.
    long_a, long_b = "x" * 60 + "A", "x" * 60 + "B"
    codes, unknown = mon.multiclass_outcome_codes(pd.Series([long_b, long_a, "x" * 60]), [long_a, long_b])
    assert codes.iloc[:2].tolist() == [1.0, 0.0] and np.isnan(codes.iloc[2]) and unknown == 1


def test_outcome_metrics_minimums_class_rule_and_task_types():
    rng = np.random.default_rng(1)
    scores = pd.DataFrame({"probability": rng.uniform(0, 1, 200)})
    y = pd.Series((scores["probability"] > 0.5).astype(float))
    kwargs = {"metric": "roc_auc", "threshold": 0.5, "class_labels": None, "min_labels": 100, "min_per_class": 10}
    measured = mon.outcome_metrics("binary", y, scores, **kwargs)
    assert measured["status"] == "measured" and measured["n_labelled"] == 200 and measured["metric_value"] == 1.0
    assert measured["metrics"]["precision"] == 1.0 and set(measured["confusion"]) == {"tn", "fp", "fn", "tp"}
    assert measured["label_positive_rate"] == pytest.approx(float(y.mean()), abs=1e-4)
    assert mon.outcome_metrics("binary", y.head(99), scores.head(99), **kwargs)["status"] == "not_enough_labels"
    one_class = pd.Series([1.0] * 195 + [0.0] * 5)
    assert mon.outcome_metrics("binary", one_class, scores, **kwargs) == {
        "status": "not_enough_labels", "n_labelled": 200, "per_class": [5, 195]}
    assert mon.outcome_metrics("binary", y.where(y > 0), scores, **kwargs)["n_labelled"] < 200
    regression = mon.outcome_metrics("regression", pd.Series(np.arange(150.0)),
                                     pd.DataFrame({"prediction": np.arange(150.0) + 1.0}),
                                     **{**kwargs, "metric": "mae"})
    assert regression["metrics"]["mae"] == 1.0 and regression["metric_value"] == 1.0
    labels = ["a", "b", "c"]
    proba = pd.DataFrame(rng.dirichlet([1, 1, 1], 150), columns=[f"probability_{x}" for x in labels])
    truth = pd.Series([labels[i] for i in proba.to_numpy().argmax(axis=1)])
    multi = mon.outcome_metrics("multiclass", truth, proba, **{**kwargs, "metric": "accuracy", "class_labels": labels})
    assert multi["status"] == "measured" and multi["metric_value"] == 1.0
    rare = pd.Series(["a"] * 100 + ["b"] * 45 + ["c"] * 5)  # a class that occurs needs 10 labelled rows
    assert mon.outcome_metrics("multiclass", rare, proba, **{**kwargs, "class_labels": labels}) == {
        "status": "not_enough_labels", "n_labelled": 150, "per_class": [100, 45, 5]}
    assert mon.outcome_metrics("multiclass", pd.Series(["zz"] * 150), proba,
                               **{**kwargs, "class_labels": labels})["n_labelled"] == 0
    assert mon.outcome_metrics("regression", pd.Series(np.arange(50.0)), pd.DataFrame({"prediction": np.arange(50.0)}),
                               **kwargs)["status"] == "not_enough_labels"


def test_plain_words_come_from_numbers_and_never_claim_a_cause():
    _frame_, ref = _reference()
    drift = mon.compare_inputs(_frame_.assign(plan="family"), ref)
    text_ = dm.summary_text("inputs_moved", n_rows=2000, min_rows=500, input_drift=drift,
                            prediction_drift={"available": False}, sampled_rows=None, scorings_total=25)
    assert text_.startswith("Inputs look different from training: plan (PSI ")
    assert "Drift means the inputs look different from training; it does not mean the model is wrong." in text_
    assert text_.endswith("Based on the newest 20 scorings of 25 this week.")
    sentence = dm.feature_sentence(next(row for row in drift["features"] if row["name"] == "plan"))
    assert sentence.startswith("plan moved a lot (PSI ") and "because" not in sentence
    finding = dm.finding_for("alert", text_, drift, {})
    assert finding.code == "inputs_look_different" and finding.title == "Inputs look different from training: plan"
    assert "check whether the data source or the population scored has changed" in finding.body
    assert dm.finding_for("watch", text_, drift, {}) is None
    assert "train it again" in dm.summary_text("no_reference", n_rows=1, min_rows=500, input_drift={},
                                               prediction_drift={}, sampled_rows=None)
    flagged = {"available": True, "status": "ok", "flagged_status": "watch", "flagged_share": 0.6,
               "flagged_share_reference": 0.4}
    assert "from 40% in cross-validation to 60%" in dm.summary_text(
        "scores_moved", n_rows=900, min_rows=500, input_drift={"features": [], "n_rows_compared": 900},
        prediction_drift=flagged, sampled_rows=None)
    limits = ms.thresholds()
    words = dm.outcome_sentences({"status": "measured", "n_labelled": 150, "n_keyed": 600, "coverage": 0.25,
                                  "label_positive_rate": 0.31, "positive_rate_reference": 0.27, "metric": "roc_auc",
                                  "metric_value": 0.71, "cv_reference": {"metric": "roc_auc", "value": 0.74},
                                  "unrecognised_outcome_values": 10}, limits)
    assert words[0] == "150 of 600 scored customers with a key have a real outcome (25%)."
    assert any("may not describe everyone scored" in w for w in words)
    assert any(w.startswith("31% of them had the outcome, vs 27% of the training rows in cross-validation") for w in words)
    assert "roc_auc on them: 0.710 (cross-validation: 0.740)." in words
    assert words[-1] == "10 outcome values did not match the training outcome labels and were left out."


def test_monitoring_modules_never_touch_the_final_holdout():
    """Property: no monitoring module reads holdout results or imports a holdout accessor."""

    forbidden_tokens = ("test_predictions", "test_metrics", "final_test_evaluation", "holdout_report",
                        "n_test_rows", "winner_test_pred", "holdout_metrics")
    for path in MONITORING_MODULES:
        source = path.read_text(encoding="utf-8")
        assert not [token for token in forbidden_tokens if token in source], path
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] + [getattr(node, "module", "") or ""]
                assert not [name for name in names if "holdout" in name.lower()], (path, names)
                assert not [name for name in names if "package_loader" in name], (path, names)  # never a model
    spec = ms._spec_input({"task": {"task_type": "binary"}, "test_metrics": {"roc_auc": 0.9},
                           "test_predictions": [1], "scientific_evidence": {"feature_actions": [], "final": 1}})
    assert set(spec) == {"task", "decision_threshold", "class_labels", "scientific_evidence"}
    assert set(spec["scientific_evidence"]) == {"feature_actions", "missing_value_plan"}
    # A binary threshold metric's CV mean is not at the locked threshold: no CV reference for it (S3).
    manifest = {"cv_metrics": {"metric": "f1", "mean": {"f1": 0.6, "roc_auc": 0.8}}}
    assert ms._cv_reference(manifest, "f1", "binary") is None
    assert ms._cv_reference(manifest, "roc_auc", "binary") == {"metric": "roc_auc", "value": 0.8}


def test_feature_psi_series_is_oldest_first_counted_features_only_with_gaps():
    weeks = [datetime(2026, 9, 7, tzinfo=UTC), datetime(2026, 9, 14, tzinfo=UTC), datetime(2026, 9, 21, tzinfo=UTC)]
    drift = {
        weeks[2]: {"features": [{"name": "plan", "kind": "categorical", "psi": 0.3, "status": "alert", "counted": True},
                                {"name": "when", "kind": "date", "psi": 9.0, "status": "ok", "counted": False}]},
        weeks[0]: {"features": [{"name": "plan", "kind": "categorical", "psi": 0.01, "status": "ok", "counted": True},
                                {"name": "age", "kind": "numeric", "psi": 0.12, "status": "watch", "counted": True}]},
        weeks[1]: {"available": False},
    }
    series = dm.feature_psi_series([(start, drift[start]) for start in reversed(weeks)])  # given newest first
    assert [row.name for row in series] == ["plan", "age"]  # highest PSI in any week first; the date never
    assert [(p.window_start, p.psi, p.status) for p in series[0].points] == [
        (weeks[0], 0.01, "ok"), (weeks[1], None, None), (weeks[2], 0.3, "alert")]
    assert [p.psi for p in series[1].points] == [0.12, None, None]
    assert dm.feature_psi_series([], limit=3) == [] and len(dm.feature_psi_series(
        [(weeks[0], {"features": [{"name": f"f{i}", "psi": i / 100, "counted": True} for i in range(20)]})])) == 8
    assert dm.outcome_rates({"status": "measured", "metrics": {"precision": 0.5, "recall": 0.25}}) == (0.5, 0.25)
    assert dm.outcome_rates({"status": "not_enough_labels"}) == (None, None)
    assert dm.outcome_rates({"status": "measured", "metrics": {"mae": 1.0}}) == (None, None)  # regression


def test_keyed_scores_and_fixed_samples():
    newest = pd.DataFrame({"customer_id": ["a", "b", None], "probability": [0.9, 0.8, 0.7]})
    older = pd.DataFrame({"customer_id": ["b", "c"], "probability": [0.1, 0.2]})
    no_key = pd.DataFrame({"probability": [0.5]})
    scored, keys = ms._keyed_scores([newest, older, no_key], "customer_id")
    assert keys.tolist() == ["a", "b", "c"] and scored["probability"].tolist() == [0.9, 0.8, 0.2]
    assert ms._keyed_scores([newest, pd.DataFrame({"customer_id": [1.0, 1], "probability": [0.1, 0.2]})],
                            "customer_id") is None  # 1.0 and 1 are the same key
    frame = pd.DataFrame({"k": [f"k{i}" for i in range(1000)], "v": range(1000)})
    first = ms._fixed_sample(frame, 100, "k", 1)
    again = ms._fixed_sample(frame.iloc[::-1], 100, "k", 2)  # the same customers whatever the file order
    assert len(first) == 100 and set(first["k"]) == set(again["k"]) and first.index.is_monotonic_increasing


def test_time_columns_of_a_run_are_dates_for_monitoring():
    result = {"task": {"prediction_time_column": "created_ts"}, "holdout_plan": {"time_column": "event_at"},
              "validation_plan": {"time_column": None}}
    assert ms._time_columns(result) == {"created_ts", "event_at"} and ms._time_columns(None) == set()
    rng = np.random.default_rng(5)
    training = pd.DataFrame({"created_ts": 1.67e9 + rng.uniform(0, 3e7, 2000), "spend": rng.normal(50, 10, 2000)})
    ref = mon.build_reference(training, numeric=["created_ts", "spend"], categorical=[], task_type="regression",
                              oof_scores=[], threshold=None, class_labels=None)
    window = training.assign(created_ts=training["created_ts"] + 4e7).head(800)
    assert mon.compare_inputs(window, ref)["status_counts"]["alert"] == 1  # a raw epoch input alerts ...
    drift = mon.compare_inputs(window, ref, date_columns=sorted(ms._time_columns(result)))
    assert drift["status_counts"]["alert"] == 0 and drift["dates_compared"] == 1  # ... unless it is the time column


def test_branch_runs_record_target_spellings_and_time_columns(tmp_path):
    """A direct run (as branches run: no auto-train cleaning) records the pool rows' label words
    (Good/bad: the later one in sorted order is positive) and a numeric time column as a date;
    a 0/1 target records no words (outcomes then read yes / no tokens)."""

    from app.engine.experiments.runner import run_experiment
    from app.engine.serving import model_package as mp
    from app.engine.types import SearchConfig, TaskSpec

    rng = np.random.default_rng(4)
    n = 160
    x = rng.normal(size=n)
    frame = pd.DataFrame({"x": x, "region": rng.choice(["N", "S"], n),
                          "created_ts": 1.67e9 + np.sort(rng.uniform(0, 3e7, n)),
                          "outcome": np.where(x + rng.normal(scale=0.7, size=n) > 0, "bad", "Good")})

    def reference(data: pd.DataFrame, name: str) -> dict:
        task = TaskSpec(id=name, name=name, task_type="binary", target="outcome", entity_id=None,
                        prediction_time_column="created_ts", evaluation_metric="roc_auc",
                        feature_groups={"features": ["x", "region", "created_ts"]}, validation_strategy="stratified",
                        column_roles={"numerical": ["x", "created_ts"], "categorical": ["region"]})
        result = run_experiment(data, task, SearchConfig(strategy="open_ingest", max_candidates=4, seed=1),
                                artifact_dir=tmp_path / name)
        assert result["status"] == "COMPLETED", result.get("error")
        package = (tmp_path / name / mp.PACKAGE_FILENAME).read_bytes()
        return mp.read_package(package).manifest["reference_summary"]

    words = reference(frame, "words")
    assert (words["target"]["positive"], words["target"]["negative"]) == (["bad"], ["Good"])
    kinds = {f["name"]: f["kind"] for f in words["features"]}
    assert kinds == {"x": "numeric", "created_ts": "date", "region": "categorical"}  # the time column: a date
    coded = reference(frame.assign(outcome=(frame["outcome"] == "bad").astype(int)), "coded")
    assert (coded["target"]["positive"], coded["target"]["negative"]) == ([], [])
    assert 0 < coded["target"]["positive_rate"] < 1


def test_multiclass_outcomes_match_stripped_and_case_and_count_the_rest(client, db_session, setup):  # noqa: F811
    db = db_session
    keys = [f"k{i:03d}" for i in range(150)]
    tiers = ["gold ", "Silver", "bronze"] * 33 + ["gold"] + ["pending"] * 50  # 100 recognised, 50 not
    labels = _upload(client, setup, pd.DataFrame({"customer_id": keys, "tier": tiers}))
    db.add(MonitoringOutcomeSet(workspace_id=setup["alpha"].id, project_id=setup["alpha_project"].id,
                                dataset_id=UUID(labels["id"]), key_column="customer_id", outcome_column="tier",
                                linked_by_user_id=setup["alpha_admin"].id))
    db.commit()
    classes = ["bronze", "silver", "gold"]
    rng = np.random.default_rng(2)
    scored = pd.DataFrame(rng.dirichlet([1, 1, 1], 150), columns=[f"probability_{c}" for c in classes])
    scored.insert(0, "customer_id", keys)
    out = ms._outcomes(db, workspace_id=setup["alpha"].id, project_id=setup["alpha_project"].id, frames=[scored],
                       entity="customer_id", task_type="multiclass", class_labels=classes, reference=None,
                       manifest={"cv_metrics": {"metric": "accuracy", "mean": {"accuracy": 0.6}}})
    assert out["status"] == "measured" and out["n_labelled"] == 100 and out["unrecognised_outcome_values"] == 50
    assert out["per_class"] == [33, 33, 34] and out["cv_reference"] == {"metric": "accuracy", "value": 0.6}
    words = dm.outcome_sentences(out, ms.thresholds())
    assert words[-1] == "50 outcome values did not match the training outcome labels and were left out."


def test_week_math_is_utc_monday():
    moment = datetime(2026, 10, 11, 23, 30, tzinfo=UTC)  # a Sunday
    assert ms.week_start(moment) == datetime(2026, 10, 5, tzinfo=UTC)
    assert ms.parse_window_start("2026-10-05") == datetime(2026, 10, 5, tzinfo=UTC)
    assert ms.parse_window_start("2026-10-06") is None and ms.parse_window_start("not-a-date") is None


# --- database backstops --------------------------------------------------------------------------


def test_window_and_outcome_set_constraints_hold_in_the_database(db_session, g, setup):  # noqa: F811
    db = db_session
    _bootstrap(db, g)  # the first approval: a release of g.mv[0]
    release = db.scalar(select(ModelRelease.id).where(ModelRelease.project_id == g.project.id))
    monday = datetime(2026, 10, 5, tzinfo=UTC)

    def window(**overrides) -> MonitoringWindow:
        values = dict(workspace_id=g.ws, project_id=g.project.id, model_version_id=g.mv[0], model_release_id=release,
                      window_start=monday, window_end=monday + timedelta(days=7), revision=1, n_rows=10,
                      n_batch_predictions=1, input_drift={}, prediction_drift={}, outcomes={}, status="ok",
                      summary_code="stable", summary="ok")
        return MonitoringWindow(id=uuid4(), **{**values, **overrides})

    first = window()
    db.add(first)
    sets = MonitoringOutcomeSet(workspace_id=g.ws, project_id=g.project.id, dataset_id=g.source.id, key_column="id",
                                outcome_column="y", linked_by_user_id=g.actor.id)
    db.add(sets)
    db.commit()

    def refused(expected: set[str], row) -> None:
        with pytest.raises(IntegrityError) as caught:
            db.add(row)
            db.flush()
        db.rollback()
        assert caught.value.orig.diag.constraint_name in expected

    tenant = {"fk_monitoring_windows_workspace_project", "fk_monitoring_windows_workspace_project_model_version",
              "fk_monitoring_windows_workspace_model_release"}
    refused({"uq_monitoring_windows_revision"}, window())  # the same revision twice
    tuesday = monday + timedelta(days=1)
    refused({"ck_monitoring_windows_week"}, window(window_start=tuesday, window_end=tuesday + timedelta(days=7)))
    late = monday + timedelta(hours=3)
    refused({"ck_monitoring_windows_week"}, window(window_start=late, window_end=late + timedelta(days=7)))
    refused(tenant, window(workspace_id=setup["beta"].id, revision=2))  # (a new revision: only the FK can refuse)
    refused(tenant, window(project_id=setup["beta_project"].id, revision=2))
    # A release that approved a different version cannot be the window's release.
    refused({"fk_monitoring_windows_workspace_model_release"}, window(model_version_id=g.mv[1]))
    refused({"fk_monitoring_outcome_sets_workspace_project", "fk_monitoring_outcome_sets_workspace_project_dataset"},
            MonitoringOutcomeSet(workspace_id=setup["beta"].id, project_id=g.project.id, dataset_id=g.source.id,
                                 key_column="id", outcome_column="y", linked_by_user_id=g.actor.id))
    for statement, row_id in (("UPDATE monitoring_windows SET status = 'alert' WHERE id = :id", first.id),
                              ("DELETE FROM monitoring_windows WHERE id = :id", first.id),
                              ("UPDATE monitoring_outcome_sets SET key_column = 'k' WHERE id = :id", sets.id),
                              ("DELETE FROM monitoring_outcome_sets WHERE id = :id", sets.id)):
        with pytest.raises(DBAPIError):
            db.execute(text(statement), {"id": row_id})
        db.rollback()


# --- jobs: debounce, kill switch -------------------------------------------------------------------


def test_enqueue_is_debounced_and_reuses_the_queued_job(db_session, setup):  # noqa: F811
    db = db_session
    ws, project = setup["alpha"].id, setup["alpha_project"].id
    version, monday = uuid4(), datetime(2026, 10, 5, tzinfo=UTC)
    first = ms.enqueue_window_job(db, workspace_id=ws, project_id=project, model_version_id=version, start=monday,
                                  reason="test")
    db.commit()
    assert first.max_attempts == 1 and first.available_at <= datetime.now(UTC)
    first.status, first.started_at = "running", datetime.now(UTC)
    db.commit()
    later = ms.enqueue_window_job(db, workspace_id=ws, project_id=project, model_version_id=version, start=monday,
                                  reason="test")
    db.commit()
    assert later.id != first.id and later.available_at >= datetime.now(UTC) + timedelta(minutes=9)
    again = ms.enqueue_window_job(db, workspace_id=ws, project_id=project, model_version_id=version, start=monday,
                                  reason="test")
    assert again.id == later.id  # a burst collapses into the one waiting job


def test_kill_switch_refuses_links_and_makes_hook_and_job_no_ops(client, db_session, setup, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "monitoring_enabled", False)
    project_id = setup["alpha_project"].id
    labels = _upload(client, setup, pd.DataFrame({"customer_id": ["a", "b"], "churned": ["yes", "no"]}))
    _assert_envelope(_link(client, setup, project_id, {"dataset_id": labels["id"], "key_column": "customer_id",
                                                        "outcome_column": "churned"}), 409, "monitoring_disabled")
    ms.after_scoring_completed(db_session, SimpleNamespace(id=uuid4()))  # returns before any query
    assert ms.compute_window_job(db_session, SimpleNamespace(id=uuid4(), payload={}, workspace_id=uuid4())) is None
    assert db_session.scalar(select(func.count()).select_from(MlJob).where(
        MlJob.handler_key == "monitoring.compute_window")) == 0
    body = client.get(f"/v1/projects/{project_id}/monitoring", headers=_h(setup)).json()
    assert body["enabled"] is False and body["status"] == "no_model_in_use"


# --- end to end: real training, scoring, worker job, reads ------------------------------------


def _monitoring_jobs(db, status: str | None = None) -> list[MlJob]:
    db.expire_all()
    query = select(MlJob).where(MlJob.handler_key == "monitoring.compute_window")
    return list(db.scalars(query.where(MlJob.status == status) if status else query))


def _run_monitoring(db) -> None:
    for job in _monitoring_jobs(db, "queued"):
        done = process_next_job(db, job_id=job.id, claimed_by="p74a-test")
        assert done.status == "completed", done.failure_reason
    db.expire_all()


def _windows(db, project_id) -> list[MonitoringWindow]:
    db.expire_all()
    return list(db.scalars(select(MonitoringWindow).where(MonitoringWindow.project_id == project_id)
                           .order_by(MonitoringWindow.revision)))


def _link(client, setup, project_id, body, headers=None):  # noqa: F811
    return client.post(f"/v1/projects/{project_id}/monitoring/outcomes", json=body,
                       headers=headers or _h(setup, key=_key()))


def _recompute(db, setup, project_id, mv_id, start) -> None:  # noqa: F811
    ms.enqueue_window_job(db, workspace_id=setup["alpha"].id, project_id=project_id, model_version_id=mv_id,
                          start=start, reason="test")
    db.commit()
    _run_monitoring(db)


def test_monitoring_end_to_end_with_planted_drift_and_outcomes(client, db_session, setup, monkeypatch):  # noqa: F811
    db = db_session
    monkeypatch.setattr(get_settings(), "monitoring_debounce_minutes", 0.0)  # recompute at once in this test
    train = _frame("binary", n=240)
    experiment, mv_id = _trained(client, db, setup, train, "label")
    project_id = experiment.project_id
    model = db.get(ModelVersion, mv_id)

    # The reference: training partition + out-of-fold CV scores, stored in the package manifest.
    manifest = json.loads(read_artifact_bytes(db, workspace_id=setup["alpha"].id,
                                              artifact_id=model.feature_manifest_artifact_id))
    reference = manifest["reference_summary"]
    n_test = int(experiment.result["best_single"]["n_test_rows"])
    assert reference["basis"] == "training_partition" and reference["n_rows"] + n_test == len(train)
    assert [f["name"] for f in reference["features"]] == manifest["features"]
    assert reference["prediction"]["basis"] == "out_of_fold_cv" and reference["prediction"]["column"] == "probability"
    time_ordered = experiment.result["validation_plan"]["strategy"] == "TimeSeriesSplit"
    assert reference["prediction"]["folds"] == ("last" if time_ordered else "all")
    if not time_ordered:  # k-fold: every training row's out-of-fold score once
        assert reference["prediction"]["n_rows"] == reference["n_rows"]
    assert {f["name"]: f["kind"] for f in reference["features"]}["signup_date"] == "date"
    target = reference["target"]  # spellings of the training rows' label, before coercion
    assert (target["positive"], target["negative"]) == (["yes"], ["no"]) and 0 < target["positive_rate"] < 1

    # A failed enqueue never fails the scoring.
    real_enqueue = ms.enqueue_window_job

    def boom(*_args, **_kwargs):
        raise RuntimeError("queue down")

    monkeypatch.setattr(ms, "enqueue_window_job", boom)
    stable = _upload(client, setup, _frame("binary", n=300, seed=21).drop(columns=["label"]))
    first = _predict(client, setup, mv_id, stable["id"])
    assert _work(db, first.json()["id"]).status == "completed" and _monitoring_jobs(db) == []
    monkeypatch.setattr(ms, "enqueue_window_job", real_enqueue)

    # Planted drift: an unseen plan for every row and many more visits.
    drifted = _frame("binary", n=300, seed=22).drop(columns=["label"]).assign(
        plan="enterprise", visits=lambda f: f["visits"] + 9,
        customer_id=[f"d{i:05d}" for i in range(300)])
    scoring = _upload(client, setup, drifted)
    second = _predict(client, setup, mv_id, scoring["id"])
    assert _work(db, second.json()["id"]).status == "completed"
    third = _predict(client, setup, mv_id, scoring["id"])  # same week: still one queued job
    assert _work(db, third.json()["id"]).status == "completed"
    assert len(_monitoring_jobs(db, "queued")) == 1
    _run_monitoring(db)
    [window] = _windows(db, project_id)
    completed = db.get(BatchPrediction, UUID(third.json()["id"])).completed_at
    assert window.window_start == ms.week_start(completed) and window.window_end - window.window_start == timedelta(days=7)
    # The file scored twice counts once (its newest scoring): 2 files, 600 rows, all compared.
    assert (window.status, window.n_batch_predictions, window.n_rows) == ("alert", 2, 600)
    assert window.input_drift["n_rows_compared"] == 600
    scores = window.prediction_drift
    scores_moved = scores.get("status") != "ok" or scores.get("flagged_status") == "watch"
    assert window.summary_code == ("inputs_and_scores_moved" if scores_moved else "inputs_moved")
    names = {row["name"]: row for row in window.input_drift["features"]}
    assert names["plan"]["status"] == "alert" and names["visits"]["status"] == "alert"
    assert names["plan"]["significant"] and names["plan"]["top_change"]["bucket"] == "other"
    # Untouched columns stay below the alert line (reference and scored rows are prepared alike).
    assert all(names[column]["status"] != "alert" for column in ("tenure", "spend", "code")), names
    # The date input is shown with its ranges and never counted (B3); the window status ignores it.
    assert names["signup_date"]["kind"] == "date" and names["signup_date"]["counted"] is False
    assert all(names["signup_date"]["range_reference"]) and all(names["signup_date"]["range"])
    assert window.input_drift["max_psi"] == max(row["psi"] for row in names.values() if row["kind"] != "date")
    assert window.outcomes == {"status": "not_linked", "n_scored": 600}
    with pytest.raises(DBAPIError, match="immutable"):
        db.execute(text("UPDATE monitoring_windows SET status = 'ok' WHERE id = :id"), {"id": window.id})
    db.rollback()

    # Deterministic rerun: same inputs -> no new revision.
    _recompute(db, setup, project_id, mv_id, window.window_start)
    assert len(_windows(db, project_id)) == 1

    # Reads.
    overview = client.get(f"/v1/projects/{project_id}/monitoring", headers=_h(setup))
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["status"] == "alert" and body["model_version_id"] == str(mv_id) and len(body["windows"]) == 1
    assert body["finding"]["code"] == "inputs_look_different" and "plan" in body["finding"]["title"]
    assert "signup_date" not in body["finding"]["title"]
    assert body["thresholds"] == {"psi_watch": 0.1, "psi_alert": 0.2, "significance_level": 0.01,
                                  "flagged_share_relative_watch": 0.3, "flagged_share_absolute_watch": 0.02,
                                  "min_rows": 500, "min_labels": 100, "min_per_class": 10,
                                  "precision_floor": None, "recall_floor": None, "version": 0, "changed_at": None,
                                  "changed_by_user_id": None}  # D5b: the defaults, never changed
    # V7 B7: PSI by week per counted feature (never the date) and no outcome rates before a link.
    assert (body["latest"]["outcome_precision"], body["latest"]["outcome_recall"]) == (None, None)
    counted = sorted((row for row in names.values() if row["kind"] != "date"), key=lambda r: (-r["psi"], r["name"]))
    series = body["feature_psi_series"]
    assert [row["name"] for row in series] == [row["name"] for row in counted][:dm.FEATURE_SERIES_MAX]
    assert series[0]["points"] == [{"window_start": body["latest"]["window_start"], "psi": counted[0]["psi"],
                                    "status": counted[0]["status"]}]
    monday = window.window_start.date().isoformat()
    detail = client.get(f"/v1/projects/{project_id}/monitoring/windows/{monday}", headers=_h(setup))
    assert detail.status_code == 200, detail.text
    sentences = detail.json()["feature_sentences"]
    assert any(s.startswith("plan moved a lot (PSI ") for s in sentences)
    assert any(s.startswith("signup_date: dates from ") for s in sentences)
    assert detail.json()["outcome_sentences"] == ["No real outcomes are linked yet."]
    assert "holdout" not in detail.text.lower() and "object_key" not in detail.text
    tuesday = (window.window_start + timedelta(days=1)).date().isoformat()
    _assert_envelope(client.get(f"/v1/projects/{project_id}/monitoring/windows/{tuesday}", headers=_h(setup)),
                     404, "not_found")
    # Tenancy: another workspace's member sees nothing of this project, nor this version from its own.
    beta = _h(setup, setup["beta_admin"], workspace=setup["beta"])
    for path in ("monitoring", f"monitoring/windows/{monday}", "monitoring/outcomes"):
        _assert_envelope(client.get(f"/v1/projects/{project_id}/{path}", headers=beta), 404, "not_found")
    _assert_envelope(client.get(f"/v1/projects/{setup['beta_project'].id}/monitoring?model_version_id={mv_id}",
                                headers=beta), 404, "model_version_not_found")

    # Outcomes: tokens, viewers and other tenants cannot link; the training file and unknown columns are refused.
    _secret = create_service_token(db, creator=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="p74a",
                                   scopes=("read", "experiments:write"), expires_in_days=1,
                                   current_password="test-password")[1]
    db.commit()
    labels = pd.DataFrame({"customer_id": [f"d{i:05d}" for i in range(160)],
                           "churned": ["yes" if i % 3 == 0 else "no" for i in range(150)] + ["pending"] * 10})
    labels_file = _upload(client, setup, labels)
    link = {"dataset_id": labels_file["id"], "key_column": "customer_id", "outcome_column": "churned"}
    refused = _link(client, setup, project_id, link, headers={"Authorization": f"Bearer {_secret}",
                                                              "Idempotency-Key": _key()})
    assert refused.status_code == 403 and refused.json()["error"]["code"] in {
        "service_token_not_permitted", "human_session_required"}
    viewer = _member(db, setup, WorkspaceRole.VIEWER)
    assert _link(client, setup, project_id, link, headers=_h(setup, viewer, key=_key())).status_code == 403
    _assert_envelope(_link(client, setup, project_id, link, headers=_h(
        setup, setup["beta_admin"], key=_key(), workspace=setup["beta"])), 404, "not_found")  # another tenant
    beta_file = _upload(client, setup, labels, workspace="beta")
    _assert_envelope(_link(client, setup, project_id, {**link, "dataset_id": beta_file["id"]}), 404,
                     "dataset_not_found")  # another tenant's file
    assert client.get(f"/v1/projects/{project_id}/monitoring", headers={"Authorization": f"Bearer {_secret}"}
                      ).status_code == 200
    _assert_envelope(_link(client, setup, project_id, {**link, "dataset_id": str(experiment.source_dataset_id)}),
                     409, "outcomes_from_training_data")
    _assert_envelope(_link(client, setup, project_id, {**link, "outcome_column": "nope"}), 422,
                     "outcome_column_unknown")
    key = _key()
    linked = _link(client, setup, project_id, link, headers=_h(setup, key=key))
    assert linked.status_code == 201, linked.text
    assert _link(client, setup, project_id, link, headers=_h(setup, key=key)).json()["id"] == linked.json()["id"]
    _assert_envelope(_link(client, setup, project_id, link), 409, "already_linked")  # a new key, the same link
    assert db.scalar(select(func.count()).select_from(MonitoringOutcomeSet)) == 1
    # One accepted History entry, written by the server (the column names are facts, not prose).
    db.expire_all()
    [record] = db.scalars(select(ProjectDecisionRecord).where(
        ProjectDecisionRecord.project_id == project_id,
        ProjectDecisionRecord.decision_type == "monitoring_outcomes_linked")).all()
    assert (record.state, record.actor_kind, record.actor_user_id) == ("accepted", "human", setup["alpha_admin"].id)
    assert record.details["monitoring_outcome_set_id"] == linked.json()["id"]
    assert record.facts["key_column"] == "customer_id" and "customer_id" not in record.rationale
    _run_monitoring(db)
    latest = _windows(db, project_id)[-1]
    outcomes = latest.outcomes
    assert latest.revision == 2 and outcomes["status"] == "measured", outcomes
    assert (outcomes["n_scored"], outcomes["n_keyed"], outcomes["n_labelled"]) == (600, 600, 150)
    assert outcomes["coverage"] == round(150 / 600, 4) and outcomes["unrecognised_outcome_values"] == 10
    assert outcomes["label_positive_rate"] == pytest.approx(50 / 150, abs=1e-4)
    assert outcomes["positive_rate_reference"] == target["positive_rate"]
    metric = manifest["cv_metrics"]["metric"]
    assert outcomes["metric"] == metric
    assert (outcomes["cv_reference"] is None) == (metric in ms._THRESHOLD_METRICS)
    words = client.get(f"/v1/projects/{project_id}/monitoring/windows/{monday}", headers=_h(setup)).json()
    assert words["outcome_sentences"][0] == "150 of 600 scored customers with a key have a real outcome (25%)."
    assert (words["outcome_precision"], words["outcome_recall"]) == (
        outcomes["metrics"]["precision"], outcomes["metrics"]["recall"])  # V7 B7: real outcomes, locked threshold
    measured = client.get(f"/v1/projects/{project_id}/monitoring", headers=_h(setup)).json()["latest"]
    assert measured["outcome_precision"] == outcomes["metrics"]["precision"] is not None
    listed = client.get(f"/v1/projects/{project_id}/monitoring/outcomes", headers=_h(setup)).json()
    assert [item["id"] for item in listed["items"]] == [linked.json()["id"]]
    assert listed["items"][0]["linked_by_user_id"] == str(setup["alpha_admin"].id)
    by_token = client.get(f"/v1/projects/{project_id}/monitoring/outcomes",
                          headers={"Authorization": f"Bearer {_secret}"}).json()
    assert by_token["items"][0]["linked_by_user_id"] is None

    # A newer labels file whose joining keys repeat is skipped; the older one still measures (N8).
    dupes = _upload(client, setup, pd.concat([labels, labels.head(5)], ignore_index=True))
    dupes_link = _link(client, setup, project_id, {**link, "dataset_id": dupes["id"]})
    assert dupes_link.status_code == 201
    _run_monitoring(db)
    newest = _windows(db, project_id)[-1].outcomes
    assert newest["status"] == "measured" and newest["outcome_set_id"] == linked.json()["id"]
    assert newest["refused_sets"] == [{"outcome_set_id": dupes_link.json()["id"], "reason_code": "key_not_unique"}]
    # At most LINKS_PER_HOUR links per project and hour.
    monkeypatch.setattr(dm, "LINKS_PER_HOUR", 2)
    third_file = _upload(client, setup, labels.head(120))
    _assert_envelope(_link(client, setup, project_id, {**link, "dataset_id": third_file["id"]}), 429, "rate_limited")
    monkeypatch.setattr(dm, "LINKS_PER_HOUR", 10)

    # A model saved before monitoring existed (no reference summary): not_enough_data, in plain words.
    real_manifest = ms._manifest

    def saved_before_monitoring(db_, model_version):
        manifest_ = real_manifest(db_, model_version)
        manifest_.pop("reference_summary")
        return manifest_

    monkeypatch.setattr(ms, "_manifest", saved_before_monitoring)
    _recompute(db, setup, project_id, mv_id, window.window_start)
    old = _windows(db, project_id)[-1]
    assert (old.status, old.summary_code) == ("not_enough_data", "no_reference") and "train it again" in old.summary
    assert old.input_drift == {"available": False, "reason_code": "no_reference"}
    assert old.outcomes["status"] == "measured"  # outcomes only need yes/no tokens without recorded spellings
    monkeypatch.setattr(ms, "_manifest", real_manifest)

    # A failure is a job failure with a plain code, is not retried and writes no window revision;
    # an unexpected error carries a fixed code, never its text.
    def unreadable(*_args, **_kwargs):
        raise MonitoringError(dm.SCORES_UNREADABLE, "a scored file is missing", status_code=409)

    def crash(*_args, **_kwargs):
        raise RuntimeError("s3://bucket/secret-key exploded")

    revisions = len(_windows(db, project_id))
    for failure, code in ((unreadable, "monitoring_scores_unreadable:"), (crash, "monitoring_failed:")):
        monkeypatch.setattr(ms, "_scored_rows", failure)
        job = ms.enqueue_window_job(db, workspace_id=setup["alpha"].id, project_id=project_id,
                                    model_version_id=mv_id, start=window.window_start, reason="test")
        db.commit()
        failed = process_next_job(db, job_id=job.id, claimed_by="p74a-test")
        assert failed.status == "failed" and failed.failure_reason.startswith(code), failed.failure_reason
        assert "secret-key" not in failed.failure_reason
    assert len(_windows(db, project_id)) == revisions


def test_monitoring_reads_before_any_window(client, db_session, setup):  # noqa: F811
    project_id = setup["alpha_project"].id
    body = client.get(f"/v1/projects/{project_id}/monitoring", headers=_h(setup)).json()
    assert body["status"] == "no_model_in_use" and body["windows"] == [] and body["finding"] is None
    assert client.get(f"/v1/projects/{project_id}/monitoring/outcomes", headers=_h(setup)).json()["items"] == []
    _assert_envelope(client.get(f"/v1/projects/{project_id}/monitoring/windows/2026-10-05", headers=_h(setup)),
                     404, "not_found")
    _assert_envelope(client.get(f"/v1/projects/{project_id}/monitoring?model_version_id={uuid4()}",
                                headers=_h(setup)), 404, "model_version_not_found")
