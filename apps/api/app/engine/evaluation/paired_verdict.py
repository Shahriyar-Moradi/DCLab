"""The "beats the noise" verdict for ONE comparison of two runs on the same folds (P5.5-B-read,
ADR 0007 §5, option B).

Pure: the caller hands in each run's locked per-fold scores of the shared ranking metric keyed by
``fold_number`` (natural units, as stored in ``evaluation_metrics``). Scores are oriented larger is
better (error metrics negated), paired by fold number, and judged with the Nadeau-Bengio corrected
standard error ``se_NB = sd_d * sqrt(1/K + 1/(K-1))`` against the one-sided margin
``t_{1-alpha/T, K-1} * se_NB``. Outputs are converted back to the metric's own units.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Mapping

from scipy.stats import t as student_t

from app.engine.evaluation.metrics import LOWER_IS_BETTER

Verdict = Literal["beats_the_noise", "within_the_noise", "worse", "not_comparable"]

ALPHA = 0.05
MIN_FOLDS = 5  # ADR 0007 §5: K >= 5 is required (10 recommended)
MIN_EFFECT_FLOOR = 0.001
MIN_EFFECT_RELATIVE = 0.001
ZERO_TOLERANCE = 1e-12  # relative to the largest |score| (at least 1): "no change" and "no spread"
# Metrics computed from decision counts at a threshold: a smaller mean difference than one decision on
# the smallest fold denominator is never an effect (δ_min = max(0.001, 1/n_min)). Accuracy-like metrics
# move by 1/rows per decision; class-sensitive ones by 1/(rows of the smallest class) (ADR 0007 §5: the
# positives for recall). The caller passes the smallest class count when the locked folds carry it
# (binary: the fold's positive rate); otherwise the validation rows are used (a smaller δ_min: less
# conservative) and ``minimum_effect_basis`` says so.
COUNT_BASED_METRICS = frozenset({
    "accuracy", "balanced_accuracy", "precision", "recall", "f1", "specificity", "macro_f1", "weighted_f1",
    "macro_precision", "macro_recall", "top_k_precision",
})
CLASS_SENSITIVE_METRICS = frozenset({
    "balanced_accuracy", "precision", "recall", "f1", "macro_f1", "weighted_f1", "macro_precision", "macro_recall",
})


@dataclass(frozen=True)
class FoldScore:
    value: float  # natural units (an error metric is positive)
    validation_rows: int | None = None
    smallest_class_rows: int | None = None  # rows of the fold's smallest class (training-partition labels)


@dataclass(frozen=True)
class PairedFold:
    fold_number: int
    experiment: float
    against: float
    difference: float  # experiment minus against, natural units


@dataclass(frozen=True)
class PairedVerdict:
    verdict: Verdict
    reason: str | None
    metric: str
    direction: str
    folds: int
    comparisons: int
    mean_difference: float | None = None  # natural units, experiment minus against
    margin: float | None = None  # natural units (a magnitude)
    minimum_effect: float | None = None  # natural units (a magnitude)
    standard_error: float | None = None  # se_NB, natural units
    t_quantile: float | None = None
    minimum_effect_basis: str | None = None  # smallest_class_rows | validation_rows | relative | floor
    paired: tuple[PairedFold, ...] = field(default_factory=tuple)


def nb_factor(k: int) -> float:
    """The Nadeau-Bengio inflation of sd_d: sqrt(1/K + 1/(K-1)) (x0.671 at K = 5)."""

    return math.sqrt(1.0 / k + 1.0 / (k - 1))


def t_quantile(k: int, comparisons: int, alpha: float = ALPHA) -> float:
    return float(student_t.ppf(1.0 - alpha / comparisons, k - 1))


def minimum_effect_with_basis(metric: str, against_scores: list[float], min_rows: int | None,
                              min_class_rows: int | None = None) -> tuple[float, str]:
    """(δ_min in the metric's units, its basis): max(0.001, 1/n_min) for count-based metrics, where
    n_min is the smallest class count for class-sensitive metrics when known, else the smallest
    validation fold; 0.001 x |mean score of the run compared against| otherwise."""

    if metric in COUNT_BASED_METRICS:
        if metric in CLASS_SENSITIVE_METRICS and min_class_rows:
            return max(MIN_EFFECT_FLOOR, 1.0 / min_class_rows), "smallest_class_rows"
        if min_rows:
            return max(MIN_EFFECT_FLOOR, 1.0 / min_rows), "validation_rows"
        return MIN_EFFECT_FLOOR, "floor"
    mean = sum(against_scores) / len(against_scores) if against_scores else 0.0
    return MIN_EFFECT_RELATIVE * abs(mean), "relative"


def minimum_effect(metric: str, against_scores: list[float], min_rows: int | None,
                   min_class_rows: int | None = None) -> float:
    return minimum_effect_with_basis(metric, against_scores, min_rows, min_class_rows)[0]


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def paired_verdict(
    metric: str,
    experiment: Mapping[int, FoldScore],
    against: Mapping[int, FoldScore],
    *,
    comparisons: int = 1,
    alpha: float = ALPHA,
    lower_is_better: bool | None = None,
    count_denominator: int | None = None,
) -> PairedVerdict:
    """The §5 rule for one comparison (``comparisons`` = T, 1 for a single click).
    ``lower_is_better`` overrides the metric's direction (e.g. a constraint shortfall);
    ``count_denominator`` (the smallest per-fold denominator behind a count-based score, e.g. the
    flagged rows behind precision) sets δ_min = max(0.001, 1/n_min) with basis ``fold_denominator``."""

    if comparisons < 1:
        raise ValueError("comparisons must be >= 1")
    lower = metric in LOWER_IS_BETTER if lower_is_better is None else lower_is_better
    direction = "lower_is_better" if lower else "higher_is_better"
    base = {"metric": metric, "direction": direction, "comparisons": comparisons}
    if set(experiment) != set(against):
        return PairedVerdict("not_comparable", "different_folds", folds=0, **base)
    numbers = sorted(experiment)  # pairing is by fold number, never by position
    k = len(numbers)
    for number in numbers:
        a, b = experiment[number], against[number]
        if not (_finite(a.value) and _finite(b.value)):
            return PairedVerdict("not_comparable", "missing_locked_folds", folds=k, **base)
        if a.validation_rows is not None and b.validation_rows is not None and a.validation_rows != b.validation_rows:
            return PairedVerdict("not_comparable", "different_folds", folds=k, **base)
    paired = tuple(PairedFold(n, experiment[n].value, against[n].value, experiment[n].value - against[n].value)
                   for n in numbers)
    if k < MIN_FOLDS:
        return PairedVerdict("not_comparable", "too_few_folds", folds=k, paired=paired, **base)
    sign = -1.0 if lower else 1.0
    d = [sign * fold.difference for fold in paired]  # oriented: larger is better
    mean_d = sum(d) / k
    sd_d = math.sqrt(sum((x - mean_d) ** 2 for x in d) / (k - 1))
    rows = [r for n in numbers for r in (experiment[n].validation_rows, against[n].validation_rows) if r]
    smallest = [r for n in numbers for r in (experiment[n].smallest_class_rows, against[n].smallest_class_rows)
                if r is not None]
    # A class count is used only when every fold of both runs carries one.
    class_rows = min(smallest) if len(smallest) == 2 * k and min(smallest) > 0 else None
    delta_min, basis = minimum_effect_with_basis(metric, [against[n].value for n in numbers],
                                                 min(rows) if rows else None, class_rows)
    if count_denominator is not None and count_denominator > 0:
        delta_min, basis = max(MIN_EFFECT_FLOOR, 1.0 / count_denominator), "fold_denominator"
    q = t_quantile(k, comparisons, alpha)
    se = sd_d * nb_factor(k)
    margin = q * se
    stats = {"folds": k, "paired": paired, "minimum_effect": delta_min, "t_quantile": q,
             "minimum_effect_basis": basis}
    scale = max(1.0, *(abs(v) for n in numbers for v in (experiment[n].value, against[n].value)))
    tolerance = ZERO_TOLERANCE * scale
    if all(abs(x) <= tolerance for x in d):
        return PairedVerdict("within_the_noise", "no_change", mean_difference=0.0, margin=0.0, standard_error=0.0,
                             **stats, **base)
    if sd_d <= tolerance:
        # Every fold moved by exactly the same amount: no spread to judge noise by.
        return PairedVerdict("not_comparable", "constant_difference", mean_difference=sign * mean_d, **stats, **base)
    numbers_out = {"mean_difference": sign * mean_d, "margin": margin, "standard_error": se}
    if mean_d > margin and mean_d >= delta_min:
        return PairedVerdict("beats_the_noise", None, **numbers_out, **stats, **base)
    if -mean_d > margin and -mean_d >= delta_min:
        return PairedVerdict("worse", None, **numbers_out, **stats, **base)
    reason = "inside_margin" if abs(mean_d) <= margin else "below_minimum_effect"
    return PairedVerdict("within_the_noise", reason, **numbers_out, **stats, **base)
