"""User objective for a run: primary-metric override, metric constraints, cost matrix,
and the out-of-fold decision threshold that satisfies them (P1.4-B).

The objective is user intent (ProblemSpec). The threshold is chosen only from
out-of-fold CV predictions of the locked winner; the final holdout is scored once
at that locked threshold and never moves it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

OBJECTIVE_VERSION = "dclab.objective.v1"
THRESHOLD_VERSION = "dclab.decision_threshold.v1"
DEFAULT_THRESHOLD = 0.5
MAX_THRESHOLD_CANDIDATES = 512

# Precision or recall alone is degenerate as a ranking target (flag almost nothing
# / almost everything); they are allowed as constraints only.
PRIMARY_METRICS: dict[str, frozenset[str]] = {
    "binary": frozenset(
        {"pr_auc", "roc_auc", "f1", "balanced_accuracy", "accuracy", "log_loss", "brier_score"}
    ),
    "multiclass": frozenset(
        {"macro_f1", "weighted_f1", "balanced_accuracy", "accuracy", "log_loss", "roc_auc_ovr"}
    ),
    "regression": frozenset({"mae", "rmse", "mse", "r2", "median_absolute_error"}),
}
# A ProblemSpec may say "classification" before the target is inspected; the
# run re-validates against the inferred binary/multiclass task.
PRIMARY_METRICS["classification"] = PRIMARY_METRICS["binary"] | PRIMARY_METRICS["multiclass"]
METRIC_ALIASES = {
    "auc": "roc_auc",
    "roc-auc": "roc_auc",
    "average_precision": "pr_auc",
    "pr-auc": "pr_auc",
    "f1_macro": "macro_f1",
    "macro-f1": "macro_f1",
    "f1_weighted": "weighted_f1",
    "logloss": "log_loss",
    "brier": "brier_score",
}
COST_MATRIX_TASKS = frozenset({"binary", "classification"})
# Binary metrics that depend on the decision threshold (and can be tuned by it).
THRESHOLD_METRICS = frozenset({"precision", "recall", "specificity", "f1", "accuracy", "balanced_accuracy"})
CONSTRAINT_METRICS: dict[str, frozenset[str]] = {
    "binary": PRIMARY_METRICS["binary"] | THRESHOLD_METRICS,
    "multiclass": PRIMARY_METRICS["multiclass"] | {"macro_precision", "macro_recall"},
    "regression": PRIMARY_METRICS["regression"] | {"mape", "smape"},
}
CONSTRAINT_METRICS["classification"] = CONSTRAINT_METRICS["binary"] | CONSTRAINT_METRICS["multiclass"]


def normalize_metric(name: Any) -> str | None:
    text = str(name or "").strip().lower()
    if not text:
        return None
    return METRIC_ALIASES.get(text, text)
OPERATORS = (">=", "<=")


class ObjectiveError(ValueError):
    """The declared objective is invalid for the task."""


@dataclass
class MetricConstraint:
    metric: str
    op: str
    value: float

    @property
    def label(self) -> str:
        bound = "min" if self.op == ">=" else "max"
        return f"constraint_{self.metric}_{bound}"

    def satisfied(self, observed: float | None) -> bool | None:
        if observed is None or not np.isfinite(observed):
            return None
        return observed >= self.value if self.op == ">=" else observed <= self.value

    def to_dict(self) -> dict[str, Any]:
        return {"metric": self.metric, "op": self.op, "value": self.value}


@dataclass
class Objective:
    primary_metric: str | None = None
    primary_metric_reason: str | None = None
    constraints: list[MetricConstraint] = field(default_factory=list)
    # Binary only: relative cost of each error type.
    cost_false_positive: float | None = None
    cost_false_negative: float | None = None
    version: str = OBJECTIVE_VERSION

    @property
    def has_cost_matrix(self) -> bool:
        return self.cost_false_positive is not None and self.cost_false_negative is not None

    @property
    def is_empty(self) -> bool:
        return self.primary_metric is None and not self.constraints and not self.has_cost_matrix

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["constraints"] = [item.to_dict() for item in self.constraints]
        return payload


def parse_objective(
    task_type: str,
    *,
    primary_metric: str | None = None,
    constraints: dict[str, Any] | None = None,
) -> Objective:
    """Validate ProblemSpec intent. Unknown keys in ``constraints`` stay free-form.

    Structured keys: ``primary_metric_reason`` (str), ``metric_constraints``
    (list of ``{"metric", "op", "value"}``), ``cost_matrix``
    (``{"false_positive", "false_negative"}``, binary only).
    """
    payload = dict(constraints or {})
    task = str(task_type or "").strip().lower()
    primary = normalize_metric(primary_metric)
    if primary is not None and task in PRIMARY_METRICS and primary not in PRIMARY_METRICS[task]:
        allowed = ", ".join(sorted(PRIMARY_METRICS[task]))
        raise ObjectiveError(f"primary_metric {primary!r} is not valid for {task}; use one of: {allowed}")
    reason = payload.get("primary_metric_reason")
    if reason is not None and not isinstance(reason, str):
        raise ObjectiveError("primary_metric_reason must be a string")

    parsed: list[MetricConstraint] = []
    raw_constraints = payload.get("metric_constraints") or []
    if not isinstance(raw_constraints, list):
        raise ObjectiveError("metric_constraints must be a list")
    seen: set[tuple[str, str]] = set()
    for item in raw_constraints:
        if not isinstance(item, dict):
            raise ObjectiveError("each metric constraint must be an object")
        metric = normalize_metric(item.get("metric")) or ""
        op = str(item.get("op") or "").strip()
        value = item.get("value")
        allowed_metrics = CONSTRAINT_METRICS.get(task)
        if not metric or (allowed_metrics is not None and metric not in allowed_metrics):
            raise ObjectiveError(f"constraint metric {metric!r} is not valid for {task}")
        if op not in OPERATORS:
            raise ObjectiveError(f"constraint op must be one of {OPERATORS}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
            raise ObjectiveError(f"constraint value for {metric!r} must be a number")
        if (metric, op) in seen:
            raise ObjectiveError(f"duplicate constraint {metric} {op}")
        seen.add((metric, op))
        parsed.append(MetricConstraint(metric=metric, op=op, value=float(value)))

    cost_fp = cost_fn = None
    cost = payload.get("cost_matrix")
    if cost is not None:
        if task not in COST_MATRIX_TASKS:
            raise ObjectiveError("cost_matrix is supported for binary tasks only")
        if not isinstance(cost, dict):
            raise ObjectiveError("cost_matrix must be an object")
        values = [cost.get("false_positive"), cost.get("false_negative")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in values):
            raise ObjectiveError("cost_matrix needs non-negative false_positive and false_negative")
        if values[0] == 0 and values[1] == 0:
            raise ObjectiveError("cost_matrix costs cannot both be zero")
        cost_fp, cost_fn = float(values[0]), float(values[1])

    return Objective(
        primary_metric=primary,
        primary_metric_reason=reason.strip() if isinstance(reason, str) and reason.strip() else None,
        constraints=parsed,
        cost_false_positive=cost_fp,
        cost_false_negative=cost_fn,
    )


def objective_from_dict(payload: Any, *, task_type: str | None = None) -> Objective | None:
    """Rebuild an Objective; with ``task_type`` it is re-validated like a ProblemSpec."""
    if not isinstance(payload, dict):
        return None
    if task_type is not None:
        cost = (
            {"false_positive": payload.get("cost_false_positive"), "false_negative": payload.get("cost_false_negative")}
            if payload.get("cost_false_positive") is not None or payload.get("cost_false_negative") is not None
            else None
        )
        spec_constraints: dict[str, Any] = {
            "metric_constraints": list(payload.get("constraints") or []),
            "primary_metric_reason": payload.get("primary_metric_reason"),
        }
        if cost is not None:
            spec_constraints["cost_matrix"] = cost
        return parse_objective(task_type, primary_metric=payload.get("primary_metric"), constraints=spec_constraints)
    return Objective(
        primary_metric=payload.get("primary_metric"),
        primary_metric_reason=payload.get("primary_metric_reason"),
        constraints=[
            MetricConstraint(metric=str(item["metric"]), op=str(item["op"]), value=float(item["value"]))
            for item in payload.get("constraints") or []
            if isinstance(item, dict)
        ],
        cost_false_positive=payload.get("cost_false_positive"),
        cost_false_negative=payload.get("cost_false_negative"),
    )


def confusion_counts(
    y: np.ndarray, scores: np.ndarray, thresholds: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(tp, fp, fn, tn) as floats for every threshold: a row is flagged when its score is
    >= the threshold (a NaN score never is). Counted on sorted scores (O(n log n)), the
    same integers as comparing every row with every threshold."""

    positives = np.asarray(y).astype(bool)
    values = np.asarray(scores, dtype=float)
    cuts = np.asarray(thresholds, dtype=float)

    def flagged(part: np.ndarray) -> np.ndarray:
        ordered = np.sort(part[~np.isnan(part)])
        return (len(ordered) - np.searchsorted(ordered, cuts, side="left")).astype(float)

    tp, fp = flagged(values[positives]), flagged(values[~positives])
    return tp, fp, float(positives.sum()) - tp, float((~positives).sum()) - fp


