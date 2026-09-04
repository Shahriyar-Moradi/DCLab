from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np
import pandas as pd

from app.db.models import ClientLabUpload, Experiment, UserRole, WorkflowRun
from app.services.auth_service import create_access_token, create_user
from app.services.auto_train_service import run_auto_train_job
from app.services.observability_service import append_ml_run_event


def _upload_and_run(auth_client, db_session, monkeypatch, *, regression: bool = False):
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None
    )
    rng = np.random.default_rng(801 if regression else 802)
    rows = 110
    feature = rng.normal(0, 1, rows)
    frame = pd.DataFrame(
        {
            "feature": feature,
            "segment": rng.choice(["a", "b", "c"], rows),
            "outcome": (
                feature * 18 + rng.normal(0, 2, rows)
                if regression
                else (feature + rng.normal(0, 0.5, rows) > 0).astype(int)
            ),
        }
    )
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Customer Value", "target_column": "outcome"},
        files={
            "file": (
                "regression.csv" if regression else "classification.csv",
                frame.to_csv(index=False).encode(),
                "text/csv",
            )
        },
    )
    assert response.status_code == 200, response.text
    upload = db_session.get(ClientLabUpload, response.json()["id"])
    run_auto_train_job(db_session, upload.id)
    db_session.expire_all()
    upload = db_session.get(ClientLabUpload, upload.id)
    pipeline = db_session.get(Experiment, upload.experiment_id)
    run = db_session.get(WorkflowRun, pipeline.workflow_run_id)
    assert pipeline.status == "COMPLETED"
    return upload, run, pipeline


def test_platform_hierarchy_fast_replay_multi_pipeline_and_readonly_role(
    auth_client, admin_client, db_session, monkeypatch
):
    upload, run, pipeline = _upload_and_run(
        auth_client, db_session, monkeypatch, regression=False
    )
    assert run.task_type == "binary"

    # A run that completed in two seconds remains inspectable from persisted data.
    ended = datetime.now(UTC)
    pipeline.started_at = ended - timedelta(seconds=2)
    pipeline.ended_at = ended
    db_session.commit()

    businesses = admin_client.get("/admin/businesses")
    assert businesses.status_code == 200, businesses.text
    business = next(row for row in businesses.json() if row["id"] == str(upload.workspace_id))
    assert business["domain_count"] >= 1
    detail = admin_client.get(f"/admin/businesses/{upload.workspace_id}")
    assert detail.status_code == 200, detail.text
    labs_domain = next(row for row in detail.json()["domains"] if row["slug"] == "labs")
    workflow = next(row for row in detail.json()["workflows"] if row["id"] == str(run.workflow_id))
    assert detail.json()["runs"][0]["pipeline_count"] >= 1

    assert admin_client.get(
        f"/admin/businesses/{upload.workspace_id}/domains/{labs_domain['id']}"
    ).status_code == 200
    assert admin_client.get(
        f"/admin/businesses/{upload.workspace_id}/workflows/{workflow['id']}"
    ).status_code == 200

    # The WorkflowRun contract renders every Pipeline Run independently.
    failed_pipeline = Experiment(
        workspace_id=upload.workspace_id,
        workflow_run_id=run.id,
        pipeline_name="secondary_diagnostic_pipeline",
        pipeline_index=1,
        pipeline_purpose="diagnostic",
        environment_id=pipeline.environment_id,
        dataset_id=pipeline.dataset_id,
        task_id=pipeline.task_id,
        status="FAILED",
        failure_reason="synthetic failed pipeline for hierarchy replay",
        config={},
    )
    db_session.add(failed_pipeline)
    db_session.commit()
    append_ml_run_event(
        db_session,
        workspace_id=failed_pipeline.workspace_id,
        workflow_run_id=run.id,
        experiment_id=failed_pipeline.id,
        stage="terminal",
        event_type="pipeline_terminal",
        status="failed",
        payload={"reason": "bounded synthetic failure"},
    )
    run_response = admin_client.get(
        f"/admin/businesses/{upload.workspace_id}/workflow-runs/{run.id}"
    )
    assert run_response.status_code == 200, run_response.text
    assert [row["pipeline_index"] for row in run_response.json()["pipelines"]] == [0, 1]
    assert run_response.json()["pipelines"][1]["status"] == "FAILED"

    monitor = admin_client.get(f"/admin/pipeline-runs/{pipeline.id}/monitor")
    assert monitor.status_code == 200, monitor.text
    body = monitor.json()
    assert body["summary"]["task_type"] == "binary"
    assert body["summary"]["event_count"] > 20
    assert any(row["event_type"] == "cv_fold_completed" for row in body["events"])
    assert any(row["event_type"] == "winner_locked" for row in body["events"])
    assert body["preprocessing"]["numerical"] == ["Median Imputer", "StandardScaler"]
    assert body["preprocessing"]["one_hot"] == {
        "drop": "first",
        "handle_unknown": "ignore",
    }
    assert body["predictions"]["raw_rows_included"] is False
    plan = body["scientific_plan"]
    assert plan["validation"]["strategy"] == "StratifiedKFold"
    assert plan["holdout"]["strategy"] == "stratified_random"
    assert plan["metric"]["primary_metric"] == "pr_auc"
    assert plan["problem_profile"]["task_type"] == "binary"
    assert plan["leakage"]["partition"] == "train"
    assert "tenure" in plan["allowed_features"] or plan["allowed_features"]
    assert any(row["event_type"] == "model_development_plan_locked" for row in body["events"])
    assert any(
        row["purpose"].startswith("semantic_") and row["llm_used"] is False
        for row in body["llm_invocations"]
    )
    assert "test_predictions" not in str(body["sanitized_evidence"])
    serialized_report = str(body["reports"])
    assert "prediction_evidence" not in serialized_report
    assert "raw_llm_output" not in serialized_report
    assert upload.stored_path not in serialized_report
    # Replay is database-backed and stable after completion.
    replay = admin_client.get(f"/admin/pipeline-runs/{pipeline.id}/monitor")
    assert [row["sequence"] for row in replay.json()["events"]] == [
        row["sequence"] for row in body["events"]
    ]

    failed_monitor = admin_client.get(
        f"/admin/pipeline-runs/{failed_pipeline.id}/monitor"
    )
    assert failed_monitor.status_code == 200
    assert failed_monitor.json()["summary"]["failure_reason"]
    assert failed_monitor.json()["events"][0]["status"] == "failed"

    version = pipeline.model_version
    assert version is not None
    model = admin_client.get(
        f"/admin/businesses/{upload.workspace_id}/models/{version.model_asset_id}"
    )
    assert model.status_code == 200
    assert model.json()["versions"][0]["pipeline_run_id"] == str(pipeline.id)

    developer = create_user(
        db_session,
        email=f"platform-explorer-{uuid4().hex}@test.invalid",
        password="password",
        role=UserRole.DCLAB_DEVELOPER,
    )
    db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(developer)}"}
    assert auth_client.get("/admin/businesses", headers=headers).status_code == 200
    assert auth_client.get(
        f"/admin/pipeline-runs/{pipeline.id}/monitor", headers=headers
    ).status_code == 200
    assert auth_client.post(
        "/admin/datasets/sample-workbook", headers=headers, json={}
    ).status_code == 403


