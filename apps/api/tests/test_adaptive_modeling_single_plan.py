"""One production ModelDevelopmentPlan is created once and consumed by the runner."""

from __future__ import annotations

import tempfile
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from app.engine.experiments.runner import run_experiment
from app.engine.lab.auto_prepare import engineer_features, split_column_roles
from app.engine.modeling.holdout_planner import plan_holdout, require_supported_holdout
from app.engine.modeling.leakage_auditor import plan_model_development
from app.engine.search.generator import assemble_candidates
from app.engine.types import SearchConfig, TaskSpec
from app.engine.validation.splits import SOURCE_ROW_COLUMN, split_train_test_holdout
from adaptive_modeling.fixtures import (
    binary_balanced,
    leakage_fixture,
    regression,
    repeated_entity,
    temporal,
)

PLANNING_EVENT_TYPES = {
    "problem_profile_started",
    "problem_profile_completed",
    "validation_plan_selected",
    "metric_plan_selected",
    "leakage_audit_started",
    "leakage_audit_completed",
    "model_development_plan_locked",
    "holdout_plan_selected",
    "holdout_locked",
}


def _with_source(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if SOURCE_ROW_COLUMN not in out.columns:
        out.insert(0, SOURCE_ROW_COLUMN, list(range(len(out))))
    return out


def _roles(frame: pd.DataFrame, target: str) -> tuple[list[str], list[str]]:
    columns = [name for name in frame.columns if name not in {target, SOURCE_ROW_COLUMN}]
    return split_column_roles(frame, columns)


def _task(frame: pd.DataFrame, *, target: str, task_type: str, metric: str) -> TaskSpec:
    num_cols, cat_cols = _roles(frame, target)
    return TaskSpec(
        id="single-plan",
        name="single-plan",
        task_type=task_type,
        target=target,
        entity_id=None,
        prediction_time_column=None,
        evaluation_metric=metric,
        feature_groups={"features": num_cols + cat_cols},
        validation_strategy="stratified" if task_type == "binary" else "random",
        column_roles={"numerical": num_cols, "categorical": cat_cols},
    )


def _lock_and_plan(frame: pd.DataFrame, *, target: str, task_type: str):
    table = _with_source(frame)
    holdout = plan_holdout(table, target=target, task_type=task_type, test_size=0.2, random_state=42)
    require_supported_holdout(holdout)
    train, _val, _test, _meta = split_train_test_holdout(
        table,
        target=target,
        test_size=holdout.test_size,
        seed=holdout.random_state,
        plan=holdout,
    )
    _profile, validation_plan, metric_plan, _audit, development = plan_model_development(
        train,
        target=target,
        task_type=task_type,
        requested_folds=5,
        random_state=42,
    )
    return table, holdout, development, validation_plan, metric_plan


def _config(holdout, development) -> SearchConfig:
    return SearchConfig(
        strategy="open_ingest",
        max_candidates=8,
        seed=42,
        holdout_plan=holdout.to_dict(),
        model_development_plan=development.to_dict(),
    )


def _run_with_plan(frame: pd.DataFrame, *, target: str, task_type: str, metric: str, on_event=None):
    table, holdout, development, validation_plan, metric_plan = _lock_and_plan(
        frame, target=target, task_type=task_type
    )
    task = _task(table, target=target, task_type=task_type, metric=metric)
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            table,
            task,
            _config(holdout, development),
            artifact_dir=Path(tmp),
            on_event=on_event,
        )
    return result, holdout, development, validation_plan, metric_plan


def _candidate_features(result: dict) -> set[str]:
    names: set[str] = set()
    for row in result.get("candidates") or []:
        names.update(row.get("feature_set") or row.get("features") or [])
    for cols in ((result.get("task") or {}).get("feature_groups") or {}).values():
        names.update(cols)
    return names


