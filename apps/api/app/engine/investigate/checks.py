"""The five core trust checks (P4.10-A).

Pure functions over ``RunEvidence`` (training rows and CV only). The final holdout is
never an input: the duplicate check receives the test partition as 64-bit row hashes
of the model columns, never values or labels, and ``run_evidence_from_result`` copies
no ``test_*`` / holdout key of the run result. A check whose evidence is missing, or
that raises, reports ``not_evaluated`` with a reason -- never ``pass``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Collection, Mapping
from typing import Any

import numpy as np
import pandas as pd

from app.engine.evaluation.metrics import LOWER_IS_BETTER
from app.engine.investigate.types import Finding, RunEvidence
from app.engine.modeling.objective import THRESHOLD_METRICS

logger = logging.getLogger(__name__)

# --- thresholds ------------------------------------------------------------------------
# Higher-is-better metrics bounded by 1.0 (perfect). Lower-is-better metrics
# (LOWER_IS_BETTER) are errors whose perfect value is 0.
BOUNDED_SCORE_METRICS = frozenset({
    "accuracy", "balanced_accuracy", "roc_auc", "roc_auc_ovr", "pr_auc", "precision", "recall", "f1",
    "macro_f1", "weighted_f1", "macro_precision", "macro_recall", "top_k_precision", "r2",
})
# Overfit gap, bounded scores: train-minus-CV on the metric's own 0..1 scale (AUC 0.95
# on training rows vs 0.85 in CV = 0.10). Error metrics use the unit-free share of the
# CV error that disappears on the training rows, (cv - train) / cv. Overfitting never
# invalidates the honest CV estimate, so this check warns and never fails.
OVERFIT_ABSOLUTE_GAP_WARN = 0.10
OVERFIT_RELATIVE_GAP_WARN = 0.35
# ... and the gap must exceed 3 fold standard deviations of the winner's CV score:
# smaller gaps are within what fold-to-fold variation (and the full-partition refit's
# larger training set) produces.
OVERFIT_NOISE_STD = 3.0
# A large gap matters when the extra flexibility buys nothing: the winner's CV margin
# over the best linear/logistic candidate is within one fold standard deviation.
SIMPLE_MARGIN_NOISE_STD = 1.0
SIMPLE_FAMILIES = frozenset({"logistic_regression", "linear_regression", "ridge", "lasso", "elasticnet"})
# Fully grown forests fit their own training rows (near) perfectly by construction.
NEAR_PERFECT_FIT_FAMILIES = frozenset({
    "random_forest", "extra_trees", "random_forest_regressor", "extra_trees_regressor",
})
# Binary threshold metrics are compared on the first threshold-free metric present
# (training rows are scored at the locked decision threshold, CV folds at 0.5).
THRESHOLD_FREE_METRICS = ("roc_auc", "pr_auc", "log_loss")
# Too good to be true: CV score of a bounded metric at/above these is near perfect,
# which on tabular business data usually means leakage (warn) and is certainly worth
# stopping for when exact (fail) ...
NEAR_PERFECT_WARN = 0.99
NEAR_PERFECT_FAIL = 0.999
# ... unless the dummy baseline explains it: the model still leaves at least a quarter
# of the baseline's residual error, (1 - cv) / (1 - baseline) >= 0.25.
TRIVIAL_RESIDUAL_RATIO = 0.25
# Error metrics: winner CV error as a share of the dummy baseline's CV error. 5%
# corresponds to R^2 ~ 0.9975 for squared error; 1% is essentially exact.
ERROR_RATIO_WARN = 0.05
ERROR_RATIO_FAIL = 0.01
# Class imbalance: smallest class's share of its fair share (fraction x class count),
# so a binary 10% minority equals 0.20 and multiclass scales with the class count. It
# fails only when the minority is too rare to measure: under 10 rows per CV fold.
IMBALANCE_FAIR_SHARE_WARN = 0.20
MIN_MINORITY_ROWS_PER_FOLD = 10
DEFAULT_FOLDS = 5
# Duplicates: rows identical on every model column beyond what independent columns
# would produce by chance. 1% of rows warns; 20% within the training rows / 10% of
# test rows matching training rows fails -- but only when a duplicate cannot be a
# natural repeat: some model column is near-unique (>= 50% distinct, e.g. a
# continuous measurement) or the columns allow >= 100x more combinations than rows.
# Otherwise (correlated low-cardinality columns) the independence estimate is too
# weak to fail on and the warning says so.
DUPLICATE_EXCESS_WARN = 0.01
DUPLICATE_TRAIN_EXCESS_FAIL = 0.20
DUPLICATE_CROSS_EXCESS_FAIL = 0.10
NEAR_UNIQUE_RATIO = 0.5
DUPLICATE_CAPACITY_FACTOR = 100.0
LEAKAGE_RISKY = frozenset({"MEDIUM", "HIGH", "CRITICAL"})
COLUMN_DETAIL_LIMIT = 20
CLASS_WEIGHT_KEYS = ("class_weight", "auto_class_weights", "scale_pos_weight")
DUMMY_FAMILIES = frozenset({"majority", "mean", "median"})
CHECK_ORDER = ("target_leakage", "overfit_gap", "duplicate_rows", "class_imbalance", "implausible_score")


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _numbers(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    return {str(k): v for k, raw in value.items() if (v := _float(raw)) is not None}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _weighted(row: Mapping[str, Any]) -> bool:
    params = _mapping(row.get("hyperparameters"))
    return any(params.get(key) not in (None, "", {}) for key in CLASS_WEIGHT_KEYS)


def run_evidence_from_result(result: Mapping[str, Any]) -> RunEvidence:
    """The allowlisted, holdout-free view of an open-ingest run result."""

    metric_plan, baseline = _mapping(result.get("metric_plan")), _mapping(result.get("baseline_comparison"))
    winner, profile = _mapping(result.get("best_single")), _mapping(result.get("problem_profile"))
    plan, task = _mapping(result.get("model_development_plan")), _mapping(result.get("task"))
    assessment = plan.get("leakage_assessment")
    trained = [row for row in result.get("candidates") or [] if isinstance(row, Mapping) and row.get("status") == "trained"]
    baseline_row = next(
        (row for row in trained if row.get("candidate_id") == baseline.get("baseline_candidate_id")),
        next((row for row in trained if row.get("model_family") in DUMMY_FAMILIES), {}),
    )
    simple = [row for row in trained if row.get("model_family") in SIMPLE_FAMILIES and _float(row.get("score")) is not None]
    simple_row = max(simple, key=lambda row: float(row["score"]), default={})  # score: oriented CV mean
    distribution = profile.get("class_distribution")
    folds = winner.get("n_folds") or winner.get("actual_folds")
    return RunEvidence(
        task_type=str(profile.get("task_type") or task.get("task_type") or "") or None,
        primary_metric=str(baseline.get("metric") or metric_plan.get("primary_metric") or "") or None,
        winner_family=winner.get("model_family"),
        winner_features=tuple(str(name) for name in winner.get("features") or []),
        winner_cv=_numbers(winner.get("cv_mean")),
        winner_cv_std=_numbers(winner.get("cv_std")),
        winner_class_weighted=_weighted(winner),
        final_fit_retuned=bool(winner.get("tuning_trials_used")),
        n_folds=int(folds) if isinstance(folds, int) and not isinstance(folds, bool) else None,
        train_metrics=_numbers(result.get("train_metrics")),
        baseline_family=baseline_row.get("model_family"),
        baseline_cv=_numbers(baseline_row.get("cv_mean")),
        simple_family=simple_row.get("model_family"),
        simple_cv=_numbers(simple_row.get("cv_mean")),
        class_weighted_candidates=sum(1 for row in trained if _weighted(row)),
        class_distribution=(
            {str(k): int(v) for k, v in distribution.items()} if isinstance(distribution, Mapping) else None
        ),
        minority_class_fraction=_float(profile.get("minority_class_fraction")),
        imbalance_ratio=_float(profile.get("imbalance_ratio")),
        leakage_audited=isinstance(assessment, Mapping),
        leakage_risks=tuple(row for row in _mapping(assessment).get("findings") or [] if isinstance(row, Mapping)),
        leakage_exclusions=tuple(row for row in plan.get("excluded_features") or [] if isinstance(row, Mapping)),
        allowed_features=tuple(str(name) for name in plan.get("allowed_features") or []),
    )


def _finding(check: str, status: str, evidence: dict[str, Any], keys: tuple[str, ...],
             recommendation: str | None = None, severity: str | None = None) -> Finding:
    default = {"pass": "info", "not_evaluated": "info", "warning": "warning", "fail": "error"}[status]
    return Finding(check=check, status=status, severity=severity or default,  # type: ignore[arg-type]
                   evidence=evidence,
                   recommendation_kind=recommendation if status in {"warning", "fail"} else None,  # type: ignore[arg-type]
                   message_keys=keys)


def _not_evaluated(check: str, reason: str, key: str, evidence: dict[str, Any] | None = None) -> Finding:
    return _finding(check, "not_evaluated", {**(evidence or {}), "not_evaluated_reason": reason}, (key,))


# --- 1. target leakage (summary of the existing audit; nothing is recomputed) -----------


def check_target_leakage(ev: RunEvidence) -> Finding:
    if not ev.leakage_audited:
        return _not_evaluated("target_leakage", "audit_missing", "target_leakage.not_evaluated")
    modeled = set(ev.winner_features or ev.allowed_features)
    excluded = [row for row in ev.leakage_exclusions if str(row.get("risk")) in LEAKAGE_RISKY]
    kept = [row for row in ev.leakage_risks if row.get("column") in modeled and str(row.get("risk")) != "NONE"]
    risky = [row for row in kept if str(row.get("risk")) in LEAKAGE_RISKY]
    flagged = [row for row in kept if row not in risky]

    def detail(row: Mapping[str, Any]) -> dict[str, Any]:
        signals = _mapping(row.get("evidence"))
        return {"column": str(row.get("column")), "risk": row.get("risk"), "action": row.get("action"),
                "reasons": [str(r) for r in row.get("reasons") or []][:5],
                "single_feature_score": _float(signals.get("single_feature_score"))}

    evidence = {
        "modeled_feature_count": len(modeled),
        "excluded_count": len(excluded), "excluded_columns": [str(row.get("column")) for row in excluded],
        "flagged_count": len(flagged), "flagged_columns": [str(row.get("column")) for row in flagged],
        "risky_count": len(risky), "risky_columns": [str(row.get("column")) for row in risky],
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
    if ev.task_type == "binary" and ev.primary_metric in THRESHOLD_METRICS:
        return next((name for name in THRESHOLD_FREE_METRICS
                     if name in ev.train_metrics and name in ev.winner_cv), None)
    return ev.primary_metric


def check_overfit_gap(ev: RunEvidence) -> Finding:
    metric = _overfit_metric(ev)
    lower = metric in LOWER_IS_BETTER
    std = ev.winner_cv_std.get(metric) if metric else None
    evidence: dict[str, Any] = {
        "metric": metric, "direction": "lower_is_better" if lower else "higher_is_better",
        "winner_family": ev.winner_family, "train_score": ev.train_metrics.get(metric) if metric else None,
        "cv_score": ev.winner_cv.get(metric) if metric else None, "cv_std": std,
        "final_fit_retuned": ev.final_fit_retuned,
    }
    swapped = (("overfit_gap.swapped_metric",) if metric != ev.primary_metric else ())
    if swapped:
        evidence["primary_metric"] = ev.primary_metric
    if metric is None:
        return _not_evaluated("overfit_gap", "no_comparable_metric", "overfit_gap.no_comparable_metric",
                              {**evidence, "primary_metric": ev.primary_metric})
    train, cv = evidence["train_score"], evidence["cv_score"]
    if train is None:
        return _not_evaluated("overfit_gap", "train_score_missing", "overfit_gap.missing_train_score", evidence)
    if cv is None:
        return _not_evaluated("overfit_gap", "cv_score_missing", "overfit_gap.missing_cv_score", evidence)
    # Raw fold mean vs raw training-row score on one scale; positive = training rows look better.
    gap = (cv - train) if lower else (train - cv)
    relative = gap / max(abs(cv), 1e-12)
    by_size = relative >= OVERFIT_RELATIVE_GAP_WARN if (lower or metric not in BOUNDED_SCORE_METRICS) else (
        gap >= OVERFIT_ABSOLUTE_GAP_WARN)
    beyond_noise = gap > OVERFIT_NOISE_STD * (std or 0.0)
    evidence.update(absolute_gap=gap, relative_gap=relative, gap_in_cv_std=gap / std if std else None,
                    threshold={"absolute_gap": OVERFIT_ABSOLUTE_GAP_WARN, "relative_gap": OVERFIT_RELATIVE_GAP_WARN,
                               "cv_std_multiple": OVERFIT_NOISE_STD})
    if not (by_size and beyond_noise):
        key = "overfit_gap.within_noise" if by_size else "overfit_gap.pass"
        return _finding("overfit_gap", "pass", evidence, (key, *swapped))
    retuned = ("overfit_gap.retuned",) if ev.final_fit_retuned else ()
    simple_cv = ev.simple_cv.get(metric) if ev.winner_family not in SIMPLE_FAMILIES else None
    if simple_cv is not None:
        margin = (simple_cv - cv) if lower else (cv - simple_cv)
        evidence.update(simple_family=ev.simple_family, simple_cv_score=simple_cv, margin_over_simple=margin)
        if margin > SIMPLE_MARGIN_NOISE_STD * (std or 0.0) and margin > 0:
            return _finding("overfit_gap", "pass", evidence, ("overfit_gap.flexible_by_design", *swapped))
        return _finding("overfit_gap", "warning", evidence,
                        ("overfit_gap.gap", "overfit_gap.simpler_matches", *retuned, *swapped), "simpler_model")
    if ev.winner_family in NEAR_PERFECT_FIT_FAMILIES:
        return _finding("overfit_gap", "pass", evidence, ("overfit_gap.fit_by_design", *swapped))
    return _finding("overfit_gap", "warning", evidence, ("overfit_gap.gap", "overfit_gap.memorizing", *retuned,
                                                         *swapped), "regularize")


# --- 3. duplicate rows ------------------------------------------------------------------


def row_hashes(features: pd.DataFrame) -> np.ndarray:
    """64-bit hash per row over the given columns (sorted by name; index ignored)."""

    if features.empty:
        return np.asarray([], dtype=np.uint64)
    ordered = features.reindex(columns=sorted(features.columns, key=str))
    return pd.util.hash_pandas_object(ordered, index=False).to_numpy(dtype=np.uint64)


def _chance_match_probability(features: pd.DataFrame) -> float:
    """P(two distinct training rows agree on every column) IF columns were independent:
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
        return _not_evaluated("duplicate_rows", "partition_unavailable", "duplicate_rows.not_evaluated")
    train_hashes = row_hashes(train_features)
    test_hashes = np.asarray(test_row_hashes, dtype=np.uint64)
    n_train, n_test = int(len(train_hashes)), int(len(test_hashes))
    within = int(pd.Series(train_hashes).duplicated().sum())
    groups = int((pd.Series(train_hashes).value_counts() > 1).sum())
    across = int(np.isin(test_hashes, train_hashes).sum())
    chance = _chance_match_probability(train_features)
    expected_within, expected_across = _expected_repeats(n_train, chance), _expected_matches(n_test, n_train, chance)
    within_excess = max(0.0, within - expected_within) / max(n_train, 1)
    across_excess = max(0.0, across - expected_across) / max(n_test, 1)
    distinct = {str(c): int(train_features[c].nunique(dropna=False)) for c in train_features.columns}
    identifying = sorted(c for c, k in distinct.items() if k >= NEAR_UNIQUE_RATIO * max(n_train, 1))
    log_capacity = sum(math.log(max(k, 1)) for k in distinct.values())
    fail_eligible = bool(identifying) or log_capacity >= math.log(DUPLICATE_CAPACITY_FACTOR * max(n_train + n_test, 1))
    evidence = {
        "feature_count": int(train_features.shape[1]), "train_rows": n_train, "test_rows": n_test,
        "train_duplicate_rows": within, "train_duplicate_groups": groups,
        "train_duplicate_fraction": within / max(n_train, 1),
        "expected_train_duplicate_rows": round(expected_within, 1), "excess_train_duplicate_fraction": within_excess,
        "train_test_duplicate_rows": across, "train_test_duplicate_fraction": across / max(n_test, 1),
        "expected_train_test_duplicate_rows": round(expected_across, 1),
        "excess_train_test_duplicate_fraction": across_excess,
        "chance_model": "independent_columns", "near_unique_columns": identifying,
        "log10_distinct_combination_capacity": round(log_capacity / math.log(10), 2), "fail_eligible": fail_eligible,
    }
    keys = tuple(key for key, hit in (("duplicate_rows.within_train", within_excess >= DUPLICATE_EXCESS_WARN),
                                      ("duplicate_rows.across_split", across_excess >= DUPLICATE_EXCESS_WARN))
                 if hit)
    if not keys:
        minor = within > expected_within + 0.5 or across > expected_across + 0.5
        return _finding("duplicate_rows", "pass", evidence, ("duplicate_rows.pass_minor" if minor else "duplicate_rows.pass",))
    if not fail_eligible:
        return _finding("duplicate_rows", "warning", evidence, (*keys, "duplicate_rows.independence_caveat"),
                        "deduplicate")
    fail = within_excess >= DUPLICATE_TRAIN_EXCESS_FAIL or across_excess >= DUPLICATE_CROSS_EXCESS_FAIL
    return _finding("duplicate_rows", "fail" if fail else "warning", evidence, keys, "deduplicate")


