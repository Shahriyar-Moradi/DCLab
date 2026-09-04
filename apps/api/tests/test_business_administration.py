from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.db.models import (
    BusinessDomain,
    ClientLabUpload,
    Experiment,
    LlmInvocation,
    ModelVersion,
    UserRole,
    Workspace,
    WorkspaceCapability,
    WorkspaceDomain,
    WorkspaceMembership,
    WorkflowRun,
)
from app.services.auth_service import create_access_token, create_user
from app.services.auto_train_service import run_auto_train_job
from app.services.pipeline_audit_service import request_pipeline_verification
from app.services.workspace_capability_service import BUSINESS_CAPABILITIES


def _headers(user, workspace_id=None):
    headers = {"Authorization": f"Bearer {create_access_token(user)}"}
    if workspace_id is not None:
        headers["X-Workspace-Id"] = str(workspace_id)
    return headers


def _set_capability(db, workspace_id, key: str, enabled: bool = True):
    row = db.scalar(
        select(WorkspaceCapability).where(
            WorkspaceCapability.workspace_id == workspace_id,
            WorkspaceCapability.capability == key,
        )
    )
    if row is None:
        row = WorkspaceCapability(
            workspace_id=workspace_id,
            capability=key,
            enabled=enabled,
            configuration={},
        )
        db.add(row)
    else:
        row.enabled = enabled
    db.commit()


