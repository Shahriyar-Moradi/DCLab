"""Which input columns drive the locked winner (P4.11-A): permutation importance on
cross-validation validation folds only.

For each outer fold, a fresh copy of the winning configuration (its full sklearn
Pipeline with the fold's own tuned settings, preprocessing inside) is fit on that
fold's training rows; then each raw input column of that fold's validation rows is
shuffled (not removed) and the drop in the run's selection metric is recorded.
Correlated columns share credit: shuffling one leaves the other to carry the signal.
The caller passes the training pool only, so final-holdout rows are never an
argument and can never be scored here. Never fails the run: any error or an exhausted
time budget returns ``status="skipped"`` with a reason.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

logger = logging.getLogger(__name__)

IMPORTANCE_METHOD = "permutation_validation_folds"
DEFAULT_REPEATS = 3
DEFAULT_MAX_ROWS_PER_FOLD = 2000
MAX_SECONDS = 90.0  # importance's own cap, inside the run's time budget
MAX_FEATURES_STORED = 100
ALPHA = 0.05  # family-wise, across the columns evaluated


@dataclass(frozen=True)
class ImportanceFold:
    """Positions into the training pool; ``params`` are the fold's tuned settings (or None)."""

    fold_number: int
    train_index: np.ndarray
    validation_index: np.ndarray
    params: Mapping[str, Any] | None = None


def _skipped(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "skipped", "method": IMPORTANCE_METHOD, "reason": reason, "features": [], **extra}


def _sample(val_idx: np.ndarray, y: np.ndarray, limit: int, seed: int, stratify: bool) -> np.ndarray:
    """At most ``limit`` validation positions, seeded; class proportions kept for classification."""

    if len(val_idx) <= limit:
        return val_idx
    rng = np.random.default_rng(seed)
    if not stratify:
        return np.sort(rng.choice(val_idx, size=limit, replace=False))
    labels = y[val_idx]
    picked: list[np.ndarray] = []
    classes, counts = np.unique(labels, return_counts=True)
    exact = counts * limit / len(val_idx)
    quotas = np.floor(exact).astype(int)
    for index in np.argsort(-(exact - quotas), kind="stable")[: limit - int(quotas.sum())]:
        quotas[index] += 1  # largest remainder: exactly ``limit`` rows
    for index in np.flatnonzero(quotas == 0):  # keep every class present
        largest = int(np.argmax(quotas))
        quotas[largest] -= 1
        quotas[index] = 1
    for cls, quota in zip(classes, quotas):
        members = val_idx[labels == cls]
        picked.append(rng.choice(members, size=min(int(quota), len(members)), replace=False))
    return np.sort(np.concatenate(picked))


def validation_fold_importance(
    make_pipeline: Callable[[Mapping[str, Any] | None], Any],
    X_pool: pd.DataFrame,
    y_pool: np.ndarray,
    folds: Sequence[ImportanceFold],
    scorer: Callable[[Any, pd.DataFrame, np.ndarray], float],
    *,
    scoring: str,
    seed: int,
    stratify: bool = False,
    n_repeats: int = DEFAULT_REPEATS,
    max_rows_per_fold: int = DEFAULT_MAX_ROWS_PER_FOLD,
    deadline: float | None = None,
    on_fold: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Score drop per column (larger = more important), mean over folds x repeats.

    ``scorer(estimator, X, y)`` must be oriented so larger is better. A column counts as
    a clear driver only when its mean drop is positive and a one-sided Student-t test on its
    per-fold means (repeats within one fold are not independent) passes at alpha 0.05,
    Bonferroni-corrected across all columns evaluated.
    """

    started = time.perf_counter()
    cap = time.time() + MAX_SECONDS
    deadline = cap if deadline is None else min(deadline, cap)
    meta = {"scoring": scoring, "n_repeats": int(n_repeats), "folds": len(folds)}
    if not folds or X_pool.shape[1] == 0:
        return {**_skipped("no validation folds or no input columns"), **meta}
    columns = [str(column) for column in X_pool.columns]
    fold_means: list[np.ndarray] = []  # per fold: (n_features,)
    rows_used: list[int] = []
    for fold in folds:
        if time.time() > deadline:  # before this fold's refit
            return {**_skipped("time budget reached before feature importance finished"), **meta}
        try:
            train_idx = np.asarray(fold.train_index, dtype=int)
            val_idx = _sample(np.asarray(fold.validation_index, dtype=int), y_pool, max_rows_per_fold,
                              seed + int(fold.fold_number), stratify)
            pipeline = make_pipeline(dict(fold.params) if fold.params else None)
            pipeline.fit(X_pool.iloc[train_idx], y_pool[train_idx])
            X_val, y_val = X_pool.iloc[val_idx], y_pool[val_idx]
            base = float(scorer(pipeline, X_val, y_val))
            rng = np.random.default_rng(seed + 7919 * int(fold.fold_number))
            drops = np.zeros(len(columns), dtype=float)
            for position, column in enumerate(X_pool.columns):
                if time.time() > deadline:
                    return {**_skipped("time budget reached before feature importance finished"), **meta}
                shuffled = X_val.copy()
                values = shuffled[column].to_numpy()
                total = 0.0
                for _ in range(int(n_repeats)):
                    shuffled[column] = values[rng.permutation(len(values))]
                    total += base - float(scorer(pipeline, shuffled, y_val))
                drops[position] = total / int(n_repeats)
        except Exception as exc:  # noqa: BLE001 - importance is advisory; never fail the run
            logger.warning("feature importance skipped (fold %s): %s", fold.fold_number, type(exc).__name__)
            return {**_skipped(f"feature importance could not be computed ({type(exc).__name__})"), **meta}
        fold_means.append(drops)
        rows_used.append(int(len(val_idx)))
        if on_fold is not None:
            on_fold({"fold_number": int(fold.fold_number), "validation_row_count": int(len(val_idx)),
                     "feature_count": len(columns)})
    per_fold = np.vstack(fold_means)  # (folds, n_features)
    means = per_fold.mean(axis=0)
    k, m = per_fold.shape[0], len(columns)
    se = per_fold.std(axis=0, ddof=1) / np.sqrt(k) if k > 1 else np.full(m, np.inf)
    # One-sided Student-t on the per-fold means, Bonferroni-corrected across the m columns.
    critical = float(student_t.ppf(1 - ALPHA / m, k - 1)) if k > 1 else None

    def clear(i: int) -> bool:
        if critical is None or not means[i] > 0:
            return False
        return bool(se[i] == 0 or means[i] / se[i] > critical)

    order = sorted(range(m), key=lambda i: (-float(means[i]), columns[i]))
    features = [
        {
            "column": columns[i],
            "importance_mean": float(means[i]),
            "importance_std": float(per_fold[:, i].std(ddof=1)) if k > 1 else None,
            "importance_se": float(se[i]) if np.isfinite(se[i]) else None,
            "distinguishable": clear(i),
        }
        for i in order[:MAX_FEATURES_STORED]
    ]
    significance = {"test": "one_sided_t_bonferroni", "alpha": ALPHA, "columns_tested": m,
                    "folds_scored": k, "critical_value": critical}
    return {
        "status": "computed",
        "method": IMPORTANCE_METHOD,
        **meta,
        "row_source": "cv_validation_folds",
        "validation_rows_per_fold": rows_used,
        "feature_count": len(columns),
        "significance": significance,
        "clear_drivers": [columns[i] for i in order if clear(i)],
        "features": features,
        "duration_ms": max(0.001, (time.perf_counter() - started) * 1000.0),
    }
