"""Turn mixed CSV columns into numeric matrices the sklearn families can fit."""

from __future__ import annotations

import numpy as np
import pandas as pd

_TRUE = {"1", "true", "yes", "y", "won", "converted", "churned", "purchased"}
_FALSE = {"0", "false", "no", "n", "lost", "open"}


def coerce_binary_target(series: pd.Series) -> pd.Series:
    """Map common label spellings to 0/1. Unrecognised values become NA."""
    if pd.api.types.is_bool_dtype(series):
        return series.astype(int)
    if pd.api.types.is_numeric_dtype(series):
        numeric = pd.to_numeric(series, errors="coerce")
        return numeric.where(numeric.isin({0, 1}), np.nan)

    def _one(value: object) -> float:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return np.nan
        token = str(value).strip().lower()
        if token in _TRUE:
            return 1.0
        if token in _FALSE:
            return 0.0
        return np.nan

    return series.map(_one)


def encode_feature_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Bools → 0/1, dates → unix seconds, other objects → factor codes.

    Time / entity / target columns should be omitted from ``columns`` so splits
    still see original timestamps and ids.
    """
    out = frame.copy()
    for name in columns:
        if name not in out.columns:
            continue
        series = out[name]
        if pd.api.types.is_bool_dtype(series):
            out[name] = series.astype(float)
            continue
        if pd.api.types.is_datetime64_any_dtype(series):
            parsed = pd.to_datetime(series, errors="coerce")
            out[name] = parsed.map(lambda value: value.timestamp() if pd.notna(value) else 0.0)
            continue
        if pd.api.types.is_numeric_dtype(series):
            continue
        looks_like_time = any(token in name.lower() for token in ("date", "time", "timestamp"))
        if looks_like_time:
            parsed = pd.to_datetime(series, errors="coerce")
            if float(parsed.notna().mean()) >= 0.8:
                out[name] = parsed.map(lambda value: value.timestamp() if pd.notna(value) else 0.0)
                continue
        codes, _uniques = pd.factorize(series.astype(str), sort=True)
        out[name] = codes.astype(float)
    return out