def rates_from_counts(
    tp: np.ndarray, fp: np.ndarray, fn: np.ndarray, tn: np.ndarray, total: int
) -> dict[str, np.ndarray]:
    """Threshold metrics from confusion counts (``total`` = rows, at least 1)."""

    with np.errstate(divide="ignore", invalid="ignore"):
        precision = np.where(tp + fp > 0, tp / (tp + fp), 0.0)
        recall = np.where(tp + fn > 0, tp / (tp + fn), 0.0)
        specificity = np.where(tn + fp > 0, tn / (tn + fp), 0.0)
        f1 = np.where(precision + recall > 0, 2 * precision * recall / (precision + recall), 0.0)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "f1": f1,
        "accuracy": (tp + tn) / total,
        "balanced_accuracy": (recall + specificity) / 2.0,
    }


def _rates_at(y: np.ndarray, scores: np.ndarray, thresholds: np.ndarray) -> dict[str, np.ndarray]:
    """Confusion counts and threshold metrics for every candidate threshold at once."""
    return rates_from_counts(*confusion_counts(y, scores, thresholds), total=max(len(y), 1))


def candidate_thresholds(scores: np.ndarray) -> np.ndarray:
    """The decision-threshold candidates of a score vector (shared by the lock and the
    P5.2-A operating curve: there is one threshold search)."""

    return _candidate_thresholds(np.asarray(scores, dtype=float))


