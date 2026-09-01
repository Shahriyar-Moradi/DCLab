"""The `strategy="open_ingest"` search/runner path: ColumnTransformer +
real K-fold on the training split only, then a locked-model holdout test.
Must not change the default `use_case`/`progressive` behaviour used by
manual `/admin/lab` experiments.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from app.engine.experiments.runner import run_experiment
from app.engine.lab.auto_prepare import pick_target_heuristic, split_column_roles
from app.engine.models.registry import available_families
from app.engine.search.generator import assemble_candidates, open_ingest_families
from app.engine.types import SearchConfig, TaskSpec


def _frame(n: int = 220, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 72, n)
    monthly = rng.uniform(20, 120, n)
    total = tenure * monthly + rng.normal(0, 40, n)
    contract = rng.choice(["Month-to-month", "One year", "Two year"], n)
    gender = rng.choice(["Male", "Female"], n)
    churn_p = np.where(contract == "Month-to-month", 0.6, 0.15)
    churn = rng.binomial(1, churn_p)
    return pd.DataFrame(
        {
            "tenure": tenure,
            "MonthlyCharges": monthly,
            "TotalCharges": total,
            "gender": gender,
            "contract": contract,
            "churn": np.where(churn == 1, "Yes", "No"),
        }
    )


def _task_and_config(frame: pd.DataFrame) -> tuple[TaskSpec, SearchConfig]:
    columns = [c for c in frame.columns if c != "churn"]
    num_cols, cat_cols = split_column_roles(frame, columns)
    task = TaskSpec(
        id="open_ingest_runner_test",
        name="test",
        task_type="binary",
        target="churn",
        entity_id="tenure",
        prediction_time_column=None,
        evaluation_metric="pr_auc",
        feature_groups={"features": num_cols + cat_cols},
        validation_strategy="stratified",
        column_roles={"numerical": num_cols, "categorical": cat_cols},
    )
    config = SearchConfig(strategy="open_ingest", max_candidates=8, seed=42)
    return task, config


def test_open_ingest_strategy_generates_one_candidate_per_registry_family():
    frame = _frame()
    task, config = _task_and_config(frame)
    candidates = assemble_candidates(task, config, dataset_version="v1")
    expected = open_ingest_families("binary")
    assert [c.model_family for c in candidates] == expected
    assert "logistic_regression" in expected
    assert "random_forest" in expected
    assert "majority" not in expected
    avail = available_families("binary")
    if "xgboost" in avail:
        assert "xgboost" in expected
    if "lightgbm" in avail:
        assert "lightgbm" in expected
    for candidate in candidates:
        assert candidate.preprocessing.get("kind") == "column_transformer"
        assert candidate.preprocessing.get("numeric_imputer") == "median"
        assert "missing_variant" not in candidate.preprocessing


def test_open_ingest_run_experiment_completes_with_real_kfold_and_holdout_test():
    frame = _frame()
    task, config = _task_and_config(frame)
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(frame, task, config, artifact_dir=Path(tmp), dataset_version="v1")
        pred_path = Path(tmp) / "test_predictions.csv"
        assert pred_path.exists()
        saved = pd.read_csv(pred_path)

    assert result["status"] == "COMPLETED"
    expected_n = len(open_ingest_families("binary"))
    assert result["funnel"]["trained"] == expected_n
    assert result["funnel"]["failed"] == 0
    assert result["best_single"] is not None
    assert result["best_single"]["model_family"]
    assert result["split"]["strategy"] == "train_test_split"
    assert result["split"]["test_size"] == 0.2
    assert result["split"]["n_val"] == 0
    assert result["split"]["n_test"] > 0
    assert abs(result["split"]["n_test"] / len(frame) - 0.2) < 0.05
    assert "accuracy" in result["test_metrics"]
    assert "roc_auc" in result["test_metrics"]
    assert "precision" in result["test_metrics"]
    assert "recall" in result["test_metrics"]
    assert "f1" in result["test_metrics"]
    assert "accuracy" in result["train_metrics"]
    trained = [row for row in result["candidates"] if row["status"] == "trained"]
    assert all("cv_score" in row for row in trained)
    assert all("cv_mean" in row and "cv_std" in row for row in trained)
    assert all(row["n_folds"] == 5 for row in trained)
    assert all(len(row["fold_metrics"]) == 5 for row in trained)
    assert "accuracy" in trained[0]["fold_metrics"][0]
    assert "f1" in trained[0]["cv_mean"]
    assert "accuracy" in trained[0]["cv_std"]
    winner_id = result["best_single"]["candidate_id"]
    winner_cv = result["best_single"]["score"]
    for row in trained:
        assert "test_metrics" in row
        if row["candidate_id"] == result["selection"]["selected_candidate_id"]:
            assert "roc_auc" in row["test_metrics"]
        else:
            assert row["test_metrics"] is None
        assert row["cv_strategy"] == "StratifiedKFold"
        if row["candidate_id"] != winner_id:
            assert row["score"] <= winner_cv + 1e-12
            assert not row.get("locked")
    assert result["best_single"].get("locked") is True
    assert result["validation"]["n_folds"] == 5
    assert result["validation"]["cv_strategy"] == "StratifiedKFold"
    assert result["validation"]["random_state"] == 42
    assert len(result["test_predictions"]) == result["split"]["n_test"]
    assert len(saved) == result["split"]["n_test"]
    assert {"row_index", "y_true", "y_pred", "score"} <= set(result["test_predictions"][0])
    assert 0 < result["test_metrics"]["accuracy"] <= 1
    assert result["fusion"] is None
    assert "column_names" in result["profile"]


def test_open_ingest_regression_families_use_registry_when_present():
    families = open_ingest_families("regression")
    assert families[0] == "linear_regression"
    assert "random_forest_regressor" in families
    avail = available_families("regression")
    if "xgboost_regressor" in avail:
        assert "xgboost_regressor" in families
    if "lightgbm_regressor" in avail:
        assert "lightgbm_regressor" in families
    assert "mean" not in families


def test_open_ingest_regression_run_uses_kfold_and_regression_metrics():
    rng = np.random.default_rng(4)
    n = 180
    tenure = rng.integers(1, 72, n)
    monthly = rng.uniform(20, 120, n)
    segment = rng.choice(["A", "B", "C"], n)
    revenue = 40 + 2.1 * tenure + 0.4 * monthly + rng.normal(0, 8, n)
    frame = pd.DataFrame(
        {
            "tenure": tenure,
            "MonthlyCharges": monthly,
            "segment": segment,
            "revenue_60d": revenue,
        }
    )
    columns = [c for c in frame.columns if c != "revenue_60d"]
    num_cols, cat_cols = split_column_roles(frame, columns)
    task = TaskSpec(
        id="open_ingest_regression_test",
        name="test",
        task_type="regression",
        target="revenue_60d",
        entity_id="tenure",
        prediction_time_column=None,
        evaluation_metric="mae",
        feature_groups={"features": num_cols + cat_cols},
        validation_strategy="random",
        column_roles={"numerical": num_cols, "categorical": cat_cols},
    )
    config = SearchConfig(strategy="open_ingest", max_candidates=8, seed=42)
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(frame, task, config, artifact_dir=Path(tmp), dataset_version="v1")
        assert (Path(tmp) / "test_predictions.csv").exists()

    assert result["status"] == "COMPLETED"
    expected_n = len(open_ingest_families("regression"))
    assert result["funnel"]["trained"] == expected_n
    trained = [row for row in result["candidates"] if row["status"] == "trained"]
    assert all(row["cv_strategy"] == "KFold" for row in trained)
    assert all(row["n_folds"] == 5 for row in trained)
    assert result["validation"]["cv_strategy"] == "KFold"
    assert "mae" in result["test_metrics"]
    assert "rmse" in result["test_metrics"]
    assert "r2" in result["test_metrics"]
    assert "roc_auc" not in result["test_metrics"]
    assert result["best_single"]["model_family"] in {
        "linear_regression",
        "random_forest_regressor",
        "xgboost_regressor",
        "lightgbm_regressor",
    }
    assert len(result["test_predictions"]) == result["split"]["n_test"]
    assert result["best_single"].get("locked") is True


def test_open_ingest_does_not_affect_default_use_case_strategy():
    """Same shape task, default strategy — must behave exactly as before:
    one candidate per family x combo, no `preprocessing.kind`."""
    frame = _frame()
    task, _ = _task_and_config(frame)
    default_config = SearchConfig(strategy="use_case", max_candidates=10, seed=42)
    candidates = assemble_candidates(task, default_config, dataset_version="v1")
    assert candidates
    for candidate in candidates:
        assert candidate.preprocessing == {}


def test_target_heuristic_finds_a_binary_label_without_catalog_dependency():
    frame = _frame()
    choice = pick_target_heuristic(frame, list(frame.columns))
    assert choice.column == "churn"
