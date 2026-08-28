"""The `strategy="open_ingest"` search/runner path: ColumnTransformer +
real K-fold retrain, two competing missing-value variants, RandomForest and a
boosted family. Must not change the default `use_case`/`progressive`
behaviour used by manual `/admin/lab` experiments. See
docs/LABS_DATA_UNDERSTANDING.md.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from app.engine.experiments.runner import run_experiment
from app.engine.lab.auto_prepare import pick_target_heuristic, split_column_roles
from app.engine.search.generator import assemble_candidates
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


def test_open_ingest_strategy_generates_two_variants_per_family():
    frame = _frame()
    task, config = _task_and_config(frame)
    candidates = assemble_candidates(task, config, dataset_version="v1")
    assert len(candidates) == 8
    variants = {c.preprocessing.get("missing_variant") for c in candidates}
    assert variants == {"drop_sparse_rows", "impute_all"}
    families = {c.model_family for c in candidates}
    assert "random_forest" in families
    assert families & {"xgboost", "gradient_boosting"}
    for candidate in candidates:
        assert candidate.preprocessing.get("kind") == "column_transformer"


def test_open_ingest_run_experiment_completes_with_real_kfold_and_holdout_test():
    frame = _frame()
    task, config = _task_and_config(frame)
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(frame, task, config, artifact_dir=Path(tmp), dataset_version="v1")

    assert result["status"] == "COMPLETED"
    assert result["funnel"]["trained"] == 8
    assert result["funnel"]["failed"] == 0
    assert result["best_single"] is not None
    assert result["best_single"]["model_family"]
    assert "roc_auc" in result["test_metrics"]
    trained = [row for row in result["candidates"] if row["status"] == "trained"]
    assert all("cv_score" in row for row in trained)
    assert all(row["n_test_rows"] > 0 for row in trained)
    # Fusion/blending across differently-imputed candidates is deliberately skipped.
    assert result["fusion"] is None


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


def test_target_heuristic_matches_known_churn_alias():
    frame = _frame()
    choice = pick_target_heuristic(frame, list(frame.columns))
    assert choice.column == "churn"
