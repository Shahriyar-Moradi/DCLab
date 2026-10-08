"""Out-of-fold evidence of the locked winner (P5.1-A): ``result["oof_evidence"]``.

The runner (``_run_open_ingest_candidates`` via ``_winner_oof_evidence``) calls
``oof_evidence`` right after the winner is selected -- before ``_lock_decision_threshold``
and before the final holdout is touched -- with OUT-OF-FOLD data only: the winner's CV
validation-row predictions (each training-pool row predicted by the fold model that did not
train on it) and those rows' labels. Only aggregates are stored -- calibration bins and
per-subgroup scores with their standard errors, without the group values -- so the trust
checks can judge calibration and subgroup gaps without a per-row table. Pure: numpy/pandas
only, no I/O.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from app.engine.evaluation.metrics import LOWER_IS_BETTER, selection_metric_value

OOF_EVIDENCE_VERSION = 3  # 2: per-group standard errors; 3: skipped-group counts, fold chance spread
CALIBRATION_BINS = 10  # equal-width probability bins (the usual reliability-diagram grid)
# Subgroups: categorical model columns with 2..10 values; a group needs 30 rows (and, for
# ROC AUC, 5 of each class -- the Hanley-McNeil standard error carries the small-group
# uncertainty) to carry a score; at most 20 columns are scored, skipped groups are counted.
SUBGROUP_MAX_LEVELS = 10
SUBGROUP_MIN_ROWS = 30
SUBGROUP_MIN_CLASS_ROWS = 5
SUBGROUP_MAX_COLUMNS = 20
_DIGITS = 6


def _r(value: float) -> float:
    return round(float(value), _DIGITS)


def _calibration(task_type: str, y: np.ndarray, pred: np.ndarray) -> dict[str, Any]:
    if task_type == "binary":
        p = np.clip(np.asarray(pred, dtype=float).reshape(-1), 0.0, 1.0)
        outcome = (np.asarray(y) == 1).astype(float)
        probabilistic = bool(len(p)) and not bool(np.isin(p, (0.0, 1.0)).all())
        prevalence = float(outcome.mean()) if len(outcome) else 0.0
        brier = float(np.mean((p - outcome) ** 2)) if len(p) else 0.0
        base = prevalence * (1.0 - prevalence)
        kind = "binary"
    else:
        matrix = np.clip(np.asarray(pred, dtype=float), 0.0, None)
        matrix = matrix / np.clip(matrix.sum(axis=1, keepdims=True), 1e-12, None)
        labels = np.asarray(y).astype(int)
        onehot = np.zeros_like(matrix)
        onehot[np.arange(len(labels)), labels] = 1.0
        p = matrix.max(axis=1)  # top-label confidence
        outcome = (matrix.argmax(axis=1) == labels).astype(float)
        probabilistic = bool(len(p)) and not bool(np.isin(matrix, (0.0, 1.0)).all())
        brier = float(np.mean(((matrix - onehot) ** 2).sum(axis=1))) if len(p) else 0.0
        shares = onehot.mean(axis=0) if len(p) else np.zeros(matrix.shape[1])
        base = float(1.0 - (shares ** 2).sum())
        kind = "multiclass_top_label"
    n = len(p)
    index = np.minimum((p * CALIBRATION_BINS).astype(int), CALIBRATION_BINS - 1)
    bins: list[dict[str, Any]] = []
    ece = noise = 0.0
    for b in range(CALIBRATION_BINS):
        mask = index == b
        rows = int(mask.sum())
        if not rows:
            continue
        mean_p, observed = float(p[mask].mean()), float(outcome[mask].mean())
        weight = rows / n
        ece += weight * abs(mean_p - observed)
        # |observed - mean_p| of a perfectly calibrated bin: E|N(0, s^2)| = s * sqrt(2 / pi).
        noise += weight * math.sqrt(2.0 / math.pi) * math.sqrt(max(mean_p * (1.0 - mean_p), 0.0) / rows)
        bins.append({"lower": _r(b / CALIBRATION_BINS), "upper": _r((b + 1) / CALIBRATION_BINS), "rows": rows,
                     "mean_predicted": _r(mean_p), "observed_rate": _r(observed)})
    return {"kind": kind, "probabilistic": probabilistic, "rows": n, "ece": _r(ece), "ece_noise": _r(noise),
            "brier": _r(brier), "base_rate_brier": _r(base),
            "mean_predicted": _r(p.mean()) if n else None, "observed_rate": _r(outcome.mean()) if n else None,
            "bins": bins}


def subgroup_metric(task_type: str, primary_metric: str | None) -> str:
    """Threshold-free ROC AUC for binary (comparable across groups with different base
    rates), accuracy for multiclass, the primary error (MAE unless RMSE) for regression."""

    if task_type == "binary":
        return "roc_auc"
    if task_type == "multiclass":
        return "accuracy"
    return primary_metric if primary_metric in {"mae", "rmse"} else "mae"


def _score(metric: str, task_type: str, y: np.ndarray, pred: np.ndarray, n_classes: int | None) -> float | None:
    if task_type == "binary":
        labels = np.asarray(y).astype(int)
        if min(int((labels == 1).sum()), int((labels == 0).sum())) < SUBGROUP_MIN_CLASS_ROWS:
            return None
    value = selection_metric_value(metric, y, pred, task_type=task_type, n_classes=n_classes)
    return float(value) if math.isfinite(value) else None


def _standard_error(metric: str, task_type: str, y: np.ndarray, pred: np.ndarray, score: float) -> float | None:
    """Sampling standard error of one group's score: Hanley-McNeil (ROC AUC), binomial
    (accuracy), the spread of |error| (MAE) or the delta method on squared errors (RMSE)."""

    n = len(y)
    if task_type == "binary":
        positives = int((np.asarray(y).astype(int) == 1).sum())
        negatives = n - positives
        # A perfectly (or perfectly wrongly) ranked group would get a zero error: floor it at half a pair.
        half_pair = 0.5 / max(positives * negatives, 1)
        score = min(max(score, half_pair), 1.0 - half_pair)
        q1, q2 = score / (2 - score), 2 * score ** 2 / (1 + score)
        variance = (score * (1 - score) + (positives - 1) * (q1 - score ** 2)
                    + (negatives - 1) * (q2 - score ** 2)) / max(positives * negatives, 1)
        return math.sqrt(max(variance, 0.0))
    if task_type == "multiclass":
        return math.sqrt(max(score * (1 - score), 0.25 / n) / n)
    errors = np.asarray(pred, dtype=float) - np.asarray(y, dtype=float)
    if metric == "rmse":
        return float(np.std(errors ** 2) / (2 * max(score, 1e-12) * math.sqrt(n)))
    return float(np.std(np.abs(errors)) / math.sqrt(n))


def _subgroups(task_type: str, y: np.ndarray, folds: Sequence[tuple[np.ndarray, np.ndarray]], frame: pd.DataFrame,
               columns: Sequence[str], primary_metric: str | None, n_classes: int | None) -> dict[str, Any]:
    metric = subgroup_metric(task_type, primary_metric)
    index = np.concatenate([np.asarray(ix, dtype=int) for ix, _ in folds])
    pred = np.concatenate([np.asarray(p, dtype=float) for _, p in folds])
    y_oof = np.asarray(y)[index]
    out: dict[str, Any] = {
        "metric": metric, "direction": "lower_is_better" if metric in LOWER_IS_BETTER else "higher_is_better",
        "overall": _score(metric, task_type, y_oof, pred, n_classes), "min_group_rows": SUBGROUP_MIN_ROWS,
        "max_levels": SUBGROUP_MAX_LEVELS, "columns": [],
    }
    if out["overall"] is not None:
        out["overall"] = _r(out["overall"])
    for column in sorted({str(c) for c in columns if c in frame.columns}):
        values = frame[column].iloc[index].astype(object).to_numpy()
        present = pd.Series(values).dropna()
        if not 2 <= present.nunique() <= SUBGROUP_MAX_LEVELS:
            continue
        groups: list[dict[str, Any]] = []
        small = few_class = 0
        for level in sorted(present.unique(), key=str):  # group values stay in the worker
            mask = values == level
            if int(mask.sum()) < SUBGROUP_MIN_ROWS:
                small += 1
                continue
            score = _score(metric, task_type, y_oof[mask], pred[mask], n_classes)
            if score is None:
                few_class += 1  # binary: too few rows of one class to rank
                continue
            se = _standard_error(metric, task_type, y_oof[mask], pred[mask], score)
            groups.append({"rows": int(mask.sum()), "score": _r(score), "se": _r(se) if se is not None else None})
        out["columns"].append({"column": column, "levels": int(present.nunique()), "groups": groups,
                               "skipped_small_groups": small, "skipped_class_groups": few_class})
        if len(out["columns"]) >= SUBGROUP_MAX_COLUMNS:
            break
    return out


def _fold_noise(task_type: str, y: np.ndarray, folds: Sequence[tuple[np.ndarray, np.ndarray]]) -> dict[str, Any] | None:
    """Regression: the chance spread of a fold's score. The per-row spread sigma comes from ALL
    out-of-fold rows (pooled, so a heavy-tailed fold does not understate it) -- MAE sd(|e|),
    MSE sd(e^2), RMSE sd(e^2) / (2 rmse), R2 sd(e^2 - (1 - R2)(y - ybar)^2) / var(y) (the delta
    method including each fold's own var(y)) -- and the fold sizes n_k give
    sqrt(mean_k sigma^2 / n_k), so one tiny fold (a dominant entity under group folds) counts."""

    if task_type != "regression":
        return None
    index = np.concatenate([np.asarray(ix, dtype=int) for ix, _ in folds])
    actual = np.asarray(y, dtype=float)[index]
    errors = np.concatenate([np.asarray(p, dtype=float) for _, p in folds]) - actual
    sizes = [len(ix) for ix, _ in folds if len(ix) >= 2]
    if not sizes or len(errors) < 2:
        return None
    squared = errors ** 2
    rmse = math.sqrt(max(float(squared.mean()), 1e-24))
    variance = max(float(np.var(actual)), 1e-24)
    r2 = 1.0 - float(squared.mean()) / variance
    sigma = {"mae": float(np.std(np.abs(errors))), "mse": float(np.std(squared)),
             "rmse": float(np.std(squared)) / (2 * rmse),
             "r2": float(np.std(squared - (1.0 - r2) * (actual - actual.mean()) ** 2)) / variance}
    scale = math.sqrt(float(np.mean([1.0 / n for n in sizes])))
    return {"fold_rows": int(round(float(np.mean(sizes)))), "fold_sizes": [int(n) for n in sizes],
            "sigma": {k: _r(v) for k, v in sigma.items()}, "metrics": {k: _r(v * scale) for k, v in sigma.items()}}


def oof_evidence(task_type: str, y: np.ndarray, folds: Sequence[tuple[np.ndarray, np.ndarray]] | None,
                 frame: pd.DataFrame, categorical_columns: Sequence[str], *, primary_metric: str | None,
                 n_classes: int | None = None, candidate_id: str | None = None) -> dict[str, Any] | None:
    """Calibration and subgroup aggregates of the winner's out-of-fold predictions.

    ``folds`` are (positional validation index into ``frame``/``y``, predictions) per CV
    fold; ``frame`` and ``y`` are the training pool only. ``None`` without predictions."""

    if not folds:
        return None
    index = np.concatenate([np.asarray(ix, dtype=int) for ix, _ in folds])
    pred = np.concatenate([np.asarray(p, dtype=float) for _, p in folds])
    y_oof = np.asarray(y)[index]
    return {
        "version": OOF_EVIDENCE_VERSION, "candidate_id": candidate_id, "rows": int(len(index)),
        "scope": "cv_validation_rows",
        "calibration": _calibration(task_type, y_oof, pred) if task_type in {"binary", "multiclass"} else None,
        "subgroups": _subgroups(task_type, y, folds, frame, categorical_columns, primary_metric, n_classes),
        "fold_noise": _fold_noise(task_type, y, folds),
    }
