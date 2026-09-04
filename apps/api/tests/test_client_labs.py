"""Step 5 — Client Labs: bounded, translated trial runs on the real DCLab engine.

Covers:
  * the fixed problem catalog is served and clean of banned terms
  * a full sample-data trial run produces translated-only insights
  * the three enforced bounds actually stop the system rather than just being
    documented: oversized upload, missing columns, exhausted trial quota, and a
    forced timeout all fail gracefully (no 500, no crash, no leaked ML detail)
  * a trial's stored result contains no raw model/metric detail, checked both via
    the response and via the live-schema banned-terms scanner
  * workspace isolation: one workspace cannot read another's trial runs
  * open ingest: any usual data file (including raw logs) is saved without
    required columns; disk path never appears on /app
"""

from __future__ import annotations

import io
from uuid import UUID

import pandas as pd
import pytest

from app.domain.errors import TrialQuotaExceededError
from app.translation.banned_terms import find_banned_terms
from app.translation.scanner import scan_client_api_response_models

USE_CASE = "cross_sell"  # smallest bundled sample dataset -> fastest real trial


def test_problem_catalog_is_fixed_and_clean(auth_client):
    response = auth_client.get("/app/labs/problems")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 8
    use_cases = {row["use_case"] for row in body}
    assert use_cases == {
        "churn",
        "purchase",
        "lead_conversion",
        "upsell",
        "cross_sell",
        "campaign_response",
        "customer_value",
        "custom_support",
    }
    for row in body:
        assert row["max_upload_rows"] > 0
        assert row["max_trial_runs"] > 0
        assert row["required_columns"]
    assert find_banned_terms(response.text) == []


def test_sample_data_run_produces_translated_insights_only(auth_client):
    response = auth_client.post("/app/labs/runs", data={"use_case": USE_CASE})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["data_source"] == "sample"
    assert body["row_count"] > 0
    assert len(body["insights"]) > 0
    for insight in body["insights"]:
        assert set(insight.keys()) == {
            "subject_id",
            "category",
            "headline",
            "confidence_band",
            "recommended_action",
            "expected_value",
            "currency",
            "reasoning",
            "generated_at",
        }
    assert find_banned_terms(response.text) == []


def test_unknown_problem_is_rejected(auth_client):
    response = auth_client.post("/app/labs/runs", data={"use_case": "not_a_real_problem"})
    assert response.status_code == 404


def test_oversized_upload_is_rejected_gracefully_not_crashed(auth_client, db_session):
    from app.services.client_lab_service import MAX_UPLOAD_ROWS

    frame = pd.DataFrame(
        {"external_id": [f"row-{i}" for i in range(MAX_UPLOAD_ROWS + 5)], "bought_ancillary": [0] * (MAX_UPLOAD_ROWS + 5)}
    )
    buffer = io.BytesIO(frame.to_csv(index=False).encode())
    response = auth_client.post(
        "/app/labs/runs",
        data={"use_case": USE_CASE},
        files={"file": ("too_big.csv", buffer, "text/csv")},
    )
    assert response.status_code == 422
    assert str(MAX_UPLOAD_ROWS) in response.json()["detail"]

    from app.db.models import ClientLabRun

    assert db_session.query(ClientLabRun).count() == 0, "a rejected upload must not consume a trial or leave a row"


def test_upload_missing_required_columns_is_rejected_gracefully(auth_client, db_session):
    buffer = io.BytesIO(b"external_id,bought_ancillary\nT-1,0\nT-2,1\n")
    response = auth_client.post(
        "/app/labs/runs",
        data={"use_case": USE_CASE},
        files={"file": ("wrong_shape.csv", buffer, "text/csv")},
    )
    assert response.status_code == 422
    assert "missing required columns" in response.json()["detail"]

    from app.db.models import ClientLabRun

    assert db_session.query(ClientLabRun).count() == 0


