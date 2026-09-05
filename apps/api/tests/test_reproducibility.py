"""Runtime environment, code snapshot, and model artifact lineage."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from adaptive_modeling.fixtures import ordinary_binary
from adaptive_modeling.production import labs_upload_and_train
from app.db.models import (
    Artifact,
    CodeSnapshot,
    ExperimentCandidate,
    ModelVersion,
    RuntimeEnvironment,
    UserRole,
)
from app.services.artifact_service import store_artifact
from app.services.auth_service import create_access_token, create_user
from app.services.pipeline_verifier import PipelineVerifier
from app.services.workspace_service import create_business_workspace
from app.storage.factory import get_object_storage


@pytest.fixture
def _rule_engine_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )


def _headers(user, workspace_id=None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {create_access_token(user)}"}
    if workspace_id is not None:
        headers["X-Workspace-Id"] = str(workspace_id)
    return headers


def _status_for(result, check_id: str) -> str:
    return next(row["status"] for row in result["checks"] if row["check_id"] == check_id)


def test_runtime_code_and_model_artifact_lineage_from_auto_train(
    auth_client, db_session, monkeypatch, _rule_engine_only
):
    frame = ordinary_binary()
    upload, _workflow_run, experiment, model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="reproducibility.csv",
        target="outcome",
    )
    assert upload.pipeline_status == "completed"
    db_session.refresh(model_version)
    assert model_version.runtime_environment_id is not None
    runtime = db_session.get(RuntimeEnvironment, model_version.runtime_environment_id)
    assert runtime is not None
    assert runtime.python_version
    assert runtime.environment_digest
    assert len(runtime.environment_digest) == 64
    snapshot = db_session.scalar(
        select(CodeSnapshot).where(CodeSnapshot.pipeline_run_id == experiment.id)
    )
    assert snapshot is not None
    assert snapshot.id == model_version.code_snapshot_id
    assert snapshot.language == "python"
    assert snapshot.artifact_id
    assert snapshot.runtime_environment_id == runtime.id
    assert snapshot.dependency_lock_artifact_id is not None
    assert snapshot.dependency_lock_digest
    assert model_version.model_artifact_id is not None
    model_artifact = db_session.get(Artifact, model_version.model_artifact_id)
    assert model_artifact is not None
    stored = get_object_storage().get(model_artifact.object_key)
    assert model_artifact.content_digest == hashlib.sha256(stored).hexdigest()
    assert model_version.artifact_uri
    assert model_version.selected_candidate_id
    winner = db_session.get(ExperimentCandidate, model_version.selected_candidate_id)
    assert winner is not None
    assert winner.experiment_id == experiment.id
    assert model_version.dataset_id == experiment.dataset_id
    assert model_version.feature_set_version_id is not None
    assert model_version.workspace_id == experiment.workspace_id
    assert model_version.project_id == experiment.project_id
    assert model_version.pipeline_run_id == experiment.id

    workspace_id = model_version.workspace_id
    meta = auth_client.get(
        f"/workspaces/{workspace_id}/model-versions/{model_version.id}/reproducibility"
    )
    assert meta.status_code == 200, meta.text
    body = meta.json()
    assert body["model_artifact_id"] == str(model_artifact.id)
    assert body["code_snapshot"]["artifact_id"] == str(snapshot.artifact_id)
    artifacts = auth_client.get(
        f"/workspaces/{workspace_id}/model-versions/{model_version.id}/artifacts"
    )
    assert artifacts.status_code == 200
    types = {row["artifact_type"] for row in artifacts.json()}
    assert "model" in types
    assert "source_code" in types
    assert "dependency_lock" in types
    source = auth_client.get(
        f"/workspaces/{workspace_id}/artifacts/{snapshot.artifact_id}/download"
    )
    assert source.status_code == 200
    assert source.content[:2] == b"PK"
    lock_id = body["code_snapshot"]["dependency_lock_artifact_id"]
    assert lock_id is not None
    assert "dependency_lock_artifact_id" not in body["runtime_environment"]
    lock_row = db_session.get(Artifact, lock_id)
    assert lock_row is not None
    assert lock_row.workspace_id == workspace_id
    assert snapshot.dependency_lock_artifact_id == lock_row.id
    lock = auth_client.get(f"/workspaces/{workspace_id}/artifacts/{lock_id}/download")
    assert lock.status_code == 200
    assert b"==" in lock.content
    signed = auth_client.get(
        f"/workspaces/{workspace_id}/artifacts/{model_artifact.id}/signed-url"
    )
    assert signed.status_code == 200
    signed_body = signed.json()
    blob = json_blob = str(signed_body).lower()
    assert "aws_secret" not in blob
    assert "aws_access_key" not in blob
    assert "credential" not in blob
    assert "google_application" not in blob
    assert signed_body["url"]
    assert signed_body["artifact_id"] == str(model_artifact.id)


def test_shared_runtime_environment_keeps_per_workspace_lock_artifacts(
    client, db_session
):
    from app.db.models import Dataset, DatasetAsset, Experiment
    from app.services.lab_service import seed_dogfood
    from app.services.reproducibility_service import (
        artifacts_for_model_version,
        persist_reproducibility,
    )

    owner_a = create_user(
        db_session,
        email=f"lock-a-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Lock A",
    )
    owner_b = create_user(
        db_session,
        email=f"lock-b-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Lock B",
    )
    workspace_a = create_business_workspace(db_session, owner=owner_a, name="Lock Tenant A")
    workspace_b = create_business_workspace(db_session, owner=owner_b, name="Lock Tenant B")
    db_session.commit()
    environment = seed_dogfood(db_session)

    def _pipeline_run(workspace, name: str) -> Experiment:
        slug = f"lock-{uuid4().hex[:12]}"
        asset = DatasetAsset(workspace_id=workspace.id, name=name, slug=slug)
        db_session.add(asset)
        db_session.flush()
        dataset = Dataset(
            workspace_id=workspace.id,
            dataset_asset_id=asset.id,
            environment_id=environment.id,
            name=name,
            source_type="csv",
            location=f"/tmp/{slug}.csv",
            version="v1",
            row_count=1,
            column_count=1,
        )
        db_session.add(dataset)
        db_session.flush()
        experiment = Experiment(
            workspace_id=workspace.id,
            environment_id=environment.id,
            dataset_id=dataset.id,
            status="CREATED",
            config={},
            result={},
        )
        db_session.add(experiment)
        db_session.flush()
        return experiment

    experiment_a = _pipeline_run(workspace_a, "Tenant A run")
    experiment_b = _pipeline_run(workspace_b, "Tenant B run")
    repro_a = persist_reproducibility(db_session, experiment_a, {})
    repro_b = persist_reproducibility(db_session, experiment_b, {})
    db_session.commit()

    assert "dependency_lock_artifact_id" not in RuntimeEnvironment.__table__.c
    assert repro_a.runtime_environment.id == repro_b.runtime_environment.id
    assert repro_a.runtime_environment.environment_digest == (
        repro_b.runtime_environment.environment_digest
    )
    lock_a = repro_a.dependency_lock_artifact
    lock_b = repro_b.dependency_lock_artifact
    assert lock_a is not None and lock_b is not None
    assert lock_a.id != lock_b.id
    assert lock_a.workspace_id == workspace_a.id
    assert lock_b.workspace_id == workspace_b.id
    assert repro_a.code_snapshot is not None
    assert repro_b.code_snapshot is not None
    assert repro_a.code_snapshot.dependency_lock_artifact_id == lock_a.id
    assert repro_b.code_snapshot.dependency_lock_artifact_id == lock_b.id
    assert repro_a.code_snapshot.runtime_environment_id == repro_a.runtime_environment.id

    listed_b = artifacts_for_model_version(
        db_session,
        SimpleNamespace(
            workspace_id=workspace_b.id,
            model_artifact_id=None,
            preprocessor_artifact_id=None,
            feature_manifest_artifact_id=None,
            code_snapshot=repro_b.code_snapshot,
            pipeline_run=experiment_b,
            runtime_environment=repro_a.runtime_environment,
        ),
    )
    assert {row.id for row in listed_b}.isdisjoint({lock_a.id})
    assert lock_b.id in {row.id for row in listed_b}
    assert all(row.workspace_id == workspace_b.id for row in listed_b)

    denied = client.get(
        f"/workspaces/{workspace_b.id}/artifacts/{lock_a.id}/download",
        headers=_headers(owner_b, workspace_b.id),
    )
    assert denied.status_code in {403, 404}
    foreign_workspace = client.get(
        f"/workspaces/{workspace_a.id}/artifacts/{lock_a.id}/download",
        headers=_headers(owner_b, workspace_a.id),
    )
    assert foreign_workspace.status_code in {403, 404}
    own = client.get(
        f"/workspaces/{workspace_b.id}/artifacts/{lock_b.id}/download",
        headers=_headers(owner_b, workspace_b.id),
    )
    assert own.status_code == 200, own.text


def test_developer_cannot_read_another_workspace_artifact_but_platform_developer_can(
    client, db_session, client_user
):
    owner = create_user(
        db_session,
        email=f"owner-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Other Owner",
    )
    other = create_business_workspace(db_session, owner=owner, name="Other Co")
    artifact = store_artifact(
        db_session,
        workspace_id=other.id,
        artifact_type="source_code",
        filename="secret.zip",
        data=b"PK\x03\x04secret",
        mime_type="application/zip",
        extra_metadata={"role": "source_package"},
    )
    db_session.commit()
    denied = client.get(
        f"/workspaces/{other.id}/artifacts/{artifact.id}/download",
        headers=_headers(client_user, other.id),
    )
    assert denied.status_code in {403, 404}
    platform_dev = create_user(
        db_session,
        email=f"dev-{uuid4().hex}@dclab.test",
        password="test-password",
        role=UserRole.DCLAB_DEVELOPER,
        full_name="Internal Developer",
    )
    db_session.commit()
    allowed = client.get(
        f"/admin/artifacts/{artifact.id}/download",
        headers=_headers(platform_dev),
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.content == b"PK\x03\x04secret"
    cross = client.get(
        f"/workspaces/{other.id}/artifacts/{artifact.id}",
        headers=_headers(platform_dev, other.id),
    )
    assert cross.status_code == 200


def test_missing_model_artifact_fails_verifier_when_blob_is_gone(
    auth_client, db_session, monkeypatch, _rule_engine_only
):
    frame = ordinary_binary()
    _upload, _workflow_run, experiment, model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="missing_artifact.csv",
        target="outcome",
    )
    db_session.refresh(model_version)
    artifact = db_session.get(Artifact, model_version.model_artifact_id)
    assert artifact is not None
    get_object_storage().delete(artifact.object_key)
    report = (experiment.result or {}).get("technical_report") or {
        "run": {"experiment_id": str(experiment.id), "status": "completed"}
    }
    result = PipelineVerifier().verify(report, db=db_session)
    assert _status_for(result, "model_artifact_registered") == "FAIL"
    assert result["overall_status"] == "FAILED"