def _upload_and_run(auth_client, db, monkeypatch):
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None
    )
    rng = np.random.default_rng(9321)
    feature = rng.normal(size=110)
    frame = pd.DataFrame(
        {
            "feature": feature,
            "segment": rng.choice(["small", "large"], len(feature)),
            "outcome": (feature + rng.normal(scale=0.3, size=len(feature)) > 0).astype(int),
        }
    )
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Customer Value", "target_column": "outcome"},
        files={"file": ("business-plane.csv", frame.to_csv(index=False).encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    upload = db.get(ClientLabUpload, response.json()["id"])
    run_auto_train_job(db, upload.id)
    db.expire_all()
    upload = db.get(ClientLabUpload, upload.id)
    pipeline = db.get(Experiment, upload.experiment_id)
    workflow_run = db.get(WorkflowRun, pipeline.workflow_run_id)
    assert pipeline.status == "COMPLETED"
    return upload, workflow_run, pipeline


def test_business_plane_is_tenant_scoped_capability_filtered_and_readonly(
    auth_client, admin_client, db_session, monkeypatch
):
    upload, workflow_run, pipeline = _upload_and_run(
        auth_client, db_session, monkeypatch
    )
    workspace_id = upload.workspace_id
    business_admin = create_user(
        db_session,
        email=f"business-plane-admin-{uuid4().hex}@test.invalid",
        password="password",
        role=UserRole.BUSINESS_ADMIN,
        workspace_id=workspace_id,
    )
    business_developer = create_user(
        db_session,
        email=f"business-plane-developer-{uuid4().hex}@test.invalid",
        password="password",
        role=UserRole.BUSINESS_DEVELOPER,
        workspace_id=workspace_id,
    )
    foreign = Workspace(slug=f"foreign-{uuid4().hex}", name="Foreign Business")
    db_session.add(foreign)
    db_session.flush()
    labs_domain = db_session.scalar(
        select(BusinessDomain).where(BusinessDomain.slug == "labs")
    )
    foreign_domain = WorkspaceDomain(
        workspace_id=foreign.id,
        business_domain_id=labs_domain.id,
        enabled=True,
        config={},
    )
    disabled_catalog = db_session.scalar(
        select(BusinessDomain).where(BusinessDomain.slug == "marketing")
    )
    disabled_domain = WorkspaceDomain(
        workspace_id=workspace_id,
        business_domain_id=disabled_catalog.id,
        enabled=False,
        config={},
    )
    db_session.add_all([foreign_domain, disabled_domain])
    db_session.commit()

    admin_headers = _headers(business_admin, workspace_id)
    developer_headers = _headers(business_developer, workspace_id)

    listed = auth_client.get("/business/workspaces", headers=admin_headers)
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [str(workspace_id)]
    assert set(listed.json()[0]["capabilities"]) == set(BUSINESS_CAPABILITIES)
    assert not any(listed.json()[0]["capabilities"].values())

    detail = auth_client.get(
        f"/business/workspaces/{workspace_id}", headers=admin_headers
    )
    assert detail.status_code == 200
    assert all(row["enabled"] for row in detail.json()["domains"])
    assert str(disabled_domain.id) not in {row["id"] for row in detail.json()["domains"]}
    assert detail.json()["memberships"] == []
    assert auth_client.get(
        f"/business/workspaces/{foreign.id}", headers=admin_headers
    ).status_code == 404
    assert auth_client.get(
        f"/business/workspaces/{workspace_id}/domains/{foreign_domain.id}",
        headers=admin_headers,
    ).status_code == 404

    monitor_path = (
        f"/business/workspaces/{workspace_id}/pipeline-runs/{pipeline.id}/monitor"
    )
    assert auth_client.get(monitor_path, headers=admin_headers).status_code == 403
    # Platform roles are deliberately not constrained by business flags.
    assert admin_client.get(monitor_path).status_code == 200

    _set_capability(db_session, workspace_id, "pipeline_monitor")
    limited = auth_client.get(monitor_path, headers=admin_headers)
    assert limited.status_code == 200, limited.text
    limited_body = limited.json()
    assert limited_body["sanitized_evidence"] == {}
    assert all(row["payload"] == {} for row in limited_body["events"])
    assert not any(
        row["event_type"].startswith("cv_fold_") for row in limited_body["events"]
    )
    assert not any(
        row["purpose"].startswith("semantic_")
        for row in limited_body["llm_invocations"]
    )
    assert "decision_records" not in str(limited_body["reports"])

    semantic_invocation = db_session.scalar(
        select(LlmInvocation).where(
            LlmInvocation.experiment_id == pipeline.id,
            LlmInvocation.purpose.like("semantic_%"),
        )
    )
    assert semantic_invocation is not None
    assert auth_client.get(
        f"/business/observatory/llm-invocations/{semantic_invocation.id}",
        headers=admin_headers,
    ).status_code == 403

    summary = auth_client.get(
        f"/business/observatory/pipeline-runs/{pipeline.id}/summary",
        headers=admin_headers,
    )
    assert summary.status_code == 200
    assert auth_client.get(
        f"/business/observatory/pipeline-runs/{pipeline.id}/events",
        headers=admin_headers,
    ).status_code == 403

    for capability in (
        "cv_fold_details",
        "semantic_llm_audit",
        "openai_pipeline_audit",
        "raw_pipeline_debug",
        "decision_ledger",
    ):
        _set_capability(db_session, workspace_id, capability)
    complete = auth_client.get(monitor_path, headers=developer_headers)
    assert complete.status_code == 200
    body = complete.json()
    assert body["sanitized_evidence"]
    assert any(row["payload"] for row in body["events"])
    assert any(
        row["event_type"] == "cv_fold_completed" for row in body["events"]
    )
    semantic = [
        row
        for row in body["llm_invocations"]
        if row["purpose"].startswith("semantic_")
    ]
    assert semantic
    assert auth_client.get(
        f"/business/observatory/pipeline-runs/{pipeline.id}/events",
        headers=admin_headers,
    ).status_code == 200

    # Even a user authorized for both workspaces cannot substitute an object ID
    # from one tenant beneath the other tenant's URL.
    db_session.add(
        WorkspaceMembership(
            workspace_id=foreign.id,
            user_id=business_admin.id,
            role="business_admin",
        )
    )
    db_session.commit()
    assert auth_client.get(
        f"/business/workspaces/{foreign.id}/pipeline-runs/{pipeline.id}/monitor",
        headers=_headers(business_admin, foreign.id),
    ).status_code == 404
    _set_capability(db_session, foreign.id, "pipeline_monitor")
    assert auth_client.get(
        f"/business/workspaces/{foreign.id}/pipeline-runs/{pipeline.id}/monitor",
        headers=_headers(business_admin, foreign.id),
    ).status_code == 404


def test_prediction_download_and_deep_audit_cannot_bypass_capabilities(
    auth_client, db_session, monkeypatch
):
    upload, _workflow_run, pipeline = _upload_and_run(
        auth_client, db_session, monkeypatch
    )
    workspace_id = upload.workspace_id
    business_admin = create_user(
        db_session,
        email=f"business-download-admin-{uuid4().hex}@test.invalid",
        password="password",
        role=UserRole.BUSINESS_ADMIN,
        workspace_id=workspace_id,
    )
    business_developer = create_user(
        db_session,
        email=f"business-download-developer-{uuid4().hex}@test.invalid",
        password="password",
        role=UserRole.BUSINESS_DEVELOPER,
        workspace_id=workspace_id,
    )
    db_session.commit()
    admin_headers = _headers(business_admin, workspace_id)
    developer_headers = _headers(business_developer, workspace_id)
    business_download = f"/business/workspaces/{workspace_id}/client-uploads/{upload.id}/predictions.csv"
    legacy_download = f"/app/labs/uploads/{upload.id}/predictions.csv"

    assert auth_client.get(business_download, headers=admin_headers).status_code == 403
    assert auth_client.get(legacy_download, headers=admin_headers).status_code == 403
    _set_capability(db_session, workspace_id, "prediction_download")
    assert auth_client.get(business_download, headers=admin_headers).status_code == 200
    assert auth_client.get(legacy_download, headers=admin_headers).status_code == 200

    deep_path = f"/business/workspaces/{workspace_id}/lab-runs/{upload.id}/verification/deep"
    assert auth_client.post(deep_path, headers=admin_headers).status_code == 403
    _set_capability(db_session, workspace_id, "openai_pipeline_audit")
    assert auth_client.post(deep_path, headers=admin_headers).status_code == 403
    _set_capability(db_session, workspace_id, "deep_audit")
    assert auth_client.post(deep_path, headers=developer_headers).status_code == 403

    attempt = request_pipeline_verification(
        db_session,
        upload.id,
        deep=True,
        settings=SimpleNamespace(
            pipeline_llm_verifier_enabled=False,
            pipeline_llm_verifier_api_key="",
            pipeline_llm_verifier_model="gpt-5.6-luna",
            pipeline_llm_verifier_deep_model="gpt-5.6-terra",
            pipeline_llm_timeout_seconds=1.0,
        ),
    )
    monkeypatch.setattr(
        "app.api.business_explorer.request_pipeline_verification",
        lambda *_args, **_kwargs: attempt,
    )
    triggered = auth_client.post(deep_path, headers=admin_headers)
    assert triggered.status_code == 200, triggered.text
    assert triggered.json()["audit_mode"] == "deep"
    assert triggered.json()["llm_model"] == "gpt-5.6-terra"

    audit_invocation = db_session.get(LlmInvocation, attempt.llm_invocation_id)
    assert audit_invocation.purpose == "pipeline_audit_deep"
    _set_capability(db_session, workspace_id, "pipeline_monitor")
    detail_path = f"/business/observatory/llm-invocations/{audit_invocation.id}"
    _set_capability(db_session, workspace_id, "openai_pipeline_audit", False)
    assert auth_client.get(detail_path, headers=admin_headers).status_code == 403
    _set_capability(db_session, workspace_id, "openai_pipeline_audit")
    assert auth_client.get(detail_path, headers=admin_headers).status_code == 200


def test_model_management_fails_closed_on_business_model_routes(
    auth_client, db_session, monkeypatch
):
    upload, _workflow_run, pipeline = _upload_and_run(
        auth_client, db_session, monkeypatch
    )
    workspace_id = upload.workspace_id
    version = db_session.scalar(
        select(ModelVersion).where(ModelVersion.pipeline_run_id == pipeline.id)
    )
    assert version is not None
    business_admin = create_user(
        db_session,
        email=f"business-model-admin-{uuid4().hex}@test.invalid",
        password="password",
        role=UserRole.BUSINESS_ADMIN,
        workspace_id=workspace_id,
    )
    db_session.commit()
    headers = _headers(business_admin, workspace_id)
    model_path = (
        f"/business/workspaces/{workspace_id}/models/{version.model_asset_id}"
    )

    assert auth_client.get(model_path, headers=headers).status_code == 403
    detail = auth_client.get(
        f"/business/workspaces/{workspace_id}", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["models"] == []
    assert detail.json()["model_count"] == 0
    assert detail.json()["capabilities"]["model_management"] is False

    _set_capability(db_session, workspace_id, "model_management")
    allowed = auth_client.get(model_path, headers=headers)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["id"] == str(version.model_asset_id)
    enabled_detail = auth_client.get(
        f"/business/workspaces/{workspace_id}", headers=headers
    )
    assert enabled_detail.status_code == 200
    assert str(version.model_asset_id) in {
        row["id"] for row in enabled_detail.json()["models"]
    }
