"""One canonical scientific plan row per PipelineRun."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from adaptive_modeling.fixtures import ordinary_binary, regression, repeated_entity, temporal
from adaptive_modeling.production import labs_upload_and_train
from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    Dataset,
    DatasetAsset,
    Experiment,
    PipelineScientificPlan,
)
from app.engine.modeling.holdout_planner import plan_holdout, require_supported_holdout
from app.engine.modeling.leakage_auditor import plan_model_development
from app.engine.validation.splits import SOURCE_ROW_COLUMN, split_train_test_holdout
from app.services.lab_service import seed_dogfood
from app.services.scientific_lineage_service import persist_scientific_plan


@pytest.fixture
def _rule_engine_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )


def _with_source(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if SOURCE_ROW_COLUMN not in out.columns:
        out.insert(0, SOURCE_ROW_COLUMN, list(range(len(out))))
    return out


def _lock_and_plan(frame: pd.DataFrame, *, target: str, task_type: str, requested_folds: int = 5):
    table = _with_source(frame)
    holdout = plan_holdout(table, target=target, task_type=task_type, test_size=0.2, random_state=42)
    require_supported_holdout(holdout)
    train, _val, _test, split = split_train_test_holdout(
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
        requested_folds=requested_folds,
        random_state=42,
    )
    return holdout, development, validation_plan, metric_plan, split


def _pipeline_run(db_session) -> Experiment:
    environment = seed_dogfood(db_session)
    slug = f"plan-{uuid4().hex[:12]}"
    asset = DatasetAsset(workspace_id=DEFAULT_WORKSPACE_ID, name=slug, slug=slug)
    db_session.add(asset)
    db_session.flush()
    dataset = Dataset(
        workspace_id=DEFAULT_WORKSPACE_ID,
        dataset_asset_id=asset.id,
        environment_id=environment.id,
        name=slug,
        source_type="csv",
        location=f"/tmp/{slug}.csv",
        version="v1",
        row_count=1,
        column_count=1,
    )
    db_session.add(dataset)
    db_session.flush()
    experiment = Experiment(
        workspace_id=DEFAULT_WORKSPACE_ID,
        environment_id=environment.id,
        dataset_id=dataset.id,
        status="CREATED",
        config={},
        result={},
    )
    db_session.add(experiment)
    db_session.flush()
    return experiment


def _persist(db_session, experiment, holdout, development, split=None):
    return persist_scientific_plan(
        db_session,
        experiment,
        holdout_plan=holdout,
        development_plan=development,
        split=split if isinstance(split, dict) else {},
    )


def test_binary_stratified_random_holdout_is_queryable(db_session):
    holdout, development, validation, metric, split = _lock_and_plan(
        ordinary_binary(), target="outcome", task_type="binary"
    )
    experiment = _pipeline_run(db_session)
    row = _persist(db_session, experiment, holdout, development, split)
    db_session.commit()
    assert row is not None
    assert holdout.strategy == "stratified_random"
    assert row.task_type == "binary"
    assert row.holdout_strategy == "stratified_random"
    assert row.holdout_test_size == pytest.approx(holdout.test_size)
    assert row.validation_strategy == validation.strategy
    assert row.primary_metric == metric.primary_metric
    assert row.group_column is None
    assert row.time_column is None
    assert row.allowed_feature_count == len(development.allowed_features)
    assert row.pipeline_run_id == experiment.id
    assert row.full_plan["holdout_plan"]["strategy"] == "stratified_random"


def test_grouped_holdout_records_group_column(db_session):
    holdout, development, validation, _metric, split = _lock_and_plan(
        repeated_entity(), target="outcome", task_type="binary"
    )
    experiment = _pipeline_run(db_session)
    row = _persist(db_session, experiment, holdout, development, split)
    db_session.commit()
    assert holdout.strategy == "group_disjoint"
    assert row.holdout_strategy == "group_disjoint"
    assert row.group_column == "customer_id"
    assert row.validation_strategy in {"StratifiedGroupKFold", "GroupKFold"}
    assert row.validation_strategy == validation.strategy
    assert row.time_column is None


def test_temporal_holdout_records_time_column(db_session):
    holdout, development, validation, _metric, split = _lock_and_plan(
        temporal(), target="revenue", task_type="regression"
    )
    experiment = _pipeline_run(db_session)
    row = _persist(db_session, experiment, holdout, development, split)
    db_session.commit()
    assert holdout.strategy == "temporal_future"
    assert row.holdout_strategy == "temporal_future"
    assert row.time_column == "as_of_date"
    assert row.validation_strategy == "TimeSeriesSplit"
    assert row.validation_strategy == validation.strategy
    assert row.task_type == "regression"
    assert row.group_column is None


def test_regression_random_holdout_is_queryable(db_session):
    holdout, development, validation, metric, split = _lock_and_plan(
        regression(), target="revenue", task_type="regression"
    )
    experiment = _pipeline_run(db_session)
    row = _persist(db_session, experiment, holdout, development, split)
    db_session.commit()
    assert holdout.strategy == "random"
    assert row.holdout_strategy == "random"
    assert row.task_type == "regression"
    assert row.validation_strategy == "KFold"
    assert row.validation_strategy == validation.strategy
    assert row.primary_metric == metric.primary_metric
    assert row.group_column is None
    assert row.time_column is None


def test_requested_folds_can_differ_from_actual_folds(db_session):
    from app.engine.modeling.holdout_planner import HoldoutPlan, STRATIFIED_RANDOM
    from app.engine.modeling.leakage_auditor import ModelDevelopmentPlan
    from app.engine.modeling.metric_planner import MetricPlan
    from app.engine.modeling.validation_planner import STRATIFIED_KFOLD, ValidationPlan

    holdout = HoldoutPlan(
        strategy=STRATIFIED_RANDOM,
        test_size=0.2,
        random_state=42,
        stratified=True,
        group_column=None,
        time_column=None,
        reason="binary random holdout",
    )
    validation = ValidationPlan(
        strategy=STRATIFIED_KFOLD,
        requested_folds=5,
        actual_folds=3,
        shuffle=True,
        random_state=42,
        group_column=None,
        time_column=None,
        stratified=True,
        reason="minority class cannot support five stratified folds",
        fallback_reason="Reduced folds because the minority class cannot support five stratified folds.",
    )
    metric = MetricPlan(primary_metric="pr_auc", reason="binary")
    development = ModelDevelopmentPlan(
        problem_profile={"task_type": "binary"},
        validation_plan=validation.to_dict(),
        metric_plan=metric.to_dict(),
        feature_availability=[],
        leakage_assessment={},
        allowed_features=["x"],
        excluded_features=[],
        group_column=None,
        time_column=None,
        recommended_model_family_hints=[],
    )
    experiment = _pipeline_run(db_session)
    row = _persist(db_session, experiment, holdout, development, {"n_train": 80, "n_test": 20})
    db_session.commit()
    assert row.requested_folds == 5
    assert row.actual_folds == 3
    assert row.requested_folds != row.actual_folds


def test_one_plan_per_pipeline_run(db_session):
    holdout, development, _validation, _metric, split = _lock_and_plan(
        ordinary_binary(), target="outcome", task_type="binary"
    )
    first = _pipeline_run(db_session)
    second = _pipeline_run(db_session)
    row_a = _persist(db_session, first, holdout, development, split)
    row_a_again = _persist(db_session, first, holdout, development, split)
    row_b = _persist(db_session, second, holdout, development, split)
    db_session.commit()
    assert row_a is not None and row_b is not None
    assert row_a.id == row_a_again.id
    assert row_a.pipeline_run_id != row_b.pipeline_run_id
    assert (
        db_session.scalar(
            select(func.count(PipelineScientificPlan.id)).where(
                PipelineScientificPlan.pipeline_run_id == first.id
            )
        )
        == 1
    )
    duplicate = PipelineScientificPlan(
        workspace_id=first.workspace_id,
        project_id=first.project_id,
        pipeline_run_id=first.id,
        task_type="binary",
        holdout_strategy="stratified_random",
        holdout_test_size=0.2,
        validation_strategy="StratifiedKFold",
        requested_folds=5,
        actual_folds=5,
        primary_metric="pr_auc",
        allowed_feature_count=0,
        excluded_feature_count=0,
        holdout_plan_digest="a" * 64,
        model_development_plan_digest="b" * 64,
        full_plan={},
        locked_at=row_a.locked_at,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_explorer_reads_normalized_plan_without_experiment_result(
    auth_client, admin_client, db_session, monkeypatch, _rule_engine_only
):
    frame = ordinary_binary()
    _upload, _workflow_run, experiment, _model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="scientific_plan_explorer.csv",
        target="outcome",
    )
    row = db_session.scalar(
        select(PipelineScientificPlan).where(
            PipelineScientificPlan.pipeline_run_id == experiment.id
        )
    )
    assert row is not None
    assert row.holdout_strategy == "stratified_random"
    assert row.task_type == "binary"
    result = dict(experiment.result or {})
    for key in (
        "holdout_plan",
        "validation_plan",
        "model_development_plan",
        "metric_plan",
        "split",
        "task",
    ):
        result.pop(key, None)
    experiment.result = result
    flag_modified(experiment, "result")
    db_session.commit()
    stored = db_session.get(Experiment, experiment.id)
    assert stored is not None
    assert "holdout_plan" not in (stored.result or {})
    assert "validation_plan" not in (stored.result or {})
    assert "model_development_plan" not in (stored.result or {})
    assert "split" not in (stored.result or {})

    response = admin_client.get(f"/admin/explorer/pipeline-runs/{experiment.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["development_plan"]["task_type"] == "binary"
    assert body["development_plan"]["primary_metric"] == row.primary_metric
    assert body["split_validation"]["holdout_plan"]["strategy"] == "stratified_random"
    assert body["split_validation"]["holdout_plan"]["test_size"] == pytest.approx(
        row.holdout_test_size
    )
    assert body["split_validation"]["validation_plan"]["strategy"] == row.validation_strategy
    assert body["split_validation"]["validation_plan"]["requested_folds"] == row.requested_folds
    assert body["split_validation"]["validation_plan"]["actual_folds"] == row.actual_folds
    assert body["split_validation"]["split"].get("n_train") or body["split_validation"]["split"].get(
        "n_test"
    )
