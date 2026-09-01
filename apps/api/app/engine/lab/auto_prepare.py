"""Automatic, no-questions-asked data preparation for the open-ingest auto-train job.

Pure functions: given a raw pandas DataFrame, decide (never ask) how to fill
missing values, which columns are numeric vs categorical, and build the
sklearn preprocessing pipeline. Every decision is recorded so an admin can see
why — see docs/LABS_DATA_UNDERSTANDING.md for the surrounding workflow and
apps/api/app/services/auto_train_service.py for the orchestration that calls
these functions and persists the decision log.

This module never talks to a client, a database, or a user. It is the
"prepare" step from the plan: coerce numerics, pick a heuristic target, decide
missing-value policy, split numeric/categorical roles, and build the
ColumnTransformer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.engine.features.encode import encode_datetime_columns
from app.engine.lab.schema_inference import (
    TargetChoice,
    choose_target_deterministically,
    looks_like_identifier,
)

DROP_COLUMN_MISSING_THRESHOLD = 0.5
DROP_ROWS_MAX_FRACTION = 0.05
DROP_ROWS_MIN_ABSOLUTE = 10
MAX_CATEGORICAL_CARDINALITY = 50

# Whole-cell sentinels treated as missing. Compared case-insensitively after strip.
_INVALID_STRINGS = {
    "",
    "na",
    "n/a",
    "null",
    "none",
    "nan",
    "nat",
    "?",
    "-",
    "--",
    "#n/a",
    "#na",
    "nil",
    "(null)",
    "undefined",
}


def _looks_like_identifier(name: str, series: pd.Series, n: int) -> bool:
    return looks_like_identifier(name, series, n)


def coerce_numeric_like(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Numeric-looking strings, including blank sentinels, become real floats.

    A column only converts when at least 90% of its non-null values parse as
    numbers — otherwise it stays text (a name, a free-text note, ...).
    """
    out = frame.copy()
    for name in columns:
        if name not in out.columns:
            continue
        series = out[name]
        if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
            continue
        stripped = series.astype(str).str.strip().replace({"": np.nan, "nan": np.nan, "None": np.nan})
        candidate = pd.to_numeric(stripped, errors="coerce")
        non_null = series.notna().sum()
        if non_null and candidate.notna().sum() / non_null >= 0.9:
            out[name] = candidate
    return out


def pick_target_heuristic(
    frame: pd.DataFrame,
    columns: list[str],
    *,
    explicit_target: str | None = None,
) -> TargetChoice:
    """Compatibility wrapper around the generic target-candidate engine."""
    return choose_target_deterministically(frame, columns, explicit_target=explicit_target)


@dataclass
class ColumnMissingDecision:
    column: str
    missing_count: int
    missing_fraction: float
    action: str  # "drop_column" | "impute_median" | "impute_most_frequent" | "keep" | "domain_fill"
    fill_value: Any = None


@dataclass
class MissingValuePlan:
    dropped_columns: list[str]
    column_decisions: list[ColumnMissingDecision] = field(default_factory=list)
    rows_with_missing: int = 0
    row_missing_fraction: float = 0.0
    total_rows: int = 0
    drop_rows_recommended: bool = False


def plan_missing_values(frame: pd.DataFrame, columns: list[str]) -> MissingValuePlan:
    """Decide, don't ask: drop columns that are mostly empty, then decide
    whether the remaining incomplete rows are few enough to drop outright.
    """
    n = max(len(frame), 1)
    decisions: list[ColumnMissingDecision] = []
    dropped: list[str] = []
    for name in columns:
        if name not in frame.columns:
            continue
        missing = int(frame[name].isna().sum())
        fraction = missing / n
        if fraction > DROP_COLUMN_MISSING_THRESHOLD:
            dropped.append(name)
            action = "drop_column"
        elif missing == 0:
            action = "keep"
        elif pd.api.types.is_numeric_dtype(frame[name]):
            action = "impute_median"
        else:
            action = "impute_most_frequent"
        decisions.append(
            ColumnMissingDecision(column=name, missing_count=missing, missing_fraction=fraction, action=action)
        )
    remaining = [c for c in columns if c not in dropped and c in frame.columns]
    rows_with_missing = int(frame[remaining].isna().any(axis=1).sum()) if remaining else 0
    fraction = rows_with_missing / n
    drop_recommended = rows_with_missing > 0 and (
        fraction < DROP_ROWS_MAX_FRACTION or rows_with_missing < DROP_ROWS_MIN_ABSOLUTE
    )
    return MissingValuePlan(
        dropped_columns=dropped,
        column_decisions=decisions,
        rows_with_missing=rows_with_missing,
        row_missing_fraction=fraction,
        total_rows=int(len(frame)),
        drop_rows_recommended=drop_recommended,
    )


