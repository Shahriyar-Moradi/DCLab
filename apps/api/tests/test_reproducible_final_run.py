"""P5.4-A1b: reproducible final run, comparability and per-fold goal scores (ADR 0007 §5, §6.2).

Tests (8)-(11) of the ADR table: (8) a pinned re-run that does not reproduce its probe aborts at the
winner lock, before the final test (spy), and one that does reproduces its selection within 1e-9;
(9) a manual branch of a final run inherits none of the service-only fields and no /v1 schema accepts
them; (10) a binding time cap, truncated tuning, a moved ``__tuned`` variant and a changed shared
fingerprint are each ``not_comparable`` with their reason; (11) ``goal_fold_scores`` refuses mismatched
per-fold counts and folds below ``FOLD_MIN_DENOMINATOR``.
"""

from __future__ import annotations

import math
from dataclasses import replace
from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy import select

from app.db.models import (
    ClientLabUpload,
    Experiment,
    ExperimentCandidate,
    MlRunEvent,
    ModelEvaluation,
    ModelSelectionDecision,
)
from app.domain.errors import ExperimentNotBranchableError
from app.domain.pipeline_run_branch import (
    SERVICE_ONLY_CONFIG_KEYS,
    SERVICE_OVERRIDES_CONFIG_KEY,
    ServiceOverrides,
    strip_service_only,
)
from app.engine.evaluation.comparability import (
    FINAL_RUN_BUDGET_FLOOR_SECONDS,
    CandidateEvidence,
    PROBE_BUDGET_MAX_SECONDS,
    RunEvidence,
    comparability,
    evidence_from_records,
    final_run_budget_seconds,
    probe_budget_seconds,
    realised_training_seconds,
    selection_mismatch,
)
from app.engine.modeling.objective import MetricConstraint
from app.engine.modeling.operating_points import (
    FOLD_MIN_DENOMINATOR,
    GoalScoreRefusal,
    PointObjective,
    build_operating_curve,
    curve_digest,
    goal_fold_scores,
    load_curve,
    solve,
)
from app.services.auto_train_service import run_auto_train_job
from app.services.experiment_branch_service import branch_experiment
from app.services.experiment_verdict_service import locked_run_evidence, plan_looks
from app.services.lab_service import search_from_mapping
from test_experiment_branching import _parent


def _record(key, family, fingerprint, scores, *, status="trained", trials=None, truncated=False, seconds=2.0):
    folds = [{"fold_number": n, "metrics": {"roc_auc": value, "confusion_matrix": {"tp": 1}},
              "tuning": None if trials is None else {"trials_completed": trials, "truncated": truncated}}
             for n, value in enumerate(scores, start=1)]
    return {"candidate_id": key, "model_family": family, "fingerprint": fingerprint, "status": status,
            "folds": folds if status == "trained" else [], "train_seconds": seconds}


BASE = [0.70, 0.72, 0.69, 0.71, 0.73]
INCUMBENT = [
    _record("majority", "majority", "a" * 40, [0.5] * 5, seconds=0.1),
    _record("random_forest", "random_forest", "b" * 40, BASE),
    _record("random_forest__tuned", "random_forest", "c" * 40, [v + 0.01 for v in BASE], trials=12, seconds=8.0),
]
NEW = _record("extra_trees", "extra_trees", "d" * 40, [v - 0.02 for v in BASE], seconds=3.0)


def _evidence(records, winner="random_forest__tuned") -> RunEvidence:
    return evidence_from_records(records, winner)


