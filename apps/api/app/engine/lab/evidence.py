"""Per-column evidence for a table already in memory.

Pure functions: given a pandas DataFrame, describe one column. No I/O, no
LLM, no randomness — the same frame always produces the same dataclass.

Two evidence types:

- missingness (``build_column_evidence``): correlation with a label, and
  where missingness lines up with a specific value in another column. The
  crosstab check surfaces patterns like Telco ``TotalCharges`` empty exactly
  when ``tenure == 0``.
- column type (``build_column_type_evidence``): dtype, cardinality, and a
  sample of raw values, used when a numeric-looking column might actually be
  a category code or identifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# A co-occurrence is reported only when missingness and a single other-column
# value explain each other at least this strongly.
_COOCCURRENCE_THRESHOLD = 0.8
_SAMPLE_LIMIT = 5
_SAMPLE_VALUES_LIMIT = 10

# Numeric columns with this many or fewer distinct values may be category
# codes stored as integers (plan codes, 0/1 flags). Kept well below typical
# continuous unique counts (tenure, charges) so those never reach the LLM.
# Identifier-like uniqueness is a separate, narrower band below.
COLUMN_TYPE_AMBIGUOUS_UNIQUE_MAX = 20

# Integer columns in this uniqueness band look like identifiers but are
# still below auto_prepare's deterministic drop (unique/n > 0.95).
COLUMN_TYPE_AMBIGUOUS_ID_RATIO_MIN = 0.93
COLUMN_TYPE_IDENTIFIER_RATIO = 0.95


def _is_missing(series: pd.Series) -> pd.Series:
    """NaN/None plus blank / whitespace-only strings (the Telco `" "` case)."""
    missing = series.isna()
    if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
        as_str = series.where(series.notna(), other="").astype(str).str.strip()
        missing = series.isna() | as_str.eq("") | as_str.str.lower().isin({"nan", "none", "null"})
    return missing


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return None if np.isnan(number) or np.isinf(number) else number
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return str(value)
    return value


def _equals(series: pd.Series, value: Any) -> pd.Series:
    if value is None:
        return series.isna()
    return series.eq(value)


def _correlation(column: pd.Series, target: pd.Series) -> float | None:
    left = pd.to_numeric(column, errors="coerce")
    right = pd.to_numeric(target, errors="coerce")
    aligned = pd.concat([left, right], axis=1).dropna()
    if len(aligned) < 2:
        return None
    if int(aligned.iloc[:, 0].nunique()) < 2 or int(aligned.iloc[:, 1].nunique()) < 2:
        return None
    corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    if corr is None or pd.isna(corr):
        return None
    return float(corr)


@dataclass(frozen=True)
class MissingnessCooccurrence:
    """`column` is missing together with `other_column == other_value`."""

    other_column: str
    other_value: Any
    missing_and_value_count: int
    rows_with_value: int
    fraction_of_missing: float
    fraction_of_value: float
    exact_match: bool


@dataclass(frozen=True)
class ColumnEvidence:
    column: str
    dtype: str
    missing_count: int
    missing_fraction: float
    correlation_with_target: float | None
    missingness_cooccurrence: list[MissingnessCooccurrence] = field(default_factory=list)
    sample_rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ColumnTypeEvidence:
    """Dtype / uniqueness snapshot used to classify numerical vs categorical vs identifier."""

    column: str
    dtype: str
    cardinality: int
    cardinality_ratio: float
    sample_values: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class TargetColumnEvidence:
    """Compact profile for one eligible target candidate."""

    name: str
    dtype: str
    unique_count: int
    unique_ratio: float
    missing_ratio: float
    identifier_likelihood: float
    probable_task_type: str
    deterministic_confidence: float
    sample_values: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class TargetSelectionEvidence:
    """Dataset metadata sent to the semantic target ranker (never raw rows)."""

    row_count: int
    column_count: int
    columns: list[TargetColumnEvidence] = field(default_factory=list)


def build_column_evidence(
    frame: pd.DataFrame,
    column: str,
    target: str | None = None,
) -> ColumnEvidence:
    """Describe `column` in `frame`. Does not modify `frame`."""
    if column not in frame.columns:
        raise KeyError(f"column {column!r} is not in the frame")

    series = frame[column]
    n = max(len(frame), 1)
    missing = _is_missing(series)
    missing_count = int(missing.sum())
    missing_fraction = missing_count / n

    correlation = None
    if target and target in frame.columns and target != column:
        correlation = _correlation(series, frame[target])

    cooccurrence = _missingness_cooccurrence(frame, column, missing)
    relevant = _relevant_columns(column, target, cooccurrence, frame.columns)
    sample_rows = _sample_rows(frame, missing, cooccurrence, relevant)

    return ColumnEvidence(
        column=column,
        dtype=str(series.dtype),
        missing_count=missing_count,
        missing_fraction=missing_fraction,
        correlation_with_target=correlation,
        missingness_cooccurrence=cooccurrence,
        sample_rows=sample_rows,
    )


def build_column_type_evidence(frame: pd.DataFrame, column: str) -> ColumnTypeEvidence:
    """Describe `column`'s dtype and uniqueness. Does not modify `frame`."""
    if column not in frame.columns:
        raise KeyError(f"column {column!r} is not in the frame")

    series = frame[column]
    n = max(len(frame), 1)
    cardinality = int(series.nunique(dropna=True))
    return ColumnTypeEvidence(
        column=column,
        dtype=str(series.dtype),
        cardinality=cardinality,
        cardinality_ratio=cardinality / n,
        sample_values=_sample_values(series),
    )


