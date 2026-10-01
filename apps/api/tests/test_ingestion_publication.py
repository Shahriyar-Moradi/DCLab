from __future__ import annotations

import zipfile
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import DatasetColumn, IngestionPublicationEvent, IngestionRun, UserRole, Workspace
from app.domain.errors import IdentityError
from app.services.artifact_service import store_artifact
from app.services.artifact_service import read_artifact_bytes
from app.services.auth_service import create_user
from app.services.data_source_service import create_data_source
from app.services.ingestion_run_service import complete_ingestion_run, start_ingestion_run, transition_publication
from app.services.lab_service import ingest_dataset, seed_dogfood
from app.services.dataset_materialization import materialize_dataset
from app.services.dataset_column_service import publish_dataset_policy_defaults, set_dataset_column_policy
from app.services.project_service import create_project
from app.services.reproducibility_service import signed_url_for_artifact
from app.storage.local import LocalStorage
from app.services.upload_structure_scan import inspect_upload_structure
from app.engine.lab.open_ingest import OpenIngestError


def _setup(db, tmp_path):
    workspace = Workspace(slug=f"publish-{uuid4().hex[:10]}", name="Publish")
    db.add(workspace)
    db.flush()
    actor = create_user(
        db, email=f"publish-{uuid4().hex}@test.invalid", password="test-password",
        role=UserRole.WORKSPACE_OWNER, full_name="Publisher", workspace_id=workspace.id,
    )
    project = create_project(db, actor=actor, workspace_id=workspace.id, name="P", slug="p")
    source = create_data_source(
        db, workspace_id=workspace.id, project_id=project.id, name="Upload",
        source_type="upload", provider="local", created_by=actor.id,
    )
    artifact = store_artifact(
        db, workspace_id=workspace.id, project_id=project.id, artifact_type="dataset",
        filename="data.csv", data=b"x,y\n1,2\n", created_by=actor.id,
        storage=LocalStorage(root=tmp_path),
    )
    run = start_ingestion_run(
        db, workspace_id=workspace.id, project_id=project.id,
        data_source_id=source.id, artifact_id=artifact.id,
    )
    return workspace, actor, artifact, run


def _advance(db, workspace, actor, artifact, run, state):
    return transition_publication(
        db, workspace_id=workspace.id, ingestion_run_id=run.id,
        to_state=state, content_digest=artifact.content_digest,
        reason_code=f"test_{state}", actor_type="user", actor_user_id=actor.id,
    )


def test_publication_transitions_are_ordered_audited_and_idempotent(db_session, tmp_path):
    workspace, actor, artifact, run = _setup(db_session, tmp_path)
    with pytest.raises(IdentityError) as exc:
        _advance(db_session, workspace, actor, artifact, run, "scanned")
    assert exc.value.status_code == 409
    _advance(db_session, workspace, actor, artifact, run, "quarantined")
    first_version = run.publication_version
    _advance(db_session, workspace, actor, artifact, run, "quarantined")
    assert run.publication_version == first_version
    _advance(db_session, workspace, actor, artifact, run, "scanned")
    _advance(db_session, workspace, actor, artifact, run, "classified")
    with pytest.raises(IdentityError) as exc:
        _advance(db_session, workspace, actor, artifact, run, "publishable")
    assert exc.value.status_code == 409  # no dataset/policy, never an implicit approval
    events = list(db_session.scalars(
        select(IngestionPublicationEvent).where(IngestionPublicationEvent.ingestion_run_id == run.id)
        .order_by(IngestionPublicationEvent.version)
    ))
    assert [(e.from_state, e.to_state, e.version) for e in events] == [
        ("received", "quarantined", 1), ("quarantined", "scanned", 2),
        ("scanned", "classified", 3),
    ]
    assert all(e.content_digest == artifact.content_digest and e.policy_schema_version == 1 for e in events)
    db_session.commit()
    with pytest.raises(DBAPIError):
        db_session.execute(text("UPDATE ingestion_publication_events SET reason_code='tamper' WHERE id=:id"), {"id": events[0].id})
    db_session.rollback()


