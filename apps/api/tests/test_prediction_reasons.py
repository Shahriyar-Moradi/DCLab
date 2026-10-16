"""P7.7-A: per-row prediction reasons (``app.engine.explain``). Pure tests, no database.

Tree SHAP (XGBoost / LightGBM / CatBoost) and linear contributions are additive: per row
they add up to the model's raw output (within a tolerance sized to the terms), which in
turn reproduces the probabilities / values the scoring file holds. Occlusion (random
forest, extra trees, scikit-learn gradient boosting) is approximate, deterministic and
finds a planted driver. Reasons use the scoring file's own column names (a one-hot column
is ONE reason), never encoded names or the row's numbers, and name the reference each
method measures against. The work is bounded (budget, deadline) and heartbeats while it
runs; a failing or slow explainer never fails scoring, a cancellation is never swallowed.
The worker path end to end is in ``test_v1_batch_predictions``.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from app.domain.batch_predictions import REASON_METHODS
from app.domain.errors import RunCancelledError
from app.engine import explain
from app.engine.explain import reasons
from app.engine.explain.reasons import _aggregate, _linear
from app.engine.lab.auto_prepare import build_preprocessor
from app.engine.models.registry import available_families, make_model
from app.engine.serving.batch_scoring import score_frame, scoring_spec
from test_model_package import _fitted, _frame, _load, _package, _result, _target

ADDITIVE_FAMILIES = {"logistic_regression", "linear_regression", "ridge", "lasso", "elasticnet", "xgboost",
                     "xgboost_regressor", "lightgbm", "lightgbm_regressor", "catboost", "catboost_regressor"}
OCCLUSION_FAMILIES = {"random_forest", "extra_trees", "gradient_boosting", "random_forest_regressor",
                      "extra_trees_regressor", "gradient_boosting_regressor"}


def _scoring_rows(seed: int = 9) -> pd.DataFrame:
    return _frame(seed=seed).assign(c=lambda f: f["c"].where(f.index % 7 != 0, "unseen"))


def _explained(pipeline, rows, task, reference=None, **kwargs):
    fmap = explain.feature_map(pipeline)
    baselines = explain.training_baselines(pipeline, fmap)
    explanation = explain.explain_rows(pipeline, rows, fmap, baselines, task_type=task, **kwargs)
    texts = explain.reason_texts(explanation, rows, fmap, task_type=task, date_columns={"when"}, reference=reference)
    return fmap, baselines, explanation, texts


def _cases(families: set[str]) -> list[tuple[str, str, bool | str]]:
    cases = [(task, family, False) for task in ("binary", "multiclass", "regression")
             for family in available_families(task) if family in families]
    return cases + [("multiclass", family, gap) for gap in (True, "two")
                    for family in available_families("multiclass") if family in families]


def _pipeline(family: str, task: str, train: pd.DataFrame, y, numeric: list[str], categorical: list[str], **hp):
    model = make_model(family, seed=3, task_type=task, hyperparameters=hp or None)
    return Pipeline([("prep", build_preprocessor(numeric, categorical)), ("model", model)]).fit(train, y)


def test_method_names_match_the_read_model():
    assert tuple(REASON_METHODS) == explain.METHODS


# --- additive methods ------------------------------------------------------------------------


@pytest.mark.parametrize(("task", "family", "gap"), _cases(ADDITIVE_FAMILIES))
def test_native_and_linear_contributions_add_up_to_the_models_output(task, family, gap):
    """Every applicable family and task, on the model as the worker loads it from its package:
    sum(contributions) + bias == the raw output, and the raw output reproduces the scored
    probability / value (independent of the explainer's own check). Bit-identical rerun."""

    frame = _frame()
    loaded = _load(*_package(_fitted(task, family, frame, _target(task, frame, gap=gap)), family, task))
    pipeline, rows = loaded.pipeline, _scoring_rows()
    fmap, _baselines, explanation, _texts = _explained(pipeline, rows, task)
    expected_method = "linear_contributions" if family in {
        "logistic_regression", "linear_regression", "ridge", "lasso", "elasticnet"} else "tree_shap"
    assert (explanation.method, explanation.additive) == (expected_method, True)
    assert explanation.contributions.shape == (len(rows), len(fmap.names)) == (len(rows), 5)
    total = explanation.contributions.sum(axis=1) + explanation.bias
    tolerance = 1e-4 if "xgboost" in family else 1e-9  # XGBoost computes in float32
    np.testing.assert_allclose(total, explanation.raw, rtol=tolerance, atol=tolerance)
    # The explained raw output is the scored output on its own scale.
    if task == "regression":
        np.testing.assert_allclose(explanation.raw, pipeline.predict(rows), rtol=1e-6, atol=1e-6)
    else:
        proba = np.clip(pipeline.predict_proba(rows), 1e-300, None)
        if task == "binary":
            expected = np.log(proba[:, 1]) - np.log(proba[:, 0])
        else:  # the predicted class against the others (mean log-probability of the others)
            picked = proba.argmax(axis=1)
            own = np.log(proba[np.arange(len(rows)), picked])
            expected = own - (np.log(proba).sum(axis=1) - own) / (proba.shape[1] - 1)
        np.testing.assert_allclose(explanation.raw, expected, rtol=1e-3, atol=1e-3)
    again = _explained(pipeline, rows, task)[2]
    assert np.array_equal(again.contributions, explanation.contributions)  # bit for bit


@pytest.mark.parametrize(("family", "offset"), [("xgboost_regressor", 1e4), ("xgboost_regressor", 0.0),
                                                ("lightgbm_regressor", 0.0), ("linear_regression", 0.0)])
def test_large_terms_and_targets_near_zero_keep_their_reasons(family, offset):
    """A price-like target around 1e4 and a target centred on 0 with large terms (1000 x the
    signal): the per-row tolerance follows the size of the terms, not of the output."""

    rng = np.random.default_rng(11)
    columns = [f"col_{name}" for name in "abcdefgh"]
    train = pd.DataFrame(rng.normal(size=(1500, 8)), columns=columns)
    signal = 3 * train["col_a"] + train["col_b"] ** 2 - train["col_c"] * train["col_d"]
    y = offset + (300 if offset else 1000) * (signal - signal.mean())
    hp = {"n_estimators": 400, "max_depth": 8} if family.startswith("xgboost") else (
        {"n_estimators": 400} if family.startswith("lightgbm") else {})
    pipeline = _pipeline(family, "regression", train, y, columns, [], **hp)
    explanation = explain.explain_rows(pipeline, train.head(500), task_type="regression")
    assert explanation.additive and explanation.rows_explained == 500
    assert np.abs(explanation.raw).min() < 50 if offset == 0 else np.abs(explanation.raw).min() > 1000


def test_the_additivity_tolerance_follows_the_size_of_the_terms():
    """Two large contributions that cancel to a small output, off by a float32-sized error of
    the terms: accepted (the old tolerance on |output| refused it); a lost term is refused."""

    values, bias = np.array([[5e4, -5e4 + 3.0]]), np.array([0.5])
    scale = np.abs(values).sum(axis=1) + np.abs(bias)
    raw = values.sum(axis=1) + bias + 0.1  # 1e-6 of the terms' size
    reasons._check_additive(values, bias, raw, scale, rtol=1e-5)
    assert not np.allclose(values.sum(axis=1) + bias, raw, rtol=1e-4, atol=1e-4)
    with pytest.raises(explain.ExplainUnavailable, match="did not add up"):
        reasons._check_additive(values, bias, raw + 3.0, scale, rtol=1e-5)
    with pytest.raises(explain.ExplainUnavailable, match="did not add up"):  # float64 methods are strict
        reasons._check_additive(values, bias, raw, scale, rtol=reasons.FLOAT64_RTOL)


@pytest.mark.parametrize("family", ["xgboost", "logistic_regression"])
def test_a_broken_contribution_mapping_fails_the_additivity_check(monkeypatch, family):
    frame = _frame()
    pipeline = _fitted("binary", family, frame, _target("binary", frame))
    name = "_tree_shap" if family == "xgboost" else "_linear"
    real = getattr(reasons, name)

    def drops_one_column(*args):
        values, bias, raw = real(*args)
        values = values.copy()
        values[..., np.abs(values).sum(axis=(0, 1)).argmax()] = 0.0  # one encoded column lost
        return values, bias, raw

    monkeypatch.setattr(reasons, name, drops_one_column)
    with pytest.raises(explain.ExplainUnavailable, match="did not add up"):
        explain.explain_rows(pipeline, _scoring_rows(), task_type="binary")


def test_a_column_with_five_categories_is_one_reason():
    rng = np.random.default_rng(4)
    train = pd.DataFrame({"level": rng.normal(size=300), "group": rng.choice(list("pqrst"), 300).astype(object)})
    y = ((train["group"].isin(["p", "q"])) ^ (train["level"] > 1)).astype(int)
    pipeline = _pipeline("logistic_regression", "binary", train, y, ["level"], ["group"])
    fmap = explain.feature_map(pipeline)
    assert fmap.names == ("level", "group") and fmap.blocks == ((0, 1), (1, 6)) and fmap.width == 6
    rows = train.head(50)
    encoded = pipeline.named_steps["prep"].transform(rows)
    values, _bias, _raw = _linear(pipeline.named_steps["model"], encoded, True)
    per_column = values[:, 0, :]
    np.testing.assert_allclose(_aggregate(per_column, fmap)[:, 1], per_column[:, 1:6].sum(axis=1), rtol=0, atol=0)
    _fmap, _b, explanation, texts = _explained(pipeline, rows, "binary")
    np.testing.assert_allclose(explanation.contributions, _aggregate(per_column, fmap), rtol=1e-12, atol=1e-12)
    encoded_names = set(pipeline.named_steps["prep"].get_feature_names_out())
    for row in texts:
        present = [text for text in row if text]
        assert 1 <= len(present) <= 2 and sum(text.startswith("group") for text in present) <= 1
        assert not any(name in text for text in present for name in encoded_names)


def test_hand_built_linear_model_gives_known_contributions_order_and_direction():
    train = pd.DataFrame({name: [1.0, 2.0, 3.0, 4.0, 5.0, 6.0] for name in ("alpha", "beta", "gamma")})
    pipeline = Pipeline([("prep", build_preprocessor(["alpha", "beta", "gamma"], [])),
                         ("model", make_model("linear_regression", task_type="regression"))])
    pipeline.fit(train, np.arange(6.0))
    linear = pipeline.named_steps["model"].named_steps["m"]
    linear.coef_, linear.intercept_ = np.array([3.0, -2.0, 0.5]), 1.0
    rows = pd.DataFrame({name: [10.0, 0.0] for name in ("alpha", "beta", "gamma")})
    prep_scaler = pipeline.named_steps["prep"].named_transformers_["num"].named_steps["scaler"]
    inner = pipeline.named_steps["model"].named_steps["scaler"]
    z = ((rows.to_numpy() - prep_scaler.mean_) / prep_scaler.scale_ - inner.mean_) / inner.scale_
    _fmap, _b, explanation, texts = _explained(pipeline, rows, "regression")
    np.testing.assert_allclose(explanation.contributions, z * np.array([3.0, -2.0, 0.5]), rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(explanation.contributions.sum(axis=1) + 1.0, pipeline.predict(rows), rtol=1e-12)
    assert texts[0] == [
        "Alpha: above the training average, raised the predicted value",
        "Beta: above the training average, lowered the predicted value",
        "Gamma: above the training average, raised the predicted value",
    ]
    assert texts[1] == [
        "Alpha: below the training average, lowered the predicted value",
        "Beta: below the training average, raised the predicted value",
        "Gamma: below the training average, lowered the predicted value",
    ]
    # A column under 1% of the row's total |contribution| is noise, not a reason.
    linear.coef_ = np.array([3.0, -2.0, 0.01])
    assert _explained(pipeline, rows, "regression")[3][0][2] is None


def test_each_method_names_the_reference_it_measures_against():
    """A skewed column (training median 22k, average 30k): a row between them is BELOW the
    average a linear model measures against, so it lowers a positive coefficient's score."""

    rng = np.random.default_rng(8)
    train = pd.DataFrame({"income": rng.lognormal(10, 0.8, 2000), "noise": rng.normal(size=2000)})
    y = (train["income"] + rng.normal(scale=5000, size=2000) > 30_000).astype(int)
    median, mean = train["income"].median(), train["income"].mean()
    assert median < 26_000 < mean
    row = pd.DataFrame({"income": [26_000.0], "noise": [0.0]})
    linear = _pipeline("logistic_regression", "binary", train, y, ["income", "noise"], [])
    _fmap, _b, explanation, texts = _explained(linear, row, "binary")
    assert np.median(train["income"]) == pytest.approx(median)
    assert explanation.references["income"] == pytest.approx(mean, rel=1e-9)
    assert explanation.contributions[0, 0] < 0
    assert texts[0][0] == "Income: below the training average, lowered the score"
    for family, start, end in (("xgboost", "Income: above the training median, ", " compared with the model's average row"),
                               ("random_forest", "Income: above the training median, ", "the score")):
        high = pd.DataFrame({"income": [60_000.0], "noise": [0.0]})  # where the trees respond
        _fmap, _b, explanation, texts = _explained(_pipeline(family, "binary", train, y, ["income", "noise"], []),
                                                   high, "binary")
        income = [text for text in texts[0] if text and text.startswith("Income")]
        assert income and income[0].startswith(start) and income[0].endswith(end), income
        assert explanation.references["income"] == pytest.approx(median)


def _decile_reference(**columns) -> dict:
    """A training reference (the package manifest's ``reference_summary`` shape) with ten
    equal-share bins per column: edges 1..9, 10 training rows per bin, none missing."""

    return {"format": "dclab.monitoring.reference.v2", "features": [
        {"name": name, "kind": kind, "edges": [float(edge) for edge in range(1, 10)], "counts": [10] * 10 + [0],
         **({"min": 0.0, "max": 10.0} if kind == "date" else {})}
        for name, kind in columns.items()]}


def _one_driver_linear(names=("monthly_fee", "other")) -> Pipeline:
    """A linear regression whose only non-zero coefficient is on the first column (+3). That
    column is skewed: training median 3.5 (a missing cell's fill), average 5.83."""

    train = pd.DataFrame({name: [1.0, 2.0, 3.0, 4.0, 5.0, 6.0] for name in names}).assign(
        **{names[0]: [1.0, 2.0, 3.0, 4.0, 5.0, 20.0]})
    pipeline = Pipeline([("prep", build_preprocessor(list(names), [])),
                         ("model", make_model("linear_regression", task_type="regression"))])
    pipeline.fit(train, np.arange(6.0))
    linear = pipeline.named_steps["model"].named_steps["m"]
    linear.coef_, linear.intercept_ = np.array([3.0] + [0.0] * (len(names) - 1)), 1.0
    return pipeline


def test_numbers_are_described_by_their_training_quantile_in_exact_sentences():
    """With the model's training reference, a number is high (top N%), low (bottom N%) or
    mid-range among the TRAINING values (right-closed bins, as monitoring counts them); the
    direction and the reference the method measures against are unchanged."""

    pipeline = _one_driver_linear()
    rows = pd.DataFrame({"monthly_fee": [10.0, 9.0, 5.0, 1.5, 0.0, np.nan], "other": [3.5] * 6})
    plain = _explained(pipeline, rows, "regression")
    with_reference = _explained(pipeline, rows, "regression", reference=_decile_reference(monthly_fee="numeric"))
    np.testing.assert_array_equal(plain[2].contributions, with_reference[2].contributions)  # the words only
    assert [row[0] for row in with_reference[3]] == [
        "Monthly fee: high (top 10%), raised the predicted value compared with the training average",
        "Monthly fee: high (top 20%), raised the predicted value compared with the training average",
        "Monthly fee: mid-range, lowered the predicted value compared with the training average",
        "Monthly fee: low (bottom 20%), lowered the predicted value compared with the training average",
        "Monthly fee: low (bottom 10%), lowered the predicted value compared with the training average",
        "Monthly fee: missing, lowered the predicted value compared with the training average",
    ]
    for plain_row, quantile_row in zip(plain[3], with_reference[3]):  # same direction either way
        assert plain_row[0].split(", ")[1].split()[0] == quantile_row[0].split(", ")[1].split()[0]
    # Dates: earliest / latest instead of low / high.
    dated = _explained(pipeline, pd.DataFrame({"monthly_fee": [9.5, 0.5, 12.0], "other": [3.5] * 3}), "regression",
                       reference=_decile_reference(monthly_fee="date"))[3]
    assert [row[0].split(", ")[0] for row in dated] == ["Monthly fee: late (latest 10%)",
                                                         "Monthly fee: early (earliest 10%)",
                                                         "Monthly fee: after the training period"]


def test_real_training_references_never_call_a_tail_value_mid_range():
    """Bins from ``monitoring.numeric_reference`` for awkward shapes: a value is high / low only
    when its bin lies inside the tail (share rounded up), mid-range only between the 20th and
    80th percentiles; a straddling bin (a common flag, a capped column's top) gets None, i.e. the
    method's reference wording. Dates past the training range say so."""

    from app.engine.serving.monitoring import numeric_reference

    def keys(train, values, kind="numeric"):
        bins = reasons._training_bins(numeric_reference(pd.Series(train, dtype=float), kind=kind))
        return list(reasons._quantile_keys(np.asarray(values, dtype=float), bins))

    assert keys([0.0] * 700 + [1.0] * 300, [0.0, 1.0]) == [None, None]  # a 30% flag: no tail, no middle
    assert keys([0.0] * 996 + [3.0] * 4, [0.0, 3.0, 100.0]) == [None, "high:1", "high:1"]  # rare counts
    capped = list(np.linspace(0, 999, 795)) + [1000.0] * 205  # 20.5% tied at the cap
    assert keys(capped, [999.0, 1000.0, 1001.0]) == [None, None, None]
    assert keys(capped, [1.0, 500.0]) == ["low:10", "mid"]
    assert keys(list(np.arange(1000.0)), [995.0, 801.0, 2.0, np.inf, -np.inf]) == ["high:10", "high:20", "low:10", None, None]
    dates = list(np.linspace(1.6e9, 1.7e9, 500))
    assert keys(dates, [1.8e9, 1.5e9, 1.699e9], kind="date") == ["after", "before", "high:10"]


def test_an_unusable_training_reference_falls_back_to_the_methods_reference():
    pipeline = _one_driver_linear()
    rows = pd.DataFrame({"monthly_fee": [10.0], "other": [3.5]})
    expected = "Monthly fee: above the training average, raised the predicted value"
    for reference in (None, {"features": [{"name": "monthly_fee", "kind": "numeric", "edges": [2.0], "counts": [60, 0, 0]}]},
                      {"features": [{"name": "monthly_fee", "kind": "numeric", "edges": [1.0, 2.0], "counts": [5, 5]}]},
                      {"features": [{"name": "monthly_fee", "kind": "categorical", "categories": []}]}):
        assert _explained(pipeline, rows, "regression", reference=reference)[3][0][0] == expected, reference


def test_categories_missing_and_unseen_values_read_as_plain_sentences():
    rng = np.random.default_rng(3)
    train = pd.DataFrame({"plan_type": rng.choice(["family_5g", "solo", "duo"], 400).astype(object),
                          "monthly_fee": rng.uniform(1, 10, 400)})
    y = ((train["plan_type"] == "family_5g") | (train["monthly_fee"] > 8)).astype(int)
    pipeline = _pipeline("logistic_regression", "binary", train, y, ["monthly_fee"], ["plan_type"])
    rows = pd.DataFrame({"plan_type": pd.Series(["family_5g", "never-seen", np.nan], dtype=object),
                         "monthly_fee": [9.5, 5.0, 1.2]})
    fmap, _b, explanation, texts = _explained(pipeline, rows, "binary",
                                              reference=_decile_reference(monthly_fee="numeric"))
    plan, fee = fmap.names.index("plan_type"), fmap.names.index("monthly_fee")

    def verb(row, column):
        return "raised" if explanation.contributions[row, column] > 0 else "lowered"

    expected_plan = ["Plan type: family_5g", "Plan type: a value not seen in training", "Plan type: missing"]
    expected_fee = ["Monthly fee: high (top 10%)", "Monthly fee: mid-range", "Monthly fee: low (bottom 20%)"]
    for row in range(3):
        present = [text for text in texts[row] if text]
        if abs(explanation.contributions[row, plan]) > 0:
            assert f"{expected_plan[row]}, {verb(row, plan)} the score compared with the training average" in present
        if abs(explanation.contributions[row, fee]) > 0:
            assert f"{expected_fee[row]}, {verb(row, fee)} the score compared with the training average" in present
        assert not any(_CAUSAL.search(text) for text in present)
    assert texts[0][0] is not None and texts[0][0].startswith(("Plan type: family_5g, ", "Monthly fee: high (top 10%), "))


@pytest.mark.parametrize(("family", "reference_words"), [
    ("xgboost_regressor", "compared with the model's average row"),
    ("random_forest_regressor", "compared with the training median"),
    ("linear_regression", "compared with the training average"),
])
def test_each_method_still_names_its_reference_next_to_a_quantile(family, reference_words):
    rng = np.random.default_rng(6)
    train = pd.DataFrame({"monthly_fee": rng.uniform(0, 10, 500), "noise": rng.normal(size=500)})
    pipeline = _pipeline(family, "regression", train, 4 * train["monthly_fee"], ["monthly_fee", "noise"], [])
    rows = pd.DataFrame({"monthly_fee": [9.8, 0.3], "noise": [0.0, 0.0]})
    _fmap, _b, explanation, texts = _explained(pipeline, rows, "regression",
                                               reference=_decile_reference(monthly_fee="numeric"))
    assert texts[0][0] == f"Monthly fee: high (top 10%), raised the predicted value {reference_words}"
    assert texts[1][0] == f"Monthly fee: low (bottom 10%), lowered the predicted value {reference_words}"
    assert explanation.contributions[0, 0] > 0 > explanation.contributions[1, 0]


def test_display_names_are_readable_but_traceable():
    assert explain.display_names(["monthly_fee", "tenure", "plan__type_", "=x", "MRR"]) == [
        "Monthly fee", "Tenure", "Plan type", "=x", "MRR"]
    # A readable name that would equal another column's keeps each column's own spelling.
    assert explain.display_names(["monthly_fee", "Monthly fee", "tenure"]) == ["monthly_fee", "Monthly fee", "Tenure"]
    assert explain.display_names(["monthly fee", "Monthly_fee"]) == ["monthly fee", "Monthly_fee"]
    assert explain.display_names(["___", ""]) == ["___", "(blank)"]


# --- occlusion ---------------------------------------------------------------------------------


@pytest.mark.parametrize("family", sorted(OCCLUSION_FAMILIES))
def test_occlusion_is_deterministic_and_finds_a_planted_driver(family):
    task = "regression" if family.endswith("regressor") else "binary"
    rng = np.random.default_rng(7)
    n = 400
    train = pd.DataFrame({"driver": rng.normal(size=n), "noise": rng.normal(size=n),
                          "group": rng.choice(["u", "v", "w"], n).astype(object)})
    y = 5 * train["driver"] + rng.normal(scale=0.1, size=n) if task == "regression" else (train["driver"] > 0).astype(int)
    pipeline = _pipeline(family, task, train, y, ["driver", "noise"], ["group"])
    rows = pd.DataFrame({"driver": np.r_[np.linspace(1.5, 3, 20), np.linspace(-3, -1.5, 20)],
                         "noise": rng.normal(size=40), "group": rng.choice(["u", "v", "w"], 40).astype(object)})
    fmap, baselines, explanation, texts = _explained(pipeline, rows, task)
    assert (explanation.method, explanation.additive, explanation.bias) == ("occlusion", False, None)
    assert explain.available_summary(explanation)["method_label"] == "occlusion (approximate; not additive)"
    top = explanation.top(1)[:, 0] == fmap.names.index("driver")
    driver = explanation.contributions[:, fmap.names.index("driver")]
    above = rows["driver"].to_numpy() > baselines["driver"]
    assert above[:20].all() and not above[20:].any()
    # A high driver raises the score and is the top reason.
    assert top[:20].mean() >= 0.9 and (driver[:20] > 0).mean() >= 0.95
    # A low one never raises it. Binary: the training median (the baseline) is on the low side of
    # the boundary too, so a tree model may score both the same -- occlusion then says nothing.
    assert (driver[20:] <= 0).all()
    if task == "regression":
        assert top[20:].mean() >= 0.9 and (driver[20:] < 0).mean() >= 0.95
    for row, up, effect in zip(texts, above, driver):  # the words say what the numbers say
        if row[0] and row[0].startswith("Driver"):
            assert row[0].startswith("Driver: above the training median" if up else "Driver: below the training median")
            assert ("raised" in row[0]) == (effect > 0)
    again = _explained(pipeline, rows, task)[2]
    assert np.array_equal(again.contributions, explanation.contributions)  # bit for bit


def test_occlusion_sets_a_whole_one_hot_column_to_its_baseline():
    frame = _frame()
    pipeline = _fitted("binary", "random_forest", frame, _target("binary", frame))
    rows = _scoring_rows()
    fmap, baselines, explanation, _texts = _explained(pipeline, rows, "binary")
    at_baseline = rows.assign(c=baselines["c"])
    expected = pipeline.predict_proba(rows)[:, 1] - pipeline.predict_proba(at_baseline)[:, 1]
    np.testing.assert_allclose(explanation.contributions[:, fmap.names.index("c")], expected, rtol=0, atol=1e-15)


def test_occlusion_cost_is_capped(monkeypatch):
    frame = _frame()
    pipeline = _fitted("binary", "random_forest", frame, _target("binary", frame))
    rows = _scoring_rows()
    capped = explain.explain_rows(pipeline, rows, task_type="binary", max_rows=25)
    assert (capped.rows_explained, capped.rows_total) == (25, len(rows))
    full = explain.explain_rows(pipeline, rows, task_type="binary")
    assert np.array_equal(full.contributions[:25], capped.contributions)  # rows are independent
    note = explain.available_summary(capped)["note"]
    assert note == f"explained the first 25 of {len(rows)} rows; the reason columns are empty for the rest"
    # The work budget counts rows x input columns x trees (5 columns, 80 trees here).
    monkeypatch.setattr(reasons, "OCCLUSION_MAX_WORK", 5 * 80 * 30)
    assert explain.explain_rows(pipeline, rows, task_type="binary").rows_explained == 30
    monkeypatch.setattr(reasons, "OCCLUSION_MAX_FEATURES", 3)
    with pytest.raises(explain.ExplainUnavailable, match="more than 3 input columns"):
        explain.explain_rows(pipeline, rows, task_type="binary")


@pytest.mark.parametrize("family", ["xgboost", "lightgbm", "catboost", "logistic_regression", "random_forest"])
def test_results_do_not_depend_on_the_chunk_size(monkeypatch, family):
    frame = _frame()
    loaded = _load(*_package(_fitted("multiclass", family, frame, _target("multiclass", frame)), family, "multiclass"))
    rows = _scoring_rows()
    default = explain.explain_rows(loaded.pipeline, rows, task_type="multiclass")
    monkeypatch.setattr(reasons, "CHUNK_ROWS", 37)
    small = explain.explain_rows(loaded.pipeline, rows, task_type="multiclass")
    assert small.rows_explained == default.rows_explained == len(rows)
    np.testing.assert_array_equal(small.contributions, default.contributions)


# --- time: heartbeats, deadline, cancellation ------------------------------------------------------


def _slow_clock(monkeypatch, model, seconds: float = 6.0) -> list[float]:
    """A fake clock that each predict of ``model`` advances by ``seconds``."""

    now = [0.0]
    monkeypatch.setattr(reasons, "_clock", lambda: now[0])
    original = model.predict_proba

    def slow(rows):
        now[0] += seconds
        return original(rows)

    monkeypatch.setattr(model, "predict_proba", slow)
    return now


def test_heartbeats_fire_while_one_long_occlusion_chunk_runs(monkeypatch):
    frame = _frame()
    pipeline = _fitted("binary", "random_forest", frame, _target("binary", frame))
    now = _slow_clock(monkeypatch, pipeline.named_steps["model"])
    beats: list[float] = []
    explanation = explain.explain_rows(pipeline, _scoring_rows().head(20), task_type="binary",
                                       on_progress=lambda: beats.append(now[0]))
    assert explanation.rows_explained == 20  # one chunk: 6 predicts x 6 s
    assert len(beats) >= 3 and all(later - earlier <= 12 for earlier, later in zip([0.0, *beats], beats))


def test_the_deadline_stops_at_a_chunk_boundary_and_scoring_still_succeeds(monkeypatch):
    frame = _frame()
    pipeline = _fitted("binary", "random_forest", frame, _target("binary", frame))
    rows = _scoring_rows().head(40)
    unlimited = explain.explain_rows(pipeline, rows, task_type="binary")
    _slow_clock(monkeypatch, pipeline.named_steps["model"])
    monkeypatch.setattr(reasons, "CHUNK_ROWS", 10)
    stopped = explain.explain_rows(pipeline, rows, task_type="binary", deadline_seconds=100)
    assert 0 < stopped.rows_explained < 40 and stopped.rows_explained % 10 == 0
    np.testing.assert_array_equal(stopped.contributions, unlimited.contributions[: stopped.rows_explained])
    assert explain.available_summary(stopped)["note"].startswith(f"explained the first {stopped.rows_explained} of 40")
    # Out of time before one chunk finished: no reasons, and the scores are written.
    spec = scoring_spec(_result(), pipeline)
    scored = score_frame(_scored_rows(), spec, pipeline, reasons=True, reasons_deadline_seconds=10)
    assert scored.reasons["available"] is False and scored.reasons["unavailable_reason"] == reasons.TOO_LONG
    assert scored.predictions["probability"].notna().all()


def test_a_cancellation_during_the_explanation_is_not_swallowed(monkeypatch):
    frame = _frame()
    pipeline = _fitted("binary", "random_forest", frame, _target("binary", frame))
    spec = scoring_spec(_result(), pipeline)
    _slow_clock(monkeypatch, pipeline.named_steps["model"])
    calls: list[int] = []

    def heartbeat():
        calls.append(1)
        if len(calls) > 1:  # the first is the scoring's own checkpoint
            raise RunCancelledError()

    with pytest.raises(RunCancelledError):
        score_frame(_scored_rows(), spec, pipeline, on_progress=heartbeat, reasons=True)
    assert len(calls) == 2


# --- values, names and failures ----------------------------------------------------------------------


@pytest.mark.parametrize("family", ["logistic_regression", "random_forest"])
def test_missing_and_unseen_values_are_described_and_never_crash(family):
    rng = np.random.default_rng(2)
    train = pd.DataFrame({"level": rng.normal(size=300), "group": rng.choice(["p", "q", "r"], 300).astype(object)})
    y = ((train["group"] == "p") | (train["level"] > 0.5)).astype(int)
    pipeline = _pipeline(family, "binary", train, y, ["level"], ["group"])
    # Missing cells as a CSV load gives them (NaN), which the fitted imputer fills.
    rows = pd.DataFrame({"level": [np.nan, 2.0, np.nan], "group": pd.Series(["never-seen", np.nan, np.nan], dtype=object)})
    _fmap, _b, explanation, texts = _explained(pipeline, rows, "binary")
    flat = [text for row in texts for text in row if text]
    if family == "logistic_regression":  # filled with the training median, which moved the score
        assert "Group: a value not seen in training, " in " ".join(text for text in texts[0] if text)
        assert any(text.startswith("Level: missing") and text.endswith("compared with the training average")
                   for text in texts[0] if text)
        assert any(text.startswith("Group: missing") for text in texts[1] if text)
    else:  # occlusion: a missing value is filled with the baseline itself, so it moved nothing
        assert not any(text.startswith(("Level: missing", "Group: missing")) for text in flat)
        assert texts[2] == [None, None, None]
    assert np.all(np.isfinite(explanation.contributions))


_REFERENCE = r"the training (?:median|average)"
_GRAMMAR = re.compile(
    rf"^(?P<name>[^:]+?): (?:(?:below|above|at) {_REFERENCE}"
    rf"|(?:earlier|later) than {_REFERENCE} date|at {_REFERENCE} date"
    r"|high \(top \d+%\)|low \(bottom \d+%\)|late \(latest \d+%\)|early \(earliest \d+%\)|mid-range"
    r"|(?:after|before) the training period"
    r"|missing|a value not seen in training|(?P<value>[^,]+)), "
    r"(?:raised|lowered) (?:the score|the predicted value"
    r"|the predicted class's (?:lead over the other classes|probability))"
    r"(?: compared with (?:the model's average row|the training average|the training median"
    r"|the most common training value))?$"
)
_CAUSAL = re.compile(r"\b(?:because|caus\w*|rose|due to|led to|drove|drives|results? in)\b", re.IGNORECASE)


@pytest.mark.parametrize(("task", "family"), [("binary", "logistic_regression"), ("multiclass", "random_forest"),
                                              ("regression", "xgboost_regressor"), ("binary", "catboost"),
                                              ("multiclass", "lightgbm")])
def test_reasons_use_original_names_and_never_the_rows_numbers(task, family):
    frame = _frame()
    loaded = _load(*_package(_fitted(task, family, frame, _target(task, frame)), family, task))
    rows = _scoring_rows()
    fmap, _b, explanation, texts = _explained(loaded.pipeline, rows, task)
    shown = dict(zip(explain.display_names(fmap.names), fmap.names))
    encoded = set(loaded.pipeline.named_steps["prep"].get_feature_names_out())
    for row_index, row in enumerate(texts):
        for text in filter(None, row):
            match = _GRAMMAR.match(text)
            assert match, text  # only the column name, a coarse bucket or category, a direction, a reference
            assert match["name"] in shown and not any(name in text for name in encoded if "__" in name)
            assert not _CAUSAL.search(text), text
            if match["value"] is not None:
                assert match["value"] in {str(value) for value in fmap.categories[shown[match["name"]]]}
            if explanation.method == "tree_shap":
                assert text.endswith("compared with the model's average row")
            for name in ("a", "b", "when"):
                value = rows[name].iloc[row_index]
                if pd.notna(value):
                    assert not {repr(value), f"{value:g}", f"{value:.2f}", f"{value:.1f}"} & set(re.findall(r"[-\d.]+", text))


def test_names_are_sanitized_capped_and_never_start_like_a_formula():
    long_name = "x" * 100
    names = ["=HYPERLINK(1)", "na‮me\nwith\ttabs", long_name]
    rng = np.random.default_rng(5)
    train = pd.DataFrame({name: rng.normal(size=120) for name in names})
    y = (train[names[0]] + train[names[1]] - train[long_name] > 0).astype(int)
    pipeline = _pipeline("logistic_regression", "binary", train, y, names, [])
    _fmap, _b, _explanation, texts = _explained(pipeline, train.head(10), "binary")
    flat = [text for row in texts for text in row if text]
    assert flat and all("‮" not in text and "\n" not in text and "\t" not in text for text in flat)
    assert all(not text.startswith(("=", "+", "-", "@")) for text in flat)
    assert any(text.startswith("'=HYPERLINK(1): ") for text in flat)
    assert any(text.startswith("Name with tabs: ") for text in flat)
    assert any(text.startswith("X" + "x" * 60 + "...: ") for text in flat)
    assert explain.plain_text("", 10) == "(blank)" and explain.plain_text(3.0, 10) == "3"


def test_a_baseline_model_has_no_reasons():
    frame = _frame()
    pipeline = _fitted("binary", "majority", frame, _target("binary", frame))
    with pytest.raises(explain.ExplainUnavailable, match="same score"):
        explain.explain_rows(pipeline, frame, task_type="binary")


# --- scoring: appended columns, failure never fails scoring --------------------------------------


def _scored_rows() -> pd.DataFrame:
    return _frame(n=40, seed=5).assign(when=lambda f: pd.to_datetime(f["when"], unit="s").dt.strftime("%Y-%m-%d"))


def test_scoring_appends_reason_columns_and_keeps_every_other_column():
    frame = _frame()
    loaded = _load(*_package(_fitted("binary", "lightgbm", frame, _target("binary", frame)), "lightgbm"))
    spec = scoring_spec(_result(), loaded.pipeline)
    rows = _scored_rows()
    plain = score_frame(rows, spec, loaded.pipeline)
    assert plain.reasons is None and not set(explain.REASON_COLUMNS) & set(plain.predictions.columns)
    explained = score_frame(rows, spec, loaded.pipeline, reasons=True)
    assert list(explained.predictions.columns) == list(plain.predictions.columns) + list(explain.REASON_COLUMNS)
    pd.testing.assert_frame_equal(explained.predictions[plain.predictions.columns], plain.predictions, check_exact=True)
    assert explained.contract == plain.contract
    assert explained.reasons == {
        "available": True, "method": "tree_shap", "method_label": reasons.METHOD_LABELS["tree_shap"],
        "additive": True, "top_k": 3, "rows_explained": len(rows), "note": None, "unavailable_reason": None,
    }
    assert explained.predictions["reason_1"].notna().all()
    # Dates are described against the training median date, never as a number.
    assert any("When: " in str(text) and "median date" in str(text)
               for text in explained.predictions[list(explain.REASON_COLUMNS)].to_numpy().ravel())
    capped = score_frame(rows, spec, loaded.pipeline, reasons=True, reasons_max_rows=5)
    assert capped.predictions["reason_1"].iloc[:5].notna().all() and capped.predictions["reason_1"].iloc[5:].isna().all()
    assert capped.reasons["rows_explained"] == 5 and capped.reasons["note"].startswith("explained the first 5 of 40")


def test_reason_column_names_are_reserved_only_when_reasons_are_on():
    frame = _frame().assign(reason_1=lambda f: [f"id{index}" for index in range(len(f))])
    pipeline = _fitted("binary", "logistic_regression", frame.drop(columns=["reason_1"]), _target("binary", frame))
    result = _result()
    result["task"] = {**result["task"], "entity_id": "reason_1"}
    spec = scoring_spec(result, pipeline)
    rows = _scored_rows().assign(reason_1=[f"id{index}" for index in range(40)])
    assert list(score_frame(rows, spec, pipeline).predictions.columns[:2]) == ["reason_1", "row_number"]
    explained = score_frame(rows, spec, pipeline, reasons=True).predictions
    assert explained.columns[0] == "row_number" and list(explained.columns).count("reason_1") == 1


@pytest.mark.parametrize("failure", [RuntimeError("boom at /internal/path"), MemoryError()])
def test_scoring_still_succeeds_when_the_explainer_raises(monkeypatch, failure):
    frame = _frame()
    pipeline = _fitted("binary", "logistic_regression", frame, _target("binary", frame))
    spec = scoring_spec(_result(), pipeline)
    rows = _scored_rows()
    plain = score_frame(rows, spec, pipeline)
    # An expected refusal is said in its own plain words.
    majority = _fitted("binary", "majority", frame, _target("binary", frame))
    refused = score_frame(rows, scoring_spec(_result(), majority), majority, reasons=True)
    assert refused.reasons["unavailable_reason"].startswith("this model gives every row the same score")

    def broken(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr("app.engine.explain.explain_rows", broken)
    scored = score_frame(rows, spec, pipeline, reasons=True)
    pd.testing.assert_frame_equal(scored.predictions[plain.predictions.columns], plain.predictions, check_exact=True)
    assert scored.predictions[list(explain.REASON_COLUMNS)].isna().all().all()
    assert scored.reasons["available"] is False and scored.reasons["method"] is None
    assert scored.reasons["unavailable_reason"] == "the reasons step failed on this file; the scores are not affected"
    assert "/internal/path" not in str(scored.reasons)