def _one_hot_encoder() -> OneHotEncoder:
    kwargs: dict[str, Any] = {"drop": "first", "handle_unknown": "ignore"}
    try:
        return OneHotEncoder(**kwargs, sparse_output=False)
    except TypeError:
        return OneHotEncoder(**kwargs, sparse=False)


def _replace_invalid_strings(series: pd.Series) -> tuple[pd.Series, int]:
    as_str = series.astype("string").str.strip()
    mask = as_str.str.lower().isin(_INVALID_STRINGS).fillna(False)
    cleared = int(mask.sum())
    if not cleared:
        return series, 0
    out = series.copy()
    out = out.mask(mask, np.nan)
    return out, cleared


def clean_frame(
    frame: pd.DataFrame,
    *,
    target: str | None = None,
    feature_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Deterministic cleaning. Missing feature values are left for the sklearn imputer."""
    out = frame.copy()
    columns = feature_columns if feature_columns is not None else [c for c in out.columns if c != target]
    transformations: list[dict[str, Any]] = []

    inf_cleared = 0
    for name in list(out.columns):
        if not pd.api.types.is_numeric_dtype(out[name]):
            continue
        replaced = out[name].replace([np.inf, -np.inf], np.nan)
        inf_cleared += int(replaced.isna().sum() - out[name].isna().sum())
        out[name] = replaced
    if inf_cleared:
        transformations.append({"step": "replace_infinite", "cells_cleared": inf_cleared})

    string_cleared = 0
    for name in columns:
        if name not in out.columns:
            continue
        series = out[name]
        if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
            continue
        if pd.api.types.is_datetime64_any_dtype(series):
            continue
        cleaned, n_cleared = _replace_invalid_strings(series)
        out[name] = cleaned
        string_cleared += n_cleared
    if string_cleared:
        transformations.append({"step": "replace_invalid_strings", "cells_cleared": string_cleared})

    coerce_cols = [c for c in out.columns if c != target]
    before_numeric = {c for c in coerce_cols if pd.api.types.is_numeric_dtype(out[c])}
    out = coerce_numeric_like(out, coerce_cols)
    coerced = [c for c in coerce_cols if c not in before_numeric and pd.api.types.is_numeric_dtype(out[c])]
    if coerced:
        transformations.append({"step": "coerce_numeric", "columns": coerced})

    duplicate_count = int(out.duplicated().sum())
    if duplicate_count:
        out = out.drop_duplicates().reset_index(drop=True)
        transformations.append({"step": "drop_duplicate_rows", "rows_removed": duplicate_count})

    missing_target_rows = 0
    if target and target in out.columns:
        missing_target_rows = int(out[target].isna().sum())
        if missing_target_rows:
            out = out.dropna(subset=[target]).reset_index(drop=True)
            transformations.append({"step": "drop_missing_target_rows", "rows_removed": missing_target_rows})

    feature_names = [c for c in columns if c in out.columns and c != target]
    missing_plan = plan_missing_values(out, feature_names)
    dropped_sparse = [c for c in missing_plan.dropped_columns if c in out.columns]
    if dropped_sparse:
        out = out.drop(columns=dropped_sparse)
        transformations.append(
            {"step": "drop_high_missing_columns", "columns": dropped_sparse, "threshold": DROP_COLUMN_MISSING_THRESHOLD}
        )
        feature_names = [c for c in feature_names if c not in dropped_sparse]

    constant_cols = [
        name for name in feature_names if name in out.columns and out[name].nunique(dropna=True) <= 1
    ]
    if constant_cols:
        out = out.drop(columns=constant_cols)
        transformations.append({"step": "drop_constant_columns", "columns": constant_cols})
        feature_names = [c for c in feature_names if c not in constant_cols]

    log = {
        "transformations": transformations,
        "rows_in": int(len(frame)),
        "rows_out": int(len(out)),
        "columns_in": [str(c) for c in frame.columns],
        "columns_out": [str(c) for c in out.columns],
        "duplicate_rows_removed": duplicate_count,
        "invalid_string_cells_cleared": string_cleared,
        "infinite_cells_cleared": inf_cleared,
        "missing_target_rows_removed": missing_target_rows,
        "dropped_columns": dropped_sparse + constant_cols,
        "missing_value_plan": {
            "dropped_columns": missing_plan.dropped_columns,
            "rows_with_missing": missing_plan.rows_with_missing,
            "row_missing_fraction": missing_plan.row_missing_fraction,
            "drop_rows_recommended": missing_plan.drop_rows_recommended,
            "column_decisions": [
                {
                    "column": item.column,
                    "missing_count": item.missing_count,
                    "missing_fraction": item.missing_fraction,
                    "action": item.action,
                    "fill_value": item.fill_value,
                }
                for item in missing_plan.column_decisions
            ],
        },
    }
    return out, log


def structural_clean_frame(
    frame: pd.DataFrame,
    *,
    target: str,
    feature_columns: list[str],
    source_row_column: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply only split-safe structural hygiene.

    These operations do not estimate modeling behavior from the complete
    dataset. Sparse/constant removal, missing-value policies, semantic role
    decisions, and feature decisions intentionally happen after the final
    holdout is locked and use training rows only.
    """
    out = frame.copy()
    transformations: list[dict[str, Any]] = []

    inf_cleared = 0
    for name in list(out.columns):
        if not pd.api.types.is_numeric_dtype(out[name]):
            continue
        replaced = out[name].replace([np.inf, -np.inf], np.nan)
        inf_cleared += int(replaced.isna().sum() - out[name].isna().sum())
        out[name] = replaced
    if inf_cleared:
        transformations.append({"step": "replace_infinite", "cells_cleared": inf_cleared})

    string_cleared = 0
    for name in feature_columns:
        if name not in out.columns:
            continue
        series = out[name]
        if (
            pd.api.types.is_numeric_dtype(series)
            or pd.api.types.is_bool_dtype(series)
            or pd.api.types.is_datetime64_any_dtype(series)
        ):
            continue
        cleaned, n_cleared = _replace_invalid_strings(series)
        out[name] = cleaned
        string_cleared += n_cleared
    if string_cleared:
        transformations.append({"step": "replace_invalid_strings", "cells_cleared": string_cleared})

    before_numeric = {c for c in feature_columns if c in out and pd.api.types.is_numeric_dtype(out[c])}
    out = coerce_numeric_like(out, feature_columns)
    coerced = [
        c for c in feature_columns if c in out and c not in before_numeric and pd.api.types.is_numeric_dtype(out[c])
    ]
    if coerced:
        transformations.append({"step": "coerce_numeric", "columns": coerced})

    duplicate_count = int(out.duplicated().sum())
    if duplicate_count:
        out = out.drop_duplicates()
        transformations.append({"step": "drop_duplicate_rows", "rows_removed": duplicate_count})

    missing_target_rows = int(out[target].isna().sum()) if target in out.columns else 0
    if missing_target_rows:
        out = out.dropna(subset=[target])
        transformations.append({"step": "drop_missing_target_rows", "rows_removed": missing_target_rows})

    if source_row_column is not None:
        # Add provenance only after duplicate detection so the unique source
        # index does not make otherwise duplicate rows appear distinct.
        out[source_row_column] = out.index.astype(int)
    return out.reset_index(drop=True), {
        "scope": "full_dataset_structural_only",
        "transformations": transformations,
        "rows_in": int(len(frame)),
        "rows_out": int(len(out)),
        "columns_in": [str(c) for c in frame.columns],
        "columns_out": [str(c) for c in out.columns if c != source_row_column],
        "duplicate_rows_removed": duplicate_count,
        "invalid_string_cells_cleared": string_cleared,
        "infinite_cells_cleared": inf_cleared,
        "missing_target_rows_removed": missing_target_rows,
        "dropped_columns": [],
    }


def engineer_features(frame: pd.DataFrame, columns: list[str]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Existing deterministic transforms only — no generated or model-invented features."""
    out, converted = encode_datetime_columns(frame, columns)
    transformations: list[dict[str, Any]] = []
    if converted:
        transformations.append(
            {
                "step": "datetime_to_unix_seconds",
                "transformation": "datetime_to_epoch",
                "columns": converted,
                "input_columns": converted,
                "output_columns": converted,
                "reason": "Convert datetime values to the numeric representation supported by the tabular pipeline.",
                "parameters": {"unit": "seconds", "epoch": "unix"},
                "learned_from_data": False,
                "decision_partition": "train",
            }
        )
    return out, transformations


def apply_feature_engineering_actions(
    frame: pd.DataFrame,
    actions: list[dict[str, Any]],
) -> pd.DataFrame:
    """Apply a feature plan learned from training rows to another partition."""
    out = frame.copy()
    for action in actions:
        if action.get("step") != "datetime_to_unix_seconds" and action.get("transformation") != "datetime_to_epoch":
            continue
        for name in action.get("output_columns") or action.get("columns") or []:
            if name not in out.columns:
                continue
            parsed = pd.to_datetime(out[name], errors="coerce")
            out[name] = parsed.map(lambda value: value.timestamp() if pd.notna(value) else np.nan)
    return out


def apply_missing_value_variant(frame: pd.DataFrame, columns: list[str], *, variant: str) -> pd.DataFrame:
    """`columns` should already exclude columns dropped for being >50% empty."""
    if variant == "drop_sparse_rows":
        present = [c for c in columns if c in frame.columns]
        if not present:
            return frame.reset_index(drop=True)
        return frame.dropna(subset=present).reset_index(drop=True)
    return frame.reset_index(drop=True)


def split_column_roles(
    frame: pd.DataFrame, columns: list[str], *, max_categorical_cardinality: int = MAX_CATEGORICAL_CARDINALITY
) -> tuple[list[str], list[str]]:
    """Numeric dtypes -> numerical_cols. Low-cardinality text/bool -> categorical_cols.

    Constant columns and free-text/identifier-like high-cardinality columns are
    dropped from both lists — they are never modeled automatically.
    """
    roles = infer_column_roles(
        frame,
        columns,
        max_categorical_cardinality=max_categorical_cardinality,
    )
    return roles.numerical, roles.categorical + roles.boolean


@dataclass(frozen=True)
class ColumnRoles:
    numerical: list[str] = field(default_factory=list)
    categorical: list[str] = field(default_factory=list)
    boolean: list[str] = field(default_factory=list)
    datetime: list[str] = field(default_factory=list)
    identifier: list[str] = field(default_factory=list)
    ignored_free_text: list[str] = field(default_factory=list)


def infer_column_roles(
    frame: pd.DataFrame,
    columns: list[str],
    *,
    max_categorical_cardinality: int = MAX_CATEGORICAL_CARDINALITY,
) -> ColumnRoles:
    """Return the complete deterministic role map used by generic uploads."""
    numerical: list[str] = []
    categorical: list[str] = []
    boolean: list[str] = []
    datetime: list[str] = []
    identifier: list[str] = []
    ignored: list[str] = []
    n = max(len(frame), 1)
    for name in columns:
        if name not in frame.columns:
            continue
        series = frame[name]
        unique = series.nunique(dropna=True)
        if unique <= 1:
            ignored.append(name)
            continue
        if _looks_like_identifier(name, series, n):
            identifier.append(name)
            continue
        if pd.api.types.is_datetime64_any_dtype(series):
            datetime.append(name)
        elif pd.api.types.is_bool_dtype(series):
            boolean.append(name)
        elif pd.api.types.is_numeric_dtype(series):
            numerical.append(name)
        elif unique <= max_categorical_cardinality:
            categorical.append(name)
        else:
            ignored.append(name)
    return ColumnRoles(
        numerical=numerical,
        categorical=categorical,
        boolean=boolean,
        datetime=datetime,
        identifier=identifier,
        ignored_free_text=ignored,
    )


def build_preprocessor(numerical_cols: list[str], categorical_cols: list[str]) -> ColumnTransformer:
    """Median-impute + scale numerics; most-frequent-impute + one-hot encode categoricals."""
    numeric_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )
    categorical_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", _one_hot_encoder()),
        ]
    )
    transformers = []
    if numerical_cols:
        transformers.append(("num", numeric_transformer, numerical_cols))
    if categorical_cols:
        transformers.append(("cat", categorical_transformer, categorical_cols))
    return ColumnTransformer(transformers=transformers)
