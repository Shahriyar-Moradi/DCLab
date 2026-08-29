"""Dataset profiler. Results are JSON-serializable and regenerable."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Matches auto_prepare.MAX_CATEGORICAL_CARDINALITY: text above this is not one-hot encoded.
HIGH_CARDINALITY_UNIQUE = 50


def _is_identifier(name: str, series: pd.Series) -> bool:
    key = name.lower()
    if key.endswith("_id") or key in {"id", "uuid", "guid"}:
        return True
    n = len(series)
    if n and series.nunique(dropna=True) / n > 0.95 and not pd.api.types.is_float_dtype(series):
        return True
    return False


def _is_high_cardinality(series: pd.Series, unique: int, n: int) -> bool:
    if pd.api.types.is_bool_dtype(series):
        return False
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_object_dtype(series):
        return False
    if unique > HIGH_CARDINALITY_UNIQUE:
        return True
    return bool(n and unique / n > 0.5)


def profile_frame(frame: pd.DataFrame) -> dict[str, Any]:
    columns: list[dict[str, Any]] = []
    n = max(len(frame), 1)
    for name in frame.columns:
        series = frame[name]
        missing = int(series.isna().sum())
        unique = int(series.nunique(dropna=True))
        info: dict[str, Any] = {
            "name": str(name),
            "dtype": str(series.dtype),
            "missing": missing,
            "missing_count": missing,
            "missing_pct": missing / n,
            "missing_percentage": missing / n,
            "unique": unique,
            "unique_count": unique,
            "cardinality": unique,
            "constant": unique <= 1,
            "near_constant": unique > 1 and unique / n < 0.01,
            "high_cardinality": _is_high_cardinality(series, unique, n),
            "identifier_like": _is_identifier(str(name), series),
            "datetime": bool(pd.api.types.is_datetime64_any_dtype(series) or "date" in str(name).lower()),
        }
        if pd.api.types.is_bool_dtype(series):
            info["categorical_distribution"] = {
                str(key).lower(): int(value) for key, value in series.value_counts(dropna=True).items()
            }
        elif pd.api.types.is_numeric_dtype(series):
            clean = pd.to_numeric(series, errors="coerce")
            info.update(
                {
                    "min": _num(clean.min()),
                    "max": _num(clean.max()),
                    "mean": _num(clean.mean()),
                    "median": _num(clean.median()),
                    "std": _num(clean.std()),
                    "quantiles": {
                        "p25": _num(clean.quantile(0.25)),
                        "p50": _num(clean.quantile(0.5)),
                        "p75": _num(clean.quantile(0.75)),
                    },
                }
            )
        else:
            top = series.astype(str).value_counts(dropna=True).head(10)
            info["categorical_distribution"] = {str(k): int(v) for k, v in top.items()}
        columns.append(info)

    identifier_like = [c["name"] for c in columns if c["identifier_like"]]
    cells = int(len(frame) * max(frame.shape[1], 1))
    total_missing = int(sum(c["missing"] for c in columns))
    numerical_statistics = {
        c["name"]: {key: c[key] for key in ("min", "max", "mean", "median", "std", "quantiles") if key in c}
        for c in columns
        if "mean" in c
    }
    categorical_statistics = {
        c["name"]: c["categorical_distribution"] for c in columns if "categorical_distribution" in c
    }

    return {
        "row_count": int(len(frame)),
        "column_count": int(frame.shape[1]),
        "column_names": [str(name) for name in frame.columns],
        "dtypes": {c["name"]: c["dtype"] for c in columns},
        "missing_count": total_missing,
        "missing_percentage": (total_missing / cells) if cells else 0.0,
        "missing_count_by_column": {c["name"]: c["missing"] for c in columns},
        "missing_percentage_by_column": {c["name"]: c["missing_pct"] for c in columns},
        "unique_count": {c["name"]: c["unique"] for c in columns},
        "duplicate_count": int(frame.duplicated().sum()),
        "duplicate_rows": int(frame.duplicated().sum()),
        "numerical_statistics": numerical_statistics,
        "categorical_statistics": categorical_statistics,
        "constant_columns": [c["name"] for c in columns if c["constant"]],
        "near_constant_columns": [c["name"] for c in columns if c["near_constant"]],
        "high_cardinality_columns": [c["name"] for c in columns if c["high_cardinality"]],
        "likely_identifier_columns": identifier_like,
        "identifier_like_columns": identifier_like,
        "datetime_columns": [c["name"] for c in columns if c["datetime"]],
        "suspicious_columns": [
            c["name"] for c in columns if c["identifier_like"] or c["constant"] or c["missing_pct"] > 0.8
        ],
        "columns": columns,
    }


def _num(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
