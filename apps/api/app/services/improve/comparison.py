"""Selection across iterations (ADR 0007 §5): is try i better than the incumbent? Pure.

Order: per-try comparability (strict superset, identical shared candidates, nothing skipped or
truncated -- ``engine/evaluation/comparability``), the probe added only candidates the plan counted
in T (else ``unplanned_candidates``), then the paired per-fold rule with the loop's pre-registered T:
plain goals on the winners' locked per-fold primary metric keyed by ``fold_number``; constrained goals
at each run's own solved threshold on its stored out-of-fold curve (``paired_goal_outcome``, shared
with the experiment verdict). ``beats_the_noise`` -> ``accepted`` unless the try's own trust checks
failed (``rejected_suspicious``); within the noise / worse / ``no_change`` -> ``rejected``; anything
that cannot be judged -> ``not_comparable``. The inputs are frozen into ``comparison``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from scipy.stats import t as student_t

from app.domain.improve import IT_ACCEPTED, IT_NOT_COMPARABLE, IT_REJECTED, ImproveGoal
from app.engine.evaluation.comparability import RunEvidence, comparability
from app.engine.evaluation.metrics import LOWER_IS_BETTER
from app.engine.evaluation.paired_verdict import ALPHA, MIN_FOLDS, FoldScore, PairedVerdict, nb_factor, paired_verdict
from app.engine.modeling.objective import MetricConstraint
from app.engine.modeling.operating_points import GoalScoreRefusal, PointObjective, curve_digest, load_curve
from app.engine.search.generator import DUMMY_FAMILIES

COMPARISON_VERSION = "dclab.improve_comparison.v1"


@dataclass(frozen=True)
class TryVerdict:
    status: str  # accepted | rejected | not_comparable
    reason: str | None
    comparison: dict[str, Any]


def point_objective(goal: ImproveGoal) -> PointObjective | None:
    if goal.objective is None:
        return None
    return PointObjective(goal=goal.objective.maximize, constraints=tuple(
        MetricConstraint(metric=item.metric, op=item.op, value=float(item.value)) for item in goal.objective.constraints))


def _json(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else float(value)


def _numbers(result: PairedVerdict) -> dict[str, Any]:
    return {
        "verdict_rule": result.verdict, "rule_reason": result.reason, "direction": result.direction,
        "folds": result.folds, "comparisons": result.comparisons, "mean_difference": _json(result.mean_difference),
        "se_nb": _json(result.standard_error), "t_quantile": _json(result.t_quantile), "margin": _json(result.margin),
        "minimum_effect": _json(result.minimum_effect), "minimum_effect_basis": result.minimum_effect_basis,
        "fold_scores": [{"fold_number": f.fold_number, "probe": _json(f.experiment), "incumbent": _json(f.against),
                         "difference": _json(f.difference)} for f in result.paired],
    }


def status_of(result_verdict: str, reason: str | None, *, suspicious: bool) -> tuple[str, str | None]:
    """The loop's reading of the paired rule (§5)."""

    if result_verdict == "beats_the_noise":
        return (IT_REJECTED, "rejected_suspicious") if suspicious else (IT_ACCEPTED, None)
    if result_verdict == "worse":
        return IT_REJECTED, "worse"
    if result_verdict == "within_the_noise":
        return IT_REJECTED, reason or "inside_margin"
    return IT_NOT_COMPARABLE, reason or "not_comparable"


def paired_try(metric: str, probe: Mapping[int, FoldScore], incumbent: Mapping[int, FoldScore], *,
               comparisons: int, suspicious: bool) -> tuple[str, str | None, PairedVerdict]:
    """The plain-goal rule: paired by fold number, error metrics flipped, se_NB, q = t_{1-α/T, K-1}."""

    result = paired_verdict(metric, probe, incumbent, comparisons=comparisons)
    status, reason = status_of(result.verdict, result.reason, suspicious=suspicious)
    return status, reason, result