def build_target_selection_evidence(
    row_count: int,
    column_count: int,
    candidates: list[Any],
) -> TargetSelectionEvidence:
    """Convert deterministic candidates into the compact LLM evidence schema."""
    columns: list[TargetColumnEvidence] = []
    for candidate in candidates:
        snapshot = dict(candidate.evidence)
        columns.append(
            TargetColumnEvidence(
                name=candidate.column,
                dtype=str(snapshot.get("dtype") or "unknown"),
                unique_count=int(snapshot.get("unique_count") or 0),
                unique_ratio=float(snapshot.get("unique_ratio") or 0.0),
                missing_ratio=float(snapshot.get("missing_ratio") or 0.0),
                identifier_likelihood=float(snapshot.get("identifier_likelihood") or 0.0),
                probable_task_type=candidate.probable_task_type,
                deterministic_confidence=float(candidate.confidence),
                sample_values=list(snapshot.get("sample_values") or []),
            )
        )
    return TargetSelectionEvidence(
        row_count=int(row_count),
        column_count=int(column_count),
        columns=columns,
    )


def is_ambiguous_column_type(frame: pd.DataFrame, column: str, evidence: ColumnTypeEvidence) -> bool:
    """True when the dtype rule is not obviously the only reasonable role.

    Most columns never qualify: continuous numerics (tenure, MonthlyCharges)
    stay on the rule engine. The traps are a numeric dtype with few distinct
    values (integer plan codes) or an integer whose uniqueness is high enough
    to look like an ID but still below the deterministic identifier drop.
    """
    if column not in frame.columns:
        return False
    series = frame[column]
    if pd.api.types.is_bool_dtype(series) or not pd.api.types.is_numeric_dtype(series):
        return False
    if evidence.cardinality <= 1:
        return False
    if evidence.cardinality <= COLUMN_TYPE_AMBIGUOUS_UNIQUE_MAX:
        return True
    if pd.api.types.is_float_dtype(series):
        return False
    return COLUMN_TYPE_AMBIGUOUS_ID_RATIO_MIN <= evidence.cardinality_ratio < COLUMN_TYPE_IDENTIFIER_RATIO


def _sample_values(series: pd.Series) -> list[Any]:
    """First distinct non-missing values, in order of appearance."""
    values: list[Any] = []
    seen: set[Any] = set()
    for raw in series.tolist():
        if len(values) >= _SAMPLE_VALUES_LIMIT:
            break
        if raw is None:
            continue
        try:
            if pd.isna(raw):
                continue
        except (TypeError, ValueError):
            pass
        item = _json_safe(raw)
        key: Any
        try:
            hash(item)
            key = item
        except TypeError:
            key = repr(item)
        if key in seen:
            continue
        seen.add(key)
        values.append(item)
    return values


def _missingness_cooccurrence(
    frame: pd.DataFrame, column: str, missing: pd.Series
) -> list[MissingnessCooccurrence]:
    if not bool(missing.any()):
        return []

    flags: list[MissingnessCooccurrence] = []
    missing_count = int(missing.sum())
    for other_name in list(frame.columns):
        if other_name == column:
            continue
        other = frame[other_name]
        observed = other[missing].dropna()
        if observed.empty:
            continue
        counts: dict[Any, int] = {}
        for raw in observed.tolist():
            key = _json_safe(raw)
            counts[key] = counts.get(key, 0) + 1
        top_value = sorted(counts.items(), key=lambda item: (-item[1], repr(item[0])))[0][0]
        match = _equals(other, top_value)
        both = missing & match
        both_count = int(both.sum())
        rows_with_value = int(match.sum())
        if rows_with_value == 0 or both_count == 0:
            continue
        fraction_of_missing = both_count / missing_count
        fraction_of_value = both_count / rows_with_value
        exact = bool((missing == match).all())
        if exact or (
            fraction_of_missing >= _COOCCURRENCE_THRESHOLD and fraction_of_value >= _COOCCURRENCE_THRESHOLD
        ):
            flags.append(
                MissingnessCooccurrence(
                    other_column=other_name,
                    other_value=top_value,
                    missing_and_value_count=both_count,
                    rows_with_value=rows_with_value,
                    fraction_of_missing=fraction_of_missing,
                    fraction_of_value=fraction_of_value,
                    exact_match=exact,
                )
            )
    flags.sort(key=lambda item: (-item.fraction_of_missing, -item.fraction_of_value, item.other_column))
    return flags


def _relevant_columns(
    column: str,
    target: str | None,
    cooccurrence: list[MissingnessCooccurrence],
    columns: pd.Index,
) -> list[str]:
    names: list[str] = [column]
    if target and target in columns and target not in names:
        names.append(target)
    for item in cooccurrence:
        if item.other_column in columns and item.other_column not in names:
            names.append(item.other_column)
    return names


def _sample_rows(
    frame: pd.DataFrame,
    missing: pd.Series,
    cooccurrence: list[MissingnessCooccurrence],
    relevant: list[str],
) -> list[dict[str, Any]]:
    if cooccurrence:
        top = cooccurrence[0]
        pattern = missing & _equals(frame[top.other_column], top.other_value)
    else:
        pattern = missing
    positions = np.flatnonzero(pattern.to_numpy())
    if len(positions) == 0:
        positions = np.arange(len(frame))
    take = positions[:_SAMPLE_LIMIT]
    rows: list[dict[str, Any]] = []
    for pos in take:
        rec = {name: _json_safe(frame.iloc[int(pos)][name]) for name in relevant}
        rows.append(rec)
    return rows