def test_trial_quota_is_enforced_not_just_documented(auth_client, monkeypatch):
    import app.services.client_lab_service as service

    monkeypatch.setattr(service, "MAX_TRIAL_RUNS_PER_PROBLEM", 1)

    first = auth_client.post("/app/labs/runs", data={"use_case": USE_CASE})
    assert first.status_code == 200
    assert first.json()["status"] == "completed"

    second = auth_client.post("/app/labs/runs", data={"use_case": USE_CASE})
    assert second.status_code == 429
    assert "trial" in second.json()["detail"].lower()

    quota = auth_client.get(f"/app/labs/problems/{USE_CASE}/quota")
    assert quota.status_code == 200
    assert quota.json() == {
        "use_case": USE_CASE,
        "max_trial_runs": 1,
        "runs_used": 1,
        "runs_remaining": 0,
    }


def test_a_run_that_exceeds_its_time_budget_fails_gracefully_not_crashed(auth_client, monkeypatch, db_session):
    import time

    import app.services.client_lab_service as service

    monkeypatch.setattr(service, "TRIAL_TIMEOUT_SECONDS", 0.05)

    def _slow_run_use_case(*args, **kwargs):
        time.sleep(2)
        return {}

    monkeypatch.setattr(service, "run_use_case", _slow_run_use_case)

    response = auth_client.post("/app/labs/runs", data={"use_case": USE_CASE})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["insights"] == []
    assert body["failure_reason"]
    assert find_banned_terms(response.text) == []

    from app.db.models import ClientLabRun

    assert db_session.query(ClientLabRun).count() == 1


