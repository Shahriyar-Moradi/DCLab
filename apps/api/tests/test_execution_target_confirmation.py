"""Ambiguous target selection is a resumable needs_input wait, not FAILED."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    ClientLabUpload,
    Experiment,
    ExperimentCandidate,
    ExecutionRequest,
    MlJob,
    MlRunEvent,
    UserRole,
    WorkflowRun,
)
from app.domain.execution_requests import (
    EXECUTION_RESUMED,
    REQUEST_NEEDS_INPUT,
    TARGET_CONFIRMATION_REQUIRED,
    TARGET_CONFIRMED,
)
from app.services.auth_service import create_access_token, create_user
from app.services.auto_train_service import run_auto_train_job
from app.services.workspace_service import create_business_workspace
from dclab_client import DCLabClient


def _disable_background_job(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None
    )
    monkeypatch.setattr(
        "app.services.auto_train_service.enqueue_auto_train", lambda _id: None
    )
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )


def _ambiguous_frame(n: int = 200, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "customerID": [f"C{i}" for i in range(n)],
            "tenure": rng.integers(1, 72, n),
            "SeniorCitizen": rng.integers(0, 2, n),
            "Partner": rng.choice(["Yes", "No"], n),
            "Dependents": rng.choice(["Yes", "No"], n),
            "PhoneService": rng.choice(["Yes", "No"], n),
            "PaperlessBilling": rng.choice(["Yes", "No"], n),
            "MonthlyCharges": rng.uniform(20, 120, n),
            "Churn": rng.choice(["Yes", "No"], n),
        }
    )


def _post_csv(auth_client, filename: str, frame: pd.DataFrame):
    return auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": (filename, frame.to_csv(index=False).encode(), "text/csv")},
    )


def _wait_for_target(auth_client, db_session, monkeypatch):
    _disable_background_job(monkeypatch)
    created = _post_csv(auth_client, "tie.csv", _ambiguous_frame())
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]
    run_auto_train_job(db_session, run_id)
    db_session.expire_all()
    waiting = auth_client.get(f"/app/labs/uploads/{run_id}")
    assert waiting.status_code == 200, waiting.text
    return run_id, waiting.json()


def _counts(db_session, upload_id):
    workflow = db_session.scalar(
        select(WorkflowRun).where(WorkflowRun.source_upload_id == upload_id)
    )
    workflows = 1 if workflow is not None else 0
    pipelines = 0
    if workflow is not None:
        pipelines = db_session.scalar(
            select(func.count()).select_from(Experiment).where(
                Experiment.workflow_run_id == workflow.id
            )
        )
    jobs = db_session.scalar(
        select(func.count()).select_from(MlJob).where(MlJob.upload_id == upload_id)
    )
    return workflows, int(pipelines or 0), int(jobs or 0)


def test_ambiguous_target_becomes_needs_input_not_failed(
    auth_client, db_session, monkeypatch
):
    run_id, body = _wait_for_target(auth_client, db_session, monkeypatch)
    assert body["status"] == "needs_input"
    assert body["pipeline_status"] == "needs_input"
    assert body["outcome"] is None
    confirmation = body["target_confirmation"]
    assert confirmation["code"] == TARGET_CONFIRMATION_REQUIRED
    assert "Multiple possible" in confirmation["reason"] or "ambiguous" in confirmation["reason"]
    assert confirmation["recommended_column"] == "Churn"
    names = [row["name"] for row in confirmation["possible_columns"]]
    assert "Churn" in names
    assert "Yes" not in json.dumps(confirmation)
    upload = db_session.get(ClientLabUpload, run_id)
    assert upload.pipeline_status == "needs_input"
    assert upload.client_status == "needs_input"
    assert "failed_at" not in (upload.pipeline_log or {})
    request = db_session.scalar(
        select(ExecutionRequest).where(
            ExecutionRequest.workspace_id == DEFAULT_WORKSPACE_ID,
            ExecutionRequest.pipeline_run_id == upload.experiment_id,
        )
    )
    assert request is not None
    assert request.status == REQUEST_NEEDS_INPUT
    assert request.result_summary["code"] == TARGET_CONFIRMATION_REQUIRED
    experiment = db_session.get(Experiment, upload.experiment_id)
    workflow = db_session.scalar(
        select(WorkflowRun).where(WorkflowRun.source_upload_id == upload.id)
    )
    assert experiment.status != "FAILED"
    assert workflow.status == "running"
    assert workflow.failure_reason is None


def test_no_training_begins_while_waiting(auth_client, db_session, monkeypatch):
    run_id, _body = _wait_for_target(auth_client, db_session, monkeypatch)
    upload = db_session.get(ClientLabUpload, run_id)
    stages = list((upload.pipeline_log or {}).get("stages") or [])
    assert "needs_input" in stages
    assert "splitting" not in stages
    assert "cross_validation" not in stages
    assert "training" not in stages
    assert db_session.scalar(
        select(func.count()).select_from(ExperimentCandidate).where(
            ExperimentCandidate.experiment_id == upload.experiment_id
        )
    ) == 0
    job = db_session.scalar(select(MlJob).where(MlJob.upload_id == upload.id))
    assert job is not None
    assert job.status != "running"


def test_confirm_churn_resumes_execution(auth_client, db_session, monkeypatch):
    run_id, body = _wait_for_target(auth_client, db_session, monkeypatch)
    request_id = body["target_confirmation"]["execution_request_id"]
    confirmed = auth_client.post(
        f"/v1/execution-requests/{request_id}/target-confirmation",
        json={"target_column": "Churn"},
    )
    assert confirmed.status_code == 200, confirmed.text
    payload = confirmed.json()
    assert payload["status"] == "running"
    assert payload["request_spec"]["target_column"] == "Churn"
    db_session.expire_all()
    upload = db_session.get(ClientLabUpload, run_id)
    assert upload.explicit_target_column == "Churn"
    run_auto_train_job(db_session, run_id)
    db_session.expire_all()
    upload = db_session.get(ClientLabUpload, run_id)
    assert upload.pipeline_status == "completed", upload.pipeline_log
    assert upload.pipeline_log["target"]["column"] == "Churn"


def test_invalid_target_rejected(auth_client, db_session, monkeypatch):
    _run_id, body = _wait_for_target(auth_client, db_session, monkeypatch)
    request_id = body["target_confirmation"]["execution_request_id"]
    response = auth_client.post(
        f"/v1/execution-requests/{request_id}/target-confirmation",
        json={"target_column": "NotAColumn"},
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "TARGET_NOT_IN_DATASET"
    db_session.expire_all()
    request = db_session.get(ExecutionRequest, request_id)
    assert request.status == REQUEST_NEEDS_INPUT


def test_cross_workspace_confirmation_rejected(
    auth_client, db_session, monkeypatch, client
):
    _run_id, body = _wait_for_target(auth_client, db_session, monkeypatch)
    request_id = body["target_confirmation"]["execution_request_id"]
    other = create_user(
        db_session,
        email=f"other-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="other",
        workspace_id=DEFAULT_WORKSPACE_ID,
    )
    workspace = create_business_workspace(db_session, owner=other, name="Other Co")
    db_session.commit()
    response = client.post(
        f"/v1/execution-requests/{request_id}/target-confirmation",
        json={"target_column": "Churn"},
        headers={
            "Authorization": f"Bearer {create_access_token(other)}",
            "X-Workspace-Id": str(workspace.id),
        },
    )
    assert response.status_code == 404
    db_session.expire_all()
    request = db_session.get(ExecutionRequest, request_id)
    assert request.status == REQUEST_NEEDS_INPUT


def test_same_confirmation_twice_is_idempotent(auth_client, db_session, monkeypatch):
    run_id, body = _wait_for_target(auth_client, db_session, monkeypatch)
    request_id = body["target_confirmation"]["execution_request_id"]
    first = auth_client.post(
        f"/v1/execution-requests/{request_id}/target-confirmation",
        json={"target_column": "Churn"},
    )
    assert first.status_code == 200, first.text
    before = _counts(db_session, run_id)
    second = auth_client.post(
        f"/v1/execution-requests/{request_id}/target-confirmation",
        json={"target_column": "Churn"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert _counts(db_session, run_id) == before
    jobs = list(db_session.scalars(select(MlJob).where(MlJob.upload_id == run_id)))
    assert len(jobs) == 1


def test_conflicting_second_confirmation_rejected(auth_client, db_session, monkeypatch):
    _run_id, body = _wait_for_target(auth_client, db_session, monkeypatch)
    request_id = body["target_confirmation"]["execution_request_id"]
    first = auth_client.post(
        f"/v1/execution-requests/{request_id}/target-confirmation",
        json={"target_column": "Churn"},
    )
    assert first.status_code == 200, first.text
    second = auth_client.post(
        f"/v1/execution-requests/{request_id}/target-confirmation",
        json={"target_column": "Partner"},
    )
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "TARGET_INTENT_CONFLICT"
    request = db_session.get(ExecutionRequest, request_id)
    assert request.request_spec["target_column"] == "Churn"


def test_resume_does_not_duplicate_lineage_or_jobs(
    auth_client, db_session, monkeypatch
):
    run_id, body = _wait_for_target(auth_client, db_session, monkeypatch)
    before = _counts(db_session, run_id)
    assert before == (1, 1, 1)
    request_id = body["target_confirmation"]["execution_request_id"]
    auth_client.post(
        f"/v1/execution-requests/{request_id}/target-confirmation",
        json={"target_column": "Churn"},
    )
    run_auto_train_job(db_session, run_id)
    db_session.expire_all()
    assert _counts(db_session, run_id) == before
    upload = db_session.get(ClientLabUpload, run_id)
    assert upload.pipeline_status == "completed"


def test_event_history_records_wait_confirm_and_resume(
    auth_client, db_session, monkeypatch
):
    run_id, body = _wait_for_target(auth_client, db_session, monkeypatch)
    request_id = body["target_confirmation"]["execution_request_id"]
    auth_client.post(
        f"/v1/execution-requests/{request_id}/target-confirmation",
        json={"target_column": "Churn"},
    )
    db_session.expire_all()
    upload = db_session.get(ClientLabUpload, run_id)
    events = list(
        db_session.scalars(
            select(MlRunEvent)
            .where(MlRunEvent.experiment_id == upload.experiment_id)
            .order_by(MlRunEvent.sequence)
        )
    )
    types = [row.event_type for row in events]
    assert TARGET_CONFIRMATION_REQUIRED in types
    assert TARGET_CONFIRMED in types
    assert EXECUTION_RESUMED in types
    blob = json.dumps([row.payload for row in events])
    assert "sample_values" not in blob
    assert "Yes" not in blob
    confirmed = next(row for row in events if row.event_type == TARGET_CONFIRMED)
    assert confirmed.payload["target_column"] == "Churn"
    assert confirmed.payload["source"] == "user"
    assert confirmed.payload.get("confirmed_by")
    assert confirmed.workflow_run_id is not None
    assert confirmed.experiment_id == upload.experiment_id
    assert confirmed.payload.get("execution_request_id")


def test_dclab_client_confirm_target_uses_v1(
    auth_client, db_session, monkeypatch, client_token
):
    _run_id, body = _wait_for_target(auth_client, db_session, monkeypatch)
    request_id = body["target_confirmation"]["execution_request_id"]
    api = DCLabClient(
        base_url=str(auth_client.base_url),
        token=client_token,
        workspace_id=DEFAULT_WORKSPACE_ID,
        http=auth_client,
    )
    row = api.execution_requests.confirm_target(request_id, target_column="Churn")
    assert row.status == "running"
    assert row.request_spec["target_column"] == "Churn"
    fetched = api.execution_requests.get(request_id)
    assert fetched.id == row.id
    assert fetched.status == "running"


def test_labs_adapter_confirm_rejects_other_workspace_upload(
    auth_client, db_session, monkeypatch
):
    run_id, _body = _wait_for_target(auth_client, db_session, monkeypatch)
    missing = auth_client.post(
        f"/app/labs/uploads/{uuid4()}/target-confirmation",
        json={"target_column": "Churn"},
    )
    assert missing.status_code == 404
    upload = db_session.get(ClientLabUpload, run_id)
    assert upload.pipeline_status == "needs_input"