def test_publication_rejects_digest_conflict_foreign_run_and_terminal_replay(db_session, tmp_path):
    workspace, actor, artifact, run = _setup(db_session, tmp_path)
    with pytest.raises(IdentityError) as exc:
        transition_publication(
            db_session, workspace_id=uuid4(), ingestion_run_id=run.id,
            to_state="quarantined", content_digest=artifact.content_digest,
            reason_code="test_quarantined", actor_type="user", actor_user_id=actor.id,
        )
    assert exc.value.status_code == 404
    with pytest.raises(IdentityError) as exc:
        transition_publication(
            db_session, workspace_id=workspace.id, ingestion_run_id=run.id,
            to_state="quarantined", content_digest="0" * 64,
            reason_code="test_quarantined", actor_type="user", actor_user_id=actor.id,
        )
    assert exc.value.status_code == 409
    _advance(db_session, workspace, actor, artifact, run, "rejected")
    with pytest.raises(IdentityError) as exc:
        _advance(db_session, workspace, actor, artifact, run, "quarantined")
    assert exc.value.status_code == 409
    assert db_session.get(IngestionRun, run.id).publication_state == "rejected"


def test_unpublished_dataset_cannot_be_downloaded_or_signed(db_session, tmp_path, monkeypatch):
    workspace, actor, artifact, run = _setup(db_session, tmp_path)
    monkeypatch.setattr("app.services.artifact_service.publication_enforced", lambda _settings: True)
    monkeypatch.setattr("app.config.publication_enforced", lambda _settings: True)
    with pytest.raises(IdentityError) as exc:
        read_artifact_bytes(
            db_session, workspace_id=workspace.id, artifact_id=artifact.id,
            storage=LocalStorage(root=tmp_path),
        )
    assert exc.value.status_code == 409
    with pytest.raises(IdentityError) as exc:
        signed_url_for_artifact(db_session, actor, artifact_id=artifact.id, workspace_id=workspace.id)
    assert exc.value.status_code == 409


def test_production_upload_path_refuses_before_writing(db_session, monkeypatch):
    from app.services.client_lab_upload_service import save_upload

    monkeypatch.setattr("app.services.client_lab_upload_service.is_production_env", lambda _settings: True)
    with pytest.raises(IdentityError) as exc:
        save_upload(
            db_session, user=None, category="Marketing", filename="data.csv",
            data=b"x,y\n1,2\n", workspace_id=uuid4(),
        )
    assert exc.value.status_code == 503


def test_direct_upload_api_denies_when_production_scanner_is_absent(auth_client, monkeypatch):
    monkeypatch.setattr("app.services.client_lab_upload_service.is_production_env", lambda _settings: True)
    response = auth_client.post(
        "/app/labs/uploads", data={"category": "Marketing"},
        files={"file": ("data.csv", b"x,y\n1,2\n", "text/csv")},
    )
    assert response.status_code == 503
    assert "safety review" in response.text


def test_unverified_upload_projection_hides_preview_and_dataset(auth_client, monkeypatch):
    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)
    uploaded = auth_client.post(
        "/app/labs/uploads", data={"category": "Marketing"},
        files={"file": ("data.csv", b"secret,target\nprivate,1\n", "text/csv")},
    )
    assert uploaded.status_code == 200
    monkeypatch.setattr("app.services.client_lab_upload_service.publication_enforced", lambda _settings: True)
    result = auth_client.get(f"/app/labs/uploads/{uploaded.json()['id']}")
    assert result.status_code == 200
    body = result.json()
    assert body["dataset_id"] is None
    assert body["fields_noticed"] == []
    assert body["record_count"] == 0
    assert body["insights"] == [] and body["outcome"] is None


def test_legacy_csv_import_api_is_not_a_production_bypass(auth_client, monkeypatch):
    monkeypatch.setattr("app.api.opportunities.is_production_env", lambda _settings: True)
    response = auth_client.post(
        "/app/opportunities/upload", files={"file": ("opp.csv", b"id,amount\n1,5\n", "text/csv")},
    )
    assert response.status_code == 503


def test_unverified_legacy_dataset_cannot_materialize_when_enforced(db_session, tmp_path, monkeypatch):
    path = tmp_path / "legacy.csv"
    path.write_bytes(b"x,y\n1,2\n")
    dataset = ingest_dataset(
        db_session, environment=seed_dogfood(db_session), name="Legacy",
        location=str(path),
    )
    monkeypatch.setattr("app.services.dataset_materialization.publication_enforced", lambda _settings: True)
    with pytest.raises(IdentityError) as exc:
        with materialize_dataset(dataset, db=db_session):
            pass
    assert exc.value.status_code == 409


