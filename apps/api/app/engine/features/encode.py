"""Turn mixed CSV columns into numeric matrices the sklearn families can fit."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

_TRUE = {"1", "true", "yes", "y", "won", "converted", "churned", "purchased"}
_FALSE = {"0", "false", "no", "n", "lost", "open"}


def binary_positive_label(series: pd.Series) -> str | None:
    """The positive label of a two-valued text target without yes/no-style tokens.

    Labels are compared stripped; the later label in sorted order is positive.
    The rule depends only on the label values (never on row counts), so every
    consumer of the same column derives the same 0/1 mapping. ``None`` when the
    series is not such a target.
    """
    if pd.api.types.is_bool_dtype(series) or pd.api.types.is_numeric_dtype(series):
        return None
    labels = {str(value).strip() for value in series.dropna()} - {""}
    if len(labels) != 2 or {label.lower() for label in labels} <= (_TRUE | _FALSE):
        return None
    return sorted(labels)[-1]


def coerce_binary_target(series: pd.Series) -> pd.Series:
    """Map common label spellings to 0/1. Unrecognised values become NA.

    Two arbitrary text labels (an explicitly chosen target) map through
    ``binary_positive_label``.
    """
    if pd.api.types.is_bool_dtype(series):
        return series.astype(int)
    if pd.api.types.is_numeric_dtype(series):
        numeric = pd.to_numeric(series, errors="coerce")
        return numeric.where(numeric.isin({0, 1}), np.nan)
    positive = binary_positive_label(series)
    if positive is not None:
        return series.map(
            lambda value: np.nan
            if value is None or (isinstance(value, float) and np.isnan(value)) or not str(value).strip()
            else float(str(value).strip() == positive)
        )

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


def encode_datetime_columns(frame: pd.DataFrame, columns: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Convert datetime (and date-named parseable) columns to unix seconds.

    Missing timestamps stay NA so the numeric imputer handles them. This is the
    deterministic date transform used by open-ingest; it does not factorize
    categoricals (ColumnTransformer one-hot needs the original strings).
    """
    out = frame.copy()
    converted: list[str] = []
    for name in columns:
        if name not in out.columns:
            continue
        series = out[name]
        if pd.api.types.is_bool_dtype(series) or pd.api.types.is_numeric_dtype(series):
            continue
        parsed: pd.Series | None = None
        if pd.api.types.is_datetime64_any_dtype(series):
            parsed = pd.to_datetime(series, errors="coerce")
        else:
            looks_like_time = any(token in name.lower() for token in ("date", "time", "timestamp"))
            if looks_like_time:
                candidate = pd.to_datetime(series, errors="coerce")
                if float(candidate.notna().mean()) >= 0.8:
                    parsed = candidate
        if parsed is None:
            continue
        out[name] = parsed.map(lambda value: value.timestamp() if pd.notna(value) else np.nan)
        converted.append(name)
    return out, converted


def infer_datetime_format(series: pd.Series) -> str | None:
    """The format ``pd.to_datetime`` infers for this column (from its first non-null
    value), ``"mixed"`` when it cannot infer one (pandas then parses each value on its
    own), or None for an already datetime-typed column.

    Recorded on the training partition so the holdout and every scoring file read an
    ambiguous ``03/04/2024`` the way training did, instead of re-guessing per frame.
    """

    if pd.api.types.is_datetime64_any_dtype(series):
        return None
    values = np.asarray(series, dtype=object)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        try:  # the helper to_datetime itself uses: recording never changes a training parse
            from pandas.core.tools.datetimes import _guess_datetime_format_for_array

            guessed = _guess_datetime_format_for_array(values, dayfirst=False)
        except ImportError:  # pragma: no cover - pandas moved its private helper
            from pandas.tseries.api import guess_datetime_format

            first = next((value for value in values if isinstance(value, str) and value.strip()), None)
            guessed = guess_datetime_format(first) if first is not None else None
    return guessed or "mixed"


def datetime_to_epoch(series: pd.Series, date_format: str | None = None) -> pd.Series:
    """Unix seconds (unparsed values stay NA), parsing with ``date_format`` when given."""

    parsed = pd.to_datetime(series, errors="coerce", format=date_format)
    return parsed.map(lambda value: value.timestamp() if pd.notna(value) else np.nan)


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