# --- 4. class imbalance (training partition profile) -------------------------------------


def check_class_imbalance(ev: RunEvidence) -> Finding:
    if ev.task_type not in {"binary", "multiclass"}:
        return _not_evaluated("class_imbalance", "not_applicable", "class_imbalance.not_applicable",
                              {"task_type": ev.task_type})
    counts = dict(ev.class_distribution or {})
    if len(counts) < 2 or not sum(counts.values()):
        return _not_evaluated("class_imbalance", "class_profile_missing", "class_imbalance.not_evaluated")
    total, minority = sum(counts.values()), min(counts.values())
    fraction = ev.minority_class_fraction if ev.minority_class_fraction is not None else minority / total
    folds = ev.n_folds or DEFAULT_FOLDS
    fair_share = fraction * len(counts)
    evidence: dict[str, Any] = {
        "class_count": len(counts), "minority_class_fraction": fraction, "minority_rows": minority,
        "imbalance_ratio": ev.imbalance_ratio, "minority_fair_share_fraction": fair_share, "n_folds": folds,
        "minority_rows_per_fold": minority / folds, "min_minority_rows_per_fold": MIN_MINORITY_ROWS_PER_FOLD,
        "class_weighted_candidates": ev.class_weighted_candidates, "winner_class_weighted": ev.winner_class_weighted,
    }
    if fair_share >= IMBALANCE_FAIR_SHARE_WARN:
        return _finding("class_imbalance", "pass", evidence, ("class_imbalance.pass",))
    weighted = ev.class_weighted_candidates > 0
    too_few = minority < MIN_MINORITY_ROWS_PER_FOLD * folds
    keys = ("class_imbalance.imbalanced", *(("class_imbalance.too_few",) if too_few else ()),
            "class_imbalance.weighted" if weighted else "class_imbalance.unweighted")
    return _finding("class_imbalance", "fail" if too_few else "warning", evidence, keys,
                    "collect_more_data" if weighted or too_few else "class_weights")


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
    if cv is None:
        return _not_evaluated("implausible_score", "cv_score_missing", "implausible_score.missing_cv", evidence)
    if base is not None:
        margin = (base - cv) if lower else (cv - base)
        evidence.update(margin_over_baseline=margin, margin_in_cv_std=margin / std if std else None)
    if lower:
        if not base:
            return _not_evaluated("implausible_score", "baseline_missing", "implausible_score.missing_baseline",
                                  evidence)
        ratio = cv / base
        evidence.update(error_ratio=ratio, threshold={"error_ratio": ERROR_RATIO_WARN})
        if ratio <= ERROR_RATIO_WARN:
            status = "fail" if ratio <= ERROR_RATIO_FAIL else "warning"
            return _finding("implausible_score", status, evidence, ("implausible_score.error_ratio",),
                            "investigate_leakage")
        return _finding("implausible_score", "pass", evidence, ("implausible_score.pass",))
    if metric not in BOUNDED_SCORE_METRICS:
        return _not_evaluated("implausible_score", "unbounded_metric", "implausible_score.unbounded", evidence)
    evidence["threshold"] = {"near_perfect": NEAR_PERFECT_WARN, "trivial_residual_ratio": TRIVIAL_RESIDUAL_RATIO}
    if cv < NEAR_PERFECT_WARN:
        return _finding("implausible_score", "pass", evidence, ("implausible_score.pass",))
    if base is not None:
        residual = (1.0 - cv) / (1.0 - base) if base < 1.0 else 1.0
        evidence["residual_error_ratio"] = residual
        if residual >= TRIVIAL_RESIDUAL_RATIO:
            return _finding("implausible_score", "pass", evidence, ("implausible_score.trivial",))
    status = "fail" if cv >= NEAR_PERFECT_FAIL else "warning"
    return _finding("implausible_score", status, evidence, ("implausible_score.near_perfect",), "investigate_leakage")