def test_comparability_reasons_and_precedence():
    incumbent = _evidence(INCUMBENT)
    probe = _evidence([*INCUMBENT, NEW])
    ok = comparability(incumbent, probe)
    assert (ok.comparable, ok.reason, ok.new_candidates) == (True, None, ("extra_trees",))
    # (10) a binding time cap skips a candidate; truncated tuning; a moved __tuned variant; a changed fingerprint.
    capped = _evidence([*INCUMBENT, {**NEW, "status": "skipped"}])
    assert comparability(incumbent, capped).reason == "truncated"
    cut = _evidence([*INCUMBENT[:2], _record("random_forest__tuned", "random_forest", "c" * 40,
                                             [v + 0.01 for v in BASE], trials=7, truncated=True), NEW])
    assert comparability(incumbent, cut).reason == "truncated"
    moved = _evidence([*INCUMBENT[:2], NEW, _record("extra_trees__tuned", "extra_trees", "e" * 40, BASE, trials=12)])
    result = comparability(incumbent, moved)
    assert (result.reason, result.comparable) == ("superset_violated", False)
    assert "random_forest__tuned" in result.detail
    drift = _evidence([INCUMBENT[0], {**INCUMBENT[1], "fingerprint": "f" * 40}, INCUMBENT[2], NEW])
    assert comparability(incumbent, drift).reason == "engine_or_data_drift"
    nudged = [v + 1e-6 if n == 2 else v for n, v in enumerate(BASE)]
    assert comparability(incumbent, _evidence([INCUMBENT[0], _record("random_forest", "random_forest", "b" * 40,
                                                                      nudged), INCUMBENT[2], NEW])).reason == \
        "engine_or_data_drift"
    tiny = [v + 1e-12 for v in BASE]
    assert comparability(incumbent, _evidence([INCUMBENT[0], _record("random_forest", "random_forest", "b" * 40, tiny),
                                               INCUMBENT[2], NEW])).comparable
    fewer_trials = _evidence([*INCUMBENT[:2], _record("random_forest__tuned", "random_forest", "c" * 40,
                                                      [v + 0.01 for v in BASE], trials=11), NEW])
    assert comparability(incumbent, fewer_trials).reason == "engine_or_data_drift"
    same = comparability(incumbent, _evidence(INCUMBENT))
    assert (same.reason, same.detail) == ("superset_violated", "no_new_trained_candidate")
    # Precedence: a time skip outranks a fingerprint change; the incumbent's own truncation counts too.
    both = _evidence([INCUMBENT[0], {**INCUMBENT[1], "fingerprint": "f" * 40}, INCUMBENT[2], {**NEW, "status": "skipped"}])
    assert comparability(incumbent, both).reason == "truncated"
    assert comparability(cut, probe).reason == "truncated"