def test_explicit_publication_disable_is_invalid_in_production():
    from app.config import Settings, validate_runtime_settings

    with pytest.raises(RuntimeError, match="DATASET_PUBLICATION_ENFORCED"):
        validate_runtime_settings(Settings(dclab_env="production", dataset_publication_enforced=False))


def test_production_cannot_forge_a_structural_scan_as_malware_attestation(db_session, tmp_path, monkeypatch):
    workspace, actor, artifact, run = _setup(db_session, tmp_path)
    _advance(db_session, workspace, actor, artifact, run, "quarantined")
    monkeypatch.setattr("app.services.ingestion_run_service.is_production_env", lambda _settings: True)
    with pytest.raises(IdentityError) as exc:
        _advance(db_session, workspace, actor, artifact, run, "scanned")
    assert exc.value.status_code == 503
    assert run.publication_state == "quarantined"


@pytest.mark.parametrize("filename,mime,payload", [
    ("../escape.csv", "text/csv", b"x,y\n1,2\n"),
    ("data.csv", "image/png", b"x,y\n1,2\n"),
    ("data.csv", "text/csv", b"MZ" + b"\0" * 20),
    ("data.parquet", "application/octet-stream", b"not parquet"),
])
def test_structural_scan_rejects_malicious_filename_mime_or_magic(tmp_path, filename, mime, payload):
    path = tmp_path / "candidate"
    path.write_bytes(payload)
    with pytest.raises(OpenIngestError):
        inspect_upload_structure(filename, path, declared_mime=mime)


def test_structural_scan_rejects_unsafe_excel_archive_member(tmp_path):
    path = tmp_path / "candidate.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../escape.txt", "unsafe")
    with pytest.raises(OpenIngestError, match="unsafe entries"):
        inspect_upload_structure(
            "candidate.xlsx", path,
            declared_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def test_structural_scan_accepts_normal_csv(tmp_path):
    path = tmp_path / "candidate.csv"
    path.write_bytes(b"x,y\n1,2\n")
    inspect_upload_structure("candidate.csv", path, declared_mime="text/csv")


def test_explicit_policy_publication_and_changed_object_denial(db_session, tmp_path, monkeypatch):
    workspace, actor, artifact, run = _setup(db_session, tmp_path)
    payload = b"x,y\n1,2\n"
    path = tmp_path / "source.csv"
    path.write_bytes(payload)
    dataset = ingest_dataset(
        db_session, environment=seed_dogfood(db_session), name="Classified",
        location=str(path), workspace_id=workspace.id,
        project_id=run.project_id, artifact_id=artifact.id, ingestion_run_id=run.id,
    )
    publish_dataset_policy_defaults(
        db_session, actor=actor, workspace_id=workspace.id, dataset_id=dataset.id,
        expected_revision=0, sensitivity_class="restricted", llm_exposure_policy="deny",
        retention_class="standard", residency_class="home_region_only",
        classification_source="manual", classification_confidence=1.0,
    )
    columns = list(db_session.scalars(
        select(DatasetColumn).where(DatasetColumn.dataset_id == dataset.id)
    ))
    for column in columns:
        set_dataset_column_policy(
            db_session, workspace_id=workspace.id, column_id=column.id,
            sensitivity_class="restricted", classification_source="manual",
            llm_exposure_policy="deny", retention_class="standard",
            residency_class="home_region_only", classification_confidence=1.0,
        )
    for state in ("quarantined", "scanned", "classified"):
        _advance(db_session, workspace, actor, artifact, run, state)
    complete_ingestion_run(
        db_session, run, rows_read=1, rows_written=1,
        bytes_read=len(payload), content_digest=artifact.content_digest,
        schema_digest=dataset.schema_digest,
    )
    _advance(db_session, workspace, actor, artifact, run, "publishable")
    _advance(db_session, workspace, actor, artifact, run, "published")
    db_session.commit()
    monkeypatch.setattr("app.services.artifact_service.publication_enforced", lambda _settings: True)
    storage = LocalStorage(root=tmp_path)
    assert read_artifact_bytes(
        db_session, workspace_id=workspace.id, artifact_id=artifact.id, storage=storage,
    ) == payload
    storage.put(artifact.object_key, b"x,y\n9,9\n", content_type="text/csv")
    with pytest.raises(IdentityError) as exc:
        read_artifact_bytes(
            db_session, workspace_id=workspace.id, artifact_id=artifact.id, storage=storage,
        )
    assert exc.value.status_code == 409
