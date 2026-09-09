"""Canonical scientific candidate fingerprints ignore run identity."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd

from adaptive_modeling.fixtures import binary_balanced
from app.engine.lab.auto_prepare import split_column_roles
from app.engine.modeling.holdout_planner import plan_holdout, require_supported_holdout
from app.engine.modeling.leakage_auditor import plan_model_development
from app.engine.search.fingerprint import (
    CANDIDATE_CONFIG_FINGERPRINT_SCHEME,
    scientific_candidate_config_payload,
    scientific_candidate_fingerprint,
)
from app.engine.search.generator import OPEN_INGEST_PREPROCESS, assemble_candidates
from app.engine.types import SearchConfig, TaskSpec
from app.engine.validation.splits import SOURCE_ROW_COLUMN, split_train_test_holdout
from app.services.candidate_modeling_service import _fingerprint_for


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


def _assemble(task, holdout, development, **kwargs):
    config = SearchConfig(strategy="open_ingest", max_candidates=8, seed=42)
    return assemble_candidates(
        task,
        config,
        dataset_version=kwargs.get("dataset_version", "v1"),
        dataset_content_digest=kwargs.get("dataset_content_digest"),
        holdout_plan=holdout,
        development_plan=development,
        feature_set_version_digest=kwargs.get("feature_set_version_digest"),
    )


def _fingerprint_kwargs(task, holdout, development, **overrides):
    features = tuple(task.feature_groups["features"])
    values = {
        "task": task,
        "features": features,
        "family": "logistic_regression",
        "seed": 42,
        "dataset_version": "v1",
        "dataset_content_digest": "ab" * 32,
        "hyperparameters": {},
        "preprocessing": dict(OPEN_INGEST_PREPROCESS),
        "holdout_plan": holdout,
        "development_plan": development,
    }
    values.update(overrides)
    return values


def test_same_scientific_config_same_fingerprint():
    table, holdout, development, _validation, _metric = _lock_and_plan(
        binary_balanced(), target="outcome", task_type="binary"
    )
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    first = _assemble(task, holdout, development, dataset_content_digest="ab" * 32)
    renamed = TaskSpec(**{**task.to_dict(), "id": "other-task-id", "name": "other"})
    second = _assemble(renamed, holdout, development, dataset_content_digest="ab" * 32)
    assert first
    assert first[0].fingerprint == second[0].fingerprint
    kwargs = _fingerprint_kwargs(task, holdout, development)
    assert scientific_candidate_fingerprint(**kwargs) == scientific_candidate_fingerprint(**kwargs)
    payload = scientific_candidate_config_payload(**kwargs)
    assert payload["scheme"] == CANDIDATE_CONFIG_FINGERPRINT_SCHEME
    assert "task_id" not in payload
    assert payload["holdout_plan_digest"]
    assert payload["validation_plan_digest"]
    assert payload["metric_plan_digest"]
    assert payload["model_development_plan_digest"]


def test_hyperparameter_change_changes_fingerprint():
    table, holdout, development, _validation, _metric = _lock_and_plan(
        binary_balanced(), target="outcome", task_type="binary"
    )
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    base = _fingerprint_kwargs(task, holdout, development)
    changed = _fingerprint_kwargs(task, holdout, development, hyperparameters={"max_iter": 250})
    assert scientific_candidate_fingerprint(**base) != scientific_candidate_fingerprint(**changed)


def test_feature_change_changes_fingerprint():
    table, holdout, development, _validation, _metric = _lock_and_plan(
        binary_balanced(), target="outcome", task_type="binary"
    )
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    first = _assemble(task, holdout, development)
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
    second = _assemble(reduced, holdout, development)
    assert first[0].features != second[0].features
    assert first[0].fingerprint != second[0].fingerprint


def test_validation_strategy_change_changes_fingerprint():
    table, holdout, development, _validation, _metric = _lock_and_plan(
        binary_balanced(), target="outcome", task_type="binary"
    )
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    first = _assemble(task, holdout, development)
    altered = deepcopy(development.to_dict())
    altered["validation_plan"]["strategy"] = "KFold"
    second = _assemble(task, holdout, altered)
    assert first[0].fingerprint != second[0].fingerprint


def test_preprocessing_change_changes_fingerprint():
    table, holdout, development, _validation, _metric = _lock_and_plan(
        binary_balanced(), target="outcome", task_type="binary"
    )
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    base = _fingerprint_kwargs(task, holdout, development)
    changed = _fingerprint_kwargs(
        task,
        holdout,
        development,
        preprocessing={**OPEN_INGEST_PREPROCESS, "numeric_imputer": "mean"},
    )
    assert scientific_candidate_fingerprint(**base) != scientific_candidate_fingerprint(**changed)


def test_run_id_and_time_alone_do_not_change_fingerprint():
    table, holdout, development, _validation, _metric = _lock_and_plan(
        binary_balanced(), target="outcome", task_type="binary"
    )
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    base = _fingerprint_kwargs(task, holdout, development)
    noisy_holdout = {
        **holdout.to_dict(),
        "id": str(uuid4()),
        "pipeline_run_id": str(uuid4()),
        "created_at": "2026-01-01T00:00:00+00:00",
        "started_at": "2026-01-01T00:00:00+00:00",
    }
    noisy_development = deepcopy(development.to_dict())
    noisy_development["pipeline_run_id"] = str(uuid4())
    noisy_development["locked_at"] = "2026-09-05T12:00:00+00:00"
    noisy_development["id"] = str(uuid4())
    same = scientific_candidate_fingerprint(
        **_fingerprint_kwargs(task, noisy_holdout, noisy_development)
    )
    assert scientific_candidate_fingerprint(**base) == same


def test_persist_fallback_fingerprint_ignores_pipeline_run_id():
    table, holdout, development, _validation, _metric = _lock_and_plan(
        binary_balanced(), target="outcome", task_type="binary"
    )
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    row = {
        "model_family": "logistic_regression",
        "features": list(task.feature_groups["features"]),
        "random_seed": 42,
        "hyperparameters": {},
        "preprocessing": dict(OPEN_INGEST_PREPROCESS),
    }
    result = {
        "task": {
            "task_type": "binary",
            "target": "outcome",
            "evaluation_metric": "pr_auc",
        },
        "holdout_plan": holdout.to_dict(),
        "model_development_plan": development.to_dict(),
        "validation_plan": development.validation_plan,
        "metric_plan": development.metric_plan,
    }
    dataset = SimpleNamespace(version="v1", content_digest="cd" * 32)
    first = SimpleNamespace(id=uuid4(), result=result, dataset=dataset, seed=42, config={})
    second = SimpleNamespace(id=uuid4(), result=result, dataset=dataset, seed=42, config={})
    assert _fingerprint_for(first, row, "logistic_regression") == _fingerprint_for(
        second, row, "logistic_regression"
    )
    assert "pipeline_run" not in scientific_candidate_config_payload(
        **_fingerprint_kwargs(task, holdout, development)
    )


def test_feature_order_does_not_change_fingerprint():
    table, holdout, development, _validation, _metric = _lock_and_plan(
        binary_balanced(), target="outcome", task_type="binary"
    )
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    features = list(task.feature_groups["features"])
    left = scientific_candidate_fingerprint(
        **_fingerprint_kwargs(task, holdout, development, features=features)
    )
    right = scientific_candidate_fingerprint(
        **_fingerprint_kwargs(task, holdout, development, features=list(reversed(features)))
    )
    assert left == right


def test_dataset_or_feature_set_identity_change_changes_fingerprint():
    table, holdout, development, _validation, _metric = _lock_and_plan(
        binary_balanced(), target="outcome", task_type="binary"
    )
    task = _task(table, target="outcome", task_type="binary", metric="pr_auc")
    base = _fingerprint_kwargs(task, holdout, development, dataset_content_digest="ab" * 32)
    other_dataset = _fingerprint_kwargs(
        task, holdout, development, dataset_content_digest="cd" * 32
    )
    other_features = _fingerprint_kwargs(
        task, holdout, development, feature_set_version_digest="ef" * 32
    )
    assert scientific_candidate_fingerprint(**base) != scientific_candidate_fingerprint(
        **other_dataset
    )
    assert scientific_candidate_fingerprint(**base) != scientific_candidate_fingerprint(
        **other_features
    )
