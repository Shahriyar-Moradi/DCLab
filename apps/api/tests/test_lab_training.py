from io import BytesIO

from app.db.models import Dataset, Experiment
from app.engine.datasets.lab_workbook import make_lab_workbook


def _workbook_bytes(n: int = 160) -> bytes:
    buffer = BytesIO()
    make_lab_workbook(n=n).to_csv(buffer, index=False)
    return buffer.getvalue()


def test_upload_plans_five_trainable_use_cases(admin_client):
    uploaded = admin_client.post(
        "/admin/datasets/upload",
        files={"file": ("lab.csv", _workbook_bytes(), "text/csv")},
        params={"name": "lab_csv"},
    )
    assert uploaded.status_code == 200, uploaded.text
    dataset_id = uploaded.json()["id"]
    assert uploaded.json()["row_count"] == 160
    profile = admin_client.get(f"/admin/datasets/{dataset_id}/profile")
    assert profile.status_code == 200
    plan = admin_client.get(f"/admin/datasets/{dataset_id}/use-cases")
    assert plan.status_code == 200
    body = plan.json()
    assert body["trainable_count"] == 5
    assert [item["slug"] for item in body["use_cases"]] == [
        "churn",
        "conversion",
        "lead_conversion",
        "purchase",
        "customer_value",
    ]
    assert all(item["trainable"] for item in body["use_cases"])


def test_train_conversion_queues_one_open_ingest_build(admin_client, db_session):
    """P1.1-A: admin training uses the single open-ingest path via the worker."""
    from app.services.ml_job_service import process_next_job

    uploaded = admin_client.post(
        "/admin/datasets/upload",
        files={"file": ("lab.csv", _workbook_bytes(180), "text/csv")},
        params={"name": "lab_train"},
    )
    dataset_id = uploaded.json()["id"]
    trained = admin_client.post(
        f"/admin/datasets/{dataset_id}/use-cases/conversion/train",
        json={"max_models": 5},
    )
    assert trained.status_code == 202, trained.text
    body = trained.json()
    assert body["status"] != "COMPLETED"  # queued; the API never trains
    assert admin_client.post(f"/admin/experiments/{body['id']}/run").status_code == 200
    assert body["use_case"] == "conversion"
    assert body["task_name"] == "Conversion"

    job = process_next_job(db_session)  # what the worker does
    assert job is not None and job.status == "completed", getattr(job, "last_error", None)

    detail = admin_client.get(f"/admin/experiments/{body['id']}")
    assert detail.status_code == 200, detail.text
    done = detail.json()
    assert done["status"] == "COMPLETED"
    assert done["config"]["strategy"] == "open_ingest"
    result = done["result"] or {}
    assert result.get("candidates") and result.get("best_single")
    plan = admin_client.get(f"/admin/datasets/{dataset_id}/use-cases").json()
    conversion = next(item for item in plan["use_cases"] if item["slug"] == "conversion")
    assert conversion["latest_experiment_id"] == body["id"]
    # Completed evidence is locked: a re-run is refused, never silently redone.
    rerun = admin_client.post(f"/admin/experiments/{body['id']}/run")
    assert rerun.status_code == 409 and "locked" in rerun.json()["detail"]

    # The worker resolved exactly the plan's target, and the other use cases'
    # labels (post-outcome columns) never reached the build as features.
    from sqlalchemy import select

    from app.db.models import DatasetColumn, ExecutionRequest, WorkflowRun

    request = db_session.scalar(
        select(ExecutionRequest).where(ExecutionRequest.pipeline_run_id == body["id"])
    )
    workflow_run = db_session.get(WorkflowRun, request.workflow_run_id)
    assert workflow_run.explicit_target == conversion["target_column"]
    assert workflow_run.resolved_target == conversion["target_column"]
    siblings = {
        item["target_column"] for item in plan["use_cases"]
        if item["target_column"] and item["slug"] != "conversion"
    }
    assert siblings, "fixture must have other use-case labels"
    assert set(request.request_spec["excluded_columns"].split(",")) == siblings
    built_columns = set(db_session.scalars(
        select(DatasetColumn.name).where(DatasetColumn.dataset_id == done["dataset_id"])
    ))
    assert conversion["target_column"] in built_columns
    assert not (siblings & built_columns)


def test_train_all_queues_one_build_per_trainable_use_case(admin_client):
    uploaded = admin_client.post(
        "/admin/datasets/upload",
        files={"file": ("lab.csv", _workbook_bytes(160), "text/csv")},
        params={"name": "lab_all"},
    )
    dataset_id = uploaded.json()["id"]
    response = admin_client.post(f"/admin/datasets/{dataset_id}/train", json={})
    assert response.status_code == 202, response.text
    runs = response.json()
    assert sorted(run["use_case"] for run in runs) == sorted(
        ["churn", "conversion", "lead_conversion", "purchase", "customer_value"]
    )
    assert all(run["status"] != "COMPLETED" for run in runs)


def test_legacy_experiment_rerun_is_gone(admin_client, db_session):
    created = admin_client.post("/admin/datasets/sample-workbook")
    assert created.status_code == 200, created.text
    dataset = db_session.get(Dataset, created.json()["id"])
    legacy = Experiment(
        workspace_id=dataset.workspace_id,
        environment_id=dataset.environment_id,
        dataset_id=dataset.id,
        task_id=None,
        status="CREATED",
        config={"strategy": "use_case"},
    )
    db_session.add(legacy)
    db_session.commit()
    response = admin_client.post(f"/admin/experiments/{legacy.id}/run")
    assert response.status_code == 410
    assert "legacy experiment runner was removed" in response.text


def test_experiments_include_pipeline_runs_without_prediction_tasks(admin_client, db_session):
    created = admin_client.post("/admin/datasets/sample-workbook")
    assert created.status_code == 200, created.text
    dataset = db_session.get(Dataset, created.json()["id"])
    assert dataset is not None
    run = Experiment(
        workspace_id=dataset.workspace_id,
        environment_id=dataset.environment_id,
        dataset_id=dataset.id,
        task_id=None,
        status="CREATED",
        config={},
    )
    db_session.add(run)
    db_session.commit()

    listed = admin_client.get("/admin/experiments")
    assert listed.status_code == 200, listed.text
    item = next(row for row in listed.json() if row["id"] == str(run.id))
    assert item["task_id"] is None
    assert item["dataset_id"] == str(dataset.id)
    detail = admin_client.get(f"/admin/experiments/{run.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["task_id"] is None


def test_sample_workbook_endpoint_is_ready_to_train(admin_client):
    created = admin_client.post("/admin/datasets/sample-workbook")
    assert created.status_code == 200, created.text
    dataset_id = created.json()["id"]
    plan = admin_client.get(f"/admin/datasets/{dataset_id}/use-cases").json()
    assert plan["trainable_count"] == 5
    assert created.json()["row_count"] >= 200


def test_client_cannot_upload_lab_dataset(auth_client):
    response = auth_client.post(
        "/admin/datasets/upload",
        files={"file": ("lab.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == 403
