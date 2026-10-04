"""Batch scoring of new rows with a stored model version (P4.9-A). Pure: no DB, no storage.

Open-ingest training transforms the upload before the fitted sklearn ``Pipeline``
(``prep`` ColumnTransformer + model) sees it: numeric coercion, cell hygiene,
train-decided ``domain_fill`` values and datetime -> epoch feature actions, then a
CSV round trip of the prepared table. New rows get the same transforms here, with
the same ``app.engine.lab.auto_prepare`` functions and only parameters learned at
training time: the fitted pipeline's input columns and encoder categories, and the
run's locked scientific evidence. Nothing is refit or re-decided on scoring rows.
A model whose transforms cannot be replayed deterministically fails closed
(``unsupported_transform``); it is never scored on an untransformed frame.
"""

from __future__ import annotations

import io
import json
import math
import numbers
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.domain.batch_predictions import (
    CONTRACT_CHECK_MAX_BYTES,
    FEATURE_CONTRACT_FAILED,
    SCORING_FAILED,
    UNSUPPORTED_MODEL,
    UNSUPPORTED_TRANSFORM,
)
from app.engine.lab.auto_prepare import (
    apply_feature_engineering_actions,
    clean_feature_cells,
    coerce_numeric_like,
)
from app.engine.modeling.objective import DEFAULT_THRESHOLD

TASK_TYPES = frozenset({"binary", "multiclass", "regression"})
# The only feature action training applies in place to other partitions (auto_prepare).
_EPOCH_STEP, _EPOCH_TRANSFORMATION = "datetime_to_unix_seconds", "datetime_to_epoch"
# Missing-value actions done by the pipeline's own imputers (or a dropped column).
_PIPELINE_MISSING_ACTIONS = frozenset({"keep", "impute_median", "impute_most_frequent", "drop_column"})
LISTED_MAX = 100
NAME_MAX = 128
CHUNK_ROWS = 50_000
ROW_COLUMN = "row_number"

_MODEL_MESSAGE = "this model version's stored pipeline cannot be used for batch scoring"
_TRANSFORM_MESSAGE = "this model version uses a preparation step that cannot be replayed on new data"


