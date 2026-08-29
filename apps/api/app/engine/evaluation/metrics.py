"""Classification, regression, and simple business metrics."""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    mean_absolute_error,
    mean_squared_error,
    median_absolute_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)


def classification_metrics(y_true, scores, *, threshold: float = 0.5) -> dict[str, Any]:
    y = np.asarray(y_true)
    p = np.clip(np.asarray(scores, dtype=float), 1e-7, 1 - 1e-7)
    pred = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    order = np.argsort(-p)
    k = max(1, int(len(y) * 0.1))
    topk = y[order][:k]
    top_decile_lift = float(topk.mean() / max(y.mean(), 1e-9))
    frac_pos, mean_pred = calibration_curve(y, p, n_bins=5, strategy="uniform")
    calibration_gap = float(abs(frac_pos - mean_pred).mean()) if len(frac_pos) else 1.0
    try:
        roc = float(roc_auc_score(y, p))
    except ValueError:
        roc = 0.5
    if not np.isfinite(roc):
        roc = 0.5
    try:
        pr = float(average_precision_score(y, p))
    except ValueError:
        pr = float(y.mean()) if len(y) else 0.0
    if not np.isfinite(pr):
        pr = float(y.mean()) if len(y) else 0.0
    return {
        "accuracy": float(accuracy_score(y, pred)),
        "roc_auc": roc,
        "pr_auc": pr,
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "brier": float(brier_score_loss(y, p)),
        "calibration_gap": calibration_gap,
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "top_decile_lift": top_decile_lift,
        "top_k_precision": float(topk.mean()) if len(topk) else 0.0,
        "positive_rate": float(y.mean()) if len(y) else 0.0,
    }


def regression_metrics(y_true, pred) -> dict[str, Any]:
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(pred, dtype=float)
    mae = float(mean_absolute_error(y, p))
    rmse = float(mean_squared_error(y, p) ** 0.5)
    mape = float(np.mean(np.abs((y - p) / np.clip(np.abs(y), 1e-6, None)))) * 100
    smape = float(np.mean(2 * np.abs(y - p) / np.clip(np.abs(y) + np.abs(p), 1e-6, None))) * 100
    return {
        "mae": mae,
        "rmse": rmse,
        "r2": float(r2_score(y, p)) if len(y) > 1 else 0.0,
        "mape": mape,
        "smape": smape,
        "median_absolute_error": float(median_absolute_error(y, p)),
    }


LOWER_IS_BETTER = {
    "mae",
    "rmse",
    "mape",
    "smape",
    "log_loss",
    "brier",
    "median_absolute_error",
    "calibration_gap",
}


def primary_score(metrics: dict[str, Any], metric_name: str, task_type: str) -> float:
    if metric_name in metrics:
        value = metrics[metric_name]
        score = float(value) if not isinstance(value, dict) else 0.0
        if metric_name in LOWER_IS_BETTER:
            return -score
        return score
    if task_type == "binary":
        return float(metrics.get("pr_auc") or metrics.get("roc_auc") or 0.0)
    if "mae" in metrics:
        return float(-metrics["mae"])
    return 0.0


def robustness_stats(scores: list[float]) -> dict[str, float]:
    arr = np.asarray(scores, dtype=float)
    if not len(arr):
        return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "range": 0.0}
    return {
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "range": float(arr.max() - arr.min()),
    }


def aggregate_fold_metrics(fold_metrics: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, float]]:
    """Mean and standard deviation of scalar metrics across CV folds."""
    if not fold_metrics:
        return {}, {}
    keys = [
        key
        for key, value in fold_metrics[0].items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    means: dict[str, float] = {}
    stds: dict[str, float] = {}
    for key in keys:
        values = [
            float(row[key])
            for row in fold_metrics
            if key in row and isinstance(row[key], (int, float)) and not isinstance(row[key], bool)
        ]
        if not values:
            continue
        arr = np.asarray(values, dtype=float)
        means[key] = float(arr.mean())
        stds[key] = float(arr.std())
    return means, stds