def test_selection_mismatch_and_the_pinned_record_round_trip():
    expected = _evidence([*INCUMBENT, NEW])
    assert selection_mismatch(expected, _evidence([*INCUMBENT, NEW])) is None
    assert "winner" in selection_mismatch(expected, _evidence([*INCUMBENT, NEW], winner="random_forest"))
    assert "skipped" in selection_mismatch(expected, _evidence([*INCUMBENT, {**NEW, "status": "skipped"}]))
    assert "trained candidates differ" in selection_mismatch(expected, _evidence(INCUMBENT))
    nudged = _record("random_forest", "random_forest", "b" * 40, [BASE[0] + 1e-6, *BASE[1:]])
    assert "fold 1 roc_auc" in selection_mismatch(expected, _evidence([INCUMBENT[0], nudged, INCUMBENT[2], NEW]))
    # A non-finite in-memory metric is dropped, as persistence drops it (the locked rows never hold one).
    nan = _record("random_forest", "random_forest", "b" * 40, [math.nan, *BASE[1:]])
    with_nan = _evidence([INCUMBENT[0], nan, INCUMBENT[2], NEW])
    assert "roc_auc" not in with_nan.trained["random_forest"].folds[1]
    assert "fold 1 records different metrics" in selection_mismatch(expected, with_nan)
    # One row per scientific configuration, as persistence keeps it.
    twin = {**NEW, "candidate_id": "extra_trees_copy"}
    assert set(_evidence([*INCUMBENT, NEW, twin]).trained) == set(expected.trained)
    # The service-only record survives JSON (NaN -> null -> NaN) and is validated.
    pinned = RunEvidence(winner="random_forest", trained={**expected.trained, "random_forest": CandidateEvidence(
        "b" * 40, {1: {"roc_auc": math.nan, "f1": 0.5}})})
    overrides = ServiceOverrides(expected_selection=pinned, max_training_seconds=90.0,
                                 objective_constraints=(("recall", ">=", 0.8),))
    record = overrides.config_record()
    assert record["expected_selection"]["trained"]["random_forest"]["folds"]["1"]["roc_auc"] is None
    restored = ServiceOverrides.from_config({SERVICE_OVERRIDES_CONFIG_KEY: record})
    assert selection_mismatch(pinned, restored.expected_selection) is None
    assert restored.max_training_seconds == 90.0 and restored.objective_constraints == (("recall", ">=", 0.8),)
    assert restored.objective({"primary_metric": "pr_auc", "constraints": [{"metric": "f1", "op": ">=",
                                                                              "value": 0.1}]}) == {
        "primary_metric": "pr_auc", "constraints": [{"metric": "recall", "op": ">=", "value": 0.8}]}
    for bad in (dict(evidence_mode="probe"), dict(evidence_mode="sealed", expected_selection=expected),
                dict(max_training_seconds=0), dict(max_training_seconds=math.inf), dict(max_training_seconds=43_201),
                dict(objective_constraints=(("pr_auc", ">=", 0.5),)), dict(objective_constraints=(("recall", "=", 0.5),)),
                dict(expected_selection={"winner": "x"})):
        with pytest.raises(ValueError):
            ServiceOverrides(**bad)
    assert ServiceOverrides.from_config({"objective": {}}) is None


def test_service_only_keys_never_reach_a_search_config():
    raw = {"max_training_seconds": 60.0, "seed": 7, "objective": {"primary_metric": "pr_auc"},
           SERVICE_OVERRIDES_CONFIG_KEY: {"max_training_seconds": 1.0}, "score_holdout": False,
           "evidence_mode": "scored", "expected_selection": {"winner": "x"}}
    config = search_from_mapping(raw, overrides={"score_holdout": True, SERVICE_OVERRIDES_CONFIG_KEY: {}})
    assert (config.max_training_seconds, config.seed) == (60.0, 7)
    assert not set(config.to_dict()) & SERVICE_ONLY_CONFIG_KEYS
    assert set(strip_service_only(raw)) == {"max_training_seconds", "seed", "objective"}


def test_time_budget_helpers():
    start = _evidence(INCUMBENT)
    assert realised_training_seconds(start) == pytest.approx(10.1)
    # 1.5 x the realised 10.1 s; learned candidates average 5 s, so each new one is max(30, 2 x 5) = 30 s.
    assert probe_budget_seconds(start, 0) == pytest.approx(15.15)
    assert probe_budget_seconds(start, 2) == pytest.approx(75.15)
    assert probe_budget_seconds(start, 1000) is None  # cannot fit the cap: never a sure-to-truncate budget
    slow = _evidence([*INCUMBENT[:2], {**INCUMBENT[2], "train_seconds": 400.0}])
    assert probe_budget_seconds(slow, 1) == pytest.approx(1.5 * 402.1 + 2 * 201.0)
    too_slow = _evidence([*INCUMBENT[:2], {**INCUMBENT[2], "train_seconds": PROBE_BUDGET_MAX_SECONDS / 1.4}])
    assert probe_budget_seconds(too_slow, 0) is None
    assert final_run_budget_seconds(start) == FINAL_RUN_BUDGET_FLOOR_SECONDS
    assert final_run_budget_seconds(slow) == pytest.approx(3 * 402.1)
    with pytest.raises(ValueError):
        probe_budget_seconds(start, -1)