def _candidate_thresholds(scores: np.ndarray) -> np.ndarray:
    unique = np.unique(np.clip(scores, 0.0, 1.0))
    if len(unique) > MAX_THRESHOLD_CANDIDATES:
        unique = np.unique(np.quantile(unique, np.linspace(0.0, 1.0, MAX_THRESHOLD_CANDIDATES)))
    # Include "flag nothing" (just above the highest score) as a candidate.
    top = float(unique.max()) if len(unique) else DEFAULT_THRESHOLD
    return np.unique(np.concatenate([unique, [DEFAULT_THRESHOLD, np.nextafter(top, np.inf)]]))


def constraint_status(rows: list[dict[str, Any]], key: str = "oof_satisfied") -> str:
    """One status over every constraint row (threshold-tunable or not)."""
    if not rows:
        return "not_requested"
    values = [row.get(key) for row in rows]
    if any(value is False for value in values):
        return "unsatisfiable" if key == "oof_satisfied" else "not_satisfied"
    if any(value is None for value in values):
        return "not_verifiable"
    return "satisfied"


def lock_goal(
    rates: dict[str, np.ndarray], objective: Objective, primary_metric: str | None
) -> tuple[np.ndarray, str, str, np.ndarray | None]:
    """(goal to maximise, goal metric, source, total cost or None) of the threshold lock:
    the expected cost with a cost matrix, else a threshold-dependent primary metric, else F1."""

    tunable = any(item.metric in THRESHOLD_METRICS for item in objective.constraints)
    if objective.has_cost_matrix:
        cost = rates["fp"] * float(objective.cost_false_positive) + rates["fn"] * float(
            objective.cost_false_negative
        )
        return -cost, "expected_cost", "cost_matrix", cost
    if primary_metric in THRESHOLD_METRICS:
        return rates[str(primary_metric)], str(primary_metric), "constraints" if tunable else "primary_metric", None
    return rates["f1"], "f1", "constraints", None


def select_index(
    thresholds: np.ndarray,
    rates: dict[str, np.ndarray],
    constraints: list[MetricConstraint],
    goal: np.ndarray,
) -> tuple[int, bool, float]:
    """The one threshold search (lock and operating-point optimizer): among the candidates
    meeting every constraint (or, when none does, those with the smallest total shortfall)
    the highest goal; ties go to the threshold closest to 0.5, then to the higher one.
    Returns (index, whether any candidate meets every constraint, its total shortfall)."""

    feasible = np.ones(len(thresholds), dtype=bool)
    shortfall = np.zeros(len(thresholds), dtype=float)
    for item in constraints:
        observed = rates[item.metric]
        gap = (item.value - observed) if item.op == ">=" else (observed - item.value)
        feasible &= gap <= 1e-12
        shortfall += np.clip(gap, 0.0, None)
    closeness = -np.abs(thresholds - DEFAULT_THRESHOLD)
    any_feasible = bool(feasible.any())
    pool = np.flatnonzero(feasible) if any_feasible else np.flatnonzero(np.isclose(shortfall, shortfall.min()))
    order = np.lexsort((closeness[pool], goal[pool]))
    index = int(pool[order[-1]])
    return index, any_feasible, float(shortfall[index])


