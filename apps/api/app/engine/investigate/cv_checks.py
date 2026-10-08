"""P5.1-A checks over the run's CV evidence and training rows (group A).

``fold_instability`` reads the winner's per-fold metrics, ``calibration`` and
``subgroup_gap`` the out-of-fold summary the runner stored before the holdout was
touched (``engine.investigate.oof``), ``multicollinearity`` the training partition's
numeric model columns. None reads a final-holdout value; missing evidence is
``not_evaluated``, never a pass.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import chi2, norm

from app.engine.evaluation.metrics import LOWER_IS_BETTER
from app.engine.investigate.checks import (
    BOUNDED_SCORE_METRICS,
    COLUMN_DETAIL_LIMIT,
    SIMPLE_FAMILIES,
    _finding,
    _float,
    _mapping,
    _not_evaluated,
)
from app.engine.investigate.oof import SUBGROUP_MAX_LEVELS, SUBGROUP_MIN_ROWS
from app.engine.investigate.types import Finding, RunEvidence

# Fold instability: fewer than 3 folds give no meaningful spread. Bounded scores: a fold
# standard deviation of 0.05 (AUC, accuracy) means folds routinely differ by ~0.1. Error
# and unbounded metrics: a standard deviation above 15% of the mean (never for a mean of ~0).
# The spread must also exceed 1.5x what folds of this size show by chance alone:
# classification sqrt(p(1 - p) / n) with n = fold rows (accuracy) or minority rows per fold
# (ranking and class-wise scores); regression the folds' own error spread stored with the
# out-of-fold evidence (MAE, MSE, RMSE, R2). Instability never invalidates the CV mean: warn only.
FOLD_MIN_FOLDS = 3
FOLD_STD_WARN = 0.05
FOLD_RELATIVE_STD_WARN = 0.15
# Regression instead uses Cochran's Q over the folds (weights n_k / sigma^2 from the pooled
# per-row spread; alpha 0.01), which handles unequal fold sizes; a regression fold under 30
# rows has a score too skewed to judge: not_evaluated.
FOLD_NOISE_MULTIPLE = 1.5
FOLD_MIN_ABS_MEAN = 1e-9
FOLD_MIN_REGRESSION_ROWS = 30
FOLD_Q_ALPHA = 0.01
# Calibration: needs 100 out-of-fold rows; warns when the expected calibration error is at
# least 0.05 AND at least twice what a perfectly calibrated model shows with these bin
# sizes (small samples inflate ECE). Miscalibration leaves rankings intact: warn only.
CALIBRATION_MIN_ROWS = 100
ECE_WARN = 0.05
ECE_NOISE_MULTIPLE = 2.0
# Subgroup gap: the weakest group trails the overall out-of-fold score by 0.05 (bounded
# scores) or has 25% more error, AND the gap is significant against that group's own
# standard error (Hanley-McNeil for ROC AUC, binomial for accuracy, the error spread for
# MAE / RMSE) after a Bonferroni correction over every group tested (family alpha 0.05,
# one-sided). Advisory: warn only.
SUBGROUP_GAP_WARN = 0.05
SUBGROUP_RELATIVE_GAP_WARN = 0.25
SUBGROUP_ALPHA = 0.05
# Multicollinearity on training rows: |r| >= 0.95 means two columns carry nearly the same
# information; a variance inflation factor >= 10 is the classic rule of thumb. An exact
# linear dependence (smallest correlation eigenvalue < 1e-8: c = a + b, dummies summing to 1)
# is flagged first -- a pseudo-inverse would hide it -- with VIF infinite (capped for display).
# With fewer than 2 rows per column VIFs inflate by chance (a VIF of independent columns is about
# (n - 1) / (n - p)), so VIFs are skipped below that; the exact-dependence (eigenvalue) test stays
# valid while n - 1 > p and is skipped only at or below it. Bounded: 20,000 rows (seeded
# sample) and 200 numeric columns (by name, recorded as ``columns_truncated``). Predictions stay
# valid: warn only.
COLLINEAR_CORR_WARN = 0.95
VIF_WARN = 10.0
VIF_CAP = 1e6
RANK_TOLERANCE = 1e-8
NULL_LOADING = 0.05
MULTICOLLINEARITY_MAX_ROWS = 20_000
MIN_ROWS_PER_COLUMN = 2
MULTICOLLINEARITY_MAX_FEATURES = 200
SAMPLE_SEED = 0


def _r(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


# --- 6. fold instability ----------------------------------------------------------------


def check_fold_instability(ev: RunEvidence) -> Finding:
    metric = ev.primary_metric
    scores = [v for row in ev.winner_fold_metrics if metric and (v := _float(_mapping(row).get(metric))) is not None]
    lower = metric in LOWER_IS_BETTER
    evidence: dict[str, Any] = {"metric": metric, "direction": "lower_is_better" if lower else "higher_is_better",
                                "n_folds": len(scores)}
    if not scores:
        return _not_evaluated("fold_instability", "fold_scores_missing", "fold_instability.not_evaluated", evidence)
    if len(scores) < FOLD_MIN_FOLDS:
        return _not_evaluated("fold_instability", "too_few_folds", "fold_instability.too_few_folds", evidence)
    mean, std = float(np.mean(scores)), float(np.std(scores))
    worst, best = (max(scores), min(scores)) if lower else (min(scores), max(scores))
    relative = std / max(abs(mean), 1e-12)
    bounded = not lower and metric in BOUNDED_SCORE_METRICS
    base = ev.baseline_cv.get(metric) if metric else None
    at_baseline = base is not None and (worst >= base if lower else worst <= base)
    fold_rows, noise = _fold_noise(ev, metric, mean, len(scores))
    if ev.task_type == "regression" and noise is None:  # no stored chance spread: cannot tell noise from instability
        return _not_evaluated("fold_instability", "chance_spread_missing", "fold_instability.chance_spread_missing",
                              evidence)
    sizes = [int(n) for n in _mapping(_mapping(ev.oof).get("fold_noise")).get("fold_sizes") or []]
    if ev.task_type == "regression" and sizes and min(sizes) < FOLD_MIN_REGRESSION_ROWS:
        evidence.update(smallest_fold_rows=min(sizes), min_fold_rows=FOLD_MIN_REGRESSION_ROWS)
        return _not_evaluated("fold_instability", "fold_too_small", "fold_instability.fold_too_small", evidence)
    evidence.update(fold_scores=[_r(v) for v in scores], cv_score=_r(mean), cv_std=_r(std),
                    relative_spread_ratio=_r(relative), fold_range=_r(abs(best - worst)),
                    worst_fold_score=_r(worst), best_fold_score=_r(best), baseline_cv_score=base,
                    worst_fold_at_baseline=at_baseline, fold_rows=fold_rows, fold_noise_std=_r(noise),
                    threshold={"cv_std": FOLD_STD_WARN, "relative_std": FOLD_RELATIVE_STD_WARN,
                               "noise_multiple": FOLD_NOISE_MULTIPLE})
    large = std >= FOLD_STD_WARN if bounded else abs(mean) > FOLD_MIN_ABS_MEAN and relative >= FOLD_RELATIVE_STD_WARN
    sigma = _float(_mapping(_mapping(_mapping(ev.oof).get("fold_noise")).get("sigma")).get(metric or ""))
    if ev.task_type == "regression" and sigma and len(sizes) == len(scores):
        weights = np.asarray(sizes, dtype=float) / sigma ** 2
        values = np.asarray(scores, dtype=float)
        centre = float((weights * values).sum() / weights.sum())
        q_value = float((weights * (values - centre) ** 2).sum())
        q_critical = float(chi2.ppf(1.0 - FOLD_Q_ALPHA, len(scores) - 1))
        evidence.update(cochran_q=_r(q_value), q_critical=_r(q_critical))
        unstable = large and q_value >= q_critical
    else:
        unstable = large and std >= FOLD_NOISE_MULTIPLE * (noise or 0.0)
    keys = (*(("fold_instability.unstable",) if unstable else ()),
            *(("fold_instability.at_baseline",) if at_baseline else ()))
    if keys:
        return _finding("fold_instability", "warning", evidence, keys, "collect_more_data")
    noisy = ("fold_instability.within_noise",) if large else ()
    return _finding("fold_instability", "pass", evidence, ("fold_instability.pass", *noisy))


def _fold_noise(ev: RunEvidence, metric: str | None, mean: float, folds: int) -> tuple[int | None, float | None]:
    """(rows per fold, chance spread of a fold score): regression from the stored fold errors,
    classification from the training class counts."""

    if ev.task_type == "regression":
        stored = _mapping(_mapping(ev.oof).get("fold_noise"))
        spread = _float(_mapping(stored.get("metrics")).get(metric or ""))
        rows = stored.get("fold_rows")
        return (int(rows) if isinstance(rows, int) else None), spread
    if metric not in BOUNDED_SCORE_METRICS:
        return None, None
    counts = [int(v) for v in (ev.class_distribution or {}).values() if int(v) > 0]
    if ev.task_type not in {"binary", "multiclass"} or len(counts) < 2:
        return None, None
    rows = sum(counts) / folds
    n = rows if metric == "accuracy" else min(counts) / folds
    p = min(max(mean, 0.0), 1.0)
    return int(round(rows)), math.sqrt(max(p * (1 - p), 0.01) / max(n, 1.0))


# --- 7. calibration (out-of-fold probabilities) -------------------------------------------


def check_calibration(ev: RunEvidence) -> Finding:
    if ev.task_type not in {"binary", "multiclass"}:
        return _not_evaluated("calibration", "not_applicable", "calibration.not_applicable", {"task_type": ev.task_type})
    summary = _mapping(_mapping(ev.oof).get("calibration"))
    if not summary:
        return _not_evaluated("calibration", "oof_missing", "calibration.not_evaluated")
    rows = int(summary.get("rows") or 0)
    evidence: dict[str, Any] = {
        "kind": summary.get("kind"), "rows": rows, "ece": _float(summary.get("ece")),
        "ece_noise": _float(summary.get("ece_noise")), "brier": _float(summary.get("brier")),
        "base_rate_brier": _float(summary.get("base_rate_brier")),
        "mean_predicted": _float(summary.get("mean_predicted")), "observed_rate": _float(summary.get("observed_rate")),
        "winner_class_weighted": ev.winner_class_weighted, "min_rows": CALIBRATION_MIN_ROWS,
        "bins": [dict(row) for row in summary.get("bins") or [] if isinstance(row, dict)],
    }
    if not summary.get("probabilistic"):
        return _not_evaluated("calibration", "no_probabilities", "calibration.no_probabilities", evidence)
    if rows < CALIBRATION_MIN_ROWS:
        return _not_evaluated("calibration", "too_few_rows", "calibration.too_few_rows", evidence)
    ece, noise = evidence["ece"], evidence["ece_noise"] or 0.0
    if ece is None:
        return _not_evaluated("calibration", "oof_missing", "calibration.not_evaluated", evidence)
    if evidence["brier"] is not None and evidence["base_rate_brier"]:
        evidence["brier_skill_ratio"] = _r(1.0 - evidence["brier"] / evidence["base_rate_brier"])
    evidence["threshold"] = {"ece": ECE_WARN, "noise_multiple": ECE_NOISE_MULTIPLE}
    top = ("calibration.top_label",) if evidence["kind"] == "multiclass_top_label" else ()
    if ece >= ECE_WARN and ece >= ECE_NOISE_MULTIPLE * noise:
        weighted = ("calibration.class_weighted",) if ev.winner_class_weighted else ()
        return _finding("calibration", "warning", evidence, ("calibration.miscalibrated", *top, *weighted), "calibrate")
    noisy = ("calibration.within_noise",) if ece >= ECE_WARN else ()
    return _finding("calibration", "pass", evidence, ("calibration.pass", *noisy, *top))


# --- 8. subgroup performance gap (out-of-fold) ----------------------------------------------


def check_subgroup_gap(ev: RunEvidence) -> Finding:
    summary = _mapping(_mapping(ev.oof).get("subgroups"))
    if not summary:
        return _not_evaluated("subgroup_gap", "oof_missing", "subgroup_gap.not_evaluated")
    metric, overall = summary.get("metric"), _float(summary.get("overall"))
    lower = metric in LOWER_IS_BETTER
    candidates = [row for row in summary.get("columns") or [] if isinstance(row, dict)]
    usable = [[g for g in row.get("groups") or [] if _float(_mapping(g).get("score")) is not None
               and (_float(_mapping(g).get("se")) or 0.0) > 0 and int(g.get("rows") or 0)] for row in candidates]
    columns = [(row, groups) for row, groups in zip(candidates, usable) if len(groups) >= 2]
    # Every group that could not be scored, in every candidate column (zero-error groups included).
    skipped = sum(int(row.get("skipped_small_groups") or 0) + int(row.get("skipped_class_groups") or 0)
                  + len(row.get("groups") or []) - len(groups) for row, groups in zip(candidates, usable))
    not_compared = [str(row.get("column")) for row, groups in zip(candidates, usable) if len(groups) < 2]
    evidence: dict[str, Any] = {
        "metric": metric, "direction": "lower_is_better" if lower else "higher_is_better", "overall_score": overall,
        "columns_checked": len(columns), "min_group_rows": int(summary.get("min_group_rows") or SUBGROUP_MIN_ROWS),
        "max_levels": int(summary.get("max_levels") or SUBGROUP_MAX_LEVELS), "groups_skipped": skipped,
        "columns_not_compared": not_compared[:COLUMN_DETAIL_LIMIT],
    }
    if not columns:
        too_few_class = any(int(row.get("skipped_class_groups") or 0) for row in candidates)
        if too_few_class:  # the columns exist; their groups have too few rows of one class
            return _not_evaluated("subgroup_gap", "too_few_class_rows", "subgroup_gap.too_few_class_rows", evidence)
        return _not_evaluated("subgroup_gap", "no_low_cardinality_column", "subgroup_gap.no_columns", evidence)
    tested = sum(len(groups) for _row, groups in columns)
    z_critical = float(norm.ppf(1.0 - SUBGROUP_ALPHA / max(tested, 1)))  # Bonferroni, one-sided
    details: list[dict[str, Any]] = []
    for row, groups in columns:
        # The reference is the row-weighted mean of the column's group scores: a pooled ROC AUC
        # is not an average of within-group AUCs (it also ranks across groups).
        rows = np.asarray([int(g["rows"]) for g in groups], dtype=float)
        weights = rows / rows.sum()
        scores = np.asarray([float(g["score"]) for g in groups])
        errors = np.asarray([float(g["se"]) for g in groups])
        reference = float((weights * scores).sum())
        index = int(np.argmax(scores) if lower else np.argmin(scores))
        score, size, w = float(scores[index]), int(rows[index]), float(weights[index])
        # gap = reference - worst = sum over the others of w_j (s_j - s_worst): its variance.
        se = math.sqrt((1 - w) ** 2 * errors[index] ** 2
                       + sum(weights[j] ** 2 * errors[j] ** 2 for j in range(len(groups)) if j != index))
        gap = (score - reference) if lower else (reference - score)
        relative = gap / max(abs(reference), 1e-12)
        big = relative >= SUBGROUP_RELATIVE_GAP_WARN if lower or metric not in BOUNDED_SCORE_METRICS else (
            gap >= SUBGROUP_GAP_WARN)
        details.append({"column": str(row.get("column")), "group_count": len(groups), "worst_group_rows": size,
                        "worst_group_score": _r(score), "reference_score": _r(reference), "gap_se": _r(se),
                        "absolute_gap": _r(gap), "relative_gap": _r(relative), "z": _r(gap / se) if se else None,
                        "skipped_groups": int(row.get("skipped_small_groups") or 0)
                        + int(row.get("skipped_class_groups") or 0),
                        "flagged": bool(big and se > 0 and gap / se >= z_critical)})
    details.sort(key=lambda d: (not d["flagged"], -(d["relative_gap"] if lower else d["absolute_gap"]), d["column"]))
    top = details[0]
    evidence.update(flagged_columns=[d["column"] for d in details if d["flagged"]], worst_column=top["column"],
                    group_count=top["group_count"], worst_group_rows=top["worst_group_rows"],
                    worst_group_score=top["worst_group_score"], reference_score=top["reference_score"],
                    absolute_gap=top["absolute_gap"], relative_gap=top["relative_gap"], groups_tested=tested,
                    z_critical=_r(z_critical), columns=details[:COLUMN_DETAIL_LIMIT],
                    threshold={"absolute_gap": SUBGROUP_GAP_WARN, "relative_gap": SUBGROUP_RELATIVE_GAP_WARN,
                               "family_alpha": SUBGROUP_ALPHA})
    if evidence["flagged_columns"]:
        key = "subgroup_gap.error_gap" if lower else "subgroup_gap.gap"
        return _finding("subgroup_gap", "warning", evidence, (key,), "review_subgroups")
    return _finding("subgroup_gap", "pass", evidence, ("subgroup_gap.pass",))


# --- 9. multicollinearity (training partition) ---------------------------------------------


def check_multicollinearity(train: pd.DataFrame | None, winner_family: str | None = None) -> Finding:
    if train is None:
        return _not_evaluated("multicollinearity", "partition_unavailable", "multicollinearity.not_evaluated")
    candidates = [c for c in sorted(train.columns, key=str)
                  if pd.api.types.is_numeric_dtype(train[c]) and not pd.api.types.is_bool_dtype(train[c])
                  and train[c].nunique(dropna=True) > 1]
    numeric = candidates[:MULTICOLLINEARITY_MAX_FEATURES]
    evidence: dict[str, Any] = {"numeric_feature_count": len(candidates), "columns_checked": len(numeric),
                                "columns_truncated": len(candidates) > len(numeric)}
    if len(numeric) < 2:
        return _not_evaluated("multicollinearity", "too_few_numeric_columns", "multicollinearity.not_applicable",
                              evidence)
    frame = train[numeric]
    if len(frame) > MULTICOLLINEARITY_MAX_ROWS:
        frame = frame.sample(n=MULTICOLLINEARITY_MAX_ROWS, random_state=SAMPLE_SEED)
    # A diagnostic on training rows only: median-fill so every row counts (nothing is fitted).
    values = frame.astype(float).fillna(frame.astype(float).median()).to_numpy()
    usable = values.std(axis=0) > 0
    names = [name for name, keep in zip(numeric, usable) if keep]
    if len(names) < 2:
        return _not_evaluated("multicollinearity", "too_few_numeric_columns", "multicollinearity.not_applicable",
                              evidence)
    corr = np.corrcoef(values[:, usable], rowvar=False)
    eigenvalues, eigenvectors = np.linalg.eigh(corr)
    # Below 2 rows per column VIFs inflate by chance (pairs only for them); with n - 1 <= p the
    # matrix is singular by construction, so the exact-dependence test needs n - 1 > p.
    wide = len(frame) < MIN_ROWS_PER_COLUMN * len(names)
    rank_testable = len(frame) - 1 > len(names)
    null = eigenvectors[:, eigenvalues < RANK_TOLERANCE] if rank_testable else eigenvectors[:, :0]
    dependent = sorted(str(names[i]) for i in range(len(names)) if null.size and np.abs(null[i]).max() > NULL_LOADING)
    vif = np.ones(len(names)) if wide else np.clip(np.diag(np.linalg.pinv(corr)), 1.0, VIF_CAP)
    for i, name in enumerate(names):
        if str(name) in dependent:
            vif[i] = VIF_CAP  # infinite: the pseudo-inverse would report ~1
    upper = np.triu_indices(len(names), k=1)
    pairs = sorted(((abs(float(corr[i, j])), names[i], names[j]) for i, j in zip(*upper)
                    if abs(float(corr[i, j])) >= COLLINEAR_CORR_WARN), key=lambda p: (-p[0], str(p[1]), str(p[2])))
    high = [str(names[i]) for i in np.argsort(-vif, kind="stable") if vif[i] >= VIF_WARN]
    worst = int(np.argmax(vif))
    drop = list(dict.fromkeys(str(b) for _, _a, b in pairs)) or dependent[-1:] or high[:1]
    evidence.update(rows_used=int(len(frame)), pairs_only=wide, rank_tested=rank_testable,
                    rank_deficient=bool(dependent),
                    dependent_count=len(dependent), dependent_columns=dependent[:COLUMN_DETAIL_LIMIT],
                    min_eigenvalue=_r(float(eigenvalues[0])) if rank_testable else None,
                    max_vif_capped=bool(vif.max() >= VIF_CAP),
                    correlated_pair_count=len(pairs),
                    correlated_pairs=[{"columns": [str(a), str(b)], "correlation": _r(r)}
                                      for r, a, b in pairs[:COLUMN_DETAIL_LIMIT]],
                    max_abs_correlation=_r(float(np.max(np.abs(corr[upper])))) if len(upper[0]) else None,
                    max_vif=None if wide else _r(float(vif[worst])), max_vif_column=None if wide else str(names[worst]),
                    high_vif_count=len(high),
                    high_vif_columns=high[:COLUMN_DETAIL_LIMIT], drop_candidates=drop[:COLUMN_DETAIL_LIMIT],
                    winner_family=winner_family,
                    threshold={"abs_correlation": COLLINEAR_CORR_WARN, "vif": VIF_WARN})
    truncated = (*(("multicollinearity.truncated",) if evidence["columns_truncated"] else ()),
                 *(("multicollinearity.pairs_only",) if wide else ()))
    if pairs or high or dependent:
        model = "multicollinearity.linear_model" if winner_family in SIMPLE_FAMILIES else "multicollinearity.other_model"
        exact = ("multicollinearity.exact",) if dependent else ()
        found = ("multicollinearity.collinear",) if pairs or len(high) > len(dependent) else ()
        return _finding("multicollinearity", "warning", evidence, (*exact, *found, model, *truncated),
                        "drop_correlated")
    return _finding("multicollinearity", "pass", evidence, ("multicollinearity.pass", *truncated))