def test_platform_monitor_supports_real_regression(
    auth_client, admin_client, db_session, monkeypatch
):
    _upload, _run, pipeline = _upload_and_run(
        auth_client, db_session, monkeypatch, regression=True
    )
    monitor = admin_client.get(f"/admin/pipeline-runs/{pipeline.id}/monitor")
    assert monitor.status_code == 200, monitor.text
    body = monitor.json()
    assert body["summary"]["task_type"] == "regression"
    final = [row for row in body["events"] if row["event_type"] == "final_test_completed"]
    assert len(final) == 1
    assert "r2" in final[0]["payload"]["metrics"]


def test_platform_monitor_preprocessing_uses_persisted_pipeline_evidence(
    auth_client, admin_client, db_session, monkeypatch
):
    _upload, _run, pipeline = _upload_and_run(
        auth_client, db_session, monkeypatch, regression=False
    )
    result = dict(pipeline.result or {})
    technical_report = dict(result.get("technical_report") or {})
    technical_report["preprocessing"] = {
        "numerical": ["Custom Numeric Transformer"],
        "categorical": ["Custom Categorical Transformer"],
        "one_hot": {"drop": None, "handle_unknown": "error"},
        "fit_partition": "full_dataset_including_holdout",
    }
    result["technical_report"] = technical_report
    pipeline.result = result
    db_session.commit()

    monitor = admin_client.get(f"/admin/pipeline-runs/{pipeline.id}/monitor")

    assert monitor.status_code == 200, monitor.text
    preprocessing = monitor.json()["preprocessing"]
    assert preprocessing["numerical"] == ["Custom Numeric Transformer"]
    assert preprocessing["categorical"] == ["Custom Categorical Transformer"]
    assert preprocessing["one_hot"] == {"drop": None, "handle_unknown": "error"}
    assert preprocessing["fit_guarantees"] == []
