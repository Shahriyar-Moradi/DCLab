"""P1.4-B: objective contract and the out-of-fold decision threshold."""

from __future__ import annotations

import numpy as np
import pytest

from app.engine.modeling.objective import (
    DEFAULT_THRESHOLD,
    ObjectiveError,
    parse_objective,
    select_decision_threshold,
)


def _scores(n: int = 400, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    y = rng.binomial(1, 0.3, n)
    scores = np.clip(0.3 + 0.35 * (y - 0.3) + rng.normal(0, 0.18, n), 0, 1)
    return y, scores


def test_parse_objective_validates_metrics_ops_and_costs():
    objective = parse_objective(
        "binary",
        primary_metric="roc_auc",
        constraints={
            "primary_metric_reason": "ranking matters",
            "metric_constraints": [{"metric": "precision", "op": ">=", "value": 0.7}],
            "cost_matrix": {"false_positive": 1, "false_negative": 5},
            "free_text_note": "kept as-is",
        },
    )
    assert objective.primary_metric == "roc_auc"
    assert objective.primary_metric_reason == "ranking matters"
    assert objective.constraints[0].label == "constraint_precision_min"
    assert objective.has_cost_matrix

    with pytest.raises(ObjectiveError):
        parse_objective("binary", primary_metric="mae")
    with pytest.raises(ObjectiveError):
        parse_objective("binary", constraints={"metric_constraints": [{"metric": "precision", "op": ">", "value": 1}]})
    with pytest.raises(ObjectiveError):
        parse_objective("regression", constraints={"cost_matrix": {"false_positive": 1, "false_negative": 1}})
    with pytest.raises(ObjectiveError):
        parse_objective("binary", constraints={"cost_matrix": {"false_positive": -1, "false_negative": 1}})
    assert parse_objective("binary").is_empty


def test_no_objective_keeps_default_threshold():
    y, scores = _scores()
    decision = select_decision_threshold(y, scores, None)
    assert decision["value"] == DEFAULT_THRESHOLD
    assert decision["status"] == "not_requested"
    assert decision["selected_on"] == "out_of_fold_cv"


def test_precision_constraint_is_met_on_oof_and_maximises_f1():
    y, scores = _scores()
    objective = parse_objective(
        "binary", constraints={"metric_constraints": [{"metric": "precision", "op": ">=", "value": 0.7}]}
    )
    decision = select_decision_threshold(y, scores, objective)
    assert decision["status"] == "satisfied"
    pred = scores >= decision["value"]
    precision = (pred & (y == 1)).sum() / max(pred.sum(), 1)
    assert precision >= 0.7
    row = decision["constraints"][0]
    assert row["oof_satisfied"] is True and row["oof_value"] == pytest.approx(precision)


def test_cost_matrix_moves_threshold_toward_the_expensive_error():
    y, scores = _scores()
    cheap_fn = select_decision_threshold(
        y, scores, parse_objective("binary", constraints={"cost_matrix": {"false_positive": 5, "false_negative": 1}})
    )
    cheap_fp = select_decision_threshold(
        y, scores, parse_objective("binary", constraints={"cost_matrix": {"false_positive": 1, "false_negative": 5}})
    )
    assert cheap_fp["value"] < cheap_fn["value"]
    assert cheap_fp["source"] == "cost_matrix" and cheap_fp["expected_cost"] is not None


def test_unsatisfiable_constraints_are_reported_not_hidden():
    y, scores = _scores()
    objective = parse_objective(
        "binary",
        constraints={
            "metric_constraints": [
                {"metric": "precision", "op": ">=", "value": 0.99},
                {"metric": "recall", "op": ">=", "value": 0.99},
            ]
        },
    )
    decision = select_decision_threshold(y, scores, objective)
    assert decision["status"] == "unsatisfiable"
    assert "no threshold satisfies every constraint" in decision["reason"]
    assert any(row["oof_satisfied"] is False for row in decision["constraints"])


def test_threshold_free_constraints_are_evaluated_from_cv_metrics():
    y, scores = _scores()
    objective = parse_objective(
        "binary", constraints={"metric_constraints": [{"metric": "roc_auc", "op": ">=", "value": 0.99}]}
    )
    decision = select_decision_threshold(y, scores, objective, cv_metrics={"roc_auc": 0.8})
    assert decision["value"] == DEFAULT_THRESHOLD
    assert decision["status"] == "unsatisfiable"
    assert decision["constraints"][0]["oof_value"] == 0.8



def _binary_run(objective=None):
    import tempfile
    from pathlib import Path

    from app.engine.experiments.runner import run_experiment
    from app.engine.types import SearchConfig
    from test_open_ingest_runner import _frame, _task_and_config

    frame = _frame(n=400)
    task, config = _task_and_config(frame)
    config = SearchConfig(
        strategy="open_ingest",
        max_candidates=8,
        seed=42,
        objective=None if objective is None else objective.to_dict(),
    )
    with tempfile.TemporaryDirectory() as tmp:
        return run_experiment(frame, task, config, artifact_dir=Path(tmp))


def test_threshold_is_tuned_on_training_rows_only_and_locked_for_the_holdout(monkeypatch):
    import app.engine.experiments.runner as runner_module

    seen: list[int] = []
    real = runner_module.select_decision_threshold

    def spy(y, scores, objective, **kwargs):
        seen.append(len(y))
        return real(y, scores, objective, **kwargs)

    monkeypatch.setattr(runner_module, "select_decision_threshold", spy)
    objective = parse_objective(
        "binary", constraints={"metric_constraints": [{"metric": "recall", "op": ">=", "value": 0.8}]}
    )
    result = _binary_run(objective)

    decision = result["decision_threshold"]
    split = result["split"]
    assert seen == [split["n_train"]]
    assert set(decision["oof_source_rows"]) <= set(split["train_source_rows"])
    assert not set(decision["oof_source_rows"]) & set(split["test_source_rows"])
    assert decision["status"] == "satisfied"
    threshold = decision["value"]
    assert result["test_metrics"]["decision_threshold"] == threshold
    assert result["selection"]["decision_threshold"] == threshold
    # Holdout predictions use the locked threshold.
    for row in result["test_predictions"]:
        assert row["y_pred"] == int(row["probability"] >= threshold)
    matrix = result["test_metrics"]["confusion_matrix"]
    flagged = sum(row["y_pred"] for row in result["test_predictions"])
    assert matrix["tp"] + matrix["fp"] == flagged
    row = decision["constraints"][0]
    assert row["holdout_value"] == pytest.approx(result["test_metrics"]["recall"])
    assert result["test_metrics"]["constraint_recall_min_satisfied"] in {0.0, 1.0}
    assert "constraints_satisfied" in result["test_metrics"]


def test_unsatisfiable_constraint_run_completes_and_says_so():
    objective = parse_objective(
        "binary",
        constraints={
            "metric_constraints": [
                {"metric": "precision", "op": ">=", "value": 0.99},
                {"metric": "recall", "op": ">=", "value": 0.99},
            ]
        },
    )
    result = _binary_run(objective)
    assert result["status"] == "COMPLETED"
    assert result["decision_threshold"]["status"] == "unsatisfiable"
    assert result["test_metrics"]["constraints_satisfied"] == 0.0


def test_spec_primary_metric_override_drives_selection():
    result = _binary_run(parse_objective("binary", primary_metric="roc_auc", constraints={"primary_metric_reason": "ranking"}))
    assert result["metric_plan"]["primary_metric"] == "roc_auc"
    assert "ranking" in result["metric_plan"]["reason"]
    assert result["selection"]["selection_metric"] == "roc_auc"


def test_default_run_keeps_half_threshold():
    result = _binary_run()
    assert result["decision_threshold"]["value"] == DEFAULT_THRESHOLD
    assert result["decision_threshold"]["status"] == "not_requested"


def test_metrics_at_threshold_one_match_the_decisions():
    from app.engine.evaluation.metrics import classification_metrics

    y = np.array([1, 1, 0, 0, 1])
    scores = np.array([1.0, 1.0, 0.2, 1.0, 0.4])
    matrix = classification_metrics(y, scores, threshold=1.0)["confusion_matrix"]
    # Three rows score exactly 1.0 and are flagged, like the prediction rows.
    assert matrix == {"tn": 1, "fp": 1, "fn": 1, "tp": 2}


def test_status_counts_threshold_free_constraints_too():
    y, scores = _scores()
    objective = parse_objective(
        "binary",
        constraints={
            "metric_constraints": [
                {"metric": "recall", "op": ">=", "value": 0.5},
                {"metric": "roc_auc", "op": ">=", "value": 0.99},
            ]
        },
    )
    decision = select_decision_threshold(y, scores, objective, cv_metrics={"roc_auc": 0.8})
    assert decision["threshold_status"] == "satisfied"
    assert decision["status"] == "unsatisfiable"


def test_cost_matrix_can_flag_nothing():
    rng = np.random.default_rng(3)
    y = rng.binomial(1, 0.05, 400)
    scores = rng.uniform(size=400)  # no signal
    objective = parse_objective(
        "binary", constraints={"cost_matrix": {"false_positive": 100, "false_negative": 1}}
    )
    decision = select_decision_threshold(y, scores, objective)
    assert not (scores >= decision["value"]).any()


def test_precision_and_recall_are_constraints_not_primaries_and_aliases_normalise():
    with pytest.raises(ObjectiveError):
        parse_objective("binary", primary_metric="precision")
    assert parse_objective("binary", primary_metric="AUC").primary_metric == "roc_auc"
    assert parse_objective(
        "classification", constraints={"cost_matrix": {"false_positive": 1, "false_negative": 2}}
    ).has_cost_matrix


def test_threshold_dependent_primary_tunes_threshold_for_that_metric():
    y, scores = _scores()
    decision = select_decision_threshold(y, scores, parse_objective("binary"), primary_metric="f1")
    assert decision["source"] == "primary_metric"
    assert decision["goal_metric"] == "f1"


def test_time_series_split_tunes_on_the_latest_fold_only():
    from app.engine.experiments.runner import _lock_decision_threshold
    import pandas as pd

    rng = np.random.default_rng(0)
    y = rng.binomial(1, 0.4, 300)
    pool = pd.DataFrame({"__source_row__": np.arange(300)})
    folds = [np.arange(100, 150), np.arange(150, 200), np.arange(200, 300)]
    fold_scores = [np.clip(y[idx] * 0.4 + rng.uniform(0, 0.6, len(idx)), 0, 1) for idx in folds]
    objective = parse_objective(
        "binary", constraints={"metric_constraints": [{"metric": "precision", "op": ">=", "value": 0.7}]}
    )
    decision = _lock_decision_threshold(
        "binary",
        {"candidate_id": "c1", "cv_mean": {}},
        (folds, fold_scores),
        y,
        pool,
        objective,
        last_fold_only=True,
    )
    assert decision["oof_folds"] == "last_fold"
    assert decision["oof_row_count"] == 100
    assert len(decision["per_fold"]) == 3


def test_cost_matrix_run_reports_holdout_expected_cost():
    objective = parse_objective(
        "binary", constraints={"cost_matrix": {"false_positive": 1, "false_negative": 4}}
    )
    result = _binary_run(objective)
    assert result["decision_threshold"]["source"] == "cost_matrix"
    assert "expected_cost" in result["test_metrics"]


def test_verifier_fails_threshold_tuned_on_holdout_rows_or_moved_after_lock():
    from app.services.pipeline_verifier import verify_pipeline

    def checks(decision, reported):
        report = {
            "split": {"train_source_rows": [0, 1, 2], "test_source_rows": [3, 4]},
            "selection": {"selection_metric": "pr_auc"},
            "final_test_evaluation": {"metrics": {"decision_threshold": reported}},
            "decision_threshold": decision,
        }
        return {item["check_id"]: item["status"] for item in verify_pipeline(report)["checks"]}

    good = {"selected_on": "out_of_fold_cv", "oof_source_rows": [0, 1, 2], "value": 0.4, "constraints": []}
    assert checks(good, 0.4)["decision_threshold_from_cv"] == "PASS"
    assert checks({**good, "oof_source_rows": [2, 3]}, 0.4)["decision_threshold_from_cv"] == "FAIL"
    assert checks(good, 0.6)["decision_threshold_from_cv"] == "FAIL"
