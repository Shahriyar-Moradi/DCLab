"""D3a item 13: fast checks run once on an uploaded file (the "checked on upload" list).

Whole-file data-quality metadata computed at ingestion, before any split exists: exact duplicate
rows, missing values per column, text columns that look like dates but do not all parse, ID-like
columns and constant columns. The target is not known at upload, so nothing here reads it as a
target, and no run reads these results (runs repeat their own checks on training rows; leakage
checks need the goal and stay inside runs). Column names only, never cell values. Bounded:
files over ``UPLOAD_CHECKS_MAX_ROWS`` rows get no checks (null); the date check judges the first
``_DATE_SAMPLE`` values of each text column (its counts and shares are of that sample).
"""

from __future__ import annotations

import re
import warnings
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from app.domain.dataset_profile import UPLOAD_CHECK_COLUMNS_MAX

UPLOAD_CHECKS_MAX_ROWS = 1_000_000
_DATE_SAMPLE = 500
_DATE_LOOKS = re.compile(
    r"^\s*(\d{4}[-/.]\d{1,2}([-/.]\d{1,2})?|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})([ T]\d{1,2}:\d{2}(:\d{2})?.*)?\s*$"
)
_ID_MIN_ROWS = 20
_ID_UNIQUE_SHARE = 0.98


def _columns(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    return items[:UPLOAD_CHECK_COLUMNS_MAX], len(items) > UPLOAD_CHECK_COLUMNS_MAX


def _check(code: str, warn: bool, count: int | None, share: float | None, items: list[dict[str, Any]]
           ) -> dict[str, Any]:
    columns, truncated = _columns(items)
    return {"code": code, "status": "warn" if warn else "ok", "count": count, "share": share,
            "columns": columns, "columns_truncated": truncated}


def _text(series: pd.Series) -> bool:
    return not (pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series)
                or pd.api.types.is_datetime64_any_dtype(series))


def run_upload_checks(frame: pd.DataFrame | None, *, now: datetime | None = None) -> dict[str, Any] | None:
    """The compact result stored with the dataset (``UploadChecksRead`` shape), or None."""

    if frame is None or len(frame) == 0 or frame.shape[1] == 0 or len(frame) > UPLOAD_CHECKS_MAX_ROWS:
        return None
    frame = frame.copy()
    frame.columns = [str(name) for name in frame.columns]
    rows = int(len(frame))
    try:
        duplicates = int(frame.duplicated(keep="first").sum())
    except TypeError:  # unhashable cells (nested JSON): compare their text form
        duplicates = int(frame.astype(str).duplicated(keep="first").sum())
    missing = frame.isna().sum()
    gaps = sorted(((name, int(n)) for name, n in missing.items() if n), key=lambda item: (-item[1], item[0]))
    total_cells = rows * frame.shape[1]
    dates: list[dict[str, Any]] = []
    ids: list[dict[str, Any]] = []
    constant: list[dict[str, Any]] = []
    for name in frame.columns:
        series = frame[name]
        present = series.dropna()
        try:
            unique = int(present.nunique())
        except TypeError:  # unhashable cells
            unique = int(present.astype(str).nunique())
        if unique <= 1:
            constant.append({"name": name, "count": unique, "share": None})
            continue
        integer_like = pd.api.types.is_integer_dtype(series)
        if (integer_like or _text(series)) and len(present) >= _ID_MIN_ROWS and unique / len(present) >= _ID_UNIQUE_SHARE:
            ids.append({"name": name, "count": unique, "share": unique / len(present)})
        if not _text(series) or present.empty:
            continue
        sample = present.astype(str).head(_DATE_SAMPLE)
        looks = sample.str.match(_DATE_LOOKS)
        if looks.mean() < 0.8:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parsed = pd.to_datetime(sample, errors="coerce")
        failed = int(parsed.isna().sum())
        if failed:
            dates.append({"name": name, "count": failed, "share": failed / len(sample)})
    return {
        "scope": "whole_upload",
        "ran_at": (now or datetime.now(UTC)).isoformat(),
        "row_count": rows,
        "checks": [
            _check("duplicate_rows", duplicates > 0, duplicates, duplicates / rows, []),
            _check("missing_values", bool(gaps), len(gaps), float(missing.sum()) / total_cells,
                   [{"name": name, "count": n, "share": n / rows} for name, n in gaps]),
            _check("date_parsing", bool(dates), len(dates), None, dates),
            _check("id_like_columns", bool(ids), len(ids), None, ids),
            _check("constant_columns", bool(constant), len(constant), None, constant),
        ],
    }
