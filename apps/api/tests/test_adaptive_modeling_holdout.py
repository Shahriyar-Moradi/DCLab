"""Adaptive final holdout: strategy matches prediction structure."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.engine.experiments.runner import run_experiment
from app.engine.lab.auto_prepare import split_column_roles
from app.engine.modeling.holdout_planner import (
    GROUP_DISJOINT,
    RANDOM,
    STRATIFIED_RANDOM,
    TEMPORAL_FUTURE,
    UNSUPPORTED,
    HoldoutUnsupportedError,
    plan_holdout,
    require_supported_holdout,
)
from app.engine.types import SearchConfig, TaskSpec
from app.engine.validation.splits import SOURCE_ROW_COLUMN, split_train_test_holdout
from adaptive_modeling.fixtures import (
    binary_balanced,
    grouped_and_temporal,
    regression,
    repeated_entity,
    temporal,
)


def _with_source(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if SOURCE_ROW_COLUMN not in out.columns:
        out.insert(0, SOURCE_ROW_COLUMN, np.arange(len(out)))
    return out


def _lock(frame: pd.DataFrame, *, target: str, task_type: str):
    table = _with_source(frame)
    plan = plan_holdout(table, target=target, task_type=task_type, test_size=0.2, random_state=42)
    require_supported_holdout(plan)
    train, _val, test, meta = split_train_test_holdout(
        table,
        target=target,
        test_size=plan.test_size,
        seed=plan.random_state,
        plan=plan,
    )
    return plan, train, test, meta


def _roles(frame: pd.DataFrame, target: str) -> tuple[list[str], list[str]]:
    columns = [name for name in frame.columns if name not in {target, SOURCE_ROW_COLUMN}]
    return split_column_roles(frame, columns)


def _task(frame: pd.DataFrame, *, target: str, task_type: str, metric: str) -> TaskSpec:
    num_cols, cat_cols = _roles(frame, target)
    return TaskSpec(
        id="holdout-repair",
        name="holdout-repair",
        task_type=task_type,
        target=target,
        entity_id=None,
        prediction_time_column=None,
        evaluation_metric=metric,
        feature_groups={"features": num_cols + cat_cols},
        validation_strategy="stratified" if task_type == "binary" else "random",
        column_roles={"numerical": num_cols, "categorical": cat_cols},
    )


def _run(frame: pd.DataFrame, *, target: str, task_type: str, metric: str):
    task = _task(frame, target=target, task_type=task_type, metric=metric)
    with tempfile.TemporaryDirectory() as tmp:
        return run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=Path(tmp),
        )


def test_ordinary_binary_uses_stratified_holdout():
    plan, train, test, meta = _lock(binary_balanced(), target="outcome", task_type="binary")
    assert plan.strategy == STRATIFIED_RANDOM
    assert plan.stratified is True
    assert meta["strategy"] == STRATIFIED_RANDOM
    assert meta["stratify"] is True
    assert meta["n_val"] == 0
    assert abs(meta["n_test"] / (meta["n_train"] + meta["n_test"]) - 0.2) < 0.06
    assert set(train["outcome"].unique()) == {0, 1}
    assert set(test["outcome"].unique()) == {0, 1}


def test_ordinary_regression_uses_random_holdout():
    plan, _train, _test, meta = _lock(regression(), target="revenue", task_type="regression")
    assert plan.strategy == RANDOM
    assert plan.stratified is False
    assert meta["strategy"] == RANDOM
    assert meta["stratify"] is False
    assert meta["n_val"] == 0


def test_repeated_customer_uses_group_disjoint_holdout():
    plan, train, test, meta = _lock(repeated_entity(), target="outcome", task_type="binary")
    assert plan.strategy == GROUP_DISJOINT
    assert plan.group_column == "customer_id"
    assert meta["strategy"] == GROUP_DISJOINT
    assert meta["group_column"] == "customer_id"
    train_groups = set(train["customer_id"])
    test_groups = set(test["customer_id"])
    assert train_groups.isdisjoint(test_groups)
    assert meta["group_overlap_count"] == 0
    assert meta["group_overlap"] == []


def test_temporal_fixture_uses_future_holdout():
    plan, train, test, meta = _lock(temporal(), target="revenue", task_type="regression")
    assert plan.strategy == TEMPORAL_FUTURE
    assert plan.time_column == "as_of_date"
    assert meta["strategy"] == TEMPORAL_FUTURE
    assert meta["time_column"] == "as_of_date"
    assert pd.Timestamp(meta["train_time_max"]) <= pd.Timestamp(meta["test_time_min"])
    assert train["as_of_date"].max() <= test["as_of_date"].min()
    assert list(train["as_of_date"]) == sorted(train["as_of_date"].tolist())
    assert list(test["as_of_date"]) == sorted(test["as_of_date"].tolist())


def test_grouped_and_temporal_is_explicitly_unsupported():
    frame = grouped_and_temporal()
    plan = plan_holdout(frame, target="outcome", task_type="binary")
    assert plan.strategy == UNSUPPORTED
    assert plan.group_column == "customer_id"
    assert plan.time_column == "as_of_date"
    with pytest.raises(HoldoutUnsupportedError):
        require_supported_holdout(plan)
    with pytest.raises(ValueError, match="combined final-holdout|unsupported"):
        split_train_test_holdout(frame, target="outcome", plan=plan)
    with pytest.raises(HoldoutUnsupportedError):
        _run(frame, target="outcome", task_type="binary", metric="pr_auc")


def test_train_test_provenance_is_disjoint():
    _plan, train, test, meta = _lock(binary_balanced(), target="outcome", task_type="binary")
    train_rows = set(meta["train_source_rows"])
    test_rows = set(meta["test_source_rows"])
    assert train_rows.isdisjoint(test_rows)
    assert set(train[SOURCE_ROW_COLUMN]) == train_rows
    assert set(test[SOURCE_ROW_COLUMN]) == test_rows
    assert meta["provenance_disjoint"] is True
    assert meta["train_test_provenance"] == "disjoint"


def test_grouped_train_test_entity_overlap_is_zero():
    _plan, train, test, meta = _lock(repeated_entity(), target="outcome", task_type="binary")
    overlap = set(train["customer_id"]) & set(test["customer_id"])
    assert overlap == set()
    assert int(meta["group_overlap_count"]) == 0


def test_temporal_train_max_is_not_after_test_min():
    _plan, train, test, meta = _lock(temporal(), target="revenue", task_type="regression")
    assert train["as_of_date"].max() <= test["as_of_date"].min()
    assert pd.Timestamp(meta["train_time_max"]) <= pd.Timestamp(meta["test_time_min"])


def test_existing_classification_run_still_completes():
    result = _run(binary_balanced(), target="outcome", task_type="binary", metric="pr_auc")
    assert result["status"] == "COMPLETED"
    assert result["holdout_plan"]["strategy"] == STRATIFIED_RANDOM
    assert result["split"]["strategy"] == STRATIFIED_RANDOM
    assert result["validation_plan"]["strategy"] == "StratifiedKFold"
    assert result["best_single"]["locked"] is True


def test_existing_regression_run_still_completes():
    result = _run(regression(), target="revenue", task_type="regression", metric="mae")
    assert result["status"] == "COMPLETED"
    assert result["holdout_plan"]["strategy"] == RANDOM
    assert result["split"]["strategy"] == RANDOM
    assert result["validation_plan"]["strategy"] == "KFold"
    assert "mae" in result["test_metrics"]


def test_repeated_entity_run_locks_group_holdout():
    result = _run(repeated_entity(), target="outcome", task_type="binary", metric="pr_auc")
    assert result["status"] == "COMPLETED"
    assert result["holdout_plan"]["strategy"] == GROUP_DISJOINT
    assert result["split"]["group_overlap_count"] == 0
    assert result["validation_plan"]["strategy"] in {"StratifiedGroupKFold", "GroupKFold"}


def test_temporal_run_locks_future_holdout():
    result = _run(temporal(), target="revenue", task_type="regression", metric="mae")
    assert result["status"] == "COMPLETED"
    assert result["holdout_plan"]["strategy"] == TEMPORAL_FUTURE
    assert pd.Timestamp(result["split"]["train_time_max"]) <= pd.Timestamp(result["split"]["test_time_min"])
    assert result["validation_plan"]["strategy"] == "TimeSeriesSplit"


def test_low_cardinality_category_id_is_not_a_grouping_key():
    """delivery_category_id with 3 levels is a feature, not a repeated entity."""
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
    plan = plan_holdout(frame, target="hyper_ack", task_type="binary")
    assert plan.strategy == TEMPORAL_FUTURE
    assert plan.group_column is None
    assert plan.time_column == "first_created_at"
    require_supported_holdout(plan)
