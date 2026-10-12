"""Per-row prediction reasons (P7.7-A). Pure: no database, storage, settings or subprocess.

A reason is one input column that moved THIS MODEL's score for one row, named by the
scoring file's own column (never an encoded column such as a one-hot indicator). Reasons
describe what the model used to produce its score; they are not causes in the world, and a
model can lean on a column that merely travels with the outcome.

Methods, chosen from the fitted estimator (``method="auto"``), each with its own reference
that every sentence names:

* ``tree_shap`` -- XGBoost (``pred_contribs``), LightGBM (``pred_contrib``) and CatBoost
  (``ShapValues``) native per-row contributions, measured against the model's average
  output over its training rows ("compared with the model's average row"). Additive: per
  row the contributions plus the bias equal the model's raw output (log-odds for
  classifiers, the predicted value for regression) within a tolerance sized to the terms
  and the library's precision. Checked on every chunk; a mismatch makes reasons unavailable
  instead of writing wrong ones.
* ``linear_contributions`` -- logistic / linear / ridge / lasso / elastic-net models:
  coefficient x standardized value, i.e. measured against the TRAINING AVERAGE of each
  column (the model's own standardization). Additive in the linear predictor.
* ``occlusion`` -- any other model (random forest, extra trees, scikit-learn gradient
  boosting): one input column at a time is set to its training baseline (the median or most
  frequent value the fitted imputer learned on the TRAINING rows) and the change in the
  model's score is measured. Approximate and NOT additive; deterministic (no sampling). A
  row whose value already equals the baseline (or was missing and filled with it) gets 0.

The explained score: binary tasks, the flagged (positive) class whatever the label;
regression, the predicted value; multiclass, the PREDICTED class against the others -- for
the additive methods its margin minus the mean margin of the other classes (for softmax
models: log of its probability over the geometric mean of the others' probabilities, its
"lead"; a column can raise this lead while lowering the class's probability when it raises
a rival class more), for occlusion its probability. A model fit on two classes of a
multiclass task explains the predicted class's log-odds against the other one.

Data: only the rows being scored (prepared exactly as for prediction) and statistics of the
fitted preprocessor (training rows) are used; no holdout value is read. Cost is bounded by
the first ``max_rows`` rows, a work budget sized by rows x trees (x input columns for
occlusion), work-sized chunks, and a wall-clock deadline that stops at a chunk / column
boundary. ``on_progress`` (the job heartbeat, which may raise the job's cancellation, a
``BaseException`` never caught here) is called at most every ``HEARTBEAT_SECONDS`` between
library calls.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

TREE_SHAP, LINEAR, OCCLUSION = "tree_shap", "linear_contributions", "occlusion"
METHODS = (TREE_SHAP, LINEAR, OCCLUSION)
METHOD_LABELS = {
    TREE_SHAP: "tree SHAP from the model's own trees, compared with the model's average row (additive)",
    LINEAR: "coefficient x value, compared with the training average (additive)",
    OCCLUSION: "occlusion (approximate; not additive)",
}
TOP_K = 3
REASON_COLUMNS = tuple(f"reason_{rank}" for rank in range(1, TOP_K + 1))
MAX_ROWS = 100_000  # rows explained per scoring (the first ones)
DEADLINE_SECONDS = 120.0  # the whole explanation; stops at a chunk / column boundary
HEARTBEAT_SECONDS = 10.0
OCCLUSION_MAX_FEATURES = 200
# Work = rows x trees evaluated (x input columns for occlusion). Measured on one laptop core:
# a 500-tree forest predicts ~2e6 row-trees/s; XGBoost tree SHAP at depth 16 ~5e5 row-trees/s.
OCCLUSION_MAX_WORK = 100_000_000
OCCLUSION_CHUNK_WORK = 20_000_000  # one chunk, all columns: ~10 s at worst
SHAP_MAX_WORK = 40_000_000
SHAP_CHUNK_WORK = 2_000_000  # one library call: ~4 s at worst
CHUNK_ROWS = 10_000
CHUNK_CELLS = 4_000_000  # contribution values held at once (rows x classes x encoded columns)
MIN_EFFECT = 1e-12  # a smaller contribution is never a reason ...
RELATIVE_EFFECT = 0.01  # ... nor one under 1% of the row's total |contribution|
# Additivity, per row: |sum + bias - raw| <= rtol x (|bias| + sum|contribution|) + 1e-9.
# XGBoost sums one float32 value per boosting round into the margin and into each
# contribution; recursive float32 summation errs by up to ~(rounds x 2**-24) of the terms'
# size, and cancelling terms make the visible sizes smaller than the summed ones: measured
# worst 1.4e-4 at 2,000 rounds (bound 4 x rounds x 2**-24 = 4.8e-4), 8e-7 at 80 rounds.
# LightGBM, CatBoost and the linear path compute in float64.
FLOAT32_UNIT = 2.0 ** -24
FLOAT64_RTOL = 1e-9
ADDITIVITY_ATOL = 1e-9
NAME_CAP, VALUE_CAP = 64, 40
_BOOSTERS = frozenset({
    "XGBClassifier", "XGBRegressor", "LightGBMBoosterModel", "LGBMClassifier", "LGBMRegressor",
    "CatBoostClassifier", "CatBoostRegressor",
})
_SAME_SCORE = "this model gives every row the same score, so no input column changed it"
TOO_LONG = "explaining the rows took longer than the time allowed; the scores are not affected"
_FORMULA_START = ("=", "+", "-", "@")
_clock = time.monotonic  # tests replace it


class ExplainUnavailable(Exception):
    """Reasons cannot be computed for this model or file; ``str(exc)`` is plain public text."""


class _OutOfTime(Exception):
    """The deadline passed; the chunk in progress is dropped."""


@dataclass(frozen=True)
class FeatureMap:
    """The pipeline's input columns and the encoded columns each one became."""

    names: tuple[str, ...]  # pipeline input order (the scoring file's column names)
    roles: tuple[str, ...]  # "numeric" | "categorical"
    blocks: tuple[tuple[int, int], ...]  # [start, stop) of each name's encoded columns
    categories: Mapping[str, tuple[Any, ...]]
    width: int


