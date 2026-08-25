"""Dataset profiler. Results are JSON-serializable and regenerable."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _is_identifier(name: str, series: pd.Series) -> bool:
    key = name.lower()
    if key.endswith("_id") or key in {"id", "uuid", "guid"}:
        return True
    n = len(series)
    if n and series.nunique(dropna=True) / n > 0.95 and not pd.api.types.is_float_dtype(series):
        return True
    return False


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
            "missing_pct": missing / n,
            "unique": unique,
            "cardinality": unique,
            "constant": unique <= 1,
            "near_constant": unique > 1 and unique / n < 0.01,
            "identifier_like": _is_identifier(str(name), series),
            "datetime": bool(pd.api.types.is_datetime64_any_dtype(series) or "date" in str(name).lower()),
        }
        if pd.api.types.is_numeric_dtype(series):
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

    return {
        "row_count": int(len(frame)),
        "column_count": int(frame.shape[1]),
        "duplicate_rows": int(frame.duplicated().sum()),
        "constant_columns": [c["name"] for c in columns if c["constant"]],
        "near_constant_columns": [c["name"] for c in columns if c["near_constant"]],
        "identifier_like_columns": [c["name"] for c in columns if c["identifier_like"]],
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