class ScoringError(Exception):
    """Deterministic scoring failure: stable ``code``, public ``message``, optional contract."""

    def __init__(self, code: str, message: str, *, contract: dict[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.public_message = message
        self.contract = contract


@dataclass(frozen=True)
class ScoringSpec:
    task_type: str
    target_column: str | None
    entity_column: str | None
    feature_columns: tuple[str, ...]
    numeric_columns: tuple[str, ...]
    categorical_columns: tuple[str, ...]
    categories: Mapping[str, tuple[Any, ...]]
    feature_actions: tuple[dict[str, Any], ...]
    domain_fills: Mapping[str, Any]
    class_labels: tuple[Any, ...] | None
    decision_threshold: float | None


@dataclass(frozen=True)
class ScoredFrame:
    predictions: pd.DataFrame
    contract: dict[str, Any]


def load_pipeline(payload: bytes) -> Any:
    """Unpickle the stored winner Pipeline. Worker only; the caller verified the digest."""

    import joblib

    try:
        return joblib.load(io.BytesIO(payload))
    except Exception as exc:  # noqa: BLE001
        raise ScoringError(UNSUPPORTED_MODEL, _MODEL_MESSAGE) from exc


def _unsupported(code: str = UNSUPPORTED_TRANSFORM) -> ScoringError:
    return ScoringError(code, _TRANSFORM_MESSAGE if code == UNSUPPORTED_TRANSFORM else _MODEL_MESSAGE)


def _pipeline_contract(pipeline: Any) -> tuple[list[str], list[str], list[str], dict[str, tuple[Any, ...]]]:
    """(input columns, numeric, categorical, categories) learned when the pipeline was fit."""

    from sklearn.compose import ColumnTransformer

    steps = getattr(pipeline, "named_steps", None)
    prep = steps.get("prep") if isinstance(steps, Mapping) else None
    features = [str(name) for name in getattr(pipeline, "feature_names_in_", [])]
    if not isinstance(prep, ColumnTransformer) or not hasattr(pipeline, "predict") or not features:
        raise _unsupported(UNSUPPORTED_MODEL)
    numeric: list[str] = []
    categorical: list[str] = []
    categories: dict[str, tuple[Any, ...]] = {}
    for name, transformer, columns in getattr(prep, "transformers_", []):
        if name == "remainder":
            if transformer != "drop":
                raise _unsupported(UNSUPPORTED_MODEL)
            continue
        names = list(columns) if isinstance(columns, (list, tuple, np.ndarray)) else None
        if names is None or not all(isinstance(item, str) for item in names):
            raise _unsupported(UNSUPPORTED_MODEL)
        if name == "num":
            numeric += names
        elif name == "cat":
            encoder = getattr(transformer, "named_steps", {}).get("onehot")
            fitted = getattr(encoder, "categories_", None)
            if fitted is None or len(fitted) != len(names):
                raise _unsupported(UNSUPPORTED_MODEL)
            categorical += names
            categories.update({column: tuple(values) for column, values in zip(names, fitted)})
        else:
            raise _unsupported(UNSUPPORTED_MODEL)
    if sorted(numeric + categorical) != sorted(features):
        raise _unsupported(UNSUPPORTED_MODEL)
    return features, numeric, categorical, categories


def _action_columns(action: Mapping[str, Any]) -> list[str]:
    # The columns apply_feature_engineering_actions converts for this action.
    return [str(name) for name in action.get("output_columns") or action.get("columns") or []]


def _feature_actions(raw: Any, categorical: set[str]) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw, list):
        raise _unsupported()
    actions = []
    for action in raw:
        if not isinstance(action, dict) or (
            action.get("step") != _EPOCH_STEP and action.get("transformation") != _EPOCH_TRANSFORMATION
        ):
            raise _unsupported()  # apply_feature_engineering_actions would skip it silently
        outputs = _action_columns(action)
        inputs = [str(name) for name in action.get("input_columns") or action.get("columns") or []]
        if not outputs or inputs != outputs or set(outputs) & categorical:
            raise _unsupported()  # only in-place, numeric (epoch) column transforms replay
        actions.append(action)
    return tuple(actions)


def _domain_fills(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict) or not isinstance(plan.get("column_decisions"), list):
        raise _unsupported()
    fills: dict[str, Any] = {}
    for item in plan["column_decisions"]:
        if not isinstance(item, dict):
            raise _unsupported()
        action = item.get("action")
        if action == "domain_fill":
            if item.get("fill_value") is not None:
                fills[str(item.get("column"))] = item["fill_value"]
        elif action not in _PIPELINE_MISSING_ACTIONS:
            raise _unsupported()
    return fills


def scoring_spec(result: Mapping[str, Any], pipeline: Any) -> ScoringSpec:
    """The replay plan for one locked run. Raises ``ScoringError`` (fail closed)."""

    task = result.get("task") if isinstance(result.get("task"), dict) else {}
    task_type = str(task.get("task_type") or "")
    if task_type not in TASK_TYPES:
        raise _unsupported(UNSUPPORTED_MODEL)
    features, numeric, categorical, categories = _pipeline_contract(pipeline)
    evidence = result.get("scientific_evidence")
    if not isinstance(evidence, dict):
        raise _unsupported()
    actions = _feature_actions(evidence.get("feature_actions"), set(categorical))
    fills = _domain_fills(evidence.get("missing_value_plan"))
    labels = None
    if task_type == "multiclass":
        raw_labels = result.get("class_labels")
        if not isinstance(raw_labels, list) or len(raw_labels) < 2:
            raise _unsupported(UNSUPPORTED_MODEL)
        labels = tuple(raw_labels)
    threshold = None
    if task_type == "binary":
        locked = result.get("decision_threshold") if isinstance(result.get("decision_threshold"), dict) else {}
        # The runner scores with the locked value, else DEFAULT_THRESHOLD (same rule here).
        value = locked.get("value")
        threshold = float(value) if value is not None else DEFAULT_THRESHOLD
        if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
            raise _unsupported(UNSUPPORTED_MODEL)
    target = task.get("target")
    entity = task.get("entity_id")
    return ScoringSpec(
        task_type=task_type,
        target_column=str(target) if target else None,
        entity_column=str(entity) if entity else None,
        feature_columns=tuple(features),
        numeric_columns=tuple(numeric),
        categorical_columns=tuple(categorical),
        categories=categories,
        feature_actions=actions,
        domain_fills=fills,
        class_labels=labels,
        decision_threshold=threshold,
    )


def _listed(names: list[str]) -> list[str]:
    return [name[:NAME_MAX] for name in names[:LISTED_MAX]]


def _bounded(contract: dict[str, Any]) -> dict[str, Any]:
    """Halve the longest name list until the contract is well inside the DB bound
    (escaped or multibyte names); the ``*_count`` fields stay exact."""

    keys = ("required_columns", "missing_columns", "ignored_columns", "values_set_missing")
    while len(json.dumps(contract, ensure_ascii=False).encode("utf-8")) > CONTRACT_CHECK_MAX_BYTES // 2:
        key = max(keys, key=lambda name: len(contract.get(name) or ()))
        items = contract.get(key) or ()
        if not items:
            break
        half = len(items) // 2
        contract[key] = dict(list(items.items())[:half]) if isinstance(items, dict) else list(items)[:half]
    return contract


def check_contract(frame: pd.DataFrame, spec: ScoringSpec) -> dict[str, Any]:
    """Required = the pipeline's input columns (all transforms are in place). Extra
    columns and the target column are ignored and listed."""

    present = [str(name) for name in frame.columns]
    required = list(spec.feature_columns)
    missing = [name for name in required if name not in present]
    target = spec.target_column
    ignored = [name for name in present if name not in required and name != target]
    return _bounded({
        "status": "failed" if missing else "passed",
        "required_columns": _listed(required),
        "required_count": len(required),
        "missing_columns": _listed(missing),
        "missing_count": len(missing),
        "ignored_columns": _listed(ignored),
        "ignored_count": len(ignored),
        "target_column": target[:NAME_MAX] if target else None,
        "target_column_ignored": bool(target and target in present),
    })


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NA


def _is_number(value: Any) -> bool:
    return isinstance(value, numbers.Number) and not isinstance(value, (bool, np.bool_))


def _as_text(value: Any) -> Any:
    if _missing(value) or isinstance(value, str):
        return value
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # a code read as float (NaN in the column) matches its text
    return str(value)


def _align_categorical(series: pd.Series, categories: tuple[Any, ...]) -> pd.Series:
    """Give a categorical column the value type its fitted encoder learned, so known
    categories match; anything else is an unseen category (all zeros, as in training)."""

    known = [value for value in categories if not _missing(value)]
    if not known or all(isinstance(value, (bool, np.bool_)) for value in known):
        return series
    if all(_is_number(value) for value in known):
        return pd.to_numeric(series, errors="coerce")
    if all(isinstance(value, str) for value in known):
        return series.astype(object).map(_as_text)
    return series


def prepare_features(frame: pd.DataFrame, spec: ScoringSpec) -> tuple[pd.DataFrame, dict[str, int]]:
    """The pipeline input for ``frame``, transformed as training transformed its rows.

    Order mirrors training: numeric coercion (target stage) -> cell hygiene
    (structural cleaning) -> ``domain_fill`` -> datetime feature actions -> the
    dtypes the fitted pipeline was trained on. Rows are never dropped.
    """

    columns = list(spec.feature_columns)
    work = frame.loc[:, columns].copy()
    epoch_columns = {name for action in spec.feature_actions for name in _action_columns(action)}
    plain_numeric = [name for name in spec.numeric_columns if name not in epoch_columns]
    present_before = {name: int(work[name].notna().sum()) for name in spec.numeric_columns}
    work = coerce_numeric_like(work, plain_numeric, min_parsed_fraction=0.0)
    work, _inf, _sentinels = clean_feature_cells(work, columns)
    for column, value in spec.domain_fills.items():
        if column in work.columns:
            work[column] = work[column].fillna(value)
    work = apply_feature_engineering_actions(work, list(spec.feature_actions))
    for name in spec.numeric_columns:
        work[name] = pd.to_numeric(work[name], errors="coerce").astype(float).replace([np.inf, -np.inf], np.nan)
    for name in spec.categorical_columns:
        work[name] = _align_categorical(work[name], tuple(spec.categories.get(name, ())))
    unparsed = {
        name[:NAME_MAX]: present_before[name] - int(work[name].notna().sum())
        for name in spec.numeric_columns
        if present_before[name] - int(work[name].notna().sum()) > 0
    }
    return work.loc[:, columns], dict(list(unparsed.items())[:LISTED_MAX])


def _scores(pipeline: Any, features: pd.DataFrame, spec: ScoringSpec, on_progress: Callable[[], None] | None) -> np.ndarray:
    # The same scoring function the run's holdout evaluation used (one implementation).
    from app.engine.experiments.runner import _predict

    classifier = spec.task_type != "regression"
    n_classes = len(spec.class_labels) if spec.class_labels else None
    parts = []
    for start in range(0, len(features), CHUNK_ROWS):
        parts.append(_predict(pipeline, features.iloc[start : start + CHUNK_ROWS], classifier, n_classes))
        if on_progress is not None:
            on_progress()
    return np.concatenate(parts) if parts else np.empty((0,))


def _prediction_table(frame: pd.DataFrame, spec: ScoringSpec, scores: np.ndarray) -> pd.DataFrame:
    table = pd.DataFrame(index=pd.RangeIndex(len(frame)))
    reserved = {ROW_COLUMN, "probability", "label", "prediction"}
    entity = spec.entity_column
    if entity and entity in frame.columns and entity not in reserved and not entity.startswith("probability_"):
        table[entity] = frame[entity].to_numpy()
    table[ROW_COLUMN] = np.arange(1, len(frame) + 1)  # 1 = the file's first data row
    if spec.task_type == "binary":
        table["probability"] = scores
        table["label"] = (scores >= float(spec.decision_threshold)).astype(int)
    elif spec.task_type == "multiclass":
        labels = list(spec.class_labels or ())
        for index, label in enumerate(labels):
            table[f"probability_{label}"] = scores[:, index]
        table["label"] = [labels[int(code)] for code in scores.argmax(axis=1)]
    else:
        table["prediction"] = scores
    return table


def score_frame(
    frame: pd.DataFrame,
    spec: ScoringSpec,
    pipeline: Any,
    *,
    on_progress: Callable[[], None] | None = None,
) -> ScoredFrame:
    """Contract check, replayed transforms, fitted pipeline, locked threshold."""

    frame = frame.copy()
    frame.columns = [str(name) for name in frame.columns]
    contract = check_contract(frame, spec)
    if contract["missing_count"]:
        names = ", ".join(contract["missing_columns"][:20])
        more = contract["missing_count"] - min(20, contract["missing_count"])
        message = f"the file is missing {contract['missing_count']} required column(s): {names}"
        raise ScoringError(FEATURE_CONTRACT_FAILED, message + (f" and {more} more" if more else ""), contract=contract)
    if frame.empty:
        raise ScoringError(FEATURE_CONTRACT_FAILED, "the file has no rows to score", contract=contract)
    features, unparsed = prepare_features(frame, spec)
    contract = _bounded({**contract, "values_set_missing": unparsed})
    table = _prediction_table(frame, spec, _scores(pipeline, features, spec, on_progress))
    if len(table) != len(frame):
        raise ScoringError(SCORING_FAILED, "scoring did not return one prediction per row", contract=contract)
    return ScoredFrame(predictions=table, contract=contract)


def serialize_predictions(table: pd.DataFrame, output_format: str) -> tuple[bytes, str, str]:
    """(bytes, mime type, file extension)."""

    if output_format == "parquet":
        out = table.copy()
        for name in out.columns:
            if out[name].dtype == object:  # mixed entity values / labels: one Arrow type
                out[name] = out[name].map(lambda value: None if _missing(value) else str(value))
        buffer = io.BytesIO()
        out.to_parquet(buffer, index=False)
        return buffer.getvalue(), "application/vnd.apache.parquet", "parquet"
    return table.to_csv(index=False).encode("utf-8"), "text/csv", "csv"
