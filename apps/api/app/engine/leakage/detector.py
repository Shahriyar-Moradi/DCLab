"""Leakage risk scoring. Never silently drops columns."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

SUSPICIOUS_TOKENS = (
    "future",
    "label",
    "target",
    "outcome",
    "converted",
    "churned",
    "won",
    "cancelled",
    "canceled",
    "delivered",
    "post_",
    "final_",
    "true_p",
    "oracle",
)


def detect_leakage(
    frame: pd.DataFrame,
    *,
    target: str,
    time_col: str | None = None,
    entity_col: str | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if target not in frame.columns:
        return {"risk": "LOW", "findings": [{"code": "missing_target", "detail": target}]}

    y = frame[target]
    cutoff = None
    if time_col and time_col in frame.columns:
        cutoff = pd.to_datetime(frame[time_col], errors="coerce")

    for col in frame.columns:
        if col == target:
            continue
        series = frame[col]
        reasons: list[str] = []
        key = col.lower()
        if any(token in key for token in SUSPICIOUS_TOKENS):
            reasons.append("name_suggests_post_outcome")
        if entity_col and col == entity_col:
            continue
        if col.lower().endswith("_id") or col.lower() in {"id", "uuid", "guid"}:
            reasons.append("identifier_like")
        if cutoff is not None and pd.api.types.is_datetime64_any_dtype(series):
            later = pd.to_datetime(series, errors="coerce") > cutoff
            if later.fillna(False).any():
                reasons.append("datetime_after_prediction_time")
        if pd.api.types.is_numeric_dtype(series) and y.nunique(dropna=True) == 2:
            clean = pd.to_numeric(series, errors="coerce")
            mask = clean.notna() & y.notna()
            if mask.sum() > 20 and y[mask].nunique() == 2:
                try:
                    auc = float(roc_auc_score(y[mask], clean[mask]))
                    auc = max(auc, 1 - auc)
                    if auc >= 0.97:
                        reasons.append(f"single_feature_auc_{auc:.3f}")
                except ValueError:
                    pass
        elif pd.api.types.is_numeric_dtype(series) and pd.api.types.is_numeric_dtype(y) and y.nunique(dropna=True) > 2:
            clean = pd.to_numeric(series, errors="coerce")
            mask = clean.notna() & y.notna()
            if mask.sum() > 20:
                corr = np.corrcoef(clean[mask].to_numpy(dtype=float), y[mask].to_numpy(dtype=float))[0, 1]
                if np.isfinite(corr) and (corr ** 2) >= 0.95:
                    reasons.append(f"single_feature_r2_{corr ** 2:.3f}")
        if reasons:
            risk = "HIGH" if any(
                token in r for r in reasons for token in ("auc", "r2", "post_outcome")
            ) else "MEDIUM"
            findings.append({"column": col, "risk": risk, "reasons": reasons})

    high = sum(1 for item in findings if item["risk"] == "HIGH")
    medium = sum(1 for item in findings if item["risk"] == "MEDIUM")
    overall = "HIGH" if high else "MEDIUM" if medium else "LOW"
    return {
        "risk": overall,
        "high_count": high,
        "medium_count": medium,
        "findings": findings,
        "high_risk_columns": [item["column"] for item in findings if item["risk"] == "HIGH"],
    }