@dataclass(frozen=True)
class Explanation:
    method: str
    additive: bool
    contributions: np.ndarray  # (rows explained, len(names)), on the explained score's scale
    bias: np.ndarray | None  # additive methods: the base value per row
    raw: np.ndarray | None  # additive methods: the explained raw output per row
    rows_total: int
    # Numeric column -> the value its sentences compare the row with (the training average
    # for linear contributions, else the training median).
    references: Mapping[str, float] = field(default_factory=dict)

    @property
    def rows_explained(self) -> int:
        return int(self.contributions.shape[0])

    def top(self, k: int = TOP_K) -> np.ndarray:
        """(rows, k) column indices by |contribution| (ties: input order); -1 = no reason
        (below ``MIN_EFFECT`` or ``RELATIVE_EFFECT`` of the row's total)."""

        magnitude = np.abs(self.contributions)
        floor = np.maximum(MIN_EFFECT, RELATIVE_EFFECT * magnitude.sum(axis=1, keepdims=True))
        order = np.argsort(-magnitude, axis=1, kind="stable")[:, :k]
        picked = np.take_along_axis(magnitude, order, axis=1)
        order = np.where(picked > floor, order, -1)
        if order.shape[1] < k:
            order = np.hstack([order, np.full((order.shape[0], k - order.shape[1]), -1)])
        return order


class _Pace:
    """Time-throttled heartbeats and the deadline. ``on_progress`` may raise the job's
    cancellation (a ``BaseException``); nothing here catches it."""

    def __init__(self, on_progress: Callable[[], None] | None, deadline_seconds: float) -> None:
        self.on_progress = on_progress
        self.last = _clock()
        self.deadline = self.last + deadline_seconds

    def beat(self) -> None:
        now = _clock()
        if self.on_progress is not None and now - self.last >= HEARTBEAT_SECONDS:
            self.last = now
            self.on_progress()

    def check(self) -> None:
        self.beat()
        if _clock() >= self.deadline:
            raise _OutOfTime