def judge_try(
    *,
    goal: ImproveGoal,
    metric: str,
    incumbent: RunEvidence,
    probe: RunEvidence,
    incumbent_scores: Mapping[int, FoldScore] | None,
    probe_scores: Mapping[int, FoldScore] | None,
    incumbent_curve: Any,
    probe_curve: Any,
    planned_candidate_ids: Sequence[str],
    comparisons: int,
    suspicious: bool,
) -> TryVerdict:
    base: dict[str, Any] = {
        "version": COMPARISON_VERSION, "basis": "locked_cv_fold_evaluations", "goal_kind":
        "objective" if goal.constrained else "plain", "goal_metric": goal.metric, "selection_metric": metric,
        "planned_comparisons": comparisons, "alpha": ALPHA, "probe_winner": probe.winner,
        "incumbent_winner": incumbent.winner, "planned_candidate_ids": sorted(planned_candidate_ids),
    }

    def verdict(status: str, reason: str | None, **extra: Any) -> TryVerdict:
        return TryVerdict(status, reason, {**base, **extra, "status": status, "reason": reason})

    comparable = comparability(incumbent, probe)
    base["new_candidates"] = list(comparable.new_candidates)
    if not comparable.comparable:
        return verdict(IT_NOT_COMPARABLE, comparable.reason, detail=comparable.detail)
    # D4c gate 3: T counted exactly the candidates the plan expected; anything more was never priced.
    unplanned = sorted(set(comparable.new_candidates) - set(planned_candidate_ids) - DUMMY_FAMILIES)
    if unplanned:
        return verdict(IT_NOT_COMPARABLE, "unplanned_candidates", detail=f"not in the plan: {unplanned}")
    objective = point_objective(goal)
    if objective is None:
        if incumbent_scores is None or probe_scores is None:
            return verdict(IT_NOT_COMPARABLE, "missing_locked_folds")
        status, reason, result = paired_try(metric, probe_scores, incumbent_scores, comparisons=comparisons,
                                            suspicious=suspicious)
        return verdict(status, reason, **_numbers(result))
    from app.services.experiment_verdict_service import paired_goal_outcome

    base.update(basis="out_of_fold_operating_curves", incumbent_curve_digest=curve_digest(incumbent_curve),
                probe_curve_digest=curve_digest(probe_curve))
    curves = []
    for stored, winner in ((probe_curve, probe.winner), (incumbent_curve, incumbent.winner)):
        # experiments.result stays writable: a curve counts only when it names the locked winner.
        named = isinstance(stored, Mapping) and winner is not None and stored.get("candidate_id") == winner
        curves.append(load_curve(dict(stored)) if named else None)
    if curves[0] is None or curves[1] is None:
        return verdict(IT_NOT_COMPARABLE, "no_goal_curve")
    try:
        outcome = paired_goal_outcome(objective, curves[0], curves[1], comparisons=comparisons, suspicious=False)
    except GoalScoreRefusal as exc:
        return verdict(IT_NOT_COMPARABLE, exc.reason)
    status, reason = status_of(outcome.verdict, outcome.reason, suspicious=suspicious)
    return verdict(status, reason, scored_by="constraint_shortfall" if outcome.by_shortfall else "goal_metric",
                   probe_threshold=outcome.own.threshold, incumbent_threshold=outcome.other.threshold,
                   probe_meets_rule=outcome.own.feasible, incumbent_meets_rule=outcome.other.feasible,
                   fold_counts=[list(item) for item in outcome.own.fold_counts], **_numbers(outcome.result))


def goal_met(goal: ImproveGoal, fold_values: Sequence[float], *, feasible: bool | None) -> bool:
    """§4 rule 4 ("met on CV"): a target exists, the incumbent is feasible (constrained goals) and the
    one-sided 95 % lower bound ``mean - t_{0.95, K-1} * se_NB`` of its fold scores clears the target
    (an error metric: the upper bound stays below it)."""

    if goal.target is None or len(fold_values) < MIN_FOLDS or any(not math.isfinite(v) for v in fold_values):
        return False
    if goal.constrained and feasible is not True:
        return False
    lower = goal.metric in LOWER_IS_BETTER and not goal.constrained
    sign = -1.0 if lower else 1.0
    values = [sign * float(v) for v in fold_values]
    k = len(values)
    mean = sum(values) / k
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (k - 1))
    bound = mean - float(student_t.ppf(0.95, k - 1)) * sd * nb_factor(k)
    return bound >= sign * float(goal.target.value)
