"""The `strategy="open_ingest"` search/runner path: ColumnTransformer +
real K-fold on the training split only, then a locked-model holdout test.
Since P1.2-A every run_experiment call executes this path; legacy strategies
are normalized onto it (the candidate generator still accepts them).
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
    # Learned families, then the chance-level baseline (P1.4-A1).
    assert [c.model_family for c in candidates] == expected + ["majority"]
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
    evidence = result["scientific_evidence"]
    assert evidence["preprocessing_fit_scope"] == "fold_train"
    assert evidence["missing_value_plan"]["column_decisions"]
    assert evidence["numerical_columns"]
    assert evidence["categorical_columns"]
    expected_n = len(open_ingest_families("binary"))
    assert result["funnel"]["trained"] == expected_n + 1  # + majority baseline
    comparison = result["baseline_comparison"]
    assert comparison["baseline_candidate_id"] == "majority"
    assert comparison["winner_candidate_id"] != "majority"
    assert comparison["beats_baseline"] is True
    assert result["funnel"]["failed"] == 0
    assert result["best_single"] is not None
    assert result["best_single"]["model_family"]
    assert result["split"]["strategy"] == "stratified_random"
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
    assert result["funnel"]["trained"] == expected_n + 1  # + median baseline
    comparison = result["baseline_comparison"]
    assert comparison["baseline_candidate_id"] == "median"
    assert comparison["metric"] == "mae" and comparison["beats_baseline"] is True
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


def _imbalanced_frame(n: int = 400, positive_rate: float = 0.12, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    region = rng.choice(["north", "south", "east"], n)
    logits = 2.2 * x1 - 1.0 * x2 + np.where(region == "north", 0.8, 0.0)
    threshold = np.quantile(logits, 1 - positive_rate)
    return pd.DataFrame({"x1": x1, "x2": x2, "region": region, "label": (logits > threshold).astype(int)})


def _plan_with_distribution(distribution: dict[str, int]) -> dict:
    total = sum(distribution.values())
    minority = min(distribution.values())
    return {
        "problem_profile": {
            "task_type": "binary",
            "target": "label",
            "row_count": total,
            "feature_count": 3,
            "class_distribution": distribution,
            "minority_class_fraction": minority / total,
            "imbalance_ratio": max(distribution.values()) / max(minority, 1),
        }
    }


def test_imbalanced_binary_adds_class_weighted_variants():
    from app.engine.search.generator import balanced_variants

    families = open_ingest_families("binary")
    variants = dict(balanced_variants("binary", families, _plan_with_distribution({"0": 880, "1": 120})))
    assert variants.get("logistic_regression") == {"class_weight": "balanced"}
    assert variants.get("random_forest") == {"class_weight": "balanced"}
    if "xgboost" in families:
        # Weight is negatives / positives from the TRAIN-partition distribution.
        assert variants["xgboost"] == {"scale_pos_weight": round(880 / 120, 6)}
    assert balanced_variants("binary", families, _plan_with_distribution({"0": 510, "1": 490})) == []
    assert balanced_variants("regression", families, _plan_with_distribution({"0": 880, "1": 120})) == []


def test_imbalanced_run_trains_balanced_variants_and_reports_baseline():
    frame = _imbalanced_frame()
    columns = [c for c in frame.columns if c != "label"]
    num_cols, cat_cols = split_column_roles(frame, columns)
    task = TaskSpec(
        id="imbalanced",
        name="imbalanced",
        task_type="binary",
        target="label",
        entity_id=None,
        prediction_time_column=None,
        evaluation_metric="pr_auc",
        feature_groups={"features": num_cols + cat_cols},
        validation_strategy="stratified",
        column_roles={"numerical": num_cols, "categorical": cat_cols},
    )
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            frame, task, SearchConfig(strategy="open_ingest", max_candidates=12, seed=42),
            artifact_dir=Path(tmp),
        )
    assert result["status"] == "COMPLETED"
    ids = {row["candidate_id"] for row in result["candidates"]}
    assert "logistic_regression__balanced" in ids and "majority" in ids
    assert result["baseline_comparison"]["beats_baseline"] is True
    assert result["best_single"]["candidate_id"] != "majority"


def test_time_budget_skips_later_candidates_but_never_the_baseline():
    frame = _frame()
    task, _ = _task_and_config(frame)
    config = SearchConfig(strategy="open_ingest", max_candidates=8, seed=42, max_training_seconds=1e-9)
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(frame, task, config, artifact_dir=Path(tmp))
    statuses = {row["candidate_id"]: row["status"] for row in result["candidates"]}
    assert statuses["majority"] == "trained"
    learned = [cid for cid in statuses if cid != "majority"]
    assert sum(statuses[cid] == "trained" for cid in learned) == 1  # always one learned model
    assert all(statuses[cid] == "SKIPPED" for cid in learned if statuses[cid] != "trained")
    assert result["status"] == "COMPLETED"


def test_unseen_category_is_distinguishable_from_every_seen_category():
    from app.engine.lab.auto_prepare import build_preprocessor

    train = pd.DataFrame({"x": [1.0, 2.0, 3.0], "color": ["red", "green", "blue"]})
    prep = build_preprocessor(["x"], ["color"]).fit(train)
    seen = prep.transform(train)[:, 1:]
    unseen = prep.transform(pd.DataFrame({"x": [1.0], "color": ["purple"]}))[:, 1:]
    assert unseen.tolist() == [[0.0, 0.0, 0.0]]
    assert all(row.sum() == 1.0 for row in seen)  # no category collapses onto "unseen"


def test_no_learned_model_means_failed_run_never_a_dummy_winner(monkeypatch):
    """P1.4-A1 review: a dummy baseline must never ship as the winner."""
    import app.engine.experiments.runner as runner_module

    real_make_model = runner_module.make_model

    def failing_for_learned(family, **kwargs):
        if family not in {"majority", "median", "mean"}:
            raise RuntimeError(f"{family} unavailable in this test")
        return real_make_model(family, **kwargs)

    monkeypatch.setattr(runner_module, "make_model", failing_for_learned)
    frame = _frame()
    task, config = _task_and_config(frame)
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(frame, task, config, artifact_dir=Path(tmp))
    assert result.get("best_single") is None
    assert result["status"] != "COMPLETED"


def test_skipped_candidates_pass_the_verifier_candidate_audit():
    from app.services.pipeline_verifier import verify_pipeline

    frame = _frame()
    task, _ = _task_and_config(frame)
    config = SearchConfig(strategy="open_ingest", max_candidates=8, seed=42, max_training_seconds=1e-9)
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(frame, task, config, artifact_dir=Path(tmp))
    assert any(row["status"] == "SKIPPED" for row in result["candidates"])
    report = {
        "candidate_models": result["candidates"],
        "expected_candidate_ids": result["expected_candidate_ids"],
        "selection": result["selection"],
        "baseline_comparison": result["baseline_comparison"],
    }
    checks = {item["check_id"]: item["status"] for item in verify_pipeline(report)["checks"]}
    assert checks.get("candidate_audit_complete") == "PASS"
    assert checks.get("winner_beats_baseline") == "PASS"


def test_class_weight_reaches_the_estimator():
    from app.engine.models.registry import applied_hyperparameters, make_model

    lr = make_model("logistic_regression", hyperparameters={"class_weight": "balanced"})
    assert lr.named_steps["clf"].class_weight == "balanced"
    rf = make_model("random_forest", hyperparameters={"class_weight": "balanced"})
    assert rf.class_weight == "balanced"
    assert make_model("random_forest").class_weight is None
    assert applied_hyperparameters("random_forest", hyperparameters={"class_weight": "balanced"})["class_weight"] == "balanced"
    assert "class_weight" not in applied_hyperparameters("random_forest")