# --- structure ------------------------------------------------------------------------


def feature_map(pipeline: Any) -> FeatureMap:
    """Input column -> encoded columns of the ``build_preprocessor`` ColumnTransformer."""

    steps = getattr(pipeline, "named_steps", None)
    prep = steps.get("prep") if isinstance(steps, Mapping) else None
    names = [str(name) for name in getattr(pipeline, "feature_names_in_", [])]
    if prep is None or not names:
        raise ExplainUnavailable("the model's input columns are not recorded")
    unsupported = ExplainUnavailable("the model's preprocessing is not supported for reasons")
    blocks: dict[str, tuple[int, int]] = {}
    roles: dict[str, str] = {}
    categories: dict[str, tuple[Any, ...]] = {}
    start = 0
    spans = getattr(prep, "output_indices_", {}) or {}
    for name, transformer, columns in getattr(prep, "transformers_", []):
        if name == "remainder":
            if transformer != "drop":
                raise unsupported
            continue
        first = start
        if name == "num":
            for column in columns:
                blocks[str(column)], roles[str(column)] = (start, start + 1), "numeric"
                start += 1
        elif name == "cat":
            encoder = getattr(transformer, "named_steps", {}).get("onehot")
            fitted = getattr(encoder, "categories_", None)
            if (fitted is None or len(fitted) != len(columns) or getattr(encoder, "drop_idx_", None) is not None
                    or getattr(encoder, "_infrequent_enabled", False)):
                raise unsupported
            for column, values in zip(columns, fitted):
                blocks[str(column)], roles[str(column)] = (start, start + len(values)), "categorical"
                categories[str(column)] = tuple(values)
                start += len(values)
        else:
            raise unsupported
        span = spans.get(name)
        if span is not None and (span.start, span.stop) != (first, start):
            raise unsupported
    if sorted(blocks) != sorted(names):
        raise unsupported
    return FeatureMap(
        names=tuple(names),
        roles=tuple(roles[name] for name in names),
        blocks=tuple(blocks[name] for name in names),
        categories=categories,
        width=start,
    )


def training_baselines(pipeline: Any, fmap: FeatureMap) -> dict[str, Any]:
    """Per input column, the value the fitted imputer fills a missing cell with: the median
    (numeric) or most frequent value (categorical) of the TRAINING rows the pipeline was fit on."""

    prep = pipeline.named_steps["prep"]
    baselines: dict[str, Any] = {}
    for name, transformer, columns in prep.transformers_:
        if name == "remainder":
            continue
        imputer = getattr(transformer, "named_steps", {}).get("imputer")
        statistics = getattr(imputer, "statistics_", None)
        if statistics is None or len(statistics) != len(columns):
            raise ExplainUnavailable("the model's training baselines are not recorded")
        baselines.update({str(column): value for column, value in zip(columns, statistics)})
    if set(baselines) != set(fmap.names):
        raise ExplainUnavailable("the model's training baselines are not recorded")
    return baselines


def _contiguous_inner(model: Any) -> Any:
    return getattr(model, "estimator_", model) if type(model).__name__ == "ContiguousLabelClassifier" else model


def _linear_parts(model: Any) -> tuple[list[Any], Any] | None:
    from sklearn.linear_model import ElasticNet, Lasso, LinearRegression, LogisticRegression, Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    steps = [step for _name, step in model.steps] if isinstance(model, Pipeline) else [model]
    *transforms, last = steps
    linear = (LogisticRegression, LinearRegression, Ridge, Lasso, ElasticNet)
    if isinstance(last, linear) and all(isinstance(step, StandardScaler) for step in transforms):
        return transforms, last
    return None


def choose_method(model: Any) -> str:
    """The method for the fitted model step. Raises ``ExplainUnavailable`` for a baseline
    model (every row gets the same score)."""

    name = type(_contiguous_inner(model)).__name__
    if name in {"DummyClassifier", "DummyRegressor"}:
        raise ExplainUnavailable(_SAME_SCORE)
    if name in _BOOSTERS:
        return TREE_SHAP
    return LINEAR if _linear_parts(model) is not None else OCCLUSION


