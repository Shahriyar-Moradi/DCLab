from __future__ import annotations

import hashlib
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    Artifact,
    ClientLabUpload,
    DataSource,
    Dataset,
    DatasetAsset,
    DatasetColumn,
    IngestionRun,
    Project,
    UserRole,
    Workspace,
)
from app.domain.data_plane import LABS_PROJECT_SLUG
from app.domain.errors import (
    ArtifactNotFoundError,
    DataSourceConfigurationError,
    IdentityError,
)
from app.services.artifact_service import read_artifact_bytes, store_artifact
from app.services.auth_service import create_user
from app.services.data_source_service import create_data_source, get_data_source
from app.services.ingestion_run_service import (
    complete_ingestion_run,
    fail_ingestion_run,
    start_ingestion_run,
)
from app.services.lab_service import ingest_dataset, seed_dogfood
from app.services.lineage_service import create_dataset_asset, create_pipeline_run, create_workflow_run
from app.services.project_service import create_project
from app.services.reproducibility_service import artifacts_for_pipeline_run
from app.storage.exceptions import GCS_SDK_INSTALL, ObjectStorageError, S3_SDK_INSTALL
from app.storage.gcs import GCSStorage
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage
from test_data_model_lineage import make_lineage_setup


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _user(db, *, workspace_id, email_prefix: str = "lineage"):
    return create_user(
        db,
        email=f"{email_prefix}-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name=email_prefix,
        workspace_id=workspace_id,
    )


def _workspace(db, name: str) -> Workspace:
    row = Workspace(slug=f"{name}-{uuid4().hex[:10]}", name=name)
    db.add(row)
    db.flush()
    return row


def test_local_artifact_upload_read_and_digest(tmp_path):
    storage = LocalStorage(root=tmp_path)
    payload = b"alpha,beta\n1,2\n"
    result = storage.put("tenants/a/file.csv", payload, content_type="text/csv")
    assert result.content_digest == _sha256(payload)
    assert storage.get("tenants/a/file.csv") == payload
    assert storage.exists("tenants/a/file.csv")
    assert storage.checksum("tenants/a/file.csv") == result.content_digest
    assert storage.metadata("tenants/a/file.csv").size_bytes == len(payload)
    with storage.open("tenants/a/file.csv") as handle:
        assert handle.read() == payload
    assert storage.signed_url("tenants/a/file.csv").startswith("file://")
    storage.delete("tenants/a/file.csv")
    assert storage.exists("tenants/a/file.csv") is False


def test_artifact_digest_preserved_and_workspace_isolated(db_session, tmp_path):
    storage = LocalStorage(root=tmp_path)
    alpha = _workspace(db_session, "alpha")
    beta = _workspace(db_session, "beta")
    actor = _user(db_session, workspace_id=alpha.id)
    payload = b"feature,target\n1,0\n"
    artifact = store_artifact(
        db_session,
        workspace_id=alpha.id,
        artifact_type="dataset",
        filename="leads.csv",
        data=payload,
        created_by=actor.id,
        storage=storage,
    )
    db_session.commit()
    assert artifact.content_digest == _sha256(payload)
    assert read_artifact_bytes(
        db_session,
        workspace_id=alpha.id,
        artifact_id=artifact.id,
        storage=storage,
    ) == payload
    with pytest.raises(ArtifactNotFoundError):
        read_artifact_bytes(
            db_session,
            workspace_id=beta.id,
            artifact_id=artifact.id,
            storage=storage,
        )
    assert (
        db_session.query(Artifact)
        .filter(Artifact.workspace_id == beta.id)
        .count()
        == 0
    )


def test_data_source_creation_rejects_inline_secrets(db_session):
    workspace = _workspace(db_session, "source")
    actor = _user(db_session, workspace_id=workspace.id)
    source = create_data_source(
        db_session,
        workspace_id=workspace.id,
        name="Uploads",
        source_type="upload",
        provider="local",
        created_by=actor.id,
        configuration={"format": "csv"},
        credential_reference="vault:uploads",
    )
    db_session.commit()
    loaded = get_data_source(
        db_session, workspace_id=workspace.id, data_source_id=source.id
    )
    assert loaded.source_type == "upload"
    assert "password" not in loaded.configuration
    with pytest.raises(DataSourceConfigurationError, match="secret"):
        create_data_source(
            db_session,
            workspace_id=workspace.id,
            name="DB",
            source_type="database",
            provider="postgres",
            created_by=actor.id,
            configuration={"host": "db.internal", "password": "nopenope"},
        )