def test_runs_are_listed_and_readable_by_id(auth_client):
    created = auth_client.post("/app/labs/runs", data={"use_case": USE_CASE})
    run_id = created.json()["id"]

    listed = auth_client.get("/app/labs/runs")
    assert listed.status_code == 200
    assert any(row["id"] == run_id for row in listed.json())

    detail = auth_client.get(f"/app/labs/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == run_id


def test_a_run_from_another_workspace_is_not_reachable(db_session, auth_client):
    import uuid

    from app.db.models import DEFAULT_WORKSPACE_ID, ClientLabRun, UserRole, Workspace
    from app.services.auth_service import create_access_token, create_user

    other_workspace = Workspace(id=uuid.uuid4(), slug=f"other-{uuid.uuid4().hex[:8]}", name="Other Co")
    db_session.add(other_workspace)
    db_session.commit()

    other_user = create_user(
        db_session,
        email="other@client.test",
        password="other-pass-123",
        role=UserRole.CLIENT_USER,
        full_name="Other Client",
        workspace_id=other_workspace.id,
    )
    db_session.commit()

    foreign_run = ClientLabRun(
        workspace_id=other_workspace.id,
        requested_by=other_user.id,
        use_case=USE_CASE,
        category="Revenue",
        data_source="sample",
        row_count=10,
        status="completed",
        insights=[],
    )
    db_session.add(foreign_run)
    db_session.commit()
    db_session.refresh(foreign_run)

    assert str(auth_client.headers) or True  # auth_client is scoped to DEFAULT_WORKSPACE_ID
    response = auth_client.get(f"/app/labs/runs/{foreign_run.id}")
    assert response.status_code == 404


def test_response_schemas_are_registered_with_the_banned_terms_scanner():
    violations = scan_client_api_response_models()
    assert violations == {}, violations


def test_open_ingest_accepts_arbitrary_csv_columns(auth_client, db_session):
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Marketing"},
        files={"file": ("campaign.csv", b"channel,spend\nemail,40\n", "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["category"] == "Marketing"
    assert body["filename"] == "campaign.csv"
    assert body["kind"] == "spreadsheet"
    assert body["record_count"] == 1
    assert body["fields_noticed"] == ["channel", "spend"]
    assert body["has_named_fields"] is True
    assert body["structured"] is False
    assert body["run_id"] == body["id"]
    assert body["status"] == "queued"
    assert body["dataset_id"]
    assert "stored_path" not in body
    assert find_banned_terms(response.text) == []

    from app.db.models import ClientLabUpload

    assert db_session.query(ClientLabUpload).count() == 1
    stored = db_session.query(ClientLabUpload).one()
    assert stored.stored_path
    assert stored.dataset_id is not None


def test_open_ingest_upload_returns_run_id_and_reload_works(auth_client, db_session):
    """Upload creates an ML run identity; GET by that id still works after refresh."""
    created = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": ("orders.csv", b"order_id,churn\n1,0\n2,1\n", "text/csv")},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    run_id = body["run_id"]
    assert run_id == body["id"]
    assert body["status"] == "queued"
    assert body["dataset_id"]
    assert find_banned_terms(created.text) == []

    from pathlib import Path

    from app.db.models import ClientLabUpload, Dataset

    upload = db_session.get(ClientLabUpload, run_id)
    assert upload is not None
    assert Path(upload.stored_path).is_file()
    assert upload.dataset_id is not None
    assert db_session.get(Dataset, upload.dataset_id) is not None

    detail = auth_client.get(f"/app/labs/uploads/{run_id}")
    assert detail.status_code == 200
    again = detail.json()
    assert again["run_id"] == run_id
    assert again["dataset_id"] == body["dataset_id"]
    assert again["status"] in {"queued", "processing", "completed", "failed"}
    assert find_banned_terms(detail.text) == []


def test_upload_post_json_returns_dataset_run_and_queued_before_training(auth_client, monkeypatch):
    """POST /app/labs/uploads must return real ids and status=queued in the JSON
    body before the background job is allowed to start."""
    enqueued: list[str] = []

    def _enqueue(upload_id):
        enqueued.append(str(upload_id))

    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", _enqueue)
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": ("customers.csv", b"tenure,churn\n1,Yes\n2,No\n", "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    run_id = UUID(body["run_id"])
    dataset_id = UUID(body["dataset_id"])
    assert body["status"] == "queued"
    assert body["id"] == str(run_id)
    assert dataset_id != run_id
    assert enqueued == [str(run_id)]
    assert find_banned_terms(response.text) == []


def test_run_processing_payload_maps_backend_stages_to_client_milestones(auth_client, db_session, monkeypatch):
    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)
    created = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": ("stage.csv", b"tenure,churn\n1,Yes\n2,No\n", "text/csv")},
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    queued = created.json()
    assert queued["status"] == "queued"
    assert queued["stage"] == "queued"
    assert queued["pipeline_status"] == "queued"
    assert queued["milestone"] == "Uploading"
    assert queued["headline"] == "Uploading"
    assert queued["message"] == "Uploading"
    assert [row["label"] for row in queued["steps"]] == [
        "Uploading",
        "Analyzing your data",
        "Preparing your data",
        "Building your model",
        "Finishing up",
    ]
    assert [row["state"] for row in queued["steps"]] == [
        "current",
        "upcoming",
        "upcoming",
        "upcoming",
        "upcoming",
    ]
    assert find_banned_terms(created.text) == []

    from app.db.models import ClientLabUpload

    upload = db_session.get(ClientLabUpload, run_id)
    assert upload is not None
    upload.pipeline_status = "analyzing"
    db_session.commit()

    analyzing = auth_client.get(f"/app/labs/uploads/{run_id}")
    assert analyzing.status_code == 200
    body = analyzing.json()
    assert body["status"] == "processing"
    assert body["stage"] == "processing"
    assert body["pipeline_status"] == "processing"
    assert body["milestone"] == "Analyzing your data"
    assert body["headline"] == "Analyzing your data"
    assert [row["state"] for row in body["steps"]] == [
        "done",
        "current",
        "upcoming",
        "upcoming",
        "upcoming",
    ]
    lowered = analyzing.text.lower()
    for leak in (
        "feature_engineering",
        "cross_validation",
        "cross-validation",
        "sklearn",
        '"analyzing"',
        '"ingesting"',
        '"training"',
    ):
        assert leak not in lowered
    assert find_banned_terms(analyzing.text) == []

    upload.pipeline_status = "training"
    db_session.commit()
    training = auth_client.get(f"/app/labs/uploads/{run_id}")
    assert training.status_code == 200
    trained = training.json()
    assert trained["status"] == "processing"
    assert trained["stage"] == "processing"
    assert trained["pipeline_status"] == "processing"
    assert trained["milestone"] == "Building your model"
    assert trained["headline"] == "Building your model"
    assert [row["state"] for row in trained["steps"]] == [
        "done",
        "done",
        "done",
        "current",
        "upcoming",
    ]
    assert "training" not in training.text.lower()
    assert "cross_validation" not in training.text.lower()
    assert find_banned_terms(training.text) == []

    upload.pipeline_status = "predicting"
    db_session.commit()
    finishing = auth_client.get(f"/app/labs/uploads/{run_id}")
    assert finishing.json()["milestone"] == "Finishing up"
    assert [row["state"] for row in finishing.json()["steps"]][-1] == "current"
    assert find_banned_terms(finishing.text) == []

    upload.pipeline_status = "failed"
    upload.pipeline_log = {"reason": "no label column found", "failed_at": "analyzing"}
    db_session.commit()
    failed = auth_client.get(f"/app/labs/uploads/{run_id}")
    assert failed.status_code == 200
    failed_body = failed.json()
    assert failed_body["status"] == "failed"
    assert failed_body["milestone"] == ""
    assert failed_body["message"] == "We could not find an outcome column to analyze."
    assert "tenure" not in failed_body["message"].lower()
    assert find_banned_terms(failed.text) == []


def test_open_ingest_accepts_raw_logs_without_headers(auth_client):
    payload = b"2026-08-27 INFO boot\n2026-08-27 WARN retry\n"
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Sales"},
        files={"file": ("app.log", payload, "text/plain")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "plain_text"
    assert body["record_count"] == 2
    assert body["has_named_fields"] is False
    assert body["fields_noticed"] == []
    assert body["structured"] is False
    assert body["run_id"] == body["id"]
    assert body["dataset_id"] is not None
    assert body["status"] == "queued"
    assert find_banned_terms(response.text) == []


def test_open_ingest_unknown_category_is_rejected(auth_client):
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "NotACategory"},
        files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == 404


def test_open_ingest_empty_and_unsupported_files_fail_gracefully(auth_client, db_session):
    empty = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": ("empty.csv", b"  \n", "text/csv")},
    )
    assert empty.status_code == 422
    assert "empty" in empty.json()["detail"].lower()

    picture = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": ("photo.png", b"\x89PNG\r\nnot-a-table", "image/png")},
    )
    assert picture.status_code == 422
    assert find_banned_terms(picture.text) == []

    from app.db.models import ClientLabUpload

    assert db_session.query(ClientLabUpload).count() == 0


