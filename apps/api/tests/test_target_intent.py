"""Canonical target-intent resolution: ProblemSpec first, never silent re-inference."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    ClientLabUpload,
    ProblemSpec,
    UserRole,
)
from app.domain.errors import TargetIntentConflictError, TargetNotInDatasetError
from app.engine.lab.schema_inference import (
    TARGET_CONFIDENCE_THRESHOLD,
    TARGET_MARGIN_THRESHOLD,
    choose_target_deterministically,
    generate_target_candidates,
)
from app.services.auth_service import create_user
from app.services.auto_train_service import run_auto_train_job
from app.services.lineage_service import (
    create_workflow_run,
    get_or_create_labs_workflow,
    seed_business_domains,
)
from app.services.problem_spec_service import create_problem_spec, populate_unlocked_problem_spec_target
from app.services.project_service import get_or_create_labs_project
from app.services.target_intent_service import (
    coalesce_request_targets,
    load_workspace_problem_spec,
    public_target_payload,
    resolve_execution_target,
    strip_raw_dataset_values,
)
from app.services.workspace_service import create_business_workspace


def _ambiguous_binary_tie_frame(n: int = 200, seed: int = 7) -> pd.DataFrame:
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


def _actor(db_session, prefix: str = "target-intent"):
    return create_user(
        db_session,
        email=f"{prefix}-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name=prefix,
        workspace_id=DEFAULT_WORKSPACE_ID,
    )


def _linked_spec(
    db_session,
    upload: ClientLabUpload,
    *,
    target_column: str | None,
    explicit_target: str | None = None,
    status: str = "locked",
    actor=None,
) -> ProblemSpec:
    actor = actor or _actor(db_session)
    seed_business_domains(db_session)
    project = get_or_create_labs_project(
        db_session, workspace_id=DEFAULT_WORKSPACE_ID, actor=actor
    )
    spec = create_problem_spec(
        db_session,
        actor=actor,
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=project.id,
        task_type="binary",
        business_objective="Predict the recorded outcome column.",
        target_column=target_column,
        status=status,
    )
    workflow = get_or_create_labs_workflow(
        db_session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        actor=actor,
        project=project,
    )
    create_workflow_run(
        db_session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        workflow=workflow,
        requester=actor,
        trigger_type="upload",
        source_type="spreadsheet",
        source_upload=upload,
        explicit_target=explicit_target,
        problem_spec_id=spec.id,
    )
    db_session.commit()
    return spec


def test_regression_fixture_churn_tie_is_unresolved_without_explicit_intent():
    assert TARGET_CONFIDENCE_THRESHOLD == 0.65
    assert TARGET_MARGIN_THRESHOLD == 0.08
    frame = _ambiguous_binary_tie_frame()
    candidates = generate_target_candidates(frame, list(frame.columns))
    by_name = {item.column: item.confidence for item in candidates}
    assert by_name["Churn"] == 0.65
    assert by_name["Partner"] == 0.65
    assert round(by_name["Churn"] - by_name["Partner"], 2) == 0.00
    choice = choose_target_deterministically(frame, list(frame.columns))
    assert choice.column is None
    assert choice.intent_source == "unresolved"


def test_problem_spec_churn_bypasses_ambiguous_heuristic(db_session, tmp_path):
    frame = _ambiguous_binary_tie_frame(n=120)
    path = tmp_path / "tie.csv"
    frame.to_csv(path, index=False)
    upload = ClientLabUpload(
        workspace_id=DEFAULT_WORKSPACE_ID,
        category="Revenue",
        original_filename="tie.csv",
        stored_path=str(path),
        kind="spreadsheet",
        record_count=len(frame),
        fields_noticed=list(frame.columns),
        has_named_fields=True,
    )
    db_session.add(upload)
    db_session.commit()
    _linked_spec(db_session, upload, target_column="Churn")

    seen = []

    def forbidden(*_args, **_kwargs):
        seen.append(1)
        raise AssertionError("heuristic/semantic path must not run for a canonical target")

    from app.services import target_intent_service as service

    original = service.resolve_target_selection
    service.resolve_target_selection = forbidden
    try:
        run_auto_train_job(db_session, upload.id)
    finally:
        service.resolve_target_selection = original

    db_session.refresh(upload)
    assert seen == []
    assert upload.pipeline_status == "completed", upload.pipeline_log
    assert upload.pipeline_log["target"]["column"] == "Churn"
    assert upload.pipeline_log["target"]["intent_source"] == "problem_spec"
    assert upload.pipeline_log["target"]["source"] == "explicit"
    assert "sample_values" not in str(upload.pipeline_log["target"])


def test_explicit_request_target_when_problem_spec_has_no_target(db_session):
    frame = _ambiguous_binary_tie_frame()
    choice = resolve_execution_target(
        db_session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        frame=frame,
        columns=list(frame.columns),
        requested_target="Churn",
    )
    assert choice.column == "Churn"
    assert choice.task_type == "binary"
    assert choice.source == "explicit"
    assert choice.intent_source == "request"
    assert choice.confidence == 1.0


def test_explicit_target_absent_from_dataset_is_rejected(db_session):
    frame = _ambiguous_binary_tie_frame()
    with pytest.raises(TargetNotInDatasetError) as caught:
        resolve_execution_target(
            db_session,
            workspace_id=DEFAULT_WORKSPACE_ID,
            frame=frame,
            columns=list(frame.columns),
            requested_target="invented",
        )
    detail = json.dumps(caught.value.public_detail())
    assert caught.value.status_code == 422
    assert caught.value.code == "TARGET_NOT_IN_DATASET"
    assert "Yes" not in detail
    assert "No" not in detail
    assert "invented" in detail


def test_request_target_conflicts_with_authoritative_problem_spec(db_session):
    actor = _actor(db_session, "conflict")
    seed_business_domains(db_session)
    project = get_or_create_labs_project(
        db_session, workspace_id=DEFAULT_WORKSPACE_ID, actor=actor
    )
    spec = create_problem_spec(
        db_session,
        actor=actor,
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=project.id,
        task_type="binary",
        business_objective="Canonical monthly spend target.",
        target_column="MonthlyCharges",
        status="locked",
    )
    db_session.commit()
    frame = _ambiguous_binary_tie_frame()
    with pytest.raises(TargetIntentConflictError) as caught:
        resolve_execution_target(
            db_session,
            workspace_id=DEFAULT_WORKSPACE_ID,
            frame=frame,
            columns=list(frame.columns),
            problem_spec_id=spec.id,
            project_id=project.id,
            requested_target="Churn",
        )
    detail = caught.value.public_detail()
    assert caught.value.status_code == 409
    assert detail["code"] == "TARGET_INTENT_CONFLICT"
    assert detail["problem_spec_target"] == "MonthlyCharges"
    assert detail["requested_target"] == "Churn"
    blob = json.dumps(detail)
    assert "Yes" not in blob
    assert "No" not in blob


def test_ambiguous_deterministic_target_without_explicit_intent_stays_unresolved(
    db_session, monkeypatch
):
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )
    frame = _ambiguous_binary_tie_frame()
    choice = resolve_execution_target(
        db_session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        frame=frame,
        columns=list(frame.columns),
    )
    assert choice.column is None
    assert choice.intent_source == "unresolved"
    assert "target selection is ambiguous" in choice.reason
    payload = public_target_payload(choice)
    assert payload["status"] == "unresolved"
    assert "sample_values" not in json.dumps(payload)


def test_semantic_assistance_is_not_called_when_target_is_canonical(db_session, monkeypatch):
    actor = _actor(db_session, "canonical-skip-llm")
    seed_business_domains(db_session)
    project = get_or_create_labs_project(
        db_session, workspace_id=DEFAULT_WORKSPACE_ID, actor=actor
    )
    spec = create_problem_spec(
        db_session,
        actor=actor,
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=project.id,
        task_type="binary",
        business_objective="Honor the labeled churn column.",
        target_column="Churn",
        status="locked",
    )
    db_session.commit()
    called = []

    def boom(*_args, **_kwargs):
        called.append("semantic")
        raise AssertionError("semantic target assistance must not run")

    monkeypatch.setattr(
        "app.services.target_intent_service.resolve_target_selection", boom
    )
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.request_target_selection_decision", boom
    )
    frame = _ambiguous_binary_tie_frame()
    choice = resolve_execution_target(
        db_session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        frame=frame,
        columns=list(frame.columns),
        problem_spec_id=spec.id,
        project_id=project.id,
    )
    assert called == []
    assert choice.column == "Churn"
    assert choice.intent_source == "problem_spec"


def test_semantic_assistance_may_resolve_genuinely_ambiguous_inference(db_session, monkeypatch):
    from app.engine.lab import llm_client
    from app.services import lab_decision_ledger

    llm_client._TARGET_SELECTION_CACHE.clear()
    settings = SimpleNamespace(
        decision_agent_enabled=True,
        decision_agent_api_key="sk-test",
        decision_agent_model="configured-small-model",
    )
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    monkeypatch.setattr(lab_decision_ledger, "get_settings", lambda: settings)

    def fake_post(url, **kwargs):
        payload = {
            "target": "measure_b",
            "task_type": "regression",
            "evidence_field": "columns",
            "rationale": "measure_b is the intended response",
            "confidence": 0.91,
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload)}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(llm_client.httpx, "post", fake_post)
    frame = pd.DataFrame(
        {
            "measure_a": np.linspace(1, 50, 100),
            "measure_b": np.linspace(5, 100, 100) ** 1.1,
            "measure_c": np.linspace(3, 75, 100) ** 1.05,
        }
    )
    choice = resolve_execution_target(
        db_session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        frame=frame,
        columns=list(frame.columns),
    )
    assert choice.column == "measure_b"
    assert choice.source == "llm"
    assert choice.intent_source == "llm"


def test_foreign_problem_spec_is_not_applied(db_session):
    owner = _actor(db_session, "tenant-a")
    other_owner = create_user(
        db_session,
        email=f"tenant-b-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="tenant-b",
        workspace_id=DEFAULT_WORKSPACE_ID,
    )
    other_workspace = create_business_workspace(
        db_session, owner=other_owner, name="Other Co"
    )
    other_project = get_or_create_labs_project(
        db_session, workspace_id=other_workspace.id, actor=other_owner
    )
    foreign = create_problem_spec(
        db_session,
        actor=other_owner,
        workspace_id=other_workspace.id,
        project_id=other_project.id,
        task_type="binary",
        business_objective="Foreign churn spec must not leak.",
        target_column="Churn",
        status="locked",
    )
    db_session.commit()
    loaded = load_workspace_problem_spec(
        db_session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        problem_spec_id=foreign.id,
    )
    assert loaded is None
    frame = _ambiguous_binary_tie_frame()
    choice = resolve_execution_target(
        db_session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        frame=frame,
        columns=list(frame.columns),
        problem_spec_id=foreign.id,
        project_id=None,
    )
    assert choice.column is None
    assert choice.intent_source == "unresolved"
    assert owner.workspace_id != other_workspace.id


def test_error_and_llm_metadata_omit_raw_dataset_values():
    from app.services.lab_decision_ledger import _safe_semantic_output

    payload = strip_raw_dataset_values(
        {
            "column": "Churn",
            "sample_values": ["Yes", "No"],
            "candidates": [{"column": "Partner", "evidence": {"sample_values": ["Yes"]}}],
        }
    )
    blob = json.dumps(payload)
    assert "sample_values" not in blob
    assert "Yes" not in blob
    assert payload["column"] == "Churn"
    safe = _safe_semantic_output(
        {
            "target": "Churn",
            "task_type": "binary",
            "confidence": 0.9,
            "rationale": "values look like Yes/No",
            "sample_values": ["Yes", "No"],
        }
    )
    assert safe == {"target": "Churn", "task_type": "binary", "confidence": 0.9}


def test_legacy_upload_explicit_target_still_resolves_a_tie(db_session, tmp_path):
    frame = _ambiguous_binary_tie_frame(n=120)
    path = tmp_path / "legacy.csv"
    frame.to_csv(path, index=False)
    upload = ClientLabUpload(
        workspace_id=DEFAULT_WORKSPACE_ID,
        category="Revenue",
        original_filename="legacy.csv",
        stored_path=str(path),
        kind="spreadsheet",
        record_count=len(frame),
        fields_noticed=list(frame.columns),
        has_named_fields=True,
        explicit_target_column="Churn",
    )
    db_session.add(upload)
    db_session.commit()
    run_auto_train_job(db_session, upload.id)
    db_session.refresh(upload)
    assert upload.pipeline_status == "completed", upload.pipeline_log
    assert upload.pipeline_log["target"]["column"] == "Churn"
    assert upload.pipeline_log["target"]["intent_source"] == "request"


def test_unlocked_problem_spec_is_populated_from_request_target(db_session):
    actor = _actor(db_session, "populate")
    seed_business_domains(db_session)
    project = get_or_create_labs_project(
        db_session, workspace_id=DEFAULT_WORKSPACE_ID, actor=actor
    )
    spec = create_problem_spec(
        db_session,
        actor=actor,
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=project.id,
        task_type="binary",
        business_objective="Draft spec waiting for a target.",
        target_column=None,
        status="draft",
    )
    db_session.commit()
    populate_unlocked_problem_spec_target(db_session, spec, "Churn")
    db_session.commit()
    db_session.refresh(spec)
    assert spec.target_column == "Churn"
    assert spec.status == "draft"
    assert spec.locked_at is None


def test_compatibility_fields_cannot_silently_disagree():
    with pytest.raises(TargetIntentConflictError) as caught:
        coalesce_request_targets(["Churn", "MonthlyCharges"])
    assert caught.value.requested_target == "Churn"
    assert caught.value.conflicting_target == "MonthlyCharges"


def test_labs_upload_conflict_returns_typed_409(
    auth_client, db_session, client_user, monkeypatch
):
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None
    )
    seed_business_domains(db_session)
    project = get_or_create_labs_project(
        db_session, workspace_id=DEFAULT_WORKSPACE_ID, actor=client_user
    )
    spec = create_problem_spec(
        db_session,
        actor=client_user,
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=project.id,
        task_type="binary",
        business_objective="Canonical monthly spend target.",
        target_column="MonthlyCharges",
        status="locked",
    )
    db_session.commit()
    frame = _ambiguous_binary_tie_frame(n=40)
    response = auth_client.post(
        "/app/labs/uploads",
        data={
            "category": "Revenue",
            "target_column": "Churn",
            "problem_spec_id": str(spec.id),
        },
        files={"file": ("tie.csv", frame.to_csv(index=False).encode(), "text/csv")},
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "TARGET_INTENT_CONFLICT"
    assert detail["problem_spec_target"] == "MonthlyCharges"
    assert detail["requested_target"] == "Churn"
    assert "Yes" not in response.text
    assert db_session.scalar(select(ClientLabUpload.id)) is None


def test_labs_upload_missing_target_column_returns_422(
    auth_client, db_session, monkeypatch
):
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None
    )
    frame = _ambiguous_binary_tie_frame(n=40)
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue", "target_column": "invented"},
        files={"file": ("tie.csv", frame.to_csv(index=False).encode(), "text/csv")},
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "TARGET_NOT_IN_DATASET"
    assert detail["target_column"] == "invented"


def test_v1_execution_request_conflict_returns_409(
    auth_client, db_session, client_user
):
    seed_business_domains(db_session)
    project = get_or_create_labs_project(
        db_session, workspace_id=DEFAULT_WORKSPACE_ID, actor=client_user
    )
    spec = create_problem_spec(
        db_session,
        actor=client_user,
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=project.id,
        task_type="binary",
        business_objective="Canonical monthly spend target.",
        target_column="MonthlyCharges",
        status="locked",
    )
    db_session.commit()
    response = auth_client.post(
        "/v1/execution-requests",
        json={
            "operation": "model_build",
            "project_id": str(project.id),
            "request_spec": {
                "target_column": "Churn",
                "problem_spec_id": str(spec.id),
            },
        },
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "TARGET_INTENT_CONFLICT"
    assert detail["problem_spec_target"] == "MonthlyCharges"
    assert detail["requested_target"] == "Churn"