def test_ingestion_run_lifecycle(db_session):
    workspace = _workspace(db_session, "ingest")
    actor = _user(db_session, workspace_id=workspace.id)
    project = create_project(
        db_session,
        actor=actor,
        workspace_id=workspace.id,
        name="Case",
        slug="case",
    )
    source = create_data_source(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        name="Files",
        source_type="upload",
        provider="local",
        created_by=actor.id,
    )
    run = start_ingestion_run(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        data_source_id=source.id,
    )
    assert run.status == "running"
    complete_ingestion_run(
        db_session,
        run,
        rows_read=10,
        rows_written=10,
        bytes_read=128,
        schema_digest="a" * 64,
        content_digest="b" * 64,
    )
    assert run.status == "completed"
    assert run.completed_at is not None
    with pytest.raises(IdentityError, match="already finished"):
        fail_ingestion_run(
            db_session, run, error_code="x", error_summary="nope"
        )

    failed = start_ingestion_run(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        data_source_id=source.id,
    )
    fail_ingestion_run(
        db_session, failed, error_code="read_error", error_summary="could not parse"
    )
    assert failed.status == "failed"
    assert failed.error_code == "read_error"


def test_csv_labs_upload_produces_canonical_lineage(
    auth_client, db_session, monkeypatch
):
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None
    )
    payload = b"channel,spend\nemail,40\n"
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Marketing"},
        files={"file": ("campaign.csv", payload, "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    upload = db_session.get(ClientLabUpload, body["id"])
    assert upload is not None
    assert Path(upload.stored_path).is_file()
    assert Path(upload.stored_path).read_bytes() == payload
    assert upload.artifact_id is not None
    assert upload.data_source_id is not None
    assert upload.ingestion_run_id is not None
    assert upload.dataset_id is not None

    stored = Path(upload.stored_path).resolve()
    repo_store = Path(__file__).resolve().parents[3] / "data" / "object_store"
    assert not stored.is_relative_to(repo_store.resolve())

    artifact = db_session.get(Artifact, upload.artifact_id)
    source = db_session.get(DataSource, upload.data_source_id)
    ingestion = db_session.get(IngestionRun, upload.ingestion_run_id)
    dataset = db_session.get(Dataset, upload.dataset_id)
    asset = db_session.get(DatasetAsset, dataset.dataset_asset_id)
    project = db_session.get(Project, dataset.project_id)
    assert artifact is not None
    assert artifact.content_digest == _sha256(payload)
    assert artifact.object_key in upload.stored_path
    assert source.source_type == "upload"
    assert "password" not in (source.configuration or {})
    assert ingestion.status == "completed"
    assert ingestion.content_digest == artifact.content_digest
    assert project is not None
    assert project.slug == LABS_PROJECT_SLUG
    assert asset.project_id == project.id
    assert dataset.project_id == project.id
    assert dataset.artifact_id == artifact.id
    assert dataset.ingestion_run_id == ingestion.id
    columns = (
        db_session.query(DatasetColumn)
        .filter(DatasetColumn.dataset_id == dataset.id)
        .order_by(DatasetColumn.ordinal_position)
        .all()
    )
    assert [column.name for column in columns] == ["channel", "spend"]
    assert dataset.schema_digest


def test_dataset_asset_version_uniqueness_preserved(db_session, tmp_path):
    workspace = _workspace(db_session, "versions")
    actor = _user(db_session, workspace_id=workspace.id)
    env = seed_dogfood(db_session)
    first_path = tmp_path / "v1.csv"
    first_path.write_text("feature,target\n1,0\n", encoding="utf-8")
    second_path = tmp_path / "v1-again.csv"
    second_path.write_text("feature,target\n9,1\n", encoding="utf-8")
    asset = create_dataset_asset(
        db_session,
        workspace_id=workspace.id,
        name="Leads",
        slug="leads",
        actor=actor,
    )
    ingest_dataset(
        db_session,
        environment=env,
        name="Leads",
        location=str(first_path),
        version="v1",
        workspace_id=workspace.id,
        dataset_asset=asset,
    )
    with pytest.raises(IntegrityError):
        ingest_dataset(
            db_session,
            environment=env,
            name="Leads",
            location=str(second_path),
            version="v1",
            workspace_id=workspace.id,
            dataset_asset=asset,
        )
    db_session.rollback()


def test_s3_adapter_contract_mocked():
    payload = b"hello-s3"
    digest = _sha256(payload)
    objects: dict[str, dict] = {}

    class _NotFound(Exception):
        response = {"Error": {"Code": "NoSuchKey"}}

    client = MagicMock()

    def put_object(*, Bucket, Key, Body, Metadata=None, ContentType=None):
        objects[Key] = {
            "Body": Body,
            "Metadata": Metadata or {},
            "ContentType": ContentType,
            "ContentLength": len(Body),
        }

    def get_object(*, Bucket, Key):
        if Key not in objects:
            raise _NotFound()
        body = MagicMock()
        body.read.return_value = objects[Key]["Body"]
        return {"Body": body}

    def head_object(*, Bucket, Key):
        if Key not in objects:
            raise _NotFound()
        item = objects[Key]
        return {
            "Metadata": item["Metadata"],
            "ContentType": item["ContentType"],
            "ContentLength": item["ContentLength"],
        }

    def delete_object(*, Bucket, Key):
        objects.pop(Key, None)

    client.put_object.side_effect = put_object
    client.get_object.side_effect = get_object
    client.head_object.side_effect = head_object
    client.delete_object.side_effect = delete_object
    client.generate_presigned_url.return_value = "https://s3.test/signed"
    storage = S3Storage(bucket="lab-bucket", client=client)
    result = storage.put("models/a.bin", payload, content_type="application/octet-stream")
    assert result.content_digest == digest
    assert storage.get("models/a.bin") == payload
    assert storage.exists("models/a.bin") is True
    assert storage.checksum("models/a.bin") == digest
    assert storage.metadata("models/a.bin").bucket == "lab-bucket"
    assert storage.signed_url("models/a.bin") == "https://s3.test/signed"
    storage.delete("models/a.bin")
    assert storage.exists("models/a.bin") is False


def test_gcs_adapter_contract_mocked():
    payload = b"hello-gcs"
    digest = _sha256(payload)
    blob = MagicMock()
    blob.exists.return_value = True
    blob.download_as_bytes.return_value = payload
    blob.metadata = {}
    blob.size = len(payload)
    blob.content_type = "text/plain"
    blob.generate_signed_url.return_value = "https://gcs.test/signed"
    client = MagicMock()
    client.bucket.return_value.blob.return_value = blob
    storage = GCSStorage(bucket="lab-bucket", client=client)
    result = storage.put("reports/a.txt", payload, content_type="text/plain")
    blob.upload_from_string.assert_called()
    assert result.content_digest == digest
    blob.metadata = {"sha256": digest}
    assert storage.get("reports/a.txt") == payload
    assert storage.exists("reports/a.txt") is True
    assert storage.checksum("reports/a.txt") == digest
    assert storage.signed_url("reports/a.txt") == "https://gcs.test/signed"
    storage.delete("reports/a.txt")
    blob.delete.assert_called()


def test_core_app_does_not_import_cloud_sdks():
    root = Path(__file__).resolve().parents[1] / "app"
    allowed = {
        str((root / "storage" / "s3.py").resolve()),
        str((root / "storage" / "gcs.py").resolve()),
    }
    offenders = []
    for path in root.rglob("*.py"):
        resolved = str(path.resolve())
        if resolved in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if "import boto3" in text or "from boto3" in text or "google.cloud.storage" in text:
            offenders.append(resolved)
    assert offenders == []


def test_cloud_sdks_are_optional_install_extras():
    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    base, _, extras = text.partition("[project.optional-dependencies]")
    assert "boto3" not in base
    assert "google-cloud-storage" not in base
    assert 'storage-s3 = ["boto3' in extras
    assert 'storage-gcs = ["google-cloud-storage' in extras
    assert "storage = [" in extras


def test_s3_missing_sdk_mentions_install_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)
    storage = S3Storage(bucket="lab-bucket")
    with pytest.raises(ObjectStorageError) as err:
        storage._client()
    assert S3_SDK_INSTALL in str(err.value)


def test_gcs_missing_sdk_mentions_install_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "google.cloud.storage", None)
    storage = GCSStorage(bucket="lab-bucket")
    with pytest.raises(ObjectStorageError) as err:
        storage._client()
    assert GCS_SDK_INSTALL in str(err.value)