def test_production_plan_is_consumed_without_replanning(monkeypatch):
    frame = binary_balanced()
    table, holdout, development, validation_plan, metric_plan = _lock_and_plan(
        frame, target="outcome", task_type="binary"
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("authoritative plan was already supplied")

    monkeypatch.setattr("app.engine.experiments.runner.plan_model_development", boom)
    monkeypatch.setattr("app.engine.experiments.runner.plan_holdout", boom)
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            table,
            task,
            _config(holdout, development),
            artifact_dir=Path(tmp),
        )
    assert result["status"] == "COMPLETED"
    assert result["scientific_plan_source"] == "provided"
    assert result["model_development_plan"] == development.to_dict()
    assert result["validation_plan"] == result["model_development_plan"]["validation_plan"]
    assert result["metric_plan"] == result["model_development_plan"]["metric_plan"]
    assert result["validation_plan"]["strategy"] == validation_plan.strategy
    assert result["metric_plan"]["primary_metric"] == metric_plan.primary_metric
    assert result["selection"]["selection_metric"] == metric_plan.primary_metric
    assert result["task"]["evaluation_metric"] == metric_plan.primary_metric
    assert result["task"]["validation_strategy"] == validation_plan.strategy
    trained = [row for row in result["candidates"] if row.get("status") == "trained"]
    assert trained
    assert all(row["cv_strategy"] == validation_plan.strategy for row in trained)


def test_runner_uses_exact_validation_and_metric_plans():
    result, _holdout, development, validation_plan, metric_plan = _run_with_plan(
        binary_balanced(), target="outcome", task_type="binary", metric="accuracy"
    )
    assert result["status"] == "COMPLETED"
    assert result["validation"]["cv_strategy"] == validation_plan.strategy
    assert result["selection"]["selection_metric"] == metric_plan.primary_metric
    assert result["selection"]["selection_metric"] != "accuracy"
    assert result["model_development_plan"]["plan_version"] == development.plan_version


def test_excluded_feature_never_reenters_candidates():
    frame = leakage_fixture()
    table, holdout, development, _validation, _metric = _lock_and_plan(
        frame, target="outcome", task_type="binary"
    )
    excluded = {row["column"] for row in development.excluded_features}
    assert "result_code" in excluded
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    task = TaskSpec(
        **{
            **task.to_dict(),
            "feature_groups": {"features": [*task.feature_groups["features"], "result_code"]},
            "column_roles": {
                "numerical": [*task.column_roles["numerical"], "result_code"],
                "categorical": list(task.column_roles["categorical"]),
            },
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            table,
            task,
            _config(holdout, development),
            artifact_dir=Path(tmp),
        )
    assert result["status"] == "COMPLETED"
    assert "result_code" not in _candidate_features(result)


def test_temporal_plan_survives_datetime_transformation():
    frame = temporal()
    table, holdout, development, validation_plan, _metric = _lock_and_plan(
        frame, target="revenue", task_type="regression"
    )
    assert development.time_column == "as_of_date"
    assert validation_plan.strategy == "TimeSeriesSplit"
    columns = [name for name in table.columns if name not in {"revenue", SOURCE_ROW_COLUMN}]
    engineered, actions = engineer_features(table, columns)
    assert any(item.get("step") == "datetime_to_unix_seconds" for item in actions)
    assert pd.api.types.is_numeric_dtype(engineered["as_of_date"])
    task = _task(engineered, target="revenue", task_type="regression", metric="mae")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            engineered,
            task,
            _config(holdout, development),
            artifact_dir=Path(tmp),
        )
    assert result["status"] == "COMPLETED"
    assert result["scientific_plan_source"] == "provided"
    assert result["model_development_plan"]["time_column"] == "as_of_date"
    assert result["validation_plan"]["time_column"] == "as_of_date"
    assert result["validation_plan"]["strategy"] == "TimeSeriesSplit"
    assert all(row["cv_strategy"] == "TimeSeriesSplit" for row in result["candidates"] if row["status"] == "trained")


def test_group_column_stays_available_for_validation_not_estimator():
    result, _holdout, development, validation_plan, _metric = _run_with_plan(
        repeated_entity(), target="outcome", task_type="binary", metric="pr_auc"
    )
    assert result["status"] == "COMPLETED"
    assert development.group_column == "customer_id"
    assert result["validation_plan"]["group_column"] == "customer_id"
    assert result["validation_plan"]["strategy"] == validation_plan.strategy
    assert "customer_id" not in _candidate_features(result)
    assert result["task"]["entity_id"] == "customer_id"


def test_candidate_fingerprint_changes_when_validation_strategy_changes():
    table, holdout, development, _validation, _metric = _lock_and_plan(
        binary_balanced(), target="outcome", task_type="binary"
    )
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    config = SearchConfig(strategy="open_ingest", max_candidates=8, seed=42)
    first = assemble_candidates(
        task,
        config,
        dataset_version="v1",
        holdout_plan=holdout,
        development_plan=development,
    )
    altered = deepcopy(development.to_dict())
    altered["validation_plan"]["strategy"] = "KFold"
    second = assemble_candidates(
        task,
        config,
        dataset_version="v1",
        holdout_plan=holdout,
        development_plan=altered,
    )
    assert first
    assert first[0].fingerprint != second[0].fingerprint
    assert first[0].metadata["validation_strategy"] != second[0].metadata["validation_strategy"]