def _curve(positives_per_fold=60, rows_per_fold=200, folds=5, seed=0, signal=1.5):
    rng = np.random.default_rng(seed)
    labels, scores, stored = [], [], []
    for _ in range(folds):
        y = np.array([1] * positives_per_fold + [0] * (rows_per_fold - positives_per_fold))
        s = 1 / (1 + np.exp(-(signal * (y - 0.5) + rng.normal(0, 1, len(y)))))
        labels.append(y)
        scores.append(s)
        stored.append((y, s))
    return build_operating_curve(np.concatenate(labels), np.concatenate(scores), stored,
                                 oof_folds="all_folds", candidate_id="rf")


def test_goal_fold_scores_per_fold_counts_and_min_denominator():
    stored = _curve()
    curve = load_curve(stored)
    objective = PointObjective(goal="precision", constraints=(MetricConstraint("recall", ">=", 0.6),))
    out = goal_fold_scores(curve, objective)
    index = solve(curve, objective)["index"]
    assert out.index == index and out.threshold == float(curve.thresholds[index]) and out.feasible
    assert out.fold_counts == ((60, 140),) * 5
    for (pos, _neg, tp, fp), value, denominator, shortfall in zip(curve.folds, out.values, out.denominators,
                                                                   out.shortfalls):
        assert value == pytest.approx(tp[index] / (tp[index] + fp[index]))
        assert denominator == int(tp[index] + fp[index])
        assert shortfall == pytest.approx(max(0.0, 0.6 - tp[index] / pos))
    # (11) a curve built on other folds is refused; so is a fold with too few rows behind a number.
    with pytest.raises(GoalScoreRefusal) as other:
        goal_fold_scores(curve, objective, fold_counts=((61, 139),) + ((60, 140),) * 4)
    assert other.value.reason == "fold_counts_differ"
    with pytest.raises(GoalScoreRefusal) as fewer:
        goal_fold_scores(curve, objective, fold_counts=((60, 140),) * 4)
    assert fewer.value.reason == "fold_counts_differ"
    assert goal_fold_scores(curve, objective, fold_counts=out.fold_counts).values == out.values
    thin = load_curve(_curve(positives_per_fold=FOLD_MIN_DENOMINATOR - 2, rows_per_fold=200))
    with pytest.raises(GoalScoreRefusal) as small:
        goal_fold_scores(thin, PointObjective(goal="specificity", constraints=(MetricConstraint("recall", ">=", 0.5),)))
    assert small.value.reason == "fold_below_min_denominator"  # 8 positives per fold behind the recall rule
    with pytest.raises(GoalScoreRefusal) as cost:
        goal_fold_scores(curve, PointObjective(goal="expected_cost", cost_false_positive=1.0, cost_false_negative=5.0))
    assert cost.value.reason == "goal_not_per_fold"
    # An unreachable rule is still scored at the closest threshold, with the shortfall per fold.
    strict = goal_fold_scores(curve, PointObjective(goal="precision", constraints=(
        MetricConstraint("recall", ">=", 0.95), MetricConstraint("precision", ">=", 0.95))))
    assert not strict.feasible and all(value > 0 for value in strict.shortfalls)
    # The digest anchors a comparison to the exact stored curve.
    assert curve_digest(stored) == curve_digest(dict(stored))
    changed = {**stored, "folds": [{**stored["folds"][0], "positives": 61}, *stored["folds"][1:]]}
    assert curve_digest(changed) != curve_digest(stored)


def _run_branch(db, actor, parent, overrides, changes=None) -> tuple[ClientLabUpload, Experiment]:
    result = branch_experiment(
        db, actor=actor, workspace_id=parent.workspace_id, parent_id=parent.id,
        changes=changes or [{"kind": "family_include", "family": "extra_trees"}], intent="improve try",
        service_overrides=overrides,
    )
    run_auto_train_job(db, result.upload.id)
    db.expire_all()
    return db.get(ClientLabUpload, result.upload.id), db.get(Experiment, result.experiment.id)


