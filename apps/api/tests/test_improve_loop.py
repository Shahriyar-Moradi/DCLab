"""P5.4-A2: the improve loop's pure parts (ADR 0007 §1, §1b, §3-§5) and the D4c gates 2 and 3.

(12) action-space closure, refused kinds, an F+ that would move the ``__tuned`` variant is skipped,
``k_i`` = the pure generator diff (3 for ``extra_trees`` on an imbalanced root); (13) the proposer
reads only ``PROPOSER_READABLE_CHECKS`` (byte-identical plans under mutation of the five test-partition
checks); (14) the paired rule; (15) the null simulation at rho = 1/K with best-of-k selection, and a
reseed smoke test on a real dataset; (16) goal validation, ``goal_met`` needs a feasible incumbent, a
probability-only shift under roc_auc is never accepted; (18) labels; gate 2 (the final run's lock
maximises the goal metric) and gate 3 (``nondeterministic_rerun`` is never retried); reserved
idempotency prefixes; ``labs.improve`` jobs never count toward the run capacity.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from app.domain.errors import ImproveRunError
from app.domain.findings import FINDING_CHECKS
from app.domain.idempotency import is_reserved_idempotency_key
from app.domain.improve import ImproveGoal, sealed_set_label
from app.engine.evaluation.comparability import evidence_from_records
from app.engine.evaluation.paired_verdict import ALPHA, FoldScore, nb_factor, t_quantile
from app.engine.investigate.checks import TEST_PARTITION_CHECKS
from app.engine.modeling.objective import MetricConstraint, Objective, select_decision_threshold
from app.engine.modeling.operating_points import PointObjective, build_operating_curve, load_curve, solve, solve_lock
from app.services.experiment_branch_service import ParentContext
from app.services.improve.action_space import (
    ACTION_CHANGE_KINDS,
    REFUSED_CHANGE_KINDS,
    ActionRefused,
    candidate_families,
    check_action,
    closure_ok,
)
from app.services.improve.comparison import goal_met, judge_try, paired_try
from app.services.improve.rule_proposer import (
    PROPOSER_READABLE_CHECKS,
    is_blocking,
    is_unstable,
    next_try,
    plan_tries,
)
from app.services.improve.service import generator_ids, parse_goal

IMBALANCED_PLAN = {"problem_profile": {
    "task_type": "binary", "target": "y", "row_count": 400, "feature_count": 3,
    "class_distribution": {"0": 320, "1": 80}, "minority_class_fraction": 0.2, "imbalance_ratio": 4.0}}
ROOT_CONFIG = {"strategy": "open_ingest", "max_candidates": 8, "max_hyperparameter_trials": 12, "seed": 42,
               "max_training_seconds": 600.0}


def _ctx(**fields) -> ParentContext:
    defaults = dict(
        experiment=SimpleNamespace(result={"split": {"n_train": 320}}), upload=None, workflow_run=None,
        task_type="binary", target_column="y", overrides={}, objective=None,
        portfolio=["logistic_regression", "random_forest", "xgboost", "lightgbm", "catboost"],
        dataset_columns={"y", "x"}, reserved_columns={"y"}, leakage_excluded=set(), identifiers=set(),
        dropped=set(), numeric={"x"}, categorical=set(), datetime_converted=set(), numeric_dtype={"x"},
        observed_classes=["0", "1"],
    )
    return ParentContext(**{**defaults, **fields})


def _start(config=ROOT_CONFIG, plan=IMBALANCED_PLAN):
    return SimpleNamespace(config=dict(config), result={"model_development_plan": plan})


# --- (12) action space and k_i ------------------------------------------------------------------


def test_action_space_closure_and_refused_rows():
    assert closure_ok() and ACTION_CHANGE_KINDS == {"family_include"} and len(REFUSED_CHANGE_KINDS) == 7
    check_action({"kind": "family_include", "family": "extra_trees"})
    for kind in REFUSED_CHANGE_KINDS:
        with pytest.raises(ActionRefused) as refused:
            check_action({"kind": kind, "family": "x"})
        assert refused.value.code.startswith("refused_in_v1:")
    for bad in ({"kind": "dataset_change"}, {"kind": "family_include"},
                {"kind": "family_include", "family": "extra_trees", "extra": 1}):
        with pytest.raises(ActionRefused):
            check_action(bad)
    installed = ["majority", "logistic_regression", "random_forest", "extra_trees", "gradient_boosting", "xgboost"]
    assert candidate_families("binary", ["logistic_regression", "random_forest"], installed) == [
        "xgboost", "extra_trees", "gradient_boosting"]


def test_k_i_is_the_pure_generator_diff_and_a_moved_tuned_variant_is_skipped():
    start, ctx = _start(), _ctx()
    root = generator_ids(start, ctx, None)
    assert "lightgbm__tuned" in root and "catboost__balanced" not in root and "xgboost__balanced" not in root
    probe = generator_ids(start, ctx, {"kind": "family_include", "family": "extra_trees"})
    # A branch lifts the cap: extra_trees plus the two capped *__balanced variants (ADR 0007 §1).
    assert sorted(set(probe) - set(root)) == ["catboost__balanced", "extra_trees", "xgboost__balanced"]
    plan = plan_tries(task_type="binary", constrained=False, families=["extra_trees", "gradient_boosting"],
                      start_trained_ids=root, max_iterations=6,
                      probe_ids=lambda f: generator_ids(start, ctx, {"kind": "family_include", "family": f}))
    assert [(item.family, item.k) for item in plan.tries] == [("extra_trees", 3), ("gradient_boosting", 3)]
    assert plan.planned_comparisons == 6 and plan.tries[0].change == {"kind": "family_include", "family": "extra_trees"}
    # A run whose families subset left the boosters out tunes random_forest; including lightgbm would
    # move the __tuned variant to lightgbm, so that probe is not a strict superset: skipped.
    small = _start({**ROOT_CONFIG, "families": ["logistic_regression", "random_forest"]})
    small_ctx = _ctx(portfolio=["logistic_regression", "random_forest"])
    small_root = generator_ids(small, small_ctx, None)
    assert "random_forest__tuned" in small_root
    families = candidate_families("binary", small_ctx.portfolio, ["xgboost", "lightgbm", "extra_trees"])
    moved = plan_tries(task_type="binary", constrained=True, families=families, start_trained_ids=small_root,
                       max_iterations=1,
                       probe_ids=lambda f: generator_ids(small, small_ctx, {"kind": "family_include", "family": f}))
    assert moved.tries[0].kind == "operating_point" and moved.tries[0].k == 0
    assert dict(moved.skipped)["xgboost"] == "would_drop_or_move_a_trained_candidate"
    assert dict(moved.skipped)["lightgbm"] == "would_drop_or_move_a_trained_candidate"
    assert [item.family for item in moved.retraining_tries] == ["extra_trees"]
    assert generator_ids(start, ctx, {"kind": "family_include", "family": "random_forest"}) is None  # already in


def test_next_try_retries_a_failed_try_once():
    plan = plan_tries(task_type="binary", constrained=False, families=["a", "b"], start_trained_ids=["x"],
                      max_iterations=6, probe_ids=lambda f: ["x", f])
    tries = plan.tries
    assert next_try(tries, []) is tries[0]
    assert next_try(tries, [(0, "failed")]) is tries[0]
    assert next_try(tries, [(0, "failed"), (0, "failed")]) == "failed_twice"
    assert next_try(tries, [(0, "rejected")]) is tries[1]
    assert next_try(tries, [(0, "rejected"), (1, "not_comparable")]) is None


# --- (13) the proposer reads only PROPOSER_READABLE_CHECKS -----------------------------------------


def test_proposer_never_reads_the_test_partition_checks():
    assert len(PROPOSER_READABLE_CHECKS) == 10 and not PROPOSER_READABLE_CHECKS & TEST_PARTITION_CHECKS
    assert PROPOSER_READABLE_CHECKS | TEST_PARTITION_CHECKS == set(FINDING_CHECKS)
    base = [{"check": "fold_instability", "status": "warning", "evidence": {"worst_fold_at_baseline": False}},
            {"check": "class_imbalance", "status": "warning", "evidence": {}}]

    def plan(findings):
        return plan_tries(task_type="binary", constrained=True, families=["extra_trees"], start_trained_ids=["x"],
                          max_iterations=6, probe_ids=lambda f: ["x", f], findings=findings).to_json()

    reference = plan(base)
    for status in ("pass", "warning", "fail", "not_evaluated"):
        mutated = base + [{"check": check, "status": status, "evidence": {"worst_fold_at_baseline": True,
                                                                            "psi": 9.9}}
                          for check in sorted(TEST_PARTITION_CHECKS)]
        assert plan(mutated) == reference
        assert not is_blocking(mutated) and not is_unstable(mutated)
    assert is_blocking(base + [{"check": "target_leakage", "status": "fail"}])
    assert is_unstable([{"check": "fold_instability", "status": "warning", "evidence": {"worst_fold_at_baseline": True}}])


# --- (14) the paired rule --------------------------------------------------------------------------


def _scores(values, rows=None) -> dict[int, FoldScore]:
    return {n: FoldScore(v, rows) for n, v in enumerate(values, start=1)}


def test_the_paired_rule():
    inc = [0.70, 0.72, 0.69, 0.71, 0.73]
    # Paired by fold NUMBER, not position: the same per-fold gains in a shuffled dict are the same verdict.
    gain = [0.06, 0.05, 0.07, 0.06, 0.055]
    probe = {n: FoldScore(inc[n - 1] + gain[n - 1]) for n in (3, 1, 5, 2, 4)}
    status, reason, result = paired_try("roc_auc", probe, _scores(inc), comparisons=6, suspicious=False)
    d = np.array(gain)
    se = float(np.std(d, ddof=1)) * math.sqrt(1 / 5 + 1 / 4)
    assert (status, reason) == ("accepted", None)
    assert result.standard_error == pytest.approx(se) and result.margin == pytest.approx(t_quantile(5, 6) * se)
    assert [f.fold_number for f in result.paired] == [1, 2, 3, 4, 5] and nb_factor(5) == pytest.approx(0.6708, abs=1e-4)
    # rejected_suspicious: a try failing its own leakage/implausible check never wins.
    assert paired_try("roc_auc", probe, _scores(inc), comparisons=6, suspicious=True)[:2] == (
        "rejected", "rejected_suspicious")
    # Lower is better: a log-loss DROP is the gain.
    loss = [0.50, 0.52, 0.49, 0.51, 0.53]
    better = _scores([v - g for v, g in zip(loss, gain)])
    assert paired_try("log_loss", better, _scores(loss), comparisons=6, suspicious=False)[0] == "accepted"
    assert paired_try("log_loss", _scores(loss), better, comparisons=6, suspicious=False)[:2] == ("rejected", "worse")
    # K < 5 is refused; d == 0 -> rejected no_change (counts toward patience); constant d -> not comparable.
    assert paired_try("roc_auc", _scores(inc[:4]), _scores(inc[:4]), comparisons=1, suspicious=False)[:2] == (
        "not_comparable", "too_few_folds")
    assert paired_try("roc_auc", _scores(inc), _scores(inc), comparisons=6, suspicious=False)[:2] == (
        "rejected", "no_change")
    assert paired_try("roc_auc", _scores([v + 0.01 for v in inc]), _scores(inc), comparisons=6,
                      suspicious=False)[:2] == ("not_comparable", "constant_difference")
    small = [0.004, 0.001, 0.006, 0.003, 0.0015]  # mean / se_NB = 2.3: beats t(1-0.05, 4) = 2.13, not t(1-0.05/6, 4)
    assert paired_try("roc_auc", _scores([v + g for v, g in zip(inc, small)]), _scores(inc), comparisons=1,
                      suspicious=False)[0] == "accepted"
    assert paired_try("roc_auc", _scores([v + g for v, g in zip(inc, small)]), _scores(inc), comparisons=6,
                      suspicious=False)[0] == "rejected"  # the loop's T widens the margin


def _oof(signal, positives=60, rows=200, folds=5, seed=0):
    rng = np.random.default_rng(seed)
    stored = []
    for _ in range(folds):
        y = np.array([1] * positives + [0] * (rows - positives))
        stored.append((y, 1 / (1 + np.exp(-(signal * (y - 0.5) + rng.normal(0, 1, len(y)))))))
    return np.concatenate([y for y, _ in stored]), np.concatenate([s for _, s in stored]), stored


def _curve(signal, positives=60, rows=200, folds=5, seed=0, candidate="rf"):
    y, scores, stored = _oof(signal, positives, rows, folds, seed)
    return build_operating_curve(y, scores, stored, oof_folds="all_folds", candidate_id=candidate)


def _evidence(keys, winner):
    folds = [{"fold_number": n, "metrics": {"roc_auc": 0.7}} for n in range(1, 6)]
    return evidence_from_records([{"candidate_id": key, "model_family": key, "fingerprint": key * 3,
                                   "status": "trained", "folds": folds} for key in keys], winner)


def test_constrained_try_flagged_denominators_infeasible_shortfall_and_unplanned_candidates():
    goal = ImproveGoal.model_validate({"objective": {"maximize": "precision",
                                                     "constraints": [{"metric": "recall", "op": ">=", "value": 0.6}]}})
    inc, probe = _evidence(["rf"], "rf"), _evidence(["rf", "et"], "et")
    common = dict(goal=goal, metric="pr_auc", incumbent=inc, probe=probe, incumbent_scores=None, probe_scores=None,
                  planned_candidate_ids=["et"], comparisons=1, suspicious=False)
    weak, strong = _curve(1.0, candidate="rf"), _curve(3.0, seed=1, candidate="et")
    verdict = judge_try(**common, incumbent_curve=weak, probe_curve=strong)
    assert verdict.status == "accepted" and verdict.comparison["scored_by"] == "goal_metric"
    # delta_min from the flagged rows behind precision on the smallest fold: max(0.001, 1/n_min).
    assert verdict.comparison["minimum_effect_basis"] == "fold_denominator"
    assert verdict.comparison["minimum_effect"] >= 1 / 200 and verdict.comparison["fold_counts"] == [[60, 140]] * 5
    # An unreachable rule: both scored by the per-fold shortfall; a try missing the rule never beats one meeting it.
    strict = ImproveGoal.model_validate({"objective": {"maximize": "precision", "constraints": [
        {"metric": "recall", "op": ">=", "value": 0.99}, {"metric": "precision", "op": ">=", "value": 0.99}]}})
    shortfall = judge_try(**{**common, "goal": strict}, incumbent_curve=weak, probe_curve=strong)
    assert shortfall.comparison["scored_by"] == "constraint_shortfall"
    # A curve not naming the locked winner is never read (experiments.result stays writable).
    assert judge_try(**common, incumbent_curve=weak, probe_curve=_curve(3.0, candidate="other")).reason == "no_goal_curve"
    # Gate 3: a probe that trained candidates the plan did not count in T is not comparable.
    assert judge_try(**{**common, "planned_candidate_ids": []}, incumbent_curve=weak,
                     probe_curve=strong).reason == "unplanned_candidates"
    # Comparability first: an incumbent candidate missing from the probe.
    assert judge_try(**{**common, "probe": _evidence(["et"], "et")}, incumbent_curve=weak,
                     probe_curve=strong).reason == "superset_violated"


# --- (15) null simulation and the reseed smoke test -------------------------------------------------


def _wilson_upper(hits: int, n: int, z: float = 1.959963984540054) -> float:
    p = hits / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre + spread) / (1 + z * z / n)


def test_null_simulation_rho_one_over_k_with_best_of_k_selection():
    """K = 5, T = 6 (two tries adding 3 candidates each). Each new candidate's paired fold deltas
    against the incumbent are N(0, Sigma) with fold correlation rho = 1/K (the Nadeau-Bengio model the
    rule assumes); the probe's winner is the best of the incumbent and its k new candidates by CV mean
    (the incumbent staying the winner is d == 0, ``no_change``). The NB-corrected t is calibrated at
    rho = 1/K, so each comparison accepts at about alpha/T and the loop at about the Bonferroni bound:
    20 000 loops (more than the ADR's 2 000, whose Wilson bound cannot sit below 5 % at a true rate near
    4.5 %) keep the per-loop upper bound under alpha."""

    rng = np.random.default_rng(20261017)
    k_folds, tries, sigma, base = 5, (3, 3), 0.02, 0.70
    planned = sum(tries)
    rho = 1.0 / k_folds
    chol = np.linalg.cholesky((1 - rho) * np.eye(k_folds) + rho * np.ones((k_folds, k_folds)))
    incumbent = _scores([base] * k_folds)
    loops, accepted, comparisons, loops_hit = 20_000, 0, 0, 0
    for _ in range(loops):
        hit = False
        for k in tries:
            deltas = rng.standard_normal((k, k_folds)) @ chol.T * sigma
            comparisons += k
            best = deltas[int(np.argmax(deltas.mean(axis=1)))]
            if best.mean() <= 0:
                continue  # the old winner stays: no_change
            status, _reason, _result = paired_try("roc_auc", _scores(base + best), incumbent, comparisons=planned,
                                                  suspicious=False)
            if status == "accepted":
                accepted, hit = accepted + 1, True
        loops_hit += hit
    per_comparison = accepted / comparisons
    assert 0.75 * ALPHA / planned <= per_comparison <= 1.1 * ALPHA / planned, per_comparison
    # A true rate near 4.5 % leaves the Wilson bound ~1.5 SE of headroom (fixed seed: deterministic).
    assert loops_hit / loops <= ALPHA and _wilson_upper(loops_hit, loops) <= ALPHA, loops_hit / loops


def test_reseed_null_smoke_test_on_a_real_dataset():
    """SMOKE TEST (rho ~ 0, ADR 0007 §10a): probes that differ from the incumbent only by the seed, on
    a real dataset (scikit-learn's bundled breast-cancer table), 20 loops: none is accepted."""

    from sklearn.datasets import load_breast_cancer
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedKFold

    x, y = load_breast_cancer(return_X_y=True)
    acceptances = 0
    for loop in range(20):
        folds = list(StratifiedKFold(5, shuffle=True, random_state=loop).split(x, y))

        def fold_scores(seed: int) -> dict[int, FoldScore]:
            out = {}
            for number, (train, valid) in enumerate(folds, start=1):
                model = RandomForestClassifier(n_estimators=25, max_depth=4, random_state=seed).fit(x[train], y[train])
                out[number] = FoldScore(float(roc_auc_score(y[valid], model.predict_proba(x[valid])[:, 1])))
            return out

        incumbent, reseeded = fold_scores(1000 + loop), fold_scores(2000 + loop)
        winner = reseeded if sum(s.value for s in reseeded.values()) > sum(s.value for s in incumbent.values()) \
            else incumbent
        status, _reason, _ = paired_try("roc_auc", winner, incumbent, comparisons=1, suspicious=False)
        acceptances += status == "accepted"
    assert acceptances == 0


# --- (16) goals --------------------------------------------------------------------------------------


def test_goal_validation_is_422_and_goal_met_needs_a_feasible_incumbent():
    ok = parse_goal({"plain": {"metric": "pr_auc"}}, task_type="binary", selection_metric="pr_auc")
    assert ok.metric == "pr_auc" and not ok.constrained
    cases = [
        ({"plain": {"metric": "roc_auc"}}, "goal_not_primary_metric"),
        ({"plain": {"metric": "f1"}}, "goal_needs_constraint"),
        ({"objective": {"maximize": "precision"}}, "goal_needs_constraint"),
        ({"objective": {"maximize": "precision", "constraints": [{"metric": "flagged_share", "op": "<=",
                                                                  "value": 0.2}]}}, "goal_constraint_unsupported"),
        ({"objective": {"maximize": "flagged_share", "constraints": [{"metric": "recall", "op": ">=",
                                                                      "value": 0.5}]}}, "invalid_goal_metric"),
        ({"plain": {"metric": "pr_auc"}, "objective": {"maximize": "f1"}}, "invalid_goal"),
        ({"plain": {"metric": "pr_auc"}, "target": {"metric": "roc_auc", "value": 0.8}}, "invalid_goal_target"),
    ]
    for raw, code in cases:
        with pytest.raises(ImproveRunError) as refused:
            parse_goal(raw, task_type="binary", selection_metric="pr_auc")
        assert (refused.value.code, refused.value.status_code) == (code, 422), raw
    with pytest.raises(ImproveRunError) as multi:
        parse_goal({"objective": {"maximize": "f1"}}, task_type="multiclass", selection_metric="macro_f1")
    assert multi.value.code == "goal_objective_binary_only"
    target = ImproveGoal.model_validate({"plain": {"metric": "pr_auc"}, "target": {"metric": "pr_auc", "value": 0.6}})
    assert goal_met(target, [0.70, 0.72, 0.69, 0.71, 0.73], feasible=None)
    assert not goal_met(target, [0.55, 0.75, 0.50, 0.80, 0.62], feasible=None)  # noisy: the bound misses 0.6
    constrained = ImproveGoal.model_validate({"objective": {"maximize": "precision", "constraints": [
        {"metric": "recall", "op": ">=", "value": 0.5}]}, "target": {"metric": "precision", "value": 0.5}})
    assert goal_met(constrained, [0.8] * 4 + [0.81], feasible=True)
    assert not goal_met(constrained, [0.8] * 4 + [0.81], feasible=False)  # met on CV needs a feasible incumbent
    error = ImproveGoal.model_validate({"plain": {"metric": "rmse"}, "target": {"metric": "rmse", "value": 1.0}})
    assert goal_met(error, [0.50, 0.52, 0.49, 0.51, 0.53], feasible=None)
    assert not goal_met(error, [1.50, 1.52, 1.49, 1.51, 1.53], feasible=None)


def test_a_probability_only_shift_is_never_accepted_under_rank_metrics():
    from sklearn.metrics import average_precision_score, roc_auc_score

    rng = np.random.default_rng(3)
    folds = [(rng.integers(0, 2, 120), rng.random(120)) for _ in range(5)]
    for metric, score in (("roc_auc", roc_auc_score), ("pr_auc", average_precision_score)):
        inc = {n: FoldScore(float(score(y, s))) for n, (y, s) in enumerate(folds, start=1)}
        # A class-weight-like re-calibration: a monotone transform moves every probability, no ranking.
        shifted = {n: FoldScore(float(score(y, 1 / (1 + np.exp(-3 * (s - 0.3)))))) for n, (y, s) in
                   enumerate(folds, start=1)}
        assert paired_try(metric, shifted, inc, comparisons=1, suspicious=False)[:2] == ("rejected", "no_change")


# --- (18) labels, gate 2, gate 3, prefixes, capacity ------------------------------------------------


def test_sealed_set_labels():
    assert sealed_set_label(0) == "clean" and sealed_set_label(1) == "reported_before"
    assert sealed_set_label(3) == "reused" and sealed_set_label(0, fresh_split=True, carried_over=True) == "carried_over"


def test_gate2_the_final_runs_lock_maximises_the_goal_metric_under_its_constraints():
    y, scores, folds = _oof(1.2, seed=5)
    curve = load_curve(build_operating_curve(y, scores, folds, oof_folds="all_folds", candidate_id="rf"))
    constraints = (MetricConstraint("recall", ">=", 0.7),)
    goal_index = solve(curve, PointObjective(goal="precision", constraints=constraints))["index"]
    objective = Objective(constraints=list(constraints))
    assert solve_lock(curve, objective, "pr_auc", "precision") == float(curve.thresholds[goal_index])
    # Without the goal the lock maximises F1 under the same constraint: a different operating point here.
    assert solve_lock(curve, objective, "pr_auc") != float(curve.thresholds[goal_index])
    decision = select_decision_threshold(y, scores, objective, primary_metric="pr_auc", goal_metric="precision")
    assert decision["source"] == "improve_goal" and decision["goal_metric"] == "precision"
    assert decision["value"] == float(curve.thresholds[goal_index])


def test_gate3_a_nondeterministic_rerun_is_failed_not_retried(db_session, engineer_user):
    from app.db.models import DEFAULT_WORKSPACE_ID, ClientLabUpload, MlJob
    from app.services.ml_job_service import claim_next_queued_job, create_auto_train_job, execute_job

    db = db_session
    outcomes = {}
    for reason in ("nondeterministic_rerun: winner 'a' instead of 'b'", "something transient"):
        upload = ClientLabUpload(workspace_id=DEFAULT_WORKSPACE_ID, requested_by=engineer_user.id, category="x",
                                 original_filename="x.csv", stored_path="x.csv", kind="csv", pipeline_status="failed",
                                 client_status="failed", pipeline_log={"reason": reason, "failed_at": "training"})
        db.add(upload)
        db.flush()
        job = create_auto_train_job(db, workspace_id=DEFAULT_WORKSPACE_ID, upload_id=upload.id, max_attempts=3)
        db.commit()
        claimed = claim_next_queued_job(db, job_id=job.id)
        db.commit()
        finished = execute_job(db, claimed, runner=lambda _db, _target: None)
        outcomes[reason[:8]] = (finished.status, finished.attempts)
        if finished.status == "queued":  # the transient one: back in the queue; finish it off
            db.delete(db.get(MlJob, finished.id))
            db.commit()
    assert outcomes["nondeter"] == ("failed", 1)
    assert outcomes["somethin"] == ("queued", 1)


def test_reserved_prefixes_and_improve_jobs_never_count_toward_run_capacity(db_session, engineer_client, monkeypatch):
    from app.db.models import DEFAULT_WORKSPACE_ID
    from app.domain.ml_jobs import HANDLER_LABS_IMPROVE, JOB_TYPE_IMPROVE
    from app.services.ml_job_service import create_ml_job, ensure_run_capacity
    from uuid import uuid4

    for key in ("improve:1:2", "improve-final:1:1", "improve:x:accept"):
        assert is_reserved_idempotency_key(key)
        response = engineer_client.post("/v1/projects", json={"name": "p"}, headers={"Idempotency-Key": key})
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_idempotency_key", key
    db = db_session
    for _ in range(3):
        create_ml_job(db, workspace_id=DEFAULT_WORKSPACE_ID, job_type=JOB_TYPE_IMPROVE, handler_key=HANDLER_LABS_IMPROVE,
                      target_id=uuid4(), payload={"improve_run_id": str(uuid4())})
    db.commit()
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ml_max_active_runs_per_workspace", 1)
    ensure_run_capacity(db, DEFAULT_WORKSPACE_ID, resume=True)  # a loop never blocks its own probe
    db.rollback()
