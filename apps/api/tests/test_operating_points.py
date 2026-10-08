"""P5.2-A: the out-of-fold operating curve and the operating-point optimizer.

Hand-computed curves with known optima (constraint-feasible, infeasible, cost matrix,
ties, Pareto), parity with the P1.4-B lock (same counts, same search, the locked
threshold re-solved from the stored curve), rare-class refusal, mutation checks that a
broken optimizer fails the known optima, and the holdout canary: mutating every
final-evaluation value of a real run leaves the read model byte-identical.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest

from app.engine.modeling import objective as obj
from app.engine.modeling import operating_points as op
from app.engine.modeling.objective import MetricConstraint as C
from app.engine.modeling.objective import parse_objective
from app.services.operating_point_service import operating_points_from_result

# Hand-computed: P = 40, N = 60. 0.5 and 0.55 have identical counts (no row between them).
CURVE = {
    "version": op.CURVE_VERSION, "status": "available", "rows": 100, "positives": 40, "negatives": 60,
    "oof_folds": "all_folds", "candidate_id": "c1",
    "thresholds": [0.1, 0.3, 0.45, 0.5, 0.55, 0.7, 0.9, 0.95],
    "tp": [40, 38, 34, 32, 32, 24, 10, 0],
    "fp": [60, 40, 20, 14, 14, 6, 1, 0],
    "folds": [{"positives": 20, "negatives": 30, "tp": [20, 19, 18, 17, 17, 13, 6, 0], "fp": [30, 20, 9, 6, 6, 2, 0, 0]},
              {"positives": 20, "negatives": 30, "tp": [20, 19, 16, 15, 15, 11, 4, 0], "fp": [30, 20, 11, 8, 8, 4, 1, 0]}],
}
# Equal F1 at 0.25 and 0.75, exactly equidistant from 0.5 (binary fractions): the higher wins.
TIE_CURVE = {**CURVE, "rows": 70, "positives": 30, "negatives": 40, "folds": [],
             "thresholds": [0.2, 0.25, 0.75, 0.8], "tp": [30, 25, 25, 20], "fp": [20, 10, 10, 5]}
# Cost adds Pareto points: 0.6 (p .667, r .25) is beaten on precision and recall by 0.4 (p .682,
# r .75) but is cheaper when FP costs 5x (55 vs 80); flag-nothing 0.8 is the cheapest (40).
COST_CURVE = {**CURVE, "folds": [], "thresholds": [0.2, 0.4, 0.6, 0.8], "tp": [40, 30, 10, 0], "fp": [60, 14, 5, 0]}


def _at(curve, outcome) -> float:
    return float(curve.thresholds[outcome["index"]])


def _known_optima(solver, pareto=op.pareto_indices) -> list[str]:
    """Every hand-computed optimum; the failures (empty = correct)."""

    curve, tie = op.load_curve(CURVE), op.load_curve(TIE_CURVE)
    failures = []
    checks = [
        # recall >= 0.8, best precision: 0.5 and 0.55 tie (0.696); 0.5 is closest to 0.5.
        (curve, op.PointObjective("precision", (C("recall", ">=", 0.8),)), "optimal", 0.5),
        # best recall subject to precision >= 0.75: 0.7 (recall 0.6).
        (curve, op.PointObjective("recall", (C("precision", ">=", 0.75),)), "optimal", 0.7),
        # flag at most 30 % of rows, best F1: 0.7 (flagged 0.30).
        (curve, op.PointObjective("f1", (C("flagged_share", "<=", 0.3),)), "optimal", 0.7),
        # infeasible: the smallest shortfall is 0.5 / 0.55 (precision 0.696 < 0.85): closest 0.5.
        (curve, op.PointObjective("precision", (C("recall", ">=", 0.8), C("precision", ">=", 0.85))),
         "infeasible", 0.5),
        # FN costs 5x: cost 60, 50, 50, 54, 54, 86, 151, 200 -> 0.3 / 0.45 tie -> 0.45.
        (curve, op.PointObjective("expected_cost", (), 1.0, 5.0), "optimal", 0.45),
        # FP costs 5x: 300, 202, 106, 78, 78, 46, 35, 40 -> 0.9.
        (curve, op.PointObjective("expected_cost", (), 5.0, 1.0), "optimal", 0.9),
        (tie, op.PointObjective("f1"), "optimal", 0.75),
        # FP costs 5x subject to recall >= 0.8: 300, 202, 106, 78, 78 eligible -> 0.5 (not 0.9).
        (curve, op.PointObjective("expected_cost", (C("recall", ">=", 0.8),), 5.0, 1.0), "optimal", 0.5),
    ]
    for curve_, objective, status, threshold in checks:
        outcome = solver(curve_, objective)
        if outcome["status"] != status or _at(curve_, outcome) != threshold:
            failures.append(f"{objective}: {outcome['status']} {_at(curve_, outcome)}")
    if [float(curve.thresholds[i]) for i in pareto(curve)] != [0.1, 0.3, 0.45, 0.5, 0.7, 0.9]:
        failures.append("pareto")
    costly = op.load_curve(COST_CURVE)
    if [float(costly.thresholds[i]) for i in pareto(costly)] != [0.2, 0.4]:
        failures.append("pareto 2d")
    if [float(costly.thresholds[i]) for i in pareto(costly, (5.0, 1.0))] != [0.2, 0.4, 0.6, 0.8]:
        failures.append("pareto with cost")
    return failures


def test_known_optima_ties_and_infeasible_closest_point():
    assert _known_optima(op.solve) == []
    curve = op.load_curve(CURVE)
    best = op.solve(curve, op.PointObjective("precision", (C("recall", ">=", 0.8),)))
    assert best["tied_candidates"] == 2 and best["constraints"][0]["met"] is True
    infeasible = op.solve(curve, op.PointObjective("precision", (C("recall", ">=", 0.8), C("precision", ">=", 0.85))))
    assert infeasible["reason"] == "no_threshold_meets_every_constraint"
    assert infeasible["shortfall"] == pytest.approx(0.85 - 32 / 46)
    assert [row["met"] for row in infeasible["constraints"]] == [True, False]
    cost = op.point(curve, curve.index_of(0.45), cost=(1.0, 5.0))
    assert cost["expected_cost"] == pytest.approx(50 / 100) and (cost["fn"], cost["tn"]) == (6, 40)


def test_a_broken_optimizer_fails_the_known_optima(monkeypatch):
    # Mutants: constraints ignored, cost sign flipped, ties to the lower threshold, a Pareto
    # filter that keeps dominated points. Each must break at least one known optimum.
    assert _known_optima(lambda c, o: op.solve(c, replace(o, constraints=())))
    assert _known_optima(lambda c, o: op.solve(c, replace(
        o, cost_false_positive=-(o.cost_false_positive or 0), cost_false_negative=-(o.cost_false_negative or 0))))
    assert _known_optima(op.solve, pareto=lambda c, cost=None: list(range(len(c.thresholds))))
    assert _known_optima(op.solve, pareto=lambda c, cost=None: op.pareto_indices(c))  # cost dimension dropped
    assert _known_optima(lambda c, o: op.solve(c, replace(o, constraints=()) if o.goal == "expected_cost" else o))

    real = op.select_index

    def lower_on_ties(thresholds, rates, constraints, goal):
        index, feasible, shortfall = real(thresholds, rates, constraints, goal)
        ties = [i for i in range(len(goal)) if goal[i] == goal[index]]
        return min(ties), feasible, shortfall

    monkeypatch.setattr(op, "select_index", lower_on_ties)
    assert _known_optima(op.solve)


def test_wilson_intervals_fold_spread_and_sentences_have_known_values():
    assert op.wilson(0, 20)[0] == 0.0 and op.wilson(0, 20)[1] == pytest.approx(0.1611, abs=1e-4)
    assert op.wilson(1, 1)[0] == pytest.approx(0.2065, abs=1e-4) and op.wilson(1, 1)[1] == 1.0
    assert op.wilson(0, 0) is None
    curve = op.load_curve(CURVE)
    detail = op.point(curve, curve.index_of(0.5), detail=True)
    low, high = op.wilson(32, 46)
    assert detail["precision_interval"] == {"low": low, "high": high} and low == pytest.approx(0.5519, abs=1e-4)
    assert detail["recall_interval"]["low"] == pytest.approx(op.wilson(32, 40)[0])
    spread = detail["fold_spread"]
    assert (spread["recall_min"], spread["recall_max"]) == (0.75, 0.85)
    assert (spread["recall_folds"], spread["precision_folds"], spread["min_fold_positives"]) == (2, 2, 20)
    assert spread["min_fold_flagged"] == 23 and spread["includes_folds_outside_curve"] is False
    # Folds with fewer than 10 positives / flagged rows never enter the spread.
    small = op.load_curve({**CURVE, "folds": [{**CURVE["folds"][0], "positives": 3, "negatives": 47,
                                               "tp": [min(v, 3) for v in CURVE["folds"][0]["tp"]],
                                               "fp": [min(v, 47) for v in CURVE["folds"][0]["fp"]]},
                                              CURVE["folds"][1]]})
    thin = op.fold_spread(small, small.index_of(0.9))  # one flagged row in fold 2, three positives in fold 1
    assert (thin["recall_folds"], thin["min_fold_positives"], thin["precision_folds"]) == (1, 3, 0)
    assert thin["precision_min"] is None and thin["recall_min"] == thin["recall_max"] == 4 / 20
    from app.domain.operating_points import what_this_means

    text = what_this_means(op.point(curve, curve.index_of(0.5)))
    assert "flags 46% of rows and catches 80% of the positive class; 70% of flagged rows" in text
    many = op.load_curve({**CURVE, "rows": 1000, "negatives": 960, "folds": [],
                          "tp": [40, 40, 40, 40, 40, 40, 1, 0], "fp": [960, 900, 800, 700, 700, 10, 0, 0]})
    assert "flags <1% of rows and catches 2% of" in what_this_means(op.point(many, many.index_of(0.9)))
    nearly = op.load_curve({**CURVE, "rows": 1000, "positives": 999, "negatives": 1, "folds": [],
                            "tp": [999, 998, 0, 0, 0, 0, 0, 0], "fp": [1, 0, 0, 0, 0, 0, 0, 0]})
    assert "catches >99% of the positive class" in what_this_means(op.point(nearly, 1))


def test_exact_threshold_match_keeps_flag_nothing_and_tiny_neighbours_apart():
    scores = np.array([1e-12, 3e-12, 5e-11, 0.2, 0.7, 0.7])
    thresholds = obj.candidate_thresholds(scores)
    curve = op.load_curve(op.build_operating_curve([0, 1, 0, 1, 1, 0], scores, [], oof_folds="all_folds",
                                                   candidate_id=None))
    assert [curve.index_of(t) for t in thresholds] == list(range(len(thresholds)))
    assert curve.index_of(float(thresholds[-1])) == len(thresholds) - 1  # nextafter(top): flag nothing
    assert curve.index_of(float(thresholds[-1]) - 1e-12) is None  # no nearest match
    # A cost matrix that makes a weak model flag nothing reads back as exactly that point.
    rng = np.random.default_rng(2)
    y = rng.binomial(1, 0.08, 600)
    weak = np.clip(0.2 + 0.05 * y + rng.normal(0, 0.15, 600), 0, 1)
    objective = parse_objective("binary", constraints={"cost_matrix": {"false_positive": 20, "false_negative": 1}})
    lock = obj.select_decision_threshold(y, weak, objective)
    stored = json.loads(json.dumps(op.build_operating_curve(y, weak, [], oof_folds="all_folds", candidate_id="c")))
    assert lock["value"] == stored["thresholds"][-1] and lock["oof_at_threshold"]["fp"] == 0
    result = {"task": {"task_type": "binary"}, "operating_curve": stored, "objective": objective.to_dict(),
              "decision_threshold": {"value": lock["value"], "source": lock["source"], "status": lock["status"]}}
    body = operating_points_from_result(uuid4(), result, completed=True)
    locked = body.locked.point
    assert (body.status, locked.threshold, locked.tp, locked.fp, locked.flagged_share) == (
        "available", lock["value"], 0, 0, 0.0)
    assert "flags 0% of rows" in locked.what_this_means and body.locked.reproduced_from_curve is True
    assert locked.expected_cost == pytest.approx(int(y.sum()) / 600)


def test_pareto_points_are_undominated_and_monotone_on_random_curves():
    rng = np.random.default_rng(5)
    for trial in range(40):
        n = int(rng.integers(80, 600))
        y = rng.binomial(1, rng.uniform(0.15, 0.5), n)
        scores = np.round(np.clip(0.3 + 0.35 * (y - 0.3) + rng.normal(0, 0.25, n), 0, 1), 2 if trial % 2 else 6)
        curve = op.load_curve(op.build_operating_curve(y, scores, [], oof_folds="all_folds", candidate_id=None))
        rates = curve.rates()
        for cost in (None, (1.0, 4.0)):
            front = op.pareto_indices(curve, cost)
            columns = [rates["precision"], rates["recall"]] + ([-(curve.fp * cost[0] + curve.fn * cost[1])] if cost else [])
            values = np.stack(columns, axis=1)
            for i in front:  # nothing beats a frontier point
                assert not any((values[j] >= values[i]).all() and (values[j] > values[i]).any() for j in range(len(values)))
            for j in set(range(len(values))) - set(front):  # everything else is beaten or a duplicate
                assert any((values[i] >= values[j]).all() for i in front)
        front = op.pareto_indices(curve)
        precision, recall = rates["precision"][front], rates["recall"][front]
        assert np.all(np.diff(recall) < 0) and np.all(np.diff(precision) > 0)  # ascending threshold
        assert set(front) <= set(op.pareto_indices(curve, (1.0, 4.0)))


def _broadcast_counts(y, scores, thresholds):
    pred = scores[None, :] >= thresholds[:, None]
    pos = y.astype(bool)[None, :]
    return [(pred & pos).sum(1), (pred & ~pos).sum(1)]


def test_curve_counts_are_the_lock_counts_and_folds_add_up():
    rng = np.random.default_rng(11)
    y = rng.binomial(1, 0.3, 500)
    scores = rng.choice(np.round(rng.uniform(size=60), 3), 500)  # many ties, thresholds at score values
    folds = [(y[i::5], scores[i::5]) for i in range(5)]
    stored = op.build_operating_curve(np.concatenate([f[0] for f in folds]), np.concatenate([f[1] for f in folds]),
                                      folds, oof_folds="all_folds", candidate_id="c")
    curve = op.load_curve(stored)
    thresholds = obj.candidate_thresholds(scores)
    assert np.array_equal(curve.thresholds, thresholds) and len(thresholds) <= obj.MAX_THRESHOLD_CANDIDATES + 2
    tp, fp = _broadcast_counts(y, scores, thresholds)
    assert np.array_equal(curve.tp, tp) and np.array_equal(curve.fp, fp)
    lock_rates = obj._rates_at(y, scores, thresholds)
    rates = curve.rates()
    assert all(np.array_equal(rates[k], lock_rates[k]) for k in lock_rates)  # bit-identical
    assert np.array_equal(sum(np.asarray(f["tp"]) for f in stored["folds"]), curve.tp)
    assert set(stored) >= {"thresholds", "tp", "fp", "folds"} and "scores" not in stored and "rows_index" not in stored
    # A mutated (">") counter would differ at the thresholds that equal a score.
    gt = (scores[None, :] > thresholds[:, None]) & y.astype(bool)[None, :]
    assert not np.array_equal(gt.sum(1), curve.tp)


@pytest.mark.parametrize("raw, primary", [
    (None, None), (None, "f1"), (None, "roc_auc"),
    ({"metric_constraints": [{"metric": "recall", "op": ">=", "value": 0.8}]}, "pr_auc"),
    ({"metric_constraints": [{"metric": "precision", "op": ">=", "value": 0.99}]}, None),  # unsatisfiable
    ({"cost_matrix": {"false_positive": 1, "false_negative": 6}}, None),
    ({"cost_matrix": {"false_positive": 3, "false_negative": 1},
      "metric_constraints": [{"metric": "recall", "op": ">=", "value": 0.4}]}, "balanced_accuracy"),
])
def test_the_locked_threshold_is_reproduced_from_the_stored_curve(raw, primary):
    rng = np.random.default_rng(3)
    for _ in range(10):
        y = rng.binomial(1, 0.3, 400)
        scores = np.clip(0.3 + 0.35 * (y - 0.3) + rng.normal(0, 0.2, 400), 0, 1)
        objective = parse_objective("binary", constraints=raw)
        locked = obj.select_decision_threshold(y, scores, objective, primary_metric=primary)["value"]
        curve = op.load_curve(op.build_operating_curve(y, scores, [], oof_folds="all_folds", candidate_id=None))
        assert op.solve_lock(curve, objective, primary) == locked


def test_rare_class_and_unusable_curves_never_invent_a_point():
    y = np.array([1] * 5 + [0] * 300)
    scores = np.linspace(0, 1, 305)
    curve = op.load_curve(op.build_operating_curve(y, scores, [], oof_folds="all_folds", candidate_id=None))
    assert op.solve(curve, op.PointObjective("f1"))["status"] == "not_evaluated"
    assert op.solve(curve, op.PointObjective("f1"))["index"] is None
    assert op.build_operating_curve(np.zeros(50), scores[:50], [], oof_folds="all_folds", candidate_id=None)[
        "reason"] == "single_class_out_of_fold"
    assert op.build_operating_curve(np.array([0, 1]), np.array([np.nan, 0.2]), [], oof_folds="all_folds",
                                    candidate_id=None)["reason"] == "non_finite_scores"
    for broken in ({**CURVE, "tp": CURVE["tp"][:-1]}, {**CURVE, "tp": [41] + CURVE["tp"][1:]},
                   {**CURVE, "thresholds": sorted(CURVE["thresholds"], reverse=True)}, {**CURVE, "version": "v0"}):
        assert op.load_curve(broken) is None


def _result(curve=CURVE, task="binary", **extra) -> dict:
    return {"task": {"task_type": task}, "operating_curve": curve,
            "decision_threshold": {"value": 0.5, "source": "default", "status": "not_requested"},
            "selection": {"selection_metric": "pr_auc"}, "objective": None, **extra}


def test_read_model_states_old_runs_and_other_tasks():
    eid = uuid4()
    body = operating_points_from_result(eid, _result(), completed=True)
    assert (body.status, body.outcome_scope, body.basis, len(body.points)) == ("available", "cv", "out_of_fold_cv", 8)
    assert [p.threshold for p in body.pareto] == [0.1, 0.3, 0.45, 0.5, 0.7, 0.9]
    assert body.locked.threshold == 0.5 and body.locked.reproduced_from_curve is True
    assert body.locked.point.recall == 0.8 and "flags 46% of rows" in body.locked.point.what_this_means
    assert body.locked.point.fold_spread.recall_min == 0.75 and body.scoring.threshold == 0.5
    assert body.scoring.uses == "locked_threshold"
    old = operating_points_from_result(eid, {k: v for k, v in _result().items() if k != "operating_curve"},
                                       completed=True)
    assert (old.status, old.reason, old.points, old.locked.point) == ("not_available", "no_operating_curve", [], None)
    for task in ("multiclass", "regression"):
        other = operating_points_from_result(eid, _result(task=task), completed=True)
        assert (other.status, other.locked, other.points) == ("not_applicable", None, [])
    rare = operating_points_from_result(eid, _result({**CURVE, "positives": 10, "rows": 70,
                                                       "tp": [min(v, 10) for v in CURVE["tp"]]}), completed=True)
    assert (rare.status, rare.reason, rare.points, rare.pareto) == ("not_evaluated", "too_few_positives", [], [])
    assert (rare.locked.threshold, rare.locked.point) == (0.5, None)  # small cells: the threshold only
    rare_curve = {**CURVE, "positives": 10, "rows": 70, "tp": [min(v, 10) for v in CURVE["tp"]]}
    record = SimpleNamespace(id=uuid4(), facts={"threshold": 0.5, "curve_digest": op.curve_digest(rare_curve)},
                             details={"method": "threshold"}, rationale="r", actor_user_id=None,
                             recorded_at=datetime(2026, 10, 8, tzinfo=UTC), supersedes_id=None)
    gated = operating_points_from_result(eid, _result(rare_curve), completed=True, chosen=record).chosen
    assert (gated.threshold, gated.point, gated.curve_changed) == (0.5, None, False)
    unfinished = operating_points_from_result(eid, _result(), completed=False)
    assert (unfinished.status, unfinished.reason) == ("not_available", "run_not_completed")
    unlocked = operating_points_from_result(eid, _result(), completed=True, evidence_locked=False)
    assert (unlocked.status, unlocked.reason, unlocked.locked) == ("not_available", "evidence_not_locked", None)
    failed = op.curve_failed(oof_folds="all_folds", candidate_id="c1")
    assert operating_points_from_result(eid, _result(failed), completed=True).reason == "curve_failed"


def test_reproduced_from_curve_is_false_when_the_lock_does_not_follow_from_the_curve():
    eid = uuid4()
    # (a) The stored lock came from another objective (FP costs 5x -> 0.9) than the run's own
    # (recall >= 0.8, best F1 -> 0.5): the re-solve disagrees.
    other = obj.parse_objective("binary", constraints={"metric_constraints": [
        {"metric": "recall", "op": ">=", "value": 0.8}]})
    curve = op.load_curve(CURVE)
    assert op.solve(curve, op.PointObjective("expected_cost", (), 5.0, 1.0))["index"] == curve.index_of(0.9)
    assert op.solve_lock(curve, other, "pr_auc") == 0.5
    mismatched = _result(objective=other.to_dict(), decision_threshold={"value": 0.9, "source": "cost_matrix",
                                                                         "status": "satisfied"})
    body = operating_points_from_result(eid, mismatched, completed=True)
    assert (body.locked.threshold, body.locked.reproduced_from_curve, body.locked.point.threshold) == (0.9, False, 0.9)
    # (b) The lock names the top score while the re-solve gives the next float above it ("flag
    # nothing", ~1e-16 away): exact comparison says False (a tolerance would say True).
    rng = np.random.default_rng(2)
    y = rng.binomial(1, 0.08, 600)
    weak = np.clip(0.2 + 0.05 * y + rng.normal(0, 0.15, 600), 0, 1)
    costly = obj.parse_objective("binary", constraints={"cost_matrix": {"false_positive": 20, "false_negative": 1}})
    stored = json.loads(json.dumps(op.build_operating_curve(y, weak, [], oof_folds="all_folds", candidate_id="c")))
    top, nothing = stored["thresholds"][-2:]
    assert op.solve_lock(op.load_curve(stored), costly, None) == nothing and 0 < nothing - top < 1e-15
    neighbour = {"task": {"task_type": "binary"}, "operating_curve": stored, "objective": costly.to_dict(),
                 "decision_threshold": {"value": top, "source": "cost_matrix", "status": "not_requested"}}
    body = operating_points_from_result(eid, neighbour, completed=True)
    assert (body.locked.reproduced_from_curve, body.locked.point.threshold) == (False, top)
    assert body.locked.point.tp + body.locked.point.fp >= 1  # the top score itself is flagged


def test_a_failing_curve_build_is_stored_as_failed_not_absent(monkeypatch):
    from app.engine.experiments import runner

    def boom(*args, **kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(op, "build_operating_curve", boom)
    y = np.array([0, 1] * 30)
    stored = runner._winner_operating_curve("binary", {"candidate_id": "c"}, ([np.arange(60)], [np.linspace(0, 1, 60)]),
                                            y, last_fold_only=False)
    assert (stored["status"], stored["reason"], stored["version"]) == ("not_available", "curve_failed",
                                                                       op.CURVE_VERSION)


def test_degenerate_goals_need_a_binding_constraint_and_costs_are_bounded():
    from pydantic import ValidationError

    from app.domain.operating_points import OperatingObjectiveInput, OperatingPointChoiceRequest

    for raw in ({"goal": "precision"}, {"goal": "precision", "constraints": [{"metric": "recall", "op": ">=", "value": 0}]},
                {"goal": "recall", "constraints": [{"metric": "precision", "op": "<=", "value": 1}]},
                {"goal": "recall", "constraints": [{"metric": "recall", "op": "<=", "value": 0.9}]},
                {"goal": "expected_cost", "cost_false_positive": 1e308, "cost_false_negative": 1},
                {"goal": "expected_cost", "cost_false_positive": 1, "cost_false_negative": 2e6}):
        with pytest.raises(ValidationError):
            OperatingObjectiveInput.model_validate(raw)
    assert OperatingObjectiveInput.model_validate(
        {"goal": "precision", "constraints": [{"metric": "recall", "op": ">=", "value": 0.8}]})
    for threshold in ("0.5", True):
        with pytest.raises(ValidationError):
            OperatingPointChoiceRequest.model_validate({"threshold": threshold, "reason": "x"})
    assert OperatingPointChoiceRequest.model_validate({"threshold": 0, "reason": "x"}).threshold == 0.0


HOLDOUT_FIELDS = ("test_metrics", "test_predictions", "final_test_evaluation", "train_metrics")
# The only result keys the read model may use (the service's allowlist).
ALLOWED_KEYS = ("task", "operating_curve", "decision_threshold", "objective", "selection")


def _mutate_holdout(result: dict) -> dict:
    mutated = copy.deepcopy(result)
    rng = np.random.default_rng(99)

    def scramble(value):
        if isinstance(value, dict):
            return {k: scramble(v) for k, v in value.items()}
        if isinstance(value, list):
            return [scramble(v) for v in value]
        if isinstance(value, bool):
            return not value
        if isinstance(value, (int, float)):
            return float(rng.uniform())
        return value

    for key in HOLDOUT_FIELDS:
        mutated[key] = scramble(mutated.get(key) or {"recall": 0.1, "precision": 0.2})
    lock = mutated["decision_threshold"]
    lock["holdout_status"] = "not_satisfied"
    for row in lock.get("constraints") or []:
        row.update(holdout_value=float(rng.uniform()), holdout_satisfied=not row.get("holdout_satisfied"))
    mutated["split"] = {**mutated.get("split", {}), "test_source_rows": [-1, -2]}
    for holder in [mutated.get("best_single") or {}, *(mutated.get("candidates") or [])]:
        if isinstance(holder, dict):
            holder["test_metrics"] = scramble(holder.get("test_metrics") or {"recall": 0.3})
    return mutated


def test_holdout_canary_leaves_the_read_model_and_optimizer_byte_identical():
    from test_objective_threshold import _binary_run

    objective = parse_objective("binary", constraints={
        "metric_constraints": [{"metric": "recall", "op": ">=", "value": 0.7}],
        "cost_matrix": {"false_positive": 1, "false_negative": 3}})
    result = _binary_run(objective)
    assert result["test_metrics"] and result["decision_threshold"]["constraints"][0]["holdout_value"] is not None
    eid = uuid4()
    before = operating_points_from_result(eid, result, completed=True)
    assert before.status == "available" and before.locked.reproduced_from_curve is True
    assert before.locked.threshold == result["decision_threshold"]["value"]
    mutated = _mutate_holdout(result)
    assert mutated["test_metrics"] != result["test_metrics"]
    assert mutated["best_single"]["test_metrics"] != result["best_single"]["test_metrics"]
    after = operating_points_from_result(eid, mutated, completed=True)
    assert after.model_dump_json() == before.model_dump_json()
    # Only the allowlisted keys feed the read: the rest of the result can be dropped entirely.
    lock_only = {key: result["decision_threshold"][key] for key in ("value", "source", "status")}
    minimal = {**{key: result[key] for key in ALLOWED_KEYS}, "decision_threshold": lock_only}
    assert operating_points_from_result(eid, minimal, completed=True).model_dump_json() == before.model_dump_json()
    curve = op.load_curve(mutated["operating_curve"])
    goal = op.PointObjective("precision", (C("recall", ">=", 0.75),))
    assert op.solve(curve, goal) == op.solve(op.load_curve(result["operating_curve"]), goal)
    # The comparison is sensitive: an out-of-fold count change does show.
    nudged = copy.deepcopy(result)
    nudged["operating_curve"]["fp"][0] -= 1
    assert operating_points_from_result(eid, nudged, completed=True).model_dump_json() != before.model_dump_json()


def test_runner_builds_the_curve_on_the_lock_rows_before_the_holdout(monkeypatch):
    import app.engine.experiments.runner as runner_module
    from test_objective_threshold import _binary_run

    order: list[str] = []
    real_curve, real_holdout = runner_module._winner_operating_curve, runner_module._fit_and_score_holdout

    def curve_spy(*args, **kwargs):
        order.append("curve")
        return real_curve(*args, **kwargs)

    def holdout_spy(*args, **kwargs):
        order.append("holdout")
        return real_holdout(*args, **kwargs)

    monkeypatch.setattr(runner_module, "_winner_operating_curve", curve_spy)
    monkeypatch.setattr(runner_module, "_fit_and_score_holdout", holdout_spy)
    objective = parse_objective("binary", constraints={
        "metric_constraints": [{"metric": "precision", "op": ">=", "value": 0.6}]})
    result = _binary_run(objective)
    assert order == ["curve", "holdout"]
    stored, split, lock = result["operating_curve"], result["split"], result["decision_threshold"]
    assert stored["rows"] == lock["oof_row_count"] == len(split["train_source_rows"]) == split["n_train"]
    assert stored["oof_folds"] == "all_folds" and stored["candidate_id"] == result["best_single"]["candidate_id"]
    assert sum(f["positives"] + f["negatives"] for f in stored["folds"]) == stored["rows"]
    curve = op.load_curve(stored)
    assert op.solve_lock(curve, objective, result["selection"]["selection_metric"]) == lock["value"]
    index = curve.index_of(lock["value"])
    assert op.point(curve, index)["recall"] == pytest.approx(lock["oof_at_threshold"]["recall"])


def test_runner_canary_holdout_labels_and_features_never_move_the_curve_or_lock():
    import tempfile
    from pathlib import Path

    from app.engine.experiments.runner import run_experiment
    from app.engine.types import SearchConfig
    from test_open_ingest_runner import _frame, _task_and_config

    objective = parse_objective("binary", constraints={
        "metric_constraints": [{"metric": "recall", "op": ">=", "value": 0.7}],
        "cost_matrix": {"false_positive": 1, "false_negative": 3}})
    frame = _frame(n=400)
    task, _ = _task_and_config(frame)
    config = SearchConfig(strategy="open_ingest", max_candidates=8, seed=42, objective=objective.to_dict())

    def run(data, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            return run_experiment(data, task, config, artifact_dir=Path(tmp), **kwargs)

    base = run(frame)
    split = base["split"]
    pinned = {"holdout_plan": base["holdout_plan"],
              "holdout_partition": (split["test_source_rows"], split["train_source_rows"])}
    clean = run(frame, **pinned)
    mutated = frame.copy()
    rows = mutated.index[np.asarray(split["test_source_rows"])]
    mutated.loc[rows, task.target] = np.where(mutated.loc[rows, task.target] == "Yes", "No", "Yes")
    rng = np.random.default_rng(0)
    for column in ("tenure", "MonthlyCharges", "TotalCharges"):
        mutated.loc[rows, column] = rng.permutation(mutated.loc[rows, column].to_numpy())[::-1] * 3 + 7
    flipped = run(mutated, **pinned)
    assert flipped["test_metrics"]["roc_auc"] != clean["test_metrics"]["roc_auc"]  # the holdout did change
    assert clean["operating_curve"]["status"] == "available"
    assert json.dumps(flipped["operating_curve"], sort_keys=True) == json.dumps(clean["operating_curve"], sort_keys=True)
    assert flipped["decision_threshold"]["value"] == clean["decision_threshold"]["value"]


def test_time_ordered_cv_uses_the_lock_fold_and_reproduces_it():
    from app.engine.experiments.runner import _lock_decision_threshold, _winner_operating_curve

    rng = np.random.default_rng(0)
    y = rng.binomial(1, 0.4, 300)
    pool = pd.DataFrame({"__source_row__": np.arange(300)})
    folds = [np.arange(100, 150), np.arange(150, 200), np.arange(200, 300)]
    scores = [np.clip(y[idx] * 0.4 + rng.uniform(0, 0.6, len(idx)), 0, 1) for idx in folds]
    objective = parse_objective("binary", constraints={"metric_constraints": [{"metric": "precision", "op": ">=", "value": 0.7}]})
    lock = _lock_decision_threshold("binary", {"candidate_id": "c1", "cv_mean": {}}, (folds, scores), y, pool,
                                    objective, last_fold_only=True)
    stored = _winner_operating_curve("binary", {"candidate_id": "c1"}, (folds, scores), y, last_fold_only=True)
    assert (stored["rows"], stored["oof_folds"], len(stored["folds"])) == (lock["oof_row_count"], "last_fold", 3)
    curve = op.load_curve(stored)
    assert op.solve_lock(curve, objective, None) == lock["value"]
    assert op.fold_spread(curve, curve.index_of(lock["value"]))["includes_folds_outside_curve"] is True
    assert _winner_operating_curve("multiclass", {}, (folds, scores), y, last_fold_only=False) is None