def test_pinned_final_run_reproduces_its_probe_and_a_mismatch_aborts_before_the_look(
    engineer_client, db_session, engineer_user, monkeypatch
):
    db = db_session
    parent = _parent(engineer_client, db, engineer_user, monkeypatch)
    _upload, probe = _run_branch(db, engineer_user, parent, ServiceOverrides(evidence_mode="sealed"))
    assert probe.evidence_mode == "sealed" and probe.scientific_evidence_locked_at is not None
    pinned = locked_run_evidence(db, probe.workspace_id, probe.id)
    assert pinned is not None and pinned.winner == probe.result["selection"]["selected_candidate_id"]
    assert locked_run_evidence(db, uuid4(), probe.id) is None  # another workspace reads nothing
    winner = pinned.trained[pinned.winner]
    fold, metric = 1, sorted(winner.folds[1])[0]
    tampered = replace(pinned, trained={**pinned.trained, pinned.winner: replace(winner, folds={
        **winner.folds, fold: {**winner.folds[fold], metric: winner.folds[fold][metric] + 1e-6}})})

    import app.engine.experiments.runner as runner

    real_holdout, calls = runner._fit_and_score_holdout, []

    def spy(*args, **kwargs):
        calls.append(1)
        return real_holdout(*args, **kwargs)

    # (8) A pinned re-run whose selection does not reproduce stops at the winner lock: no final fit,
    # no final_test_started, no selection row (no look is spent).
    with monkeypatch.context() as patch:
        patch.setattr(runner, "_fit_and_score_holdout", spy)
        upload, aborted = _run_branch(db, engineer_user, parent, ServiceOverrides(expected_selection=tampered))
    assert calls == [] and upload.pipeline_status == "failed"
    assert aborted.status == "FAILED" and aborted.failure_reason.startswith("nondeterministic_rerun:")
    assert f"fold {fold} {metric}" in aborted.failure_reason
    events = db.scalars(select(MlRunEvent.event_type).where(MlRunEvent.experiment_id == aborted.id)).all()
    assert "model_selection_completed" in events and "winner_locked" not in events
    assert not [name for name in events if name.startswith("final_")]
    assert db.scalar(select(ModelSelectionDecision.id).where(ModelSelectionDecision.pipeline_run_id == aborted.id)) is None
    looks, _comparisons = plan_looks(db, parent.workspace_id, parent.split_plan_id)
    assert aborted.id not in looks and probe.id not in looks and parent.id in looks

    # (8) The pinned re-run of the probe's change set reproduces its selection within 1e-9 and is
    # scored once; the goal's constraints reach the threshold lock, the pinned budget the runner.
    budget = final_run_budget_seconds(pinned)
    overrides = ServiceOverrides(expected_selection=pinned, max_training_seconds=budget,
                                 objective_constraints=(("recall", ">=", 0.5),))
    with monkeypatch.context() as patch:
        patch.setattr(runner, "_fit_and_score_holdout", spy)
        upload, final = _run_branch(db, engineer_user, parent, overrides)
    assert upload.pipeline_status == "completed", upload.pipeline_log.get("reason")
    assert calls == [1] and final.evidence_mode == "scored" and final.scientific_evidence_locked_at is not None
    assert selection_mismatch(pinned, locked_run_evidence(db, final.workspace_id, final.id)) is None
    assert final.result["pinned_selection"] == {"reproduced": True, "candidate_id": pinned.winner, "tolerance": 1e-9}
    winner_id = db.scalar(select(ExperimentCandidate.id).where(
        ExperimentCandidate.experiment_id == final.id, ExperimentCandidate.candidate_key == pinned.winner))
    assert db.scalar(select(ModelEvaluation.id).where(ModelEvaluation.candidate_id == winner_id,
                                                      ModelEvaluation.evaluation_scope == "final_holdout"))
    assert final.result["objective"]["constraints"] == [{"metric": "recall", "op": ">=", "value": 0.5}]
    assert final.result["config"]["max_training_seconds"] == budget
    # The stored config keeps the ordinary values and the service-only record (never a SearchConfig field).
    assert final.config.get("objective") == probe.config.get("objective")
    assert final.config["max_training_seconds"] != budget
    assert final.config[SERVICE_OVERRIDES_CONFIG_KEY]["expected_selection"]["winner"] == pinned.winner
    looks, _comparisons = plan_looks(db, parent.workspace_id, parent.split_plan_id)
    assert final.id in looks and probe.id not in looks

    # (9) A manual branch of the final run is an ordinary run: nothing service-only is inherited.
    manual = branch_experiment(
        db, actor=engineer_user, workspace_id=final.workspace_id, parent_id=final.id,
        changes=[{"kind": "family_exclude", "family": "extra_trees"}], intent="manual branch",
    ).experiment
    assert manual.evidence_mode == "scored" and ServiceOverrides.from_config(manual.config) is None
    assert not set(manual.config) & SERVICE_ONLY_CONFIG_KEYS
    assert manual.config.get("objective") == final.config.get("objective")
    assert manual.config["max_training_seconds"] == final.config["max_training_seconds"]

    # (10) A binding time cap on the persisted path: the locked rows show the skipped candidates.
    _upload, capped = _run_branch(db, engineer_user, parent, ServiceOverrides(evidence_mode="sealed",
                                                                            max_training_seconds=0.001))
    capped_evidence = locked_run_evidence(db, capped.workspace_id, capped.id)
    assert capped.scientific_evidence_locked_at is not None and capped_evidence.skipped
    parent_evidence = locked_run_evidence(db, parent.workspace_id, parent.id)
    assert comparability(parent_evidence, capped_evidence).reason == "truncated"

    # An idempotency key binds the service-only overrides too: a replay never swaps a probe for a final run.
    keyed = dict(actor=engineer_user, workspace_id=parent.workspace_id, parent_id=parent.id,
                 changes=[{"kind": "family_include", "family": "extra_trees"}], intent="keyed try",
                 idempotency_key="d4b-keyed-try")
    first = branch_experiment(db, **keyed, service_overrides=ServiceOverrides(evidence_mode="sealed"))
    replay = branch_experiment(db, **keyed, service_overrides=ServiceOverrides(evidence_mode="sealed"))
    assert first.created and not replay.created and replay.experiment.id == first.experiment.id
    for other in (ServiceOverrides(), None):
        with pytest.raises(ExperimentNotBranchableError) as conflict:
            branch_experiment(db, **keyed, service_overrides=other)
        assert conflict.value.code == "idempotency_key_conflict"

    # (9) No /v1 schema accepts a service-only field.
    from app.main import app

    schemas = app.openapi()["components"]["schemas"]
    forbidden = SERVICE_ONLY_CONFIG_KEYS | {"objective_constraints", "max_training_seconds"}
    for name in ("ExperimentCreateRequest", "ExperimentBranchCreateRequest"):
        assert not set(schemas[name]["properties"]) & forbidden, name
    for extra in ({"service_overrides": {"evidence_mode": "scored"}}, {"evidence_mode": "sealed"},
                  {"expected_selection": {"winner": "x"}}, {"score_holdout": False}):
        response = engineer_client.post(
            f"/v1/experiments/{parent.id}/branches", headers={"Idempotency-Key": f"extra-{sorted(extra)[0]}"},
            json={"intent": "x", "changes": [{"kind": "family_include", "family": "extra_trees"}], **extra})
        assert response.status_code == 422, response.text
        response = engineer_client.post(
            "/v1/experiments", headers={"Idempotency-Key": f"root-{sorted(extra)[0]}"},
            json={"project_id": str(parent.project_id), "dataset_id": str(parent.source_dataset_id), **extra})
        assert response.status_code == 422, response.text