def select_decision_threshold(
    y_oof: Any,
    scores_oof: Any,
    objective: Objective | None,
    *,
    cv_metrics: dict[str, Any] | None = None,
    primary_metric: str | None = None,
) -> dict[str, Any]:
    """Choose the binary decision threshold from out-of-fold predictions only.

    Goal: the expected cost when a cost matrix is declared; otherwise the primary
    metric when it depends on the threshold (f1, accuracy, balanced accuracy);
    otherwise F1. Only thresholds meeting every threshold-tunable constraint are
    eligible; ties go to the threshold closest to 0.5. When none is eligible, the
    one with the smallest total shortfall is used and the decision says so.
    Nothing requested keeps 0.5.
    """
    objective = objective or Objective()
    y = np.asarray(y_oof, dtype=int)
    scores = np.asarray(scores_oof, dtype=float)
    tunable = [item for item in objective.constraints if item.metric in THRESHOLD_METRICS]
    primary_tunable = primary_metric in THRESHOLD_METRICS
    requested = bool(tunable) or objective.has_cost_matrix or primary_tunable
    decision: dict[str, Any] = {
        "version": THRESHOLD_VERSION,
        "value": DEFAULT_THRESHOLD,
        "source": "default",
        "selected_on": "out_of_fold_cv",
        "oof_row_count": int(len(y)),
        "goal_metric": None,
        "threshold_status": "not_requested",
        "status": "not_requested",
        "reason": "No threshold constraint, cost matrix or threshold-dependent primary metric was declared; the default 0.5 is used.",
        "expected_cost": None,
        "constraints": [],
    }
    at_threshold: dict[str, float] = {}
    if requested and len(y) and len(np.unique(y)) == 2:
        thresholds = _candidate_thresholds(scores)
        rates = _rates_at(y, scores, thresholds)
        goal, goal_metric, source, cost = lock_goal(rates, objective, primary_metric)
        index, any_feasible, _shortfall = select_index(thresholds, rates, tunable, goal)
        if any_feasible:
            threshold_status = "satisfied" if tunable else "not_requested"
        else:
            threshold_status = "unsatisfiable"
        verb = "minimises expected cost" if source == "cost_matrix" else f"maximises {goal_metric}"
        decision.update(
            {
                "value": float(thresholds[index]),
                "source": source,
                "goal_metric": goal_metric,
                "threshold_status": threshold_status,
                "expected_cost": float(cost[index]) / max(len(y), 1) if cost is not None else None,
                "reason": f"Threshold {verb} on out-of-fold predictions"
                + (" subject to the declared constraints" if tunable else "")
                + (
                    "; no threshold satisfies every constraint, so the closest one is used."
                    if threshold_status == "unsatisfiable"
                    else "."
                ),
            }
        )
        at_threshold = {name: float(values[index]) for name, values in rates.items()}
    elif len(y):
        at_threshold = {
            name: float(values[0])
            for name, values in _rates_at(y, scores, np.asarray([DEFAULT_THRESHOLD])).items()
        }
        if requested:
            decision["reason"] = "Out-of-fold predictions hold a single class; the default 0.5 is used."
    # Pooled out-of-fold counts and rates at the chosen threshold, on exactly the rows it saw.
    decision["oof_at_threshold"] = dict(at_threshold)
    decision["constraints"] = evaluate_constraints(
        objective, oof_metrics={**dict(cv_metrics or {}), **at_threshold}
    )
    # Status covers every constraint, threshold-tunable or not.
    decision["status"] = constraint_status(decision["constraints"])
    return decision


def fold_constraint_values(
    folds: list[tuple[np.ndarray, np.ndarray]], threshold: float, metrics: list[str]
) -> list[dict[str, float]]:
    """Per-fold values of threshold metrics at the locked threshold (fold spread)."""
    rows: list[dict[str, float]] = []
    wanted = [name for name in metrics if name in THRESHOLD_METRICS]
    for y_fold, score_fold in folds:
        y = np.asarray(y_fold, dtype=int)
        if not len(y) or not wanted:
            continue
        rates = _rates_at(y, np.asarray(score_fold, dtype=float), np.asarray([float(threshold)]))
        rows.append({name: float(rates[name][0]) for name in wanted})
    return rows


def evaluate_constraints(
    objective: Objective | None,
    *,
    oof_metrics: dict[str, Any] | None = None,
    holdout_metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in (objective or Objective()).constraints:
        oof = _finite((oof_metrics or {}).get(item.metric))
        holdout = _finite((holdout_metrics or {}).get(item.metric))
        rows.append(
            {
                **item.to_dict(),
                "label": item.label,
                "oof_value": oof,
                "oof_satisfied": item.satisfied(oof),
                "holdout_value": holdout,
                "holdout_satisfied": item.satisfied(holdout) if holdout_metrics is not None else None,
            }
        )
    return rows


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if np.isfinite(value) else None


def threshold_metrics(y_true: Any, scores: Any, threshold: float) -> dict[str, float]:
    """Threshold-dependent binary metrics (incl. specificity) at one threshold."""
    y = np.asarray(y_true, dtype=int)
    if not len(y):
        return {}
    rates = _rates_at(y, np.asarray(scores, dtype=float), np.asarray([float(threshold)]))
    return {name: float(rates[name][0]) for name in ("specificity",)}
