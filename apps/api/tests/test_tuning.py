"""P1.4-C: CatBoost in the portfolio and bounded, nested hyperparameter tuning."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.engine.experiments.runner import run_experiment
from app.engine.search.generator import assemble_candidates, open_ingest_families
from app.engine.search.tuning import optuna_available, tuning_plan
from app.engine.types import SearchConfig
from test_open_ingest_runner import _frame, _task_and_config

pytestmark = pytest.mark.skipif(not optuna_available(), reason="optuna not installed")


def _config(**overrides) -> SearchConfig:
    values = {"strategy": "open_ingest", "max_candidates": 8, "seed": 42, "max_hyperparameter_trials": 4}
    values.update(overrides)
    return SearchConfig(**values)


def _run(config: SearchConfig, frame=None) -> dict:
    frame = _frame() if frame is None else frame
    task, _ = _task_and_config(frame)
    with tempfile.TemporaryDirectory() as tmp:
        return run_experiment(frame, task, config, artifact_dir=Path(tmp))


def test_catboost_joins_the_portfolio_when_installed():
    pytest.importorskip("catboost")
    assert "catboost" in open_ingest_families("binary")
    assert "catboost_regressor" in open_ingest_families("regression")


def test_tuning_plan_is_bounded_and_part_of_the_fingerprint():
    plan = tuning_plan("binary", ["logistic_regression", "lightgbm"], n_trials=4, seed=7)
    assert plan["family"] == "lightgbm" and plan["n_trials"] == 4 and plan["seed"] == 7
    assert all(len(spec) >= 2 for spec in plan["search_space"].values())
    assert tuning_plan("binary", ["lightgbm"], n_trials=0, seed=7) is None

    frame = _frame()
    task, _ = _task_and_config(frame)

    def tuned_fingerprint(**overrides):
        rows = assemble_candidates(task, _config(**overrides))
        return next(row.fingerprint for row in rows if row.candidate_id.endswith("__tuned"))

    assert tuned_fingerprint() != tuned_fingerprint(max_hyperparameter_trials=5)
    assert tuned_fingerprint() != tuned_fingerprint(seed=43)


def test_tuning_sees_only_training_rows(monkeypatch):
    import app.engine.experiments.runner as runner_module

    sizes: list[int] = []
    real = runner_module.tune

    def spy(plan, X, y, **kwargs):
        sizes.append(len(y))
        return real(plan, X, y, **kwargs)

    monkeypatch.setattr(runner_module, "tune", spy)
    result = _run(_config())
    tuned = next(row for row in result["candidates"] if row["candidate_id"].endswith("__tuned"))
    assert tuned["status"] == "trained"
    n_train = result["split"]["n_train"]
    fold_train_sizes = [fold["train_count"] for fold in tuned["folds"]]
    # One search per outer fold on that fold's training rows.
    assert sizes[: len(fold_train_sizes)] == fold_train_sizes
    assert all(size < n_train for size in fold_train_sizes)
    assert all(fold["tuning"]["trials_completed"] >= 1 for fold in tuned["folds"])
    if result["best_single"]["candidate_id"] == tuned["candidate_id"]:
        # The winner's final fit re-tunes on the training pool, never the holdout.
        assert sizes[-1] == n_train
        assert result["final_fit"]["tuning"]["params"]


def test_same_seed_same_tuned_params_and_winner():
    first, second = _run(_config()), _run(_config())

    def tuned_params(result):
        row = next(row for row in result["candidates"] if row["candidate_id"].endswith("__tuned"))
        return [fold["tuning"]["params"] for fold in row["folds"]]

    assert tuned_params(first) == tuned_params(second)
    assert first["selection"]["selected_candidate_id"] == second["selection"]["selected_candidate_id"]


def test_time_budget_bounds_tuning_and_the_run_still_completes():
    result = _run(_config(max_hyperparameter_trials=50, max_training_seconds=1e-6))
    assert result["status"] == "COMPLETED"
    tuned = next(row for row in result["candidates"] if row["candidate_id"].endswith("__tuned"))
    if tuned["status"] == "trained":
        assert all(fold["tuning"]["trials_completed"] < 50 for fold in tuned["folds"])
    else:
        assert tuned["status"] == "SKIPPED" and "budget" in tuned["failure_reason"]


def test_no_tuned_candidate_without_optuna(monkeypatch):
    import app.engine.search.tuning as tuning_module

    monkeypatch.setattr(tuning_module, "optuna_available", lambda: False)
    frame = _frame()
    task, _ = _task_and_config(frame)
    rows = assemble_candidates(task, _config())
    assert not [row for row in rows if row.candidate_id.endswith("__tuned")]


def _xor_frame(n: int = 320, seed: int = 4):
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    a, b = rng.uniform(size=n), rng.uniform(size=n)
    flip = rng.uniform(size=n) < 0.05
    label = ((a > 0.5) ^ (b > 0.5)) ^ flip
    return pd.DataFrame({"a": a, "b": b, "noise": rng.normal(size=n), "churn": np.where(label, "Yes", "No")})


def _xor_run(config: SearchConfig, monkeypatch=None):
    from app.engine.lab.auto_prepare import split_column_roles
    from app.engine.types import TaskSpec

    frame = _xor_frame()
    columns = [c for c in frame.columns if c != "churn"]
    num, cat = split_column_roles(frame, columns)
    task = TaskSpec(
        id="xor",
        name="xor",
        task_type="binary",
        target="churn",
        evaluation_metric="pr_auc",
        feature_groups={"features": num + cat},
        validation_strategy="stratified",
        column_roles={"numerical": num, "categorical": cat},
    )
    with tempfile.TemporaryDirectory() as tmp:
        return run_experiment(frame, task, config, artifact_dir=Path(tmp))


def test_tuned_winner_is_retuned_on_the_training_pool_with_the_cv_trial_count(monkeypatch):
    import app.engine.experiments.runner as runner_module

    calls: list[tuple[int, int]] = []
    real = runner_module.tune

    def spy(plan, X, y, **kwargs):
        calls.append((len(y), int(plan["n_trials"])))
        return real(plan, X, y, **kwargs)

    monkeypatch.setattr(runner_module, "tune", spy)
    # Only linear + tuned trees + dummy: on XOR data the tuned trees must win.
    result = _xor_run(_config(max_candidates=1, max_hyperparameter_trials=6))
    winner = result["best_single"]
    assert winner["candidate_id"].endswith("__tuned")
    n_train = result["split"]["n_train"]
    assert calls[-1] == (n_train, winner["tuning_trials_used"])
    assert result["final_fit"]["tuning"]["params"] == winner["tuned_params"]
    assert result["final_fit"]["tuning"]["inner_split"] == "stratified_random"


def test_a_fold_without_tuning_trials_skips_the_tuned_candidate(monkeypatch):
    import app.engine.experiments.runner as runner_module

    real = runner_module.tune
    seen = {"n": 0}

    def starve_third_fold(plan, X, y, **kwargs):
        seen["n"] += 1
        if seen["n"] == 3:
            return {"params": {}, "trials_completed": 0, "inner_score": None, "truncated": True}
        return real(plan, X, y, **kwargs)

    monkeypatch.setattr(runner_module, "tune", starve_third_fold)
    result = _run(_config())
    tuned = next(row for row in result["candidates"] if row["candidate_id"].endswith("__tuned"))
    assert tuned["status"] == "SKIPPED"
    assert "fold" in tuned["failure_reason"]
    assert tuned["candidate_id"] not in result["selection"]["eligible_candidate_ids"]


def test_every_failing_trial_is_a_failure_not_a_budget_cut():
    import numpy as np
    import pandas as pd

    from app.engine.search.tuning import TuningFailedError, tune

    plan = tuning_plan("binary", ["lightgbm"], n_trials=3, seed=1)
    X = pd.DataFrame({"x": np.arange(40.0)})
    y = np.array([0, 1] * 20)

    def broken(*_args):
        raise ValueError("bad parameter")

    with pytest.raises(TuningFailedError, match="bad parameter"):
        tune(plan, X, y, classification=True, score=broken)


def test_tuned_params_reach_every_estimator_shape():
    from app.engine.models.registry import ContiguousLabelClassifier, make_model

    lr = make_model("logistic_regression", hyperparameters={"tuned": {"C": 0.25}})
    assert lr.named_steps["clf"].C == 0.25
    rf = make_model("random_forest", hyperparameters={"tuned": {"max_depth": 5, "max_features": 0.5}})
    assert rf.max_depth == 5 and rf.max_features == 0.5
    multi = make_model("xgboost", task_type="multiclass", hyperparameters={"tuned": {"max_depth": 3}})
    assert isinstance(multi, ContiguousLabelClassifier) and multi.estimator.max_depth == 3
    pytest.importorskip("catboost")
    cat = make_model("catboost", hyperparameters={"tuned": {"depth": 4}})
    assert cat.get_params()["depth"] == 4


def test_deterministic_under_a_budget_that_is_never_hit():
    first = _run(_config(max_training_seconds=3600.0))
    second = _run(_config(max_training_seconds=3600.0))
    assert first["selection"]["selected_candidate_id"] == second["selection"]["selected_candidate_id"]
    assert first["test_metrics"] == second["test_metrics"]
