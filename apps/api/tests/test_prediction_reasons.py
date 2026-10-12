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


def _explained(pipeline, rows, task, **kwargs):
    fmap = explain.feature_map(pipeline)
    baselines = explain.training_baselines(pipeline, fmap)
    explanation = explain.explain_rows(pipeline, rows, fmap, baselines, task_type=task, **kwargs)
    texts = explain.reason_texts(explanation, rows, fmap, task_type=task, date_columns={"when"})
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
        "alpha: above the training average, raised the predicted value",
        "beta: above the training average, lowered the predicted value",
        "gamma: above the training average, raised the predicted value",
    ]
    assert texts[1] == [
        "alpha: below the training average, lowered the predicted value",
        "beta: below the training average, raised the predicted value",
        "gamma: below the training average, lowered the predicted value",
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
    assert texts[0][0] == "income: below the training average, lowered the model's score"
    for family, start, end in (("xgboost", "income: above the training median, ", " compared with the model's average row"),
                               ("random_forest", "income: above the training median, ", "the model's score")):
        high = pd.DataFrame({"income": [60_000.0], "noise": [0.0]})  # where the trees respond
        _fmap, _b, explanation, texts = _explained(_pipeline(family, "binary", train, y, ["income", "noise"], []),
                                                   high, "binary")
        income = [text for text in texts[0] if text and text.startswith("income")]
        assert income and income[0].startswith(start) and income[0].endswith(end), income
        assert explanation.references["income"] == pytest.approx(median)


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
        if row[0] and row[0].startswith("driver"):
            assert row[0].startswith("driver: above the training median" if up else "driver: below the training median")
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
        assert "group: a value not seen in training, " in " ".join(text for text in texts[0] if text)
        assert any(text.startswith("level: missing") and text.endswith("compared with the training average")
                   for text in texts[0] if text)
        assert any(text.startswith("group: missing") for text in texts[1] if text)
    else:  # occlusion: a missing value is filled with the baseline itself, so it moved nothing
        assert not any(text.startswith(("level: missing", "group: missing")) for text in flat)
        assert texts[2] == [None, None, None]
    assert np.all(np.isfinite(explanation.contributions))


_REFERENCE = r"the training (?:median|average)"
_GRAMMAR = re.compile(
    rf"^(?P<name>[^:=]+?)(?:: (?:below|above|at) {_REFERENCE}"
    rf"|: (?:earlier|later) than {_REFERENCE} date|: at {_REFERENCE} date"
    r"|: missing|: a value not seen in training| = (?P<value>[^,]+)), "
    r"(?:raised|lowered) (?:the model's score|the predicted value"
    r"|the predicted class's (?:lead over the other classes|probability))"
    r"(?: compared with (?:the model's average row|the training average|the training median"
    r"|the most common training value))?$"
)


@pytest.mark.parametrize(("task", "family"), [("binary", "logistic_regression"), ("multiclass", "random_forest"),
                                              ("regression", "xgboost_regressor"), ("binary", "catboost"),
                                              ("multiclass", "lightgbm")])
def test_reasons_use_original_names_and_never_the_rows_numbers(task, family):
    frame = _frame()
    loaded = _load(*_package(_fitted(task, family, frame, _target(task, frame)), family, task))
    rows = _scoring_rows()
    fmap, _b, explanation, texts = _explained(loaded.pipeline, rows, task)
    encoded = set(loaded.pipeline.named_steps["prep"].get_feature_names_out())
    for row_index, row in enumerate(texts):
        for text in filter(None, row):
            match = _GRAMMAR.match(text)
            assert match, text  # only the column name, a coarse bucket or category, a direction, a reference
            assert match["name"] in fmap.names and not any(name in text for name in encoded if "__" in name)
            if match["value"] is not None:
                assert match["value"] in {str(value) for value in fmap.categories[match["name"]]}
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
    assert any(text.startswith("name with tabs: ") for text in flat)
    assert any(text.startswith("x" * 61 + "...: ") for text in flat)
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
    assert any("when: " in str(text) and "median date" in str(text)
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