def test_same_scientific_config_keeps_fingerprint_when_task_id_changes():
    table, holdout, development, _validation, _metric = _lock_and_plan(
        binary_balanced(), target="outcome", task_type="binary"
    )
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    config = SearchConfig(strategy="open_ingest", max_candidates=8, seed=42)
    first = assemble_candidates(
        task,
        config,
        dataset_version="v1",
        holdout_plan=holdout,
        development_plan=development,
    )
    renamed = TaskSpec(**{**task.to_dict(), "id": "another-run", "name": "another-run"})
    second = assemble_candidates(
        renamed,
        config,
        dataset_version="v1",
        holdout_plan=holdout,
        development_plan=development,
    )
    assert first[0].fingerprint == second[0].fingerprint
    assert first[0].metadata["validation_strategy"] == second[0].metadata["validation_strategy"]


def test_candidate_fingerprint_changes_when_feature_set_changes():
    table, holdout, development, _validation, _metric = _lock_and_plan(
        binary_balanced(), target="outcome", task_type="binary"
    )
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    config = SearchConfig(strategy="open_ingest", max_candidates=8, seed=42)
    first = assemble_candidates(
        task,
        config,
        dataset_version="v1",
        holdout_plan=holdout,
        development_plan=development,
    )
    reduced = TaskSpec(
        **{
            **task.to_dict(),
            "feature_groups": {"features": [task.feature_groups["features"][0]]},
            "column_roles": {
                "numerical": [task.feature_groups["features"][0]],
                "categorical": [],
            },
        }
    )
    second = assemble_candidates(
        reduced,
        config,
        dataset_version="v1",
        holdout_plan=holdout,
        development_plan=development,
    )
    assert first[0].features != second[0].features
    assert first[0].fingerprint != second[0].fingerprint


def test_provided_plan_does_not_emit_planning_events():
    events: list[str] = []

    def on_event(event_type: str, _payload: dict) -> None:
        events.append(event_type)

    result, *_rest = _run_with_plan(
        binary_balanced(),
        target="outcome",
        task_type="binary",
        metric="pr_auc",
        on_event=on_event,
    )
    assert result["status"] == "COMPLETED"
    assert result["scientific_plan_source"] == "provided"
    assert not (PLANNING_EVENT_TYPES & set(events))
    assert events.count("cv_fold_started") >= 1


def test_computed_plan_emits_planning_events_once():
    events: list[str] = []

    def on_event(event_type: str, _payload: dict) -> None:
        events.append(event_type)

    frame = binary_balanced()
    task = _task(frame, target="outcome", task_type="binary", metric="pr_auc")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=Path(tmp),
            on_event=on_event,
        )
    assert result["status"] == "COMPLETED"
    assert result["scientific_plan_source"] == "computed"
    for name in PLANNING_EVENT_TYPES:
        assert events.count(name) == 1, name


@pytest.mark.parametrize(
    ("factory", "target", "task_type", "metric"),
    [
        (binary_balanced, "outcome", "binary", "pr_auc"),
        (regression, "revenue", "regression", "mae"),
        (repeated_entity, "outcome", "binary", "pr_auc"),
        (temporal, "revenue", "regression", "mae"),
    ],
)
def test_structure_specific_runs_complete_with_provided_plan(factory, target, task_type, metric):
    result, _holdout, development, validation_plan, metric_plan = _run_with_plan(
        factory(), target=target, task_type=task_type, metric=metric
    )
    assert result["status"] == "COMPLETED"
    assert result["scientific_plan_source"] == "provided"
    assert result["validation_plan"]["strategy"] == validation_plan.strategy
    assert result["selection"]["selection_metric"] == metric_plan.primary_metric
    assert result["model_development_plan"]["plan_version"] == development.plan_version
    trained = [row for row in result["candidates"] if row.get("status") == "trained"]
    assert trained
    assert all(row.get("fingerprint") for row in trained)
    identity = trained[0]["metadata"]
    assert identity["validation_strategy"] == validation_plan.strategy
    assert identity["primary_metric"] == metric_plan.primary_metric
    assert identity["model_development_plan_version"] == development.plan_version
