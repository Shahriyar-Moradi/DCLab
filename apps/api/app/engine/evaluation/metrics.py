"""Classification, regression, and simple business metrics."""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
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
    raw = np.asarray(scores, dtype=float)
    p = np.clip(raw, 1e-7, 1 - 1e-7)
    # Decisions use the unclipped scores so they match prediction rows at any
    # threshold (including 1.0); clipping only protects log-loss and Brier.
    pred = (raw >= threshold).astype(int)
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
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
        "brier": float(brier_score_loss(y, p)),
        "brier_score": float(brier_score_loss(y, p)),
        "calibration_gap": calibration_gap,
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "top_decile_lift": top_decile_lift,
        "top_k_precision": float(topk.mean()) if len(topk) else 0.0,
        "positive_rate": float(y.mean()) if len(y) else 0.0,
    }


def multiclass_metrics(y_true, proba, *, n_classes: int) -> dict[str, Any]:
    """Metrics for integer-coded labels 0..n_classes-1 and an (n, n_classes) probability matrix.

    A class absent from the training fold has a zero probability column, so rows of
    that class count as misclassified instead of being dropped.
    """
    y = np.asarray(y_true).astype(int)
    p = np.asarray(proba, dtype=float).reshape(len(y), n_classes)
    p = np.clip(p, 1e-7, 1.0)
    p = p / p.sum(axis=1, keepdims=True)
    # Ties go to the lowest code (first label in sorted order), deterministically.
    pred = p.argmax(axis=1)
    labels = list(range(n_classes))
    present = sorted(set(int(value) for value in y))
    aucs = []
    for cls in present:
        positives = y == cls
        if positives.all() or not positives.any():
            continue
        aucs.append(float(roc_auc_score(positives.astype(int), p[:, cls])))
    roc_ovr = float(np.mean(aucs)) if aucs else 0.5
    # Macro averages run over the classes present in y_true only, so every
    # candidate scored on a fold shares the same denominator; predicting a class
    # absent from the fold costs recall of the true class, not an extra F1=0 term.
    scored = present or labels
    return {
        "accuracy": float(accuracy_score(y, pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)) if len(y) else 0.0,
        "macro_f1": float(f1_score(y, pred, labels=scored, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y, pred, labels=scored, average="weighted", zero_division=0)),
        "macro_precision": float(
            precision_score(y, pred, labels=scored, average="macro", zero_division=0)
        ),
        "macro_recall": float(recall_score(y, pred, labels=scored, average="macro", zero_division=0)),
        "log_loss": float(log_loss(y, p, labels=labels)),
        "roc_auc_ovr": roc_ovr,
        "confusion_matrix": confusion_matrix(y, pred, labels=labels).tolist(),
        "n_classes": n_classes,
    }


def regression_metrics(y_true, pred) -> dict[str, Any]:
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(pred, dtype=float)
    mae = float(mean_absolute_error(y, p))
    mse = float(mean_squared_error(y, p))
    rmse = float(mse ** 0.5)
    mape = float(np.mean(np.abs((y - p) / np.clip(np.abs(y), 1e-6, None)))) * 100
    smape = float(np.mean(2 * np.abs(y - p) / np.clip(np.abs(y) + np.abs(p), 1e-6, None))) * 100
    return {
        "mae": mae,
        "mse": mse,
        "rmse": rmse,
        "r2": float(r2_score(y, p)) if len(y) > 1 else 0.0,
        "mape": mape,
        "smape": smape,
        "median_absolute_error": float(median_absolute_error(y, p)),
    }


LOWER_IS_BETTER = {
    "mae",
    "mse",
    "rmse",
    "mape",
    "smape",
    "log_loss",
    "brier",
    "brier_score",
    "median_absolute_error",
    "calibration_gap",
}


def selection_metric_value(
    metric: str, y_true, pred, *, task_type: str, n_classes: int | None = None, threshold: float = 0.5
) -> float:
    """One metric, with the exact definitions of the full metric sets above (cheap path for
    repeated scoring, e.g. permutation importance); anything else uses the full set."""

    y = np.asarray(y_true)
    if task_type == "regression" and metric in {"mae", "mse", "rmse", "r2"}:
        p = np.asarray(pred, dtype=float)
        y = y.astype(float)
        if metric == "mae":
            return float(mean_absolute_error(y, p))
        mse = float(mean_squared_error(y, p))
        if metric == "mse":
            return mse
        if metric == "rmse":
            return float(np.sqrt(mse))
        return float(r2_score(y, p)) if len(y) > 1 else 0.0
    if task_type == "binary" and metric in {"roc_auc", "pr_auc", "log_loss", "brier", "brier_score", "accuracy",
                                             "precision", "recall", "f1", "balanced_accuracy"}:
        raw = np.asarray(pred, dtype=float)
        p = np.clip(raw, 1e-7, 1 - 1e-7)
        decided = (raw >= threshold).astype(int)
        if metric == "roc_auc":
            try:
                value = float(roc_auc_score(y, p))
            except ValueError:
                value = 0.5
            return value if np.isfinite(value) else 0.5
        if metric == "pr_auc":
            fallback = float(y.mean()) if len(y) else 0.0
            try:
                value = float(average_precision_score(y, p))
            except ValueError:
                value = fallback
            return value if np.isfinite(value) else fallback
        if metric == "log_loss":
            return float(log_loss(y, p, labels=[0, 1]))
        if metric in {"brier", "brier_score"}:
            return float(brier_score_loss(y, p))
        if metric == "accuracy":
            return float(accuracy_score(y, decided))
        if metric == "precision":
            return float(precision_score(y, decided, zero_division=0))
        if metric == "recall":
            return float(recall_score(y, decided, zero_division=0))
        if metric == "f1":
            return float(f1_score(y, decided, zero_division=0))
        return float(balanced_accuracy_score(y, decided))
    if task_type == "binary":
        return float(classification_metrics(y, pred, threshold=threshold).get(metric) or 0.0)
    if task_type == "multiclass":
        return float(multiclass_metrics(y, pred, n_classes=int(n_classes or 0)).get(metric) or 0.0)
    return float(regression_metrics(y, pred).get(metric) or 0.0)


def primary_score(metrics: dict[str, Any], metric_name: str, task_type: str) -> float:
    if metric_name in metrics:
        value = metrics[metric_name]
        score = float(value) if not isinstance(value, dict) else 0.0
        if metric_name in LOWER_IS_BETTER:
            return -score
        return score
    if task_type == "binary":
        return float(metrics.get("pr_auc") or metrics.get("roc_auc") or 0.0)
    if task_type == "multiclass":
        return float(metrics.get("macro_f1") or 0.0)
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