def _tree_count(model: Any, groups: int) -> int:
    """Trees one prediction of a row evaluates (all classes), for the work budget; 1 for a
    model without trees."""

    inner = _contiguous_inner(model)
    name = type(inner).__name__
    if name in {"XGBClassifier", "XGBRegressor"}:
        return int(inner.get_booster().num_boosted_rounds()) * groups
    if name in {"LightGBMBoosterModel", "LGBMClassifier", "LGBMRegressor"}:
        return int((inner.booster if name == "LightGBMBoosterModel" else inner.booster_).num_trees())
    if name in {"CatBoostClassifier", "CatBoostRegressor"}:
        return int(inner.tree_count_) * groups  # vector leaves: SHAP work grows with the classes
    trees = getattr(inner, "estimators_", None)  # forests: one list; gradient boosting: rounds x classes
    return max(1, int(np.size(np.asarray(trees, dtype=object)))) if trees is not None else 1


def _linear_references(pipeline: Any, fmap: FeatureMap, model: Any) -> dict[str, float]:
    """Per numeric column, the value whose standardized input to the linear model is 0 -- the
    training average its contribution is measured against."""

    transforms, _last = _linear_parts(model)  # type: ignore[misc]
    zero = np.zeros((1, fmap.width))
    for step in reversed(transforms):
        zero = step.inverse_transform(zero)
    references: dict[str, float] = {}
    for name, transformer, columns in pipeline.named_steps["prep"].transformers_:
        if name != "num":
            continue
        scaler = getattr(transformer, "named_steps", {}).get("scaler")
        means = getattr(scaler, "mean_", None)
        scales = getattr(scaler, "scale_", None)
        for position, column in enumerate(columns):
            index = fmap.blocks[fmap.names.index(str(column))][0]
            mean = 0.0 if means is None else float(means[position])
            scale = 1.0 if scales is None else float(scales[position])
            references[str(column)] = mean + float(zero[0, index]) * scale
    return references


# --- contributions --------------------------------------------------------------------


def _classes(model: Any) -> np.ndarray | None:
    classes = getattr(model, "classes_", None)
    if classes is None or len(classes) == 0:
        return None
    classes = np.asarray(classes)
    if len(classes) > 1 and not np.all(classes[1:] > classes[:-1]):
        raise ExplainUnavailable("the model's classes are not in a supported order")
    return classes


def _predicted_position(model: Any, encoded: np.ndarray, task_type: str) -> np.ndarray | None:
    """Multiclass: the position (in the model's fitted classes) of each row's predicted class,
    the same argmax as the scoring file's label (absent classes have probability 0)."""

    if task_type != "multiclass":
        return None
    return np.asarray(model.predict_proba(encoded), dtype=float).argmax(axis=1)


def _tree_shap(model: Any, encoded: np.ndarray, pace: _Pace) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(contributions (n, K, p), bias (n, K), raw (n, K)) from the booster's own SHAP values."""

    inner = _contiguous_inner(model)
    name = type(inner).__name__
    if name in {"XGBClassifier", "XGBRegressor"}:
        import xgboost

        booster = inner.get_booster()
        matrix = xgboost.DMatrix(encoded, feature_names=booster.feature_names)
        values = booster.predict(matrix, pred_contribs=True)
        pace.beat()
        raw = booster.predict(matrix, output_margin=True)
    elif name in {"LightGBMBoosterModel", "LGBMClassifier", "LGBMRegressor"}:
        booster = inner.booster if name == "LightGBMBoosterModel" else inner.booster_
        values = booster.predict(encoded, pred_contrib=True)
        pace.beat()
        raw = booster.predict(encoded, raw_score=True)
        groups = int(booster.num_model_per_iteration())
        if groups > 1:
            values = np.asarray(values).reshape(len(encoded), groups, -1)
    else:  # CatBoost
        from catboost import Pool

        values = inner.get_feature_importance(data=Pool(encoded), type="ShapValues", thread_count=1, verbose=False)
        pace.beat()
        raw = inner.predict(encoded, prediction_type="RawFormulaVal", thread_count=1)
    pace.beat()
    values = np.asarray(values, dtype=np.float64)
    raw = np.asarray(raw, dtype=np.float64)
    values = values[:, None, :] if values.ndim == 2 else values
    raw = raw[:, None] if raw.ndim == 1 else raw
    if values.shape[0] != len(encoded) or values.shape[2] != encoded.shape[1] + 1 or raw.shape != values.shape[:2]:
        raise ExplainUnavailable("the model's contributions have an unexpected shape")
    return values[:, :, :-1], values[:, :, -1], raw


