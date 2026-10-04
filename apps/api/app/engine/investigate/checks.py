"""The five core trust checks (P4.10-A).

Pure functions over ``RunEvidence`` (training rows and CV only). The final holdout is
never an input: the duplicate check receives the test partition as 64-bit row hashes
of the model columns, never values or labels, and ``run_evidence_from_result`` copies
no ``test_*`` / holdout key of the run result.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping
from typing import Any

import numpy as np
import pandas as pd

from app.engine.evaluation.metrics import LOWER_IS_BETTER
from app.engine.investigate.types import Finding, RunEvidence
from app.engine.modeling.objective import THRESHOLD_METRICS

# --- thresholds ------------------------------------------------------------------------
# Higher-is-better metrics bounded by 1.0 (perfect). Lower-is-better metrics
# (LOWER_IS_BETTER) are errors whose perfect value is 0.
BOUNDED_SCORE_METRICS = frozenset({
    "accuracy", "balanced_accuracy", "roc_auc", "roc_auc_ovr", "pr_auc", "precision", "recall", "f1",
    "macro_f1", "weighted_f1", "macro_precision", "macro_recall", "top_k_precision", "r2",
})
# Overfit gap, bounded scores: train-minus-CV on the metric's own 0..1 scale. A 0.10
# gap (e.g. AUC 0.95 on training rows vs 0.85 in CV) is well beyond fold noise at
# the row counts DCLab trains on. Overfitting never invalidates the honest CV
# estimate, so this check warns and never fails.
OVERFIT_ABSOLUTE_GAP_WARN = 0.10
# Overfit gap, error metrics (unit-free): share of the CV error that disappears on
# the training rows, (cv - train) / cv. 0.35 = training error a third below CV error.
OVERFIT_RELATIVE_GAP_WARN = 0.35
# Too good to be true: CV score of a bounded metric at/above these is near perfect,
# which on tabular business data is usually leakage (warn) or certainly worth
# stopping for (fail) -- unless the dummy baseline is itself that high.
NEAR_PERFECT_WARN = 0.99
NEAR_PERFECT_FAIL = 0.999
# Error metrics: winner CV error as a share of the dummy baseline's CV error. 5%
# corresponds to R^2 ~ 0.9975 for squared error; 1% is essentially exact recall.
ERROR_RATIO_WARN = 0.05
ERROR_RATIO_FAIL = 0.01
# Class imbalance: smallest class's share of its fair share (fraction x class count),
# so binary 10% / 2% minority equals 0.20 / 0.04 and multiclass scales with classes.
IMBALANCE_FAIR_SHARE_WARN = 0.20
IMBALANCE_FAIR_SHARE_FAIL = 0.04
# Duplicates: rows identical on every model column beyond what independent columns
# would produce by chance. 1% of rows is a data-preparation issue; 20% within the
# training rows / 10% of test rows matching training rows distorts the evidence.
DUPLICATE_EXCESS_WARN = 0.01
DUPLICATE_TRAIN_EXCESS_FAIL = 0.20
DUPLICATE_CROSS_EXCESS_FAIL = 0.10
# Binary threshold metrics are compared on the first threshold-free metric present.
THRESHOLD_FREE_METRICS = ("roc_auc", "pr_auc", "log_loss")
LEAKAGE_RISKY = frozenset({"MEDIUM", "HIGH", "CRITICAL"})
COLUMN_DETAIL_LIMIT = 20
CLASS_WEIGHT_KEYS = ("class_weight", "auto_class_weights", "scale_pos_weight")
DUMMY_FAMILIES = frozenset({"majority", "mean", "median"})


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _numbers(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    return {str(k): v for k, raw in value.items() if (v := _float(raw)) is not None}


def _weighted(row: Mapping[str, Any]) -> bool:
    params = row.get("hyperparameters") if isinstance(row.get("hyperparameters"), Mapping) else {}
    return any(params.get(key) not in (None, "", {}) for key in CLASS_WEIGHT_KEYS)


def run_evidence_from_result(result: Mapping[str, Any]) -> RunEvidence:
    """The allowlisted, holdout-free view of an open-ingest run result."""

    metric_plan = result.get("metric_plan") if isinstance(result.get("metric_plan"), Mapping) else {}
    baseline = result.get("baseline_comparison") if isinstance(result.get("baseline_comparison"), Mapping) else {}
    winner = result.get("best_single") if isinstance(result.get("best_single"), Mapping) else {}
    profile = result.get("problem_profile") if isinstance(result.get("problem_profile"), Mapping) else {}
    plan = result.get("model_development_plan") if isinstance(result.get("model_development_plan"), Mapping) else {}
    assessment = plan.get("leakage_assessment") if isinstance(plan.get("leakage_assessment"), Mapping) else {}
    task = result.get("task") if isinstance(result.get("task"), Mapping) else {}
    candidates = [row for row in result.get("candidates") or [] if isinstance(row, Mapping)]
    trained = [row for row in candidates if row.get("status") == "trained"]
    baseline_row = next(
        (row for row in trained if row.get("candidate_id") == baseline.get("baseline_candidate_id")),
        next((row for row in trained if row.get("model_family") in DUMMY_FAMILIES), {}),
    )
    distribution = profile.get("class_distribution")
    return RunEvidence(
        task_type=str(profile.get("task_type") or task.get("task_type") or "") or None,
        primary_metric=str(baseline.get("metric") or metric_plan.get("primary_metric") or "") or None,
        winner_family=winner.get("model_family"),
        winner_features=tuple(str(name) for name in winner.get("features") or []),
        winner_cv=_numbers(winner.get("cv_mean")),
        winner_cv_std=_numbers(winner.get("cv_std")),
        winner_class_weighted=_weighted(winner),
        train_metrics=_numbers(result.get("train_metrics")),
        baseline_family=baseline_row.get("model_family"),
        baseline_cv=_numbers(baseline_row.get("cv_mean")),
        class_weighted_candidates=sum(1 for row in trained if _weighted(row)),
        class_distribution=(
            {str(k): int(v) for k, v in distribution.items()} if isinstance(distribution, Mapping) else None
        ),
        minority_class_fraction=_float(profile.get("minority_class_fraction")),
        imbalance_ratio=_float(profile.get("imbalance_ratio")),
        leakage_risks=tuple(row for row in assessment.get("findings") or [] if isinstance(row, Mapping)),
        leakage_exclusions=tuple(row for row in plan.get("excluded_features") or [] if isinstance(row, Mapping)),
        allowed_features=tuple(str(name) for name in plan.get("allowed_features") or []),
    )


def _finding(check: str, status: str, evidence: dict[str, Any], keys: tuple[str, ...],
             recommendation: str | None = None, severity: str | None = None) -> Finding:
    default = {"pass": "info", "warning": "warning", "fail": "error"}[status]
    return Finding(check=check, status=status, severity=severity or default,  # type: ignore[arg-type]
                   evidence=evidence, recommendation_kind=recommendation if status != "pass" else None,  # type: ignore[arg-type]
                   message_keys=keys)


# --- 1. target leakage (summary of the existing audit; nothing is recomputed) -----------


def check_target_leakage(ev: RunEvidence) -> Finding:
    modeled = set(ev.winner_features or ev.allowed_features)
    excluded = [row for row in ev.leakage_exclusions if str(row.get("risk")) in LEAKAGE_RISKY]
    kept = [row for row in ev.leakage_risks if row.get("column") in modeled and str(row.get("risk")) != "NONE"]
    risky = [row for row in kept if str(row.get("risk")) in LEAKAGE_RISKY]
    flagged = [row for row in kept if row not in risky]

    def detail(row: Mapping[str, Any]) -> dict[str, Any]:
        signals = row.get("evidence") if isinstance(row.get("evidence"), Mapping) else {}
        return {"column": str(row.get("column")), "risk": row.get("risk"), "action": row.get("action"),
                "reasons": [str(r) for r in row.get("reasons") or []][:5],
                "single_feature_score": _float(signals.get("single_feature_score"))}

    evidence = {
        "modeled_feature_count": len(modeled),
        "excluded_count": len(excluded),
        "excluded_columns": [str(row.get("column")) for row in excluded],
        "flagged_count": len(flagged),
        "flagged_columns": [str(row.get("column")) for row in flagged],
        "risky_count": len(risky),
        "risky_columns": [str(row.get("column")) for row in risky],
        "columns": [detail(row) for row in (risky + flagged + excluded)][:COLUMN_DETAIL_LIMIT],
    }
    if risky:
        critical = any(str(row.get("risk")) == "CRITICAL" for row in risky)
        return _finding("target_leakage", "fail", evidence, ("target_leakage.kept_risky",), "review_columns",
                        "critical" if critical else "error")
    keys = tuple(key for key, rows in (("target_leakage.excluded", excluded), ("target_leakage.flagged", flagged))
                 if rows)
    if keys:
        return _finding("target_leakage", "warning", evidence, keys, "review_columns")
    return _finding("target_leakage", "pass", evidence, ("target_leakage.pass",))


# --- 2. train-vs-CV overfit gap ---------------------------------------------------------


def _overfit_metric(ev: RunEvidence) -> str | None:
    """The primary metric, except a binary threshold metric: training rows are scored at
    the locked decision threshold but CV folds at 0.5, so compare a threshold-free one."""

    if ev.task_type == "binary" and ev.primary_metric in THRESHOLD_METRICS:
        return next((name for name in THRESHOLD_FREE_METRICS
                     if name in ev.train_metrics and name in ev.winner_cv), None)
    return ev.primary_metric


def check_overfit_gap(ev: RunEvidence) -> Finding:
    metric = _overfit_metric(ev)
    train = ev.train_metrics.get(metric) if metric else None
    cv = ev.winner_cv.get(metric) if metric else None
    lower = metric in LOWER_IS_BETTER
    evidence: dict[str, Any] = {"metric": metric, "direction": "lower_is_better" if lower else "higher_is_better",
                                "winner_family": ev.winner_family, "train_score": train, "cv_score": cv,
                                "cv_std": ev.winner_cv_std.get(metric) if metric else None}
    if metric != ev.primary_metric:
        evidence["primary_metric"] = ev.primary_metric
    if train is None or cv is None:
        return _finding("overfit_gap", "pass", {**evidence, "skipped_reason": "train_or_cv_metric_missing"},
                        ("overfit_gap.skipped",))
    # Both values are raw fold/train metrics on one scale; positive = training rows look better.
    gap = (cv - train) if lower else (train - cv)
    relative = gap / max(abs(cv), 1e-12)
    evidence.update(absolute_gap=gap, relative_gap=relative)
    if lower or metric not in BOUNDED_SCORE_METRICS:
        evidence["threshold"] = {"relative_gap": OVERFIT_RELATIVE_GAP_WARN}
        over = relative >= OVERFIT_RELATIVE_GAP_WARN
    else:
        evidence["threshold"] = {"absolute_gap": OVERFIT_ABSOLUTE_GAP_WARN}
        over = gap >= OVERFIT_ABSOLUTE_GAP_WARN
    if over:
        return _finding("overfit_gap", "warning", evidence, ("overfit_gap.gap",), "regularize")
    return _finding("overfit_gap", "pass", evidence, ("overfit_gap.pass",))


# --- 3. duplicate rows ------------------------------------------------------------------


def row_hashes(features: pd.DataFrame) -> np.ndarray:
    """64-bit hash per row over the given columns (sorted by name; index ignored)."""

    if features.empty:
        return np.asarray([], dtype=np.uint64)
    ordered = features.reindex(columns=sorted(features.columns, key=str))
    return pd.util.hash_pandas_object(ordered, index=False).to_numpy(dtype=np.uint64)


def _chance_match_probability(features: pd.DataFrame) -> float:
    """P(two distinct training rows agree on every column) if columns were independent:
    product over columns of sum n_v (n_v - 1) / (n (n - 1))."""

    n = len(features)
    if n < 2:
        return 0.0
    probability = 1.0
    for column in features.columns:
        counts = features[column].value_counts(dropna=False).to_numpy(dtype=float)
        probability *= float((counts * (counts - 1)).sum()) / (n * (n - 1))
        if probability == 0.0:
            break
    return probability


def _expected_matches(rows: int, others: int, probability: float) -> float:
    """Expected rows (of ``rows``) matching at least one of ``others`` rows by chance."""

    if rows <= 0 or others <= 0 or probability <= 0.0:
        return 0.0
    if probability >= 1.0:
        return float(rows)
    return float(rows * -math.expm1(others * math.log1p(-probability)))


def _expected_repeats(rows: int, probability: float) -> float:
    """Expected rows matching any EARLIER row by chance: sum_i 1 - (1 - p)^i, i < rows."""

    if rows <= 1 or probability <= 0.0:
        return 0.0
    if probability >= 1.0:
        return float(rows - 1)
    return float(rows + math.expm1(rows * math.log1p(-probability)) / probability)


def check_duplicate_rows(train_features: pd.DataFrame | None,
                         test_row_hashes: Collection[int] | np.ndarray | None) -> Finding:
    """Within-train repeats and test rows identical to a training row, on the model
    columns. The test side is hashes only (``row_hashes`` of the model columns)."""

    if train_features is None or test_row_hashes is None or train_features.shape[1] == 0:
        return _finding("duplicate_rows", "pass", {"skipped_reason": "partition_unavailable"},
                        ("duplicate_rows.skipped",))
    train_hashes = row_hashes(train_features)
    test_hashes = np.asarray(test_row_hashes, dtype=np.uint64)
    n_train, n_test = int(len(train_hashes)), int(len(test_hashes))
    within = int(pd.Series(train_hashes).duplicated().sum())
    groups = int((pd.Series(train_hashes).value_counts() > 1).sum())
    across = int(np.isin(test_hashes, train_hashes).sum())
    chance = _chance_match_probability(train_features)
    expected_within = _expected_repeats(n_train, chance)
    expected_across = _expected_matches(n_test, n_train, chance)
    within_excess = max(0.0, within - expected_within) / max(n_train, 1)
    across_excess = max(0.0, across - expected_across) / max(n_test, 1)
    evidence = {
        "feature_count": int(train_features.shape[1]), "train_rows": n_train, "test_rows": n_test,
        "train_duplicate_rows": within, "train_duplicate_groups": groups,
        "train_duplicate_fraction": within / max(n_train, 1),
        "expected_train_duplicate_rows": round(expected_within, 1), "excess_train_duplicate_fraction": within_excess,
        "train_test_duplicate_rows": across, "train_test_duplicate_fraction": across / max(n_test, 1),
        "expected_train_test_duplicate_rows": round(expected_across, 1),
        "excess_train_test_duplicate_fraction": across_excess,
    }
    keys = tuple(key for key, hit in (("duplicate_rows.within_train", within_excess >= DUPLICATE_EXCESS_WARN),
                                      ("duplicate_rows.across_split", across_excess >= DUPLICATE_EXCESS_WARN))
                 if hit)
    if not keys:
        return _finding("duplicate_rows", "pass", evidence, ("duplicate_rows.pass",))
    fail = within_excess >= DUPLICATE_TRAIN_EXCESS_FAIL or across_excess >= DUPLICATE_CROSS_EXCESS_FAIL
    return _finding("duplicate_rows", "fail" if fail else "warning", evidence, keys, "deduplicate")


# --- 4. class imbalance (training partition profile) -------------------------------------


def check_class_imbalance(ev: RunEvidence) -> Finding:
    if ev.task_type not in {"binary", "multiclass"}:
        return _finding("class_imbalance", "pass", {"task_type": ev.task_type}, ("class_imbalance.not_applicable",))
    counts = dict(ev.class_distribution or {})
    total = sum(counts.values())
    class_count = len(counts) or (2 if ev.task_type == "binary" else 0)
    fraction = ev.minority_class_fraction
    if fraction is None and total:
        fraction = min(counts.values()) / total
    evidence: dict[str, Any] = {
        "class_count": class_count, "minority_class_fraction": fraction,
        "minority_rows": min(counts.values()) if counts else None, "imbalance_ratio": ev.imbalance_ratio,
        "class_weighted_candidates": ev.class_weighted_candidates, "winner_class_weighted": ev.winner_class_weighted,
    }
    if fraction is None or class_count < 2:
        return _finding("class_imbalance", "pass", {**evidence, "skipped_reason": "class_profile_missing"},
                        ("class_imbalance.pass",))
    fair_share = fraction * class_count
    evidence["minority_fair_share_fraction"] = fair_share
    if fair_share >= IMBALANCE_FAIR_SHARE_WARN:
        return _finding("class_imbalance", "pass", evidence, ("class_imbalance.pass",))
    weighted = ev.class_weighted_candidates > 0
    keys = ("class_imbalance.imbalanced", "class_imbalance.weighted" if weighted else "class_imbalance.unweighted")
    status = "fail" if fair_share < IMBALANCE_FAIR_SHARE_FAIL else "warning"
    return _finding("class_imbalance", status, evidence, keys, "collect_more_data" if weighted else "class_weights")


# --- 5. too good to be true ------------------------------------------------------------


def check_implausible_score(ev: RunEvidence) -> Finding:
    metric = ev.primary_metric
    cv = ev.winner_cv.get(metric) if metric else None
    base = ev.baseline_cv.get(metric) if metric else None
    std = ev.winner_cv_std.get(metric) if metric else None
    lower = metric in LOWER_IS_BETTER
    evidence: dict[str, Any] = {"metric": metric, "direction": "lower_is_better" if lower else "higher_is_better",
                                "cv_score": cv, "cv_std": std, "baseline_cv_score": base,
                                "baseline_family": ev.baseline_family}
    if cv is not None and base is not None:
        margin = (base - cv) if lower else (cv - base)
        evidence["margin_over_baseline"] = margin
        evidence["margin_in_cv_std"] = margin / std if std and std > 0 else None
    if cv is None or (lower and not base):
        return _finding("implausible_score", "pass", {**evidence, "skipped_reason": "cv_or_baseline_missing"},
                        ("implausible_score.skipped",))
    if lower:
        ratio = cv / base  # type: ignore[operator]
        evidence.update(error_ratio=ratio, threshold={"error_ratio": ERROR_RATIO_WARN})
        if ratio <= ERROR_RATIO_WARN:
            status = "fail" if ratio <= ERROR_RATIO_FAIL else "warning"
            return _finding("implausible_score", status, evidence, ("implausible_score.error_ratio",),
                            "investigate_leakage")
        return _finding("implausible_score", "pass", evidence, ("implausible_score.pass",))
    if metric not in BOUNDED_SCORE_METRICS:
        return _finding("implausible_score", "pass", {**evidence, "skipped_reason": "unbounded_metric"},
                        ("implausible_score.skipped",))
    evidence["threshold"] = {"near_perfect": NEAR_PERFECT_WARN}
    if cv >= NEAR_PERFECT_WARN:
        if base is not None and base >= NEAR_PERFECT_WARN:
            return _finding("implausible_score", "pass", evidence, ("implausible_score.trivial",))
        status = "fail" if cv >= NEAR_PERFECT_FAIL else "warning"
        return _finding("implausible_score", status, evidence, ("implausible_score.near_perfect",),
                        "investigate_leakage")
    return _finding("implausible_score", "pass", evidence, ("implausible_score.pass",))


def investigate(ev: RunEvidence, *, train_features: pd.DataFrame | None = None,
                test_row_hashes: Collection[int] | np.ndarray | None = None) -> list[Finding]:
    """All five checks, in ``FINDING_CHECKS`` order."""

    return [
        check_target_leakage(ev),
        check_overfit_gap(ev),
        check_duplicate_rows(train_features, test_row_hashes),
        check_class_imbalance(ev),
        check_implausible_score(ev),
    ]
