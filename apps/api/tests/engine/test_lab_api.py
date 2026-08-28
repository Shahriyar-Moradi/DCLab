from app.engine.datasets.synthetic import SYNTHETIC_GROUPS, make_synthetic_customers
from app.engine.types import SearchConfig, TaskSpec
from app.services.lab_service import (
    create_experiment,
    execute_experiment,
    ingest_synthetic,
    profile_dataset,
    seed_dogfood,
    upsert_task,
)


def test_lab_api_synthetic_experiment(admin_client, db_session, tmp_path, monkeypatch):
    env = seed_dogfood(db_session)
    dataset = ingest_synthetic(db_session, env, n=400)
    profile = profile_dataset(db_session, dataset)
    assert profile.stats["row_count"] == 400
    spec = TaskSpec(
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
    task = upsert_task(db_session, env, spec)
    created = admin_client.post(
        "/admin/environments/dogfood",
    )
    assert created.status_code == 200
    listed = admin_client.get("/admin/datasets")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "synthetic"
    experiment = create_experiment(
        db_session,
        environment=env,
        dataset=dataset,
        task=task,
        config=SearchConfig(max_candidates=6, max_feature_group_combinations=4, n_robustness_folds=2, seed=5),
    )
    executed = execute_experiment(db_session, experiment)
    assert executed.status == "COMPLETED"
    body = admin_client.get(f"/admin/experiments/{executed.id}").json()
    assert body["status"] == "COMPLETED"
    metrics = admin_client.get(f"/admin/experiments/{executed.id}/metrics").json()
    assert metrics["funnel"]["trained"] >= 1
    report = admin_client.get(f"/admin/experiments/{executed.id}/report").json()
    assert "markdown" in report
    comparison = admin_client.get(f"/admin/experiments/{executed.id}/comparison")
    assert comparison.status_code == 200
    assert "best_single" in comparison.json()