def _linear(model: Any, encoded: np.ndarray, classifier: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    transforms, last = _linear_parts(model)  # type: ignore[misc]
    scaled = encoded
    for step in transforms:
        scaled = step.transform(scaled)
    scaled = np.asarray(scaled, dtype=np.float64)
    coef = np.atleast_2d(np.asarray(last.coef_, dtype=np.float64))
    if coef.shape[1] != encoded.shape[1]:
        raise ExplainUnavailable("the model's coefficients do not match its inputs")
    intercept = np.broadcast_to(np.atleast_1d(np.asarray(last.intercept_, dtype=np.float64)), (coef.shape[0],))
    raw = np.asarray(last.decision_function(scaled) if classifier else last.predict(scaled), dtype=np.float64)
    raw = raw[:, None] if raw.ndim == 1 else raw
    values = scaled[:, None, :] * coef[None, :, :]
    return values, np.broadcast_to(intercept, (len(encoded), coef.shape[0])), raw


def _explained(
    values: np.ndarray, bias: np.ndarray, raw: np.ndarray, task_type: str, position: np.ndarray | None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Reduce per-class margins to the one explained quantity (see the module docstring)."""

    groups = values.shape[1]
    if task_type != "multiclass" or groups == 1:
        if groups != 1:
            raise ExplainUnavailable("the model's outputs do not match the task")
        sign = np.ones(len(values)) if position is None else np.where(position == 1, 1.0, -1.0)
        return values[:, 0, :] * sign[:, None], bias[:, 0] * sign, raw[:, 0] * sign
    rows = np.arange(len(values))

    def versus(array: np.ndarray) -> np.ndarray:
        own = array[rows, position]
        return own - (array.sum(axis=1) - own) / (groups - 1)

    return versus(values), versus(bias), versus(raw)


def _check_additive(values: np.ndarray, bias: np.ndarray, raw: np.ndarray, scale: np.ndarray, rtol: float) -> None:
    """Per row: the contributions plus the bias give the raw output within ``rtol`` of the
    terms' size (``scale``: |bias| + sum|contribution| of every class involved)."""

    gap = np.abs(values.sum(axis=1) + bias - raw)
    if not np.all(gap <= rtol * scale + ADDITIVITY_ATOL):
        raise ExplainUnavailable("the model's contributions did not add up to its output")


def _aggregate(values: np.ndarray, fmap: FeatureMap) -> np.ndarray:
    out = np.empty((values.shape[0], len(fmap.names)), dtype=np.float64)
    for index, (start, stop) in enumerate(fmap.blocks):
        out[:, index] = values[:, start:stop].sum(axis=1)
    return out


def _occlusion(
    model: Any, encoded: np.ndarray, base: np.ndarray, fmap: FeatureMap, task_type: str, pace: _Pace
) -> np.ndarray:
    position = _predicted_position(model, encoded, task_type)

    def score(rows: np.ndarray, which: np.ndarray) -> np.ndarray:
        if task_type == "regression":
            return np.asarray(model.predict(rows), dtype=np.float64)
        proba = np.asarray(model.predict_proba(rows), dtype=np.float64)
        if task_type == "binary":  # the flagged class, as the scoring file's probability column
            return proba[:, 1] if proba.shape[1] > 1 else proba[:, 0]
        return proba[np.arange(len(rows)), position[which]]

    out = np.zeros((len(encoded), len(fmap.names)), dtype=np.float64)
    full = score(encoded, np.arange(len(encoded)))
    pace.check()
    for index, (start, stop) in enumerate(fmap.blocks):
        which = np.flatnonzero(np.any(encoded[:, start:stop] != base[start:stop], axis=1))
        if len(which):
            changed = encoded[which].copy()
            changed[:, start:stop] = base[start:stop]
            out[which, index] = full[which] - score(changed, which)
            pace.check()  # heartbeat; stop at this column boundary once out of time
    return out


def _baseline_row(pipeline: Any, fmap: FeatureMap, baselines: Mapping[str, Any], frame: pd.DataFrame) -> np.ndarray:
    """The encoded training baseline row, built with each column's dtype in ``frame`` (so the
    fitted encoder matches categories exactly as it does for the scored rows)."""

    columns = {}
    for name in fmap.names:
        series = pd.Series([baselines[name]], dtype=object)
        columns[name] = series if frame[name].dtype == object else series.astype(frame[name].dtype)
    encoded = np.asarray(pipeline.named_steps["prep"].transform(pd.DataFrame(columns)), dtype=np.float64)
    if encoded.shape != (1, fmap.width) or not np.all(np.isfinite(encoded)):
        raise ExplainUnavailable("the model's training baselines are not usable")
    return encoded[0]


def explain_rows(
    pipeline: Any,
    frame: pd.DataFrame,
    fmap: FeatureMap | None = None,
    baselines: Mapping[str, Any] | None = None,
    *,
    task_type: str,
    method: str = "auto",
    max_rows: int = MAX_ROWS,
    deadline_seconds: float | None = None,
    on_progress: Callable[[], None] | None = None,
) -> Explanation:
    """Contributions of each input column for the first rows of ``frame`` (the pipeline's
    prepared input, exactly what was scored). Raises ``ExplainUnavailable`` with plain text."""

    pace = _Pace(on_progress, DEADLINE_SECONDS if deadline_seconds is None else float(deadline_seconds))
    fmap = fmap or feature_map(pipeline)
    model = pipeline.named_steps["model"]
    native = choose_method(model)
    chosen = native if method == "auto" else method
    if chosen not in METHODS or (chosen != OCCLUSION and chosen != native):
        raise ExplainUnavailable("this explanation method does not apply to the model")
    baselines = baselines if baselines is not None else training_baselines(pipeline, fmap)
    classes = _classes(model) if task_type != "regression" else None
    groups = len(classes) if classes is not None and len(classes) > 2 else 1
    trees = _tree_count(model, groups)
    rows = min(len(frame), max(0, int(max_rows)))
    chunk = max(1, min(CHUNK_ROWS, CHUNK_CELLS // (groups * (fmap.width + 1))))
    base = None
    if chosen == OCCLUSION:
        if len(fmap.names) > OCCLUSION_MAX_FEATURES:
            raise ExplainUnavailable(
                f"the model has more than {OCCLUSION_MAX_FEATURES} input columns, too many for the approximate method"
            )
        per_row = len(fmap.names) * trees
        rows = min(rows, max(1, OCCLUSION_MAX_WORK // per_row))
        chunk = max(1, min(chunk, OCCLUSION_CHUNK_WORK // per_row))
        base = _baseline_row(pipeline, fmap, baselines, frame)
    elif chosen == TREE_SHAP:
        rows = min(rows, max(1, SHAP_MAX_WORK // trees))
        chunk = max(1, min(chunk, SHAP_CHUNK_WORK // trees))
    inner = _contiguous_inner(model)
    if type(inner).__name__ in {"XGBClassifier", "XGBRegressor"}:  # float32 sums, one term per round
        rtol = max(1e-5, 4 * int(inner.get_booster().num_boosted_rounds()) * FLOAT32_UNIT)
    else:
        rtol = FLOAT64_RTOL
    prep = pipeline.named_steps["prep"]
    parts: list[np.ndarray] = []
    biases: list[np.ndarray] = []
    raws: list[np.ndarray] = []
    for start in range(0, rows, chunk):
        try:
            if parts:
                pace.check()  # stop between chunks once out of time
            encoded = np.asarray(prep.transform(frame.iloc[start : min(rows, start + chunk)]), dtype=np.float64)
            if encoded.ndim != 2 or encoded.shape[1] != fmap.width:
                raise ExplainUnavailable("the model's preprocessing is not supported for reasons")
            if chosen == OCCLUSION:
                part = _occlusion(model, encoded, base, fmap, task_type, pace)  # type: ignore[arg-type]
            else:
                values, bias, raw = (_tree_shap(model, encoded, pace) if chosen == TREE_SHAP
                                     else _linear(model, encoded, task_type != "regression"))
                scale = (np.abs(values).sum(axis=2) + np.abs(bias)).sum(axis=1)
                values, bias, raw = _explained(values, bias, raw, task_type,
                                               _predicted_position(model, encoded, task_type))
                _check_additive(values, bias, raw, scale, rtol)
                part = _aggregate(values, fmap)
                biases.append(bias)
                raws.append(raw)
        except _OutOfTime:
            break
        if not np.all(np.isfinite(part)):
            raise ExplainUnavailable("the model's contributions were not finite numbers")
        parts.append(part)
        pace.beat()
    if rows and not parts:
        raise ExplainUnavailable(TOO_LONG)
    contributions = np.vstack(parts) if parts else np.zeros((0, len(fmap.names)))
    additive = chosen != OCCLUSION
    if chosen == LINEAR:
        references = _linear_references(pipeline, fmap, model)
    else:
        references = {name: baselines[name] for name, role in zip(fmap.names, fmap.roles) if role == "numeric"}
    return Explanation(
        method=chosen,
        additive=additive,
        contributions=contributions,
        bias=np.concatenate(biases)[: len(contributions)] if additive and biases else None,
        raw=np.concatenate(raws)[: len(contributions)] if additive and raws else None,
        rows_total=int(len(frame)),
        references=references,
    )


# --- words ----------------------------------------------------------------------------


def plain_text(value: Any, cap: int) -> str:
    """One line of plain text: no control, format (bidi, zero-width) or unassigned characters,
    whitespace collapsed, at most ``cap`` characters."""

    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        value = int(value)
    kept = []
    for char in str(value):
        kind = unicodedata.category(char)
        if kind in {"Cc", "Zl", "Zp"}:
            kept.append(" ")
        elif kind not in {"Cf", "Cs", "Co", "Cn"}:
            kept.append(char)
    text = " ".join("".join(kept).split())
    if len(text) > cap:
        text = text[: cap - 3].rstrip() + "..."
    return text or "(blank)"


def _cell(text: str) -> str:
    # A spreadsheet would run a cell starting like a formula: make it text.
    return "'" + text if text.startswith(_FORMULA_START) else text


def _direction(task_type: str, method: str, raised: bool) -> str:
    verb = "raised" if raised else "lowered"
    if task_type == "regression":
        return f"{verb} the predicted value"
    if task_type == "multiclass":  # see BatchPredictionReasons: the lead, not the probability
        return f"{verb} the predicted class's {'probability' if method == OCCLUSION else 'lead over the other classes'}"
    return f"{verb} the model's score"


def _buckets(frame: pd.DataFrame, fmap: FeatureMap, references: Mapping[str, Any], index: int, rows: int) -> np.ndarray:
    """Per row, a coarse description key of this column's value: ``missing``, ``unseen``,
    ``below`` / ``above`` / ``at`` the column's reference (numeric), or the category position."""

    name = fmap.names[index]
    series = frame[name].iloc[:rows]
    missing = series.isna().to_numpy()
    if fmap.roles[index] == "numeric":
        values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=np.float64)
        reference = pd.to_numeric(pd.Series([references.get(name)]), errors="coerce").iloc[0]
        keys = np.where(values < reference, "below", np.where(values > reference, "above", "at")).astype(object)
        if pd.isna(reference):
            keys[:] = "none"
        keys[missing | np.isnan(values)] = "missing"
        return keys
    known = [value for value in fmap.categories.get(name, ()) if not pd.isna(value)]
    positions = pd.Index(known).get_indexer(series) if known else np.full(len(series), -1)
    keys = np.asarray([f"value:{position}" if position >= 0 else "unseen" for position in positions], dtype=object)
    keys[missing] = "missing"
    return keys


def _sentence(name: str, key: str, categories: tuple[Any, ...], is_date: bool, method: str, direction: str) -> str:
    """Value part, direction, and the reference the contribution is measured against."""

    reference = "the training average" if method == LINEAR else "the training median"
    bucket = {
        "below": f"earlier than {reference} date" if is_date else f"below {reference}",
        "above": f"later than {reference} date" if is_date else f"above {reference}",
        "at": f"at {reference} date" if is_date else f"at {reference}",
    }
    if key.startswith("value:"):
        value = f"{name} = {plain_text(categories[int(key[6:])], VALUE_CAP)}"
    elif key in bucket:
        value = f"{name}: {bucket[key]}"
    elif key in {"missing", "unseen"}:
        value = f"{name}: {'missing' if key == 'missing' else 'a value not seen in training'}"
    else:
        value = name
    if method == TREE_SHAP:
        compared = " compared with the model's average row"
    elif key in bucket:
        compared = ""  # the bucket names the reference itself
    elif method == LINEAR:
        compared = " compared with the training average"
    else:
        compared = " compared with the most common training value" if categories else f" compared with {reference}"
    return f"{value}, {direction}{compared}"


def reason_texts(
    explanation: Explanation,
    frame: pd.DataFrame,
    fmap: FeatureMap,
    *,
    task_type: str,
    date_columns: Collection[str] = (),
    top_k: int = TOP_K,
) -> list[list[str | None]]:
    """``top_k`` sentences per explained row, e.g. ``"plan = basic, raised the model's score
    compared with the model's average row"``: the column name, a coarse description of the
    row's value (its category, which is already in the file, or where it falls against the
    TRAINING average / median -- never the number itself), the direction, and the reference
    the method measures against. ``None`` where a row has fewer columns that moved its score."""

    rows = explanation.rows_explained
    order = explanation.top(top_k)
    names = [plain_text(name, NAME_CAP) for name in fmap.names]
    buckets: dict[int, np.ndarray] = {}
    cache: dict[tuple[int, str, bool], str] = {}
    out: list[list[str | None]] = []
    for row in range(rows):
        texts: list[str | None] = []
        for index in order[row]:
            if index < 0:
                texts.append(None)
                continue
            index = int(index)
            if index not in buckets:
                buckets[index] = _buckets(frame, fmap, explanation.references, index, rows)
            key = str(buckets[index][row])
            raised = bool(explanation.contributions[row, index] > 0)
            text = cache.get((index, key, raised))
            if text is None:
                name = fmap.names[index]
                categories = tuple(value for value in fmap.categories.get(name, ()) if not pd.isna(value))
                direction = _direction(task_type, explanation.method, raised)
                text = cache[(index, key, raised)] = _cell(
                    _sentence(names[index], key, categories, name in date_columns, explanation.method, direction))
            texts.append(text)
        out.append(texts)
    return out


# --- summary --------------------------------------------------------------------------


def available_summary(explanation: Explanation, top_k: int = TOP_K) -> dict[str, Any]:
    """The prediction's ``reasons`` record (no row values)."""

    note = None
    if explanation.rows_explained < explanation.rows_total:
        note = (f"explained the first {explanation.rows_explained} of {explanation.rows_total} rows; "
                "the reason columns are empty for the rest")
    return {
        "available": True,
        "method": explanation.method,
        "method_label": METHOD_LABELS[explanation.method],
        "additive": explanation.additive,
        "top_k": top_k,
        "rows_explained": explanation.rows_explained,
        "note": note,
        "unavailable_reason": None,
    }


def unavailable_summary(reason: str, top_k: int = TOP_K) -> dict[str, Any]:
    return {
        "available": False,
        "method": None,
        "method_label": None,
        "additive": False,
        "top_k": top_k,
        "rows_explained": 0,
        "note": None,
        "unavailable_reason": plain_text(reason, 300),
    }