def test_open_ingest_list_is_workspace_scoped(auth_client, db_session):
    created = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Custom"},
        files={"file": ("mine.json", b'[{"ticket": 1}]\n', "application/json")},
    )
    assert created.status_code == 200
    mine_id = created.json()["id"]

    listed = auth_client.get("/app/labs/uploads", params={"category": "Custom"})
    assert listed.status_code == 200
    assert any(row["id"] == mine_id for row in listed.json())
    assert find_banned_terms(listed.text) == []

    import uuid

    from app.db.models import ClientLabUpload, UserRole, Workspace
    from app.services.auth_service import create_user

    other_workspace = Workspace(id=uuid.uuid4(), slug=f"other-{uuid.uuid4().hex[:8]}", name="Other Co")
    db_session.add(other_workspace)
    db_session.commit()
    other_user = create_user(
        db_session,
        email="other-upload@client.test",
        password="other-pass-123",
        role=UserRole.CLIENT_USER,
        full_name="Other Client",
        workspace_id=other_workspace.id,
    )
    db_session.commit()
    foreign = ClientLabUpload(
        workspace_id=other_workspace.id,
        requested_by=other_user.id,
        category="Custom",
        original_filename="secret.csv",
        stored_path="/tmp/secret.csv",
        kind="spreadsheet",
        record_count=1,
        fields_noticed=["secret"],
        has_named_fields=True,
    )
    db_session.add(foreign)
    db_session.commit()

    listed_again = auth_client.get("/app/labs/uploads", params={"category": "Custom"})
    ids = {row["id"] for row in listed_again.json()}
    assert mine_id in ids
    assert str(foreign.id) not in ids

    hidden = auth_client.get(f"/app/labs/uploads/{foreign.id}")
    assert hidden.status_code == 404


