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

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.domain.lab_use_cases import LAB_USE_CASES
from app.engine.lab.column_map import normalize

MISSING_VARIANTS = ("drop_sparse_rows", "impute_all")

DROP_COLUMN_MISSING_THRESHOLD = 0.5
DROP_ROWS_MAX_FRACTION = 0.05
DROP_ROWS_MIN_ABSOLUTE = 10
MAX_CATEGORICAL_CARDINALITY = 50

_BINARY_TOKENS = {"0", "1", "true", "false", "yes", "no", "y", "n"}


def _looks_like_identifier(name: str, series: pd.Series, n: int) -> bool:
    key = normalize(name)
    if key.endswith("_id") or key in {"id", "uuid", "guid"} or key.endswith("_uuid"):
        return True
    if n and not pd.api.types.is_float_dtype(series) and series.nunique(dropna=True) / n > 0.95:
        return True
    return False


def coerce_numeric_like(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Blank-string numerics (the `TotalCharges` " " case) become real floats.

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


@dataclass
class TargetChoice:
    column: str | None
    reason: str


def pick_target_heuristic(frame: pd.DataFrame, columns: list[str]) -> TargetChoice:
    """Prefer a column whose name matches a known label alias (churn, converted,
    purchased, cancelled, ...); else the unique clean binary column that is not
    an identifier. Never guesses a random column — an admin-visible failure
    beats a silently wrong target.
    """
    by_norm = {normalize(col): col for col in columns if col in frame.columns}
    for definition in LAB_USE_CASES:
        for alias in definition.target_aliases:
            hit = by_norm.get(normalize(alias))
            if hit:
                return TargetChoice(
                    column=hit,
                    reason=f"column name matches known label '{alias}' (use case: {definition.slug})",
                )

    n = max(len(frame), 1)
    candidates: list[str] = []
    for name in columns:
        if name not in frame.columns:
            continue
        series = frame[name]
        if _looks_like_identifier(name, series, n):
            continue
        distinct = set(series.dropna().astype(str).str.strip().str.lower().unique())
        if len(distinct) == 2 and distinct <= _BINARY_TOKENS:
            candidates.append(name)
    if candidates:
        return TargetChoice(
            column=candidates[0],
            reason="only binary (yes/no style) column found besides identifiers",
        )
    return TargetChoice(
        column=None,
        reason="no label column found: no known label name matched and no clean binary column exists",
    )


@dataclass
class ColumnMissingDecision:
    column: str
    missing_count: int
    missing_fraction: float
    action: str  # "drop_column" | "impute_mean" | "impute_most_frequent" | "keep"


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
            action = "impute_mean"
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
    numerical: list[str] = []
    categorical: list[str] = []
    n = max(len(frame), 1)
    for name in columns:
        if name not in frame.columns:
            continue
        series = frame[name]
        unique = series.nunique(dropna=True)
        if unique <= 1:
            continue
        if _looks_like_identifier(name, series, n):
            continue
        if pd.api.types.is_bool_dtype(series):
            categorical.append(name)
        elif pd.api.types.is_numeric_dtype(series):
            numerical.append(name)
        elif unique <= max_categorical_cardinality:
            categorical.append(name)
        # else: high-cardinality free text — not modeled automatically.
    return numerical, categorical


def build_preprocessor(numerical_cols: list[str], categorical_cols: list[str]) -> ColumnTransformer:
    """Exactly the pipeline the user specified: mean-impute + scale numerics,
    most-frequent-impute + one-hot encode categoricals.
    """
    numeric_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="mean")),
            ("scaler", StandardScaler()),
        ]
    )
    categorical_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(drop="first", handle_unknown="ignore", sparse_output=False)),
        ]
    )
    transformers = []
    if numerical_cols:
        transformers.append(("num", numeric_transformer, numerical_cols))
    if categorical_cols:
        transformers.append(("cat", categorical_transformer, categorical_cols))
    return ColumnTransformer(transformers=transformers)