# --- orchestration ----------------------------------------------------------------------

Partition = tuple[pd.DataFrame | None, Collection[int] | np.ndarray | None]


def _check_error(check: str, exc: BaseException) -> Finding:
    return _not_evaluated(check, "check_error", "check_error", {"error_type": type(exc).__name__})


def _guarded(check: str, run: Callable[[], Finding]) -> Finding:
    try:
        return run()
    except Exception as exc:  # noqa: BLE001 - one broken check must not hide the others
        logger.exception("trust check %s failed", check)
        return _check_error(check, exc)


def investigate(ev: RunEvidence, *, train_features: pd.DataFrame | None = None,
                test_row_hashes: Collection[int] | np.ndarray | None = None,
                partition: Callable[[], Partition] | None = None) -> list[Finding]:
    """All five checks in order, each guarded (an error is ``not_evaluated``, reason
    ``check_error``). ``partition`` lazily supplies (train features, test row hashes)."""

    def duplicates() -> Finding:
        features, hashes = partition() if partition is not None else (train_features, test_row_hashes)
        return check_duplicate_rows(features, hashes)

    runs: dict[str, Callable[[], Finding]] = {
        "target_leakage": lambda: check_target_leakage(ev),
        "overfit_gap": lambda: check_overfit_gap(ev),
        "duplicate_rows": duplicates,
        "class_imbalance": lambda: check_class_imbalance(ev),
        "implausible_score": lambda: check_implausible_score(ev),
    }
    return [_guarded(check, runs[check]) for check in CHECK_ORDER]


def investigate_result(result: Mapping[str, Any], *, partition: Callable[[], Partition] | None = None) -> list[Finding]:
    """``investigate`` over a run result; unreadable evidence -> every check not_evaluated."""

    try:
        ev = run_evidence_from_result(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("trust check evidence could not be read")
        return [_check_error(check, exc) for check in CHECK_ORDER]
    return investigate(ev, partition=partition)
