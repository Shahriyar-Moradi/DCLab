"""Data-quality checks. Transformations are logged; nothing is dropped silently."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def quality_report(frame: pd.DataFrame, target: str | None = None) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    n = max(len(frame), 1)
    numeric = frame.select_dtypes(include=[np.number])

    missing = frame.isna().mean()
    for col, rate in missing.items():
        if rate > 0:
            issues.append({"code": "missing_values", "column": col, "rate": float(rate)})

    inf_cols = [col for col in numeric.columns if np.isinf(numeric[col].to_numpy()).any()]
    for col in inf_cols:
        issues.append({"code": "infinite_values", "column": col})

    dupes = int(frame.duplicated().sum())
    if dupes:
        issues.append({"code": "duplicate_rows", "count": dupes})

    for col in frame.columns:
        nunique = int(frame[col].nunique(dropna=True))
        if nunique <= 1:
            issues.append({"code": "constant_column", "column": col, "unique": nunique})
        elif nunique / n < 0.01 and nunique <= 3:
            issues.append({"code": "near_constant_column", "column": col, "unique": nunique})
        if nunique > min(1000, int(n * 0.9)) and not pd.api.types.is_numeric_dtype(frame[col]):
            issues.append({"code": "high_cardinality", "column": col, "unique": nunique})

    if target and target in frame.columns:
        missing_target = float(frame[target].isna().mean())
        if missing_target:
            issues.append({"code": "target_missingness", "column": target, "rate": missing_target})
        if frame[target].nunique(dropna=True) == 2:
            pos = float((frame[target] == frame[target].dropna().max()).mean())
            if pos < 0.02 or pos > 0.98:
                issues.append({"code": "target_imbalance", "positive_rate": pos})
    if len(frame) < 50:
        issues.append({"code": "insufficient_samples", "count": int(len(frame))})

    return {
        "row_count": int(len(frame)),
        "issue_count": len(issues),
        "issues": issues,
        "log": [f"{item['code']}: {item}" for item in issues],
    }
