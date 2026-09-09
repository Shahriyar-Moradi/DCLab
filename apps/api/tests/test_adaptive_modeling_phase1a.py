"""Phase 1A: problem profile, validation plan, and metric plan."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.engine.experiments.runner import run_experiment
from app.engine.lab.auto_prepare import split_column_roles
from app.engine.modeling.metric_planner import plan_metrics
from app.engine.modeling.problem_profile import build_problem_profile
from app.engine.modeling.validation_planner import (
    ValidationUnsupportedError,
    iter_validation_folds,
    plan_validation,
)
from app.engine.types import SearchConfig, TaskSpec
from app.engine.validation.splits import SOURCE_ROW_COLUMN


def _roles(frame: pd.DataFrame, target: str) -> tuple[list[str], list[str]]:
    columns = [name for name in frame.columns if name not in {target, SOURCE_ROW_COLUMN}]
    return split_column_roles(frame, columns)


def _task(frame: pd.DataFrame, *, target: str, task_type: str, metric: str) -> TaskSpec:
    num_cols, cat_cols = _roles(frame, target)
    return TaskSpec(
        id="phase1a",
        name="phase1a",
        task_type=task_type,
        target=target,
        entity_id=None,
        prediction_time_column=None,
        evaluation_metric=metric,
        feature_groups={"features": num_cols + cat_cols},
        validation_strategy="stratified" if task_type == "binary" else "random",
        column_roles={"numerical": num_cols, "categorical": cat_cols},
    )


def _balanced_binary(n: int = 200, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    outcome = np.array([0, 1] * (n // 2) + [0] * (n % 2))
    rng.shuffle(outcome)
    return pd.DataFrame(
        {
            "age": rng.normal(40, 12, n),
            "income": rng.normal(50_000, 8_000, n),
            "region": rng.choice(["N", "S"], n),
            "outcome": outcome,
        }
    )


def _imbalanced_binary(n: int = 250, seed: int = 2) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    outcome = np.array([1] * 20 + [0] * (n - 20))
    rng.shuffle(outcome)
    return pd.DataFrame(
        {
            "age": rng.normal(40, 12, n) + outcome * 8,
            "spend": rng.normal(100, 20, n),
            "segment": rng.choice(["a", "b", "c"], n),
            "outcome": outcome,
        }
    )


def _regression_frame(n: int = 180, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 40, n)
    usage = rng.normal(20, 5, n)
    return pd.DataFrame(
        {
            "tenure": tenure,
            "usage": usage,
            "segment": rng.choice(["small", "mid"], n),
            "revenue": 80 + tenure * 3.2 + usage * 1.4 + rng.normal(0, 4, n),
        }
    )


def _repeated_entity_binary(n_entities: int = 20, repeats: int = 5, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for entity in range(n_entities):
        label = int(entity % 4 == 0)
        for visit in range(repeats):
            rows.append(
                {
                    "customer_id": f"C{entity:03d}",
                    "visit": visit,
                    "amount": float(rng.normal(50, 10) + 20 * label),
                    "channel": "web" if visit % 2 == 0 else "store",
                    "outcome": label,
                }
            )
    return pd.DataFrame(rows)


def _repeated_entity_regression(n_entities: int = 18, repeats: int = 4, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for entity in range(n_entities):
        baseline = 100 + entity
        for month in range(repeats):
            rows.append(
                {
                    "account_id": f"A{entity:03d}",
                    "month": month,
                    "usage": float(rng.normal(10, 2)),
                    "revenue": float(baseline + month * 3 + rng.normal(0, 2)),
                }
            )
    return pd.DataFrame(rows)


def _temporal_frame(n: int = 80, seed: int = 6) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2024-01-01")
    return pd.DataFrame(
        {
            "as_of_date": [start + pd.Timedelta(days=i) for i in range(n)],
            "demand": rng.normal(20, 3, n) + np.arange(n) * 0.2,
            "promo": rng.choice(["none", "on"], n),
            "revenue": 40 + np.arange(n) * 0.4 + rng.normal(0, 2, n),
        }
    )


def _geo_frame(n: int = 60, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "latitude": rng.uniform(25.0, 48.0, n),
            "longitude": rng.uniform(-122.0, -70.0, n),
            "score": rng.normal(0, 1, n),
            "outcome": rng.integers(0, 2, n),
        }
    )


def test_balanced_binary_profile():
    frame = _balanced_binary()
    profile = build_problem_profile(frame, target="outcome", task_type="binary")
    assert profile.task_type == "binary"
    assert profile.row_count == len(frame)
    assert profile.feature_count >= 2
    assert profile.class_distribution is not None
    assert profile.minority_class_fraction is not None
    assert profile.minority_class_fraction >= 0.4
    assert profile.imbalance_ratio is not None
    assert profile.imbalance_ratio < 2
    assert "age" in profile.numeric_columns
    assert "region" in profile.categorical_columns


def test_imbalanced_binary_profile():
    frame = _imbalanced_binary()
    profile = build_problem_profile(frame, target="outcome", task_type="binary")
    assert profile.imbalance_ratio is not None and profile.imbalance_ratio >= 2
    assert profile.minority_class_fraction is not None and profile.minority_class_fraction < 0.35
    assert set(profile.class_distribution or {}) >= {"0", "1"}


def test_regression_profile():
    frame = _regression_frame()
    profile = build_problem_profile(frame, target="revenue", task_type="regression")
    assert profile.class_distribution is None
    assert profile.regression_target is not None
    assert profile.regression_target["mean"] is not None
    assert "tenure" in profile.numeric_columns


def test_repeated_entity_detection():
    frame = _repeated_entity_binary()
    profile = build_problem_profile(frame, target="outcome", task_type="binary")
    names = [item["column"] for item in profile.repeated_entity_candidates]
    assert "customer_id" in names
    evidence = profile.repeated_entity_candidates[0]
    assert evidence["unique_count"] == 20
    assert evidence["max_rows_per_entity"] == 5
    assert evidence["mean_rows_per_entity"] == 5
    assert evidence["repeated_rows"] == 100


def test_low_cardinality_category_id_is_not_a_repeated_entity():
    rng = np.random.default_rng(11)
    n = 120
    start = pd.Timestamp("2024-01-01")
    frame = pd.DataFrame(
        {
            "deliverey_category_id": rng.choice([1, 2, 3], n),
            "first_created_at": [start + pd.Timedelta(hours=i) for i in range(n)],
            "amount": rng.normal(50, 10, n),
            "hyper_ack": rng.integers(0, 2, n),
        }
    )
    profile = build_problem_profile(frame, target="hyper_ack", task_type="binary")
    names = [item["column"] for item in profile.repeated_entity_candidates]
    assert "deliverey_category_id" not in names


def test_datetime_candidate_detection():
    frame = _temporal_frame()
    profile = build_problem_profile(frame, target="revenue", task_type="regression")
    names = [item["column"] for item in profile.time_candidates]
    assert "as_of_date" in names
    candidate = next(item for item in profile.time_candidates if item["column"] == "as_of_date")
    assert candidate["unique_count"] == len(frame)
    assert candidate["strong_name"] is True


def test_geo_coordinate_detection():
    frame = _geo_frame()
    profile = build_problem_profile(frame, target="outcome", task_type="binary")
    assert profile.geo_coordinate_candidates
    pair = profile.geo_coordinate_candidates[0]
    assert pair["lat_column"] == "latitude"
    assert pair["lon_column"] == "longitude"


def test_ordinary_binary_uses_stratified_kfold():
    frame = _balanced_binary()
    profile = build_problem_profile(frame, target="outcome", task_type="binary")
    plan = plan_validation(profile, y=frame["outcome"], frame=frame)
    assert plan.strategy == "StratifiedKFold"
    assert plan.requested_folds == 5
    assert plan.actual_folds == 5
    assert plan.stratified is True
    assert plan.fallback_reason is None


def test_ordinary_regression_uses_kfold():
    frame = _regression_frame()
    profile = build_problem_profile(frame, target="revenue", task_type="regression")
    plan = plan_validation(profile, y=frame["revenue"], frame=frame)
    assert plan.strategy == "KFold"
    assert plan.actual_folds == 5
    assert plan.stratified is False


def test_repeated_binary_entity_uses_group_aware_cv():
    frame = _repeated_entity_binary()
    profile = build_problem_profile(frame, target="outcome", task_type="binary")
    plan = plan_validation(profile, y=frame["outcome"], frame=frame)
    assert plan.strategy in {"StratifiedGroupKFold", "GroupKFold"}
    assert plan.group_column == "customer_id"
    assert plan.actual_folds == 5


def test_repeated_regression_entity_uses_group_kfold():
    frame = _repeated_entity_regression()
    profile = build_problem_profile(frame, target="revenue", task_type="regression")
    plan = plan_validation(profile, y=frame["revenue"], frame=frame)
    assert plan.strategy == "GroupKFold"
    assert plan.group_column == "account_id"
    assert plan.actual_folds == 5


def test_strong_temporal_fixture_uses_time_series_split():
    frame = _temporal_frame()
    profile = build_problem_profile(frame, target="revenue", task_type="regression")
    plan = plan_validation(profile, y=frame["revenue"], frame=frame)
    assert plan.strategy == "TimeSeriesSplit"
    assert plan.time_column == "as_of_date"
    assert plan.shuffle is False
    assert plan.actual_folds == 5


def test_insufficient_minority_rows_reduce_folds_truthfully():
    y = pd.Series([1, 1, 1] + [0] * 97)
    frame = pd.DataFrame({"x": np.arange(100), "customer": 0, "outcome": y})
    profile = build_problem_profile(frame, target="outcome", task_type="binary")
    plan = plan_validation(profile, y=y, frame=frame, requested_folds=5)
    assert plan.strategy == "StratifiedKFold"
    assert plan.requested_folds == 5
    assert plan.actual_folds == 3
    assert plan.fallback_reason
    assert "five" in plan.fallback_reason.lower() or "5" in plan.fallback_reason


def test_group_overlap_is_zero():
    frame = _repeated_entity_binary()
    profile = build_problem_profile(frame, target="outcome", task_type="binary")
    plan = plan_validation(profile, y=frame["outcome"], frame=frame)
    folds = list(iter_validation_folds(plan, frame, frame["outcome"].to_numpy()))
    assert folds
    for fold in folds:
        assert fold.group_overlap == []
        train_groups = set(frame.iloc[fold.train_index]["customer_id"])
        val_groups = set(frame.iloc[fold.validation_index]["customer_id"])
        assert train_groups.isdisjoint(val_groups)


def test_temporal_validation_preserves_order():
    frame = _temporal_frame()
    profile = build_problem_profile(frame, target="revenue", task_type="regression")
    plan = plan_validation(profile, y=frame["revenue"], frame=frame)
    folds = list(iter_validation_folds(plan, frame, frame["revenue"].to_numpy()))
    assert folds
    for fold in folds:
        assert fold.train_time_max is not None
        assert fold.validation_time_min is not None
        assert pd.Timestamp(fold.train_time_max) <= pd.Timestamp(fold.validation_time_min)
        train_times = pd.to_datetime(frame.iloc[fold.train_index]["as_of_date"])
        val_times = pd.to_datetime(frame.iloc[fold.validation_index]["as_of_date"])
        assert train_times.max() <= val_times.min()


def test_imbalanced_classification_chooses_pr_auc():
    frame = _imbalanced_binary()
    profile = build_problem_profile(frame, target="outcome", task_type="binary")
    plan = plan_metrics(profile)
    assert plan.primary_metric == "pr_auc"
    assert "pr_auc" in plan.reason.lower() or "imbalance" in plan.reason.lower()
    for name in ("roc_auc", "f1", "balanced_accuracy", "brier_score"):
        assert name in plan.secondary_metrics


def test_regression_chooses_mae():
    frame = _regression_frame()
    profile = build_problem_profile(frame, target="revenue", task_type="regression")
    plan = plan_metrics(profile)
    assert plan.primary_metric == "mae"
    assert plan.secondary_metrics == ["rmse", "r2", "mse"]


def test_winner_selection_metric_matches_metric_plan():
    frame = _imbalanced_binary()
    task = _task(frame, target="outcome", task_type="binary", metric="accuracy")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=Path(tmp),
        )
    assert result["status"] == "COMPLETED"
    assert result["metric_plan"]["primary_metric"] == "pr_auc"
    assert result["selection"]["selection_metric"] == "pr_auc"
    assert result["task"]["evaluation_metric"] == "pr_auc"


def test_classification_upload_still_completes():
    frame = _balanced_binary()
    task = _task(frame, target="outcome", task_type="binary", metric="pr_auc")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=Path(tmp),
        )
    assert result["status"] == "COMPLETED"
    assert result["problem_profile"]["task_type"] == "binary"
    assert result["validation_plan"]["strategy"] == "StratifiedKFold"
    assert result["best_single"]["locked"] is True
    assert result["split"]["n_val"] == 0


def test_regression_upload_still_completes():
    frame = _regression_frame()
    task = _task(frame, target="revenue", task_type="regression", metric="mae")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=Path(tmp),
        )
    assert result["status"] == "COMPLETED"
    assert result["metric_plan"]["primary_metric"] == "mae"
    assert result["validation_plan"]["strategy"] == "KFold"
    assert "mae" in result["test_metrics"]
    assert "mse" in result["test_metrics"]


def test_final_holdout_remains_isolated():
    frame = _balanced_binary()
    frame.insert(0, SOURCE_ROW_COLUMN, np.arange(len(frame)))
    task = _task(frame, target="outcome", task_type="binary", metric="pr_auc")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=Path(tmp),
        )
    split = result["split"]
    assert split["provenance_disjoint"] is True
    assert set(split["train_source_rows"]).isdisjoint(split["test_source_rows"])
    trained = [row for row in result["candidates"] if row["status"] == "trained"]
    assert trained
    for row in trained:
        for fold in row["folds"]:
            assert set(fold["train_provenance"]).isdisjoint(set(split["test_source_rows"]))
            assert set(fold["validation_provenance"]).isdisjoint(set(split["test_source_rows"]))
    winner_id = result["selection"]["selected_candidate_id"]
    assert all(row["test_metrics"] is None for row in trained if row["candidate_id"] != winner_id)


def test_temporal_and_grouping_together_are_unsupported():
    frame = _repeated_entity_binary()
    start = pd.Timestamp("2024-01-01")
    frame = frame.copy()
    frame["as_of_date"] = [start + pd.Timedelta(days=i) for i in range(len(frame))]
    profile = build_problem_profile(frame, target="outcome", task_type="binary")
    plan = plan_validation(profile, y=frame["outcome"], frame=frame)
    assert plan.strategy == "unsupported"
    assert plan.group_column == "customer_id"
    assert plan.time_column == "as_of_date"
    with pytest.raises(ValidationUnsupportedError):
        list(iter_validation_folds(plan, frame, frame["outcome"].to_numpy()))
