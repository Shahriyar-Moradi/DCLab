"""Holdout test predictions are persisted per experiment, not on Opportunity.Prediction."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.db.models import ExperimentTestPrediction, Prediction
from app.engine.datasets.synthetic import SYNTHETIC_GROUPS
from app.engine.lab.auto_prepare import split_column_roles
from app.engine.types import SearchConfig, TaskSpec
from app.services.lab_service import (
    create_experiment,
    execute_experiment,
    ingest_dataset,
    ingest_synthetic,
    seed_dogfood,
    upsert_task,
)


def _synthetic_spec() -> TaskSpec:
    return TaskSpec(
        id="purchase_prediction",
        name="Purchase",
        task_type="binary",
        target="purchase_within_60d",
        entity_id="entity_id",
        prediction_time_column="as_of_date",
        evaluation_metric="pr_auc",
        feature_groups=SYNTHETIC_GROUPS,
        validation_strategy="time",
    )


def test_completed_experiment_persists_one_row_per_test_record(db_session):
    env = seed_dogfood(db_session)
    dataset = ingest_synthetic(db_session, env, n=200)
    task = upsert_task(db_session, env, _synthetic_spec())
    experiment = create_experiment(
        db_session,
        environment=env,
        dataset=dataset,
        task=task,
        config=SearchConfig(max_candidates=4, max_feature_group_combinations=2, n_robustness_folds=2, seed=11),
    )
    executed = execute_experiment(db_session, experiment)
    assert executed.status == "COMPLETED"

    n_test = executed.result["split"]["n_test"]
    assert n_test > 0
    rows = (
        db_session.query(ExperimentTestPrediction)
        .filter(ExperimentTestPrediction.experiment_id == executed.id)
        .order_by(ExperimentTestPrediction.row_index)
        .all()
    )
    assert len(rows) == n_test
    assert {row.row_index for row in rows} == set(range(n_test))
    for row in rows:
        assert row.record_id
        assert str(row.record_id).startswith("C-")
        assert row.predicted_value in {0, 1}
        assert row.probability is not None
        assert 0.0 <= float(row.probability) <= 1.0
        assert row.y_true in {0, 1}

    assert db_session.query(Prediction).count() == 0


def test_open_ingest_persists_index_when_no_natural_id(db_session, tmp_path):
    env = seed_dogfood(db_session)
    rng = np.random.default_rng(3)
    n = 80
    tenure = rng.integers(1, 72, n)
    monthly = rng.uniform(20, 120, n)
    contract = rng.choice(["Month-to-month", "One year", "Two year"], n)
    churn_p = np.where(contract == "Month-to-month", 0.6, 0.15)
    churn = rng.binomial(1, churn_p)
    frame = pd.DataFrame(
        {
            "tenure": tenure,
            "MonthlyCharges": monthly,
            "contract": contract,
            "churn": np.where(churn == 1, "Yes", "No"),
        }
    )
    path = tmp_path / "telco_no_id.csv"
    frame.to_csv(path, index=False)
    dataset = ingest_dataset(
        db_session,
        environment=env,
        name="open-ingest-pred",
        location=str(path),
        source_type="csv",
        version="v1",
    )
    columns = [c for c in frame.columns if c != "churn"]
    num_cols, cat_cols = split_column_roles(frame, columns)
    spec = TaskSpec(
        id="open_ingest_pred_test",
        name="test",
        task_type="binary",
        target="churn",
        entity_id="entity_id",
        prediction_time_column=None,
        evaluation_metric="pr_auc",
        feature_groups={"features": num_cols + cat_cols},
        validation_strategy="stratified",
        column_roles={"numerical": num_cols, "categorical": cat_cols},
    )
    task = upsert_task(db_session, env, spec)
    experiment = create_experiment(
        db_session,
        environment=env,
        dataset=dataset,
        task=task,
        config=SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
    )
    executed = execute_experiment(db_session, experiment)
    assert executed.status == "COMPLETED"

    n_test = executed.result["split"]["n_test"]
    rows = (
        db_session.query(ExperimentTestPrediction)
        .filter(ExperimentTestPrediction.experiment_id == executed.id)
        .all()
    )
    assert len(rows) == n_test
    assert len(rows) == len(executed.result["test_predictions"])
    assert {row.record_id for row in rows} == {str(i) for i in range(n_test)}
    assert all(row.probability is not None for row in rows)
    assert all(row.predicted_value in {0, 1} for row in rows)