def test_pipeline_run_artifact_lookup_is_indexed_and_tenant_safe(db_session, tmp_path):
    source = inspect.getsource(artifacts_for_pipeline_run)
    assert "list_artifacts" not in source
    assert "extra_metadata" not in source

    setup = make_lineage_setup(db_session, tmp_path)
    alpha_run = create_workflow_run(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    alpha_pipeline = create_pipeline_run(
        db_session,
        workflow_run=alpha_run,
        environment=setup["env"],
        dataset=setup["alpha_dataset"],
        task=setup["task"],
    )
    beta_run = create_workflow_run(
        db_session,
        workspace_id=setup["beta"].id,
        workflow=setup["beta_workflow"],
        requester=setup["beta_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    beta_pipeline = create_pipeline_run(
        db_session,
        workflow_run=beta_run,
        environment=setup["env"],
        dataset=setup["beta_dataset"],
        task=setup["task"],
    )
    storage = LocalStorage(root=tmp_path)
    attached = store_artifact(
        db_session,
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        pipeline_run_id=alpha_pipeline.id,
        artifact_type="report",
        filename="report.md",
        data=b"# run",
        storage=storage,
        extra_metadata={"pipeline_run_id": str(alpha_pipeline.id), "role": "technical_report"},
    )
    other = store_artifact(
        db_session,
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        artifact_type="dataset",
        filename="unrelated.csv",
        data=b"a,b\n1,2\n",
        storage=storage,
    )
    db_session.commit()

    found = artifacts_for_pipeline_run(db_session, alpha_pipeline)
    assert [row.id for row in found] == [attached.id]
    assert attached.pipeline_run_id == alpha_pipeline.id
    assert attached.extra_metadata["pipeline_run_id"] == str(alpha_pipeline.id)
    assert other.pipeline_run_id is None
    assert artifacts_for_pipeline_run(db_session, beta_pipeline) == []

    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    indexes = {
        row[0]
        for row in db_session.execute(
            text(
                """
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'artifacts'
                  AND indexname IN (
                    'ix_artifacts_pipeline_run_id',
                    'ix_artifacts_workspace_pipeline_run_id'
                  )
                """
            )
        )
    }
    assert indexes == {
        "ix_artifacts_pipeline_run_id",
        "ix_artifacts_workspace_pipeline_run_id",
    }
    plan = "\n".join(
        row[0]
        for row in db_session.execute(
            text(
                """
                EXPLAIN SELECT id FROM artifacts
                WHERE pipeline_run_id = :pipeline_run_id
                """
            ),
            {"pipeline_run_id": alpha_pipeline.id},
        )
    )
    assert "ix_artifacts_pipeline_run_id" in plan
    assert "Seq Scan" not in plan

    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE artifacts SET pipeline_run_id = :pipeline_run_id WHERE id = :id"),
            {"pipeline_run_id": beta_pipeline.id, "id": attached.id},
        )
        db_session.commit()
    db_session.rollback()

    json_only = store_artifact(
        db_session,
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        artifact_type="result_json",
        filename="result.json",
        data=b"{}",
        storage=storage,
        extra_metadata={"pipeline_run_id": str(alpha_pipeline.id)},
    )
    db_session.commit()
    assert json_only.pipeline_run_id is None
    db_session.execute(
        text(
            """
            UPDATE artifacts AS artifact
            SET pipeline_run_id = experiment.id
            FROM experiments AS experiment
            WHERE artifact.pipeline_run_id IS NULL
              AND artifact.workspace_id = experiment.workspace_id
              AND artifact.metadata->>'pipeline_run_id' = experiment.id::text
            """
        )
    )
    db_session.commit()
    db_session.refresh(json_only)
    assert json_only.pipeline_run_id == alpha_pipeline.id
    assert {row.id for row in artifacts_for_pipeline_run(db_session, alpha_pipeline)} == {
        attached.id,
        json_only.id,
    }