def test_open_ingest_upload_detail_is_workspace_scoped_and_client_safe(auth_client):
    created = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": ("detail.csv", b"channel,spend\nemail,40\n", "text/csv")},
    )
    assert created.status_code == 200
    upload_id = created.json()["id"]

    detail = auth_client.get(f"/app/labs/uploads/{upload_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == upload_id
    assert body["run_id"] == upload_id
    assert body["status"] in {"queued", "processing", "completed", "failed"}
    assert body["progress"] in {"looking", "ready", "saved"}
    assert body["pipeline_status"]
    assert body["insights"] == []
    assert find_banned_terms(detail.text) == []


def test_open_ingest_upload_stays_free_of_auto_train_pipeline_fields(auth_client, db_session):
    """The simple-case auto-train job (apps/api/app/services/auto_train_service.py)
    runs entirely behind the client response — see docs/LABS_DATA_UNDERSTANDING.md.
    A client must never see pipeline_log/experiment_id/stored_path, even after
    the job has actually completed for that same upload.
    """
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": ("small.csv", b"channel,spend\nemail,40\nsms,10\n", "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body.keys()) == {
        "id",
        "run_id",
        "dataset_id",
        "status",
        "stage",
        "headline",
        "milestone",
        "steps",
        "category",
        "filename",
        "kind",
        "record_count",
        "fields_noticed",
        "has_named_fields",
        "structured",
        "progress",
        "message",
        "pipeline_status",
        "insights",
        "outcome",
        "created_at",
    }
    assert body["progress"] in {"looking", "ready", "saved"}
    assert body["insights"] == []
    for forbidden in ("pipeline_log", "experiment_id", "stored_path"):
        assert forbidden not in body

    from app.db.models import ClientLabUpload
    from app.services.auto_train_service import run_auto_train_job

    upload_id = db_session.query(ClientLabUpload).one().id
    run_auto_train_job(db_session, upload_id)  # too few rows -> skipped, but exercise the job synchronously
    db_session.refresh(db_session.query(ClientLabUpload).one())

    csv_missing = auth_client.get(f"/app/labs/uploads/{upload_id}/predictions.csv")
    assert csv_missing.status_code == 404

    listed = auth_client.get("/app/labs/uploads")
    assert listed.status_code == 200
    for forbidden in ("pipeline_log", "experiment_id", "stored_path"):
        assert forbidden not in listed.text
    assert find_banned_terms(listed.text) == []


