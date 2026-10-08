"""Operating points of a binary run (P5.2-A): the locked winner's out-of-fold operating
curve and pure optimizers over it. No DB, no holdout.

The runner stores one aggregate per run, ``result["operating_curve"]``: at the same
candidate thresholds the P1.4-B lock searches (``objective.candidate_thresholds``, at
most ``MAX_THRESHOLD_CANDIDATES`` + 2) the pooled out-of-fold confusion counts (tp and
fp; fn and tn follow from the class totals) and the same counts per CV fold. It is built
from exactly the rows the threshold lock saw (all validation folds; the most recent one
under time-ordered CV), before the final holdout is scored. No row ids, no scores.

Everything else is computed from that curve: derived rates, Wilson intervals, fold
spread, the Pareto frontier over (precision, recall) [and expected cost], a
constraint-aware or cost-weighted search and the re-solve of the run's own objective.
The search is ``objective.select_index`` (one threshold search: same constraints rule,
same tie-break: closest to 0.5, then the higher threshold). Too few positives or
negatives: ``not_evaluated``, never an invented point. Figures picked from many
candidates on the same out-of-fold rows are optimistic (they are training-fold figures);
the Wilson intervals are per point (counting noise at that threshold only), not corrected
for that choice nor for fold-model variation. Thresholds are matched exactly (stored JSON
floats round-trip exactly; neighbouring candidates can differ in the last bit).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from functools import cached_property
from typing import Any

import numpy as np

from app.engine.modeling.objective import (
    DEFAULT_THRESHOLD,
    MAX_THRESHOLD_CANDIDATES,
    THRESHOLD_METRICS,
    MetricConstraint,
    Objective,
    candidate_thresholds,
    confusion_counts,
    lock_goal,
    rates_from_counts,
    select_index,
)

CURVE_VERSION = "dclab.operating_curve.v1"
# Fewer rows of either class in the curve's out-of-fold rows: recall / precision move in
# steps of 1/20 or coarser and the search over hundreds of thresholds is mostly noise.
MIN_CLASS_ROWS = 20
CURVE_METRICS = ("precision", "recall", "specificity", "f1", "accuracy", "balanced_accuracy", "flagged_share")
GOALS = (*CURVE_METRICS[:-1], "expected_cost")
# Maximising one of these alone is degenerate (flag almost nothing / everything).
NEEDS_CONSTRAINT = frozenset({"precision", "recall", "specificity"})
MAX_CONSTRAINTS = 6
# A fold contributes to the spread of a proportion only with this many rows in its denominator.
FOLD_MIN_DENOMINATOR = 10
WILSON_Z = 1.959963984540054  # 95 %
# (numerator, denominator) count names of the rates that are proportions.
PROPORTIONS = {
    "precision": ("tp", "flagged"),
    "recall": ("tp", "positives"),
    "specificity": ("tn", "negatives"),
    "flagged_share": ("flagged", "rows"),
}
TIE_BREAK = ("highest goal among the eligible thresholds; ties: the threshold closest to 0.5, "
             "then the higher threshold")


@dataclass(frozen=True)
class PointObjective:
    """Maximise ``goal`` (``expected_cost`` is minimised) subject to ``constraints``."""

    goal: str
    constraints: tuple[MetricConstraint, ...] = ()
    cost_false_positive: float | None = None
    cost_false_negative: float | None = None


@dataclass(frozen=True)
class Curve:
    thresholds: np.ndarray
    tp: np.ndarray
    fp: np.ndarray
    rows: int
    positives: int
    negatives: int
    folds: tuple[tuple[int, int, np.ndarray, np.ndarray], ...]
    oof_folds: str | None

    @property
    def fn(self) -> np.ndarray:
        return float(self.positives) - self.tp

    @property
    def tn(self) -> np.ndarray:
        return float(self.negatives) - self.fp

    @cached_property
    def _rates_cache(self) -> dict[str, np.ndarray]:
        cached = rates_from_counts(self.tp, self.fp, self.fn, self.tn, total=max(self.rows, 1))
        cached["flagged_share"] = (self.tp + self.fp) / max(self.rows, 1)
        for values in cached.values():
            values.flags.writeable = False
        return cached

    def rates(self) -> dict[str, np.ndarray]:
        """``objective.rates_from_counts`` (bit-identical to the lock's rates) + flagged share;
        computed once per curve (read-only arrays)."""

        return self._rates_cache

    def index_of(self, threshold: float) -> int | None:
        """The index of exactly this candidate threshold, else None (no nearest-match: the
        last two candidates are the top score and the next float above it, "flag nothing")."""

        hits = np.flatnonzero(self.thresholds == float(threshold))
        return int(hits[0]) if len(hits) else None


# --- build (runner) ------------------------------------------------------------------------------


def build_operating_curve(
    y_oof: Any,
    scores_oof: Any,
    folds: list[tuple[Any, Any]],
    *,
    oof_folds: str,
    candidate_id: str | None,
) -> dict[str, Any]:
    """The stored curve from the lock's out-of-fold rows (``y_oof`` / ``scores_oof``) and
    every CV fold's validation rows (``folds``, for the fold spread)."""

    y = np.asarray(y_oof, dtype=int)
    scores = np.asarray(scores_oof, dtype=float)
    base: dict[str, Any] = {
        "version": CURVE_VERSION, "selected_on": "out_of_fold_cv", "outcome_scope": "cv",
        "candidate_id": candidate_id, "oof_folds": oof_folds,
    }
    if not len(y) or len(np.unique(y)) != 2 or not np.all(np.isfinite(scores)):
        reason = ("no_out_of_fold_predictions" if not len(y) else "single_class_out_of_fold"
                  if len(np.unique(y)) != 2 else "non_finite_scores")
        return {**base, "status": "not_available", "reason": reason}
    thresholds = candidate_thresholds(scores)
    tp, fp, _fn, _tn = confusion_counts(y, scores, thresholds)
    stored_folds = []
    for y_fold, score_fold in folds:
        fold_y = np.asarray(y_fold, dtype=int)
        fold_tp, fold_fp, _, _ = confusion_counts(fold_y, np.asarray(score_fold, dtype=float), thresholds)
        stored_folds.append({
            "positives": int(fold_y.astype(bool).sum()), "negatives": int((~fold_y.astype(bool)).sum()),
            "tp": [int(v) for v in fold_tp], "fp": [int(v) for v in fold_fp],
        })
    return {
        **base, "status": "available", "reason": None, "max_candidates": MAX_THRESHOLD_CANDIDATES,
        "rows": int(len(y)), "positives": int(y.astype(bool).sum()), "negatives": int((~y.astype(bool)).sum()),
        "thresholds": [float(t) for t in thresholds], "tp": [int(v) for v in tp], "fp": [int(v) for v in fp],
        "folds": stored_folds,
    }


def curve_failed(*, oof_folds: str, candidate_id: str | None) -> dict[str, Any]:
    """What the runner stores when building the curve raised (distinct from a pre-P5.2-A run)."""

    return {"version": CURVE_VERSION, "selected_on": "out_of_fold_cv", "outcome_scope": "cv",
            "candidate_id": candidate_id, "oof_folds": oof_folds, "status": "not_available", "reason": "curve_failed"}


def curve_digest(stored: Any) -> str | None:
    """sha256 of the stored curve's canonical JSON: anchors a choice to the curve it was made on."""

    if not isinstance(stored, dict):
        return None
    text = json.dumps(stored, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- read --------------------------------------------------------------------------------------


def load_curve(stored: Any) -> Curve | None:
    """The stored curve, validated (None when absent, not available or malformed)."""

    if not isinstance(stored, dict) or stored.get("version") != CURVE_VERSION or stored.get("status") != "available":
        return None
    try:
        thresholds = np.asarray(stored["thresholds"], dtype=float)
        tp, fp = np.asarray(stored["tp"], dtype=float), np.asarray(stored["fp"], dtype=float)
        rows, positives, negatives = int(stored["rows"]), int(stored["positives"]), int(stored["negatives"])
        folds = tuple(
            (int(f["positives"]), int(f["negatives"]), np.asarray(f["tp"], dtype=float), np.asarray(f["fp"], dtype=float))
            for f in stored.get("folds") or []
        )
    except (KeyError, TypeError, ValueError):
        return None
    sizes = {len(thresholds), len(tp), len(fp), *(len(f[2]) for f in folds), *(len(f[3]) for f in folds)}
    if (
        len(sizes) != 1 or not len(thresholds) or not np.all(np.isfinite(thresholds))
        or np.any(np.diff(thresholds) <= 0) or rows != positives + negatives
        or np.any((tp < 0) | (tp > positives) | (fp < 0) | (fp > negatives))
    ):
        return None
    return Curve(thresholds, tp, fp, rows, positives, negatives, folds, stored.get("oof_folds"))


def not_evaluated_reason(curve: Curve) -> str | None:
    if curve.positives < MIN_CLASS_ROWS:
        return "too_few_positives"
    if curve.negatives < MIN_CLASS_ROWS:
        return "too_few_negatives"
    return None


def wilson(successes: float, trials: float) -> tuple[float, float] | None:
    if trials <= 0:
        return None
    p, n, z2 = successes / trials, float(trials), WILSON_Z * WILSON_Z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = WILSON_Z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _counts(curve: Curve, index: int) -> dict[str, float]:
    tp, fp = float(curve.tp[index]), float(curve.fp[index])
    return {"tp": tp, "fp": fp, "fn": curve.positives - tp, "tn": curve.negatives - fp, "flagged": tp + fp,
            "positives": float(curve.positives), "negatives": float(curve.negatives), "rows": float(curve.rows)}


def point(curve: Curve, index: int, *, cost: tuple[float, float] | None = None, detail: bool = False) -> dict[str, Any]:
    """One candidate threshold: counts, rates, per-row expected cost (with a cost matrix);
    ``detail`` adds 95 % Wilson intervals and the spread across CV folds."""

    rates = curve.rates()
    counts = _counts(curve, index)
    out: dict[str, Any] = {"threshold": float(curve.thresholds[index]),
                           **{key: int(counts[key]) for key in ("tp", "fp", "fn", "tn")},
                           **{name: float(rates[name][index]) for name in CURVE_METRICS}}
    out["expected_cost"] = (
        (counts["fp"] * cost[0] + counts["fn"] * cost[1]) / max(curve.rows, 1) if cost is not None else None
    )
    if detail:
        for name in ("precision", "recall"):
            numerator, denominator = PROPORTIONS[name]
            interval = wilson(counts[numerator], counts[denominator])
            out[f"{name}_interval"] = None if interval is None else {"low": interval[0], "high": interval[1]}
        out["fold_spread"] = fold_spread(curve, index)
    return out


def fold_spread(curve: Curve, index: int) -> dict[str, Any] | None:
    """Recall / precision range across CV folds at one threshold. A fold counts for a rate only
    with at least ``FOLD_MIN_DENOMINATOR`` positives (recall) or flagged rows (precision);
    the smallest denominators and the contributing fold counts are reported. Under
    time-ordered CV the curve is the most recent fold while the spread spans every fold."""

    if not curve.folds:
        return None
    positives = [int(pos) for pos, _neg, _tp, _fp in curve.folds]
    flagged = [int(tp[index] + fp[index]) for _pos, _neg, tp, fp in curve.folds]
    recalls = [float(tp[index]) / pos for pos, _neg, tp, _fp in curve.folds if pos >= FOLD_MIN_DENOMINATOR]
    precisions = [float(tp[index]) / float(tp[index] + fp[index]) for _p, _n, tp, fp in curve.folds
                  if tp[index] + fp[index] >= FOLD_MIN_DENOMINATOR]
    return {
        "folds": len(curve.folds), "recall_folds": len(recalls), "precision_folds": len(precisions),
        "min_fold_positives": min(positives), "min_fold_flagged": min(flagged),
        "min_denominator": FOLD_MIN_DENOMINATOR, "includes_folds_outside_curve": curve.oof_folds == "last_fold",
        "recall_min": min(recalls) if recalls else None, "recall_max": max(recalls) if recalls else None,
        "precision_min": min(precisions) if precisions else None,
        "precision_max": max(precisions) if precisions else None,
    }


def pareto_indices(curve: Curve, cost: tuple[float, float] | None = None) -> list[int]:
    """Thresholds no other one beats on precision and recall (and expected cost, when a
    cost matrix applies) without being worse on one; identical points keep one
    representative (closest to 0.5, then the higher threshold). Ascending threshold."""

    rates = curve.rates()
    columns = [rates["precision"], rates["recall"]]
    if cost is not None:
        columns.append(-(curve.fp * cost[0] + curve.fn * cost[1]))
    values = np.stack(columns, axis=1)
    at_least = (values[:, None, :] >= values[None, :, :]).all(axis=2)
    better = (values[:, None, :] > values[None, :, :]).any(axis=2)
    dominated = (at_least & better).any(axis=0)
    representative: dict[tuple[float, ...], int] = {}
    for index in np.flatnonzero(~dominated):
        key = tuple(float(v) for v in values[index])
        best = representative.get(key)
        if best is None or _closer(curve.thresholds, int(index), best):
            representative[key] = int(index)
    return sorted(representative.values())


def _closer(thresholds: np.ndarray, candidate: int, incumbent: int) -> bool:
    a, b = abs(thresholds[candidate] - DEFAULT_THRESHOLD), abs(thresholds[incumbent] - DEFAULT_THRESHOLD)
    return bool(a < b or (a == b and thresholds[candidate] > thresholds[incumbent]))


def solve(curve: Curve, objective: PointObjective) -> dict[str, Any]:
    """Constraint-aware (maximise ``goal``) or cost-weighted (minimise expected cost)
    search over the stored curve. ``status``: ``optimal`` (``index`` meets every
    constraint), ``infeasible`` (``index`` is the closest point: smallest total
    shortfall, then the best goal; it is never chosen silently) or ``not_evaluated``."""

    reason = not_evaluated_reason(curve)
    if reason is not None:
        return {"status": "not_evaluated", "reason": reason, "index": None}
    rates = curve.rates()
    if objective.goal == "expected_cost":
        if objective.cost_false_positive is None or objective.cost_false_negative is None:
            raise ValueError("expected_cost needs a cost matrix")
        goal = -(rates["fp"] * objective.cost_false_positive + rates["fn"] * objective.cost_false_negative)
    else:
        goal = rates[objective.goal]
    index, feasible, shortfall = select_index(curve.thresholds, rates, list(objective.constraints), goal)
    eligible = [all(_meets(rates[c.metric][i], c) for c in objective.constraints) for i in range(len(goal))]
    tied = sum(1 for i, ok in enumerate(eligible) if (ok or not feasible) and goal[i] == goal[index])
    return {
        "status": "optimal" if feasible else "infeasible",
        "reason": None if feasible else "no_threshold_meets_every_constraint",
        "index": index, "shortfall": shortfall, "tied_candidates": tied if feasible else None,
        "constraints": constraint_report(curve, index, objective.constraints),
    }


def _meets(value: float, constraint: MetricConstraint) -> bool:
    gap = (constraint.value - value) if constraint.op == ">=" else (value - constraint.value)
    return bool(gap <= 1e-12)


def constraint_report(curve: Curve, index: int, constraints: tuple[MetricConstraint, ...]) -> list[dict[str, Any]]:
    """Each constraint at one point: the out-of-fold value, met or not and, for proportions,
    whether the point's 95 % Wilson interval lies entirely on the allowed side
    (``clear_margin``). The interval covers counting noise at this threshold only; it is not
    corrected for choosing the threshold among the candidates on the same rows nor for
    fold-model variation, so even ``clear_margin: true`` does not mean the constraint will
    hold on new data (expect the value there to be lower), and ``false`` means counting noise
    alone already reaches past the bound."""

    rates, counts = curve.rates(), _counts(curve, index)
    report = []
    for item in constraints:
        observed = float(rates[item.metric][index])
        interval = None
        if item.metric in PROPORTIONS:
            numerator, denominator = PROPORTIONS[item.metric]
            interval = wilson(counts[numerator], counts[denominator])
        clear = None if interval is None else (interval[0] >= item.value if item.op == ">=" else interval[1] <= item.value)
        report.append({**item.to_dict(), "observed": observed, "met": _meets(observed, item),
                       "interval": None if interval is None else {"low": interval[0], "high": interval[1]},
                       "clear_margin": clear})
    return report


def solve_lock(curve: Curve, objective: Objective | None, primary_metric: str | None) -> float:
    """The threshold ``select_decision_threshold`` locks for this objective, re-solved on
    the stored curve (same goal, same constraints, same search)."""

    objective = objective or Objective()
    tunable = [item for item in objective.constraints if item.metric in THRESHOLD_METRICS]
    if not (tunable or objective.has_cost_matrix or primary_metric in THRESHOLD_METRICS):
        return DEFAULT_THRESHOLD
    rates = curve.rates()
    goal, _metric, _source, _cost = lock_goal(rates, objective, primary_metric)
    index, _feasible, _shortfall = select_index(curve.thresholds, rates, tunable, goal)
    return float(curve.thresholds[index])