def test_upload_shows_real_outcome_once_trained(auth_client, db_session, monkeypatch):
    """After auto-train completes, GET /app/labs/uploads/{id} returns numbers
    from that run's persisted results — not a mock and not CV scores."""
    import numpy as np
    import pandas as pd

    from app.db.models import ClientLabUpload, Experiment, ExperimentTestPrediction
    from app.services.auto_train_service import run_auto_train_job
    from app.services.client_lab_upload_service import SAVED_MESSAGE

    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)

    rng = np.random.default_rng(0)
    n = 200
    tenure = rng.integers(1, 72, n)
    monthly = rng.uniform(20, 120, n)
    contract = rng.choice(["Month-to-month", "One year", "Two year"], n)
    churn_p = np.where(contract == "Month-to-month", 0.55, 0.15)
    churn = np.where(rng.binomial(1, churn_p) == 1, "Yes", "No")
    frame = pd.DataFrame(
        {
            "tenure": tenure,
            "MonthlyCharges": monthly,
            "contract": contract,
            "churn": churn,
        }
    )
    created = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": ("telco.csv", frame.to_csv(index=False).encode(), "text/csv")},
    )
    assert created.status_code == 200, created.text
    upload_id = created.json()["id"]
    assert created.json()["structured"] is False
    assert created.json()["insights"] == []

    run_auto_train_job(db_session, db_session.query(ClientLabUpload).one().id)
    db_session.expire_all()

    upload = db_session.get(ClientLabUpload, UUID(upload_id))
    assert upload is not None
    assert upload.pipeline_status == "completed"
    assert upload.experiment_id is not None
    experiment = db_session.get(Experiment, upload.experiment_id)
    assert experiment is not None
    result = experiment.result
    assert isinstance(result, dict)
    test_metrics = result["test_metrics"]
    metric_name = result["task"]["evaluation_metric"]
    holdout = float(test_metrics[metric_name])
    expected_percent = round(holdout * 1000) / 10
    expected_records = int(result["analysis"]["row_count"])
    roles = result["task"].get("column_roles") or {}
    expected_features = len(roles.get("numerical") or []) + len(roles.get("categorical") or [])
    if expected_features == 0:
        log = upload.pipeline_log if isinstance(upload.pipeline_log, dict) else {}
        expected_features = len(log.get("numerical_cols") or []) + len(log.get("categorical_cols") or [])
    persisted_n = (
        db_session.query(ExperimentTestPrediction)
        .filter(ExperimentTestPrediction.experiment_id == experiment.id)
        .count()
    )
    assert persisted_n == result["split"]["n_test"]
    assert persisted_n > 0

    detail = auth_client.get(f"/app/labs/uploads/{upload_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["pipeline_status"] == "completed"
    assert body["structured"] is True
    assert body["insights"], "trained upload must return real insights"
    assert SAVED_MESSAGE not in body["message"]
    assert "not available yet" not in body["message"].lower()
    text = detail.text.lower()
    assert "who is likely to leave" in text or "who may leave" in text
    assert find_banned_terms(detail.text) == []
    outcome = body["outcome"]
    assert outcome is not None
    assert outcome["title"] == "Analysis complete"
    assert "we analyzed your dataset" in outcome["summary"].lower()
    assert outcome["dataset_name"]
    assert outcome["record_count"] == expected_records
    assert outcome["feature_count"] == expected_features
    assert outcome["task_kind"] == "classification"
    assert outcome["target_label"]
    assert "churn" not in outcome["target_label"].lower()
    assert outcome["method_label"]
    assert "random_forest" not in outcome["method_label"].lower()
    assert "logistic" not in outcome["method_label"].lower()
    assert outcome["performance_percent"] == expected_percent
    assert outcome["prediction_count"] == persisted_n
    assert outcome["prediction_count"] == len(outcome["predictions"])
    assert {"record_id", "prediction", "probability"} <= set(outcome["predictions"][0])
    assert outcome["predictions"][0]["record_id"]
    assert "columntransformer" not in text
    assert "simpleimputer" not in text
    assert "sklearn" not in text
    assert "cross-validation" not in text
    assert "cross validation" not in text
    assert "fold_metrics" not in text
    assert "experiment_id" not in body

    csv_response = auth_client.get(f"/app/labs/uploads/{upload_id}/predictions.csv")
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers.get("content-type", "")
    lines = [line for line in csv_response.text.strip().splitlines() if line]
    assert lines[0].startswith("record")
    assert "prediction" in lines[0]
    assert len(lines) - 1 == outcome["prediction_count"]
    assert find_banned_terms(csv_response.text) == []
    listed = auth_client.get("/app/labs/uploads", params={"category": "Revenue"})
    assert listed.status_code == 200
    match = next(row for row in listed.json() if row["id"] == upload_id)
    assert match["insights"]
    assert match["structured"] is True
    assert match["outcome"]["record_count"] == expected_records
    assert match["outcome"]["prediction_count"] == persisted_n
    assert match["outcome"]["performance_percent"] == expected_percent
