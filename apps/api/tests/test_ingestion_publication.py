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


def _published_events(db, run_id):
    return list(db.scalars(
        select(IngestionPublicationEvent)
        .where(IngestionPublicationEvent.ingestion_run_id == run_id)
        .order_by(IngestionPublicationEvent.version)
    ))


def test_own_workspace_upload_is_published_for_internal_training(auth_client, db_session, monkeypatch):
    """ADR 0005: structural validation publishes an own-workspace upload."""
    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)
    monkeypatch.setattr("app.services.client_lab_upload_service.publication_enforced", lambda _settings: True)
    response = auth_client.post(
        "/app/labs/uploads", data={"category": "Marketing"},
        files={"file": ("data.csv", b"amount,region,target\n5,north,1\n7,south,0\n", "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dataset_id"] is not None and body["record_count"] == 2
    from app.db.models import ClientLabUpload, DatasetPolicyRevision
    upload = db_session.get(ClientLabUpload, body["id"])
    run = db_session.get(IngestionRun, upload.ingestion_run_id)
    assert run.publication_state == "published" and run.status == "completed"
    events = _published_events(db_session, run.id)
    assert [(e.to_state, e.reason_code) for e in events] == [
        ("quarantined", "upload_received"),
        ("scanned", "structural_validation_passed"),
        ("classified", "internal_training_policy_applied"),
        ("publishable", "internal_training_policy_complete"),
        ("published", "internal_training_published"),
    ]
    assert all(e.actor_type == "user" and e.actor_user_id == upload.requested_by for e in events)
    policy = db_session.scalar(
        select(DatasetPolicyRevision).where(DatasetPolicyRevision.dataset_id == upload.dataset_id)
    )
    assert (policy.sensitivity_class, policy.llm_exposure_policy, policy.classification_source) == (
        "restricted", "deny", "policy",
    )
    columns = list(db_session.scalars(select(DatasetColumn).where(DatasetColumn.dataset_id == upload.dataset_id)))
    assert columns and all(
        c.llm_exposure_policy == "deny" and c.model_use_policy == "allow" and c.classification_source == "policy"
        for c in columns
    )
    # The published projection is complete (not the redacted awaiting-review one).
    assert auth_client.get(f"/app/labs/uploads/{body['id']}").json()["dataset_id"] == body["dataset_id"]


def test_production_upload_is_published_under_internal_training_policy(auth_client, db_session, monkeypatch):
    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)
    monkeypatch.setattr("app.services.ingestion_run_service.is_production_env", lambda _settings: True)
    monkeypatch.setattr("app.services.client_lab_upload_service.publication_enforced", lambda _settings: True)
    response = auth_client.post(
        "/app/labs/uploads", data={"category": "Marketing"},
        files={"file": ("data.csv", b"x,y\n1,2\n3,4\n", "text/csv")},
    )
    assert response.status_code == 200, response.text
    from app.db.models import ClientLabUpload
    upload = db_session.get(ClientLabUpload, response.json()["id"])
    assert db_session.get(IngestionRun, upload.ingestion_run_id).publication_state == "published"


def test_structurally_invalid_upload_is_rejected_with_reason_and_not_retained(auth_client, db_session, monkeypatch):
    from app.db.models import Artifact
    before = db_session.scalar(select(text("count(*)")).select_from(Artifact.__table__))
    response = auth_client.post(
        "/app/labs/uploads", data={"category": "Marketing"},
        files={"file": ("data.csv", b"MZ" + b"\0" * 20, "text/csv")},
    )
    assert response.status_code == 422
    assert "does not match" in response.json()["detail"]
    after = db_session.scalar(select(text("count(*)")).select_from(Artifact.__table__))
    assert after == before


def test_internal_training_attestation_cannot_be_forged(db_session, tmp_path, monkeypatch):
    workspace, actor, artifact, run = _setup(db_session, tmp_path)
    _advance(db_session, workspace, actor, artifact, run, "quarantined")
    monkeypatch.setattr("app.services.ingestion_run_service.is_production_env", lambda _settings: True)
    # The policy reason without the attestation is still the generic path: 503.
    with pytest.raises(IdentityError) as exc:
        transition_publication(
            db_session, workspace_id=workspace.id, ingestion_run_id=run.id,
            to_state="scanned", content_digest=artifact.content_digest,
            reason_code="structural_validation_passed", actor_type="user", actor_user_id=actor.id,
        )
    assert exc.value.status_code == 503
    # The attestation with a mismatched reason or a system actor is rejected.
    for reason, actor_type, actor_id in (
        ("test_scanned", "user", actor.id),
        ("structural_validation_passed", "system", None),
    ):
        with pytest.raises(IdentityError) as exc:
            transition_publication(
                db_session, workspace_id=workspace.id, ingestion_run_id=run.id,
                to_state="scanned", content_digest=artifact.content_digest,
                reason_code=reason, actor_type=actor_type, actor_user_id=actor_id,
                attestation="structural_internal_training",
            )
        assert exc.value.status_code == 400
    assert run.publication_state == "quarantined"


def test_internal_training_attestation_rejects_other_strings_actors_and_users(db_session, tmp_path):
    workspace, actor, artifact, run = _setup(db_session, tmp_path)
    _advance(db_session, workspace, actor, artifact, run, "quarantined")
    other = create_user(
        db_session, email=f"other-{uuid4().hex}@test.invalid", password="test-password",
        role=UserRole.WORKSPACE_OWNER, full_name="Other", workspace_id=workspace.id,
    )
    cases = [
        ("forged_attestation", "user", actor.id, 400),
        ("structural_internal_training", "operator", actor.id, 400),
        # Same workspace writer, but not the uploader of this artifact.
        ("structural_internal_training", "user", other.id, 403),
    ]
    for attestation, actor_type, actor_id, status in cases:
        with pytest.raises(IdentityError) as exc:
            transition_publication(
                db_session, workspace_id=workspace.id, ingestion_run_id=run.id,
                to_state="scanned", content_digest=artifact.content_digest,
                reason_code="structural_validation_passed", actor_type=actor_type,
                actor_user_id=actor_id, attestation=attestation,
            )
        assert exc.value.status_code == status, (attestation, actor_type)
    assert run.publication_state == "quarantined"


def test_failed_publication_rolls_back_upload_and_object(auth_client, db_session, monkeypatch):
    from app.db.models import Artifact

    def boom(*_args, **_kwargs):
        raise IdentityError("dataset classification incomplete", status_code=409)

    deleted: list[str] = []
    monkeypatch.setattr("app.services.client_lab_upload_service.publish_upload_for_internal_training", boom)
    monkeypatch.setattr("app.storage.local.LocalStorage.delete", lambda self, key: deleted.append(key))
    before = db_session.scalar(select(text("count(*)")).select_from(Artifact.__table__))
    response = auth_client.post(
        "/app/labs/uploads", data={"category": "Marketing"},
        files={"file": ("data.csv", b"x,y\n1,2\n", "text/csv")},
    )
    assert response.status_code == 409
    assert db_session.scalar(select(text("count(*)")).select_from(Artifact.__table__)) == before
    assert len(deleted) == 1


def test_production_allows_ai_governed_by_switches_and_levels():
    # P6.9-A lifted the P6.2-B2 block: the gateway applies every dataset and column
    # llm_exposure_policy (ADR 0009 §8), kill switches and budgets, and the legacy decision
    # writers apply nothing above the ADR 0008 decision-point levels. The retired
    # DECISION_AGENT_ENABLED / PIPELINE_LLM_VERIFIER_ENABLED settings are ignored.
    from app.config import Settings, validate_runtime_settings

    secrets = {"jwt_secret": "j" * 40, "auth_token_hash_secret": "h" * 40, "auth_csrf_secret": "c" * 40}
    validate_runtime_settings(Settings(dclab_env="production", ai_enabled=True, **secrets))
    retired = Settings(dclab_env="production", decision_agent_enabled=True, pipeline_llm_verifier_enabled=True,
                       **secrets)
    validate_runtime_settings(retired)
    assert not hasattr(retired, "decision_agent_enabled") and not hasattr(retired, "pipeline_llm_verifier_enabled")


def test_unverified_upload_projection_hides_preview_and_dataset(auth_client, monkeypatch):
    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)
    uploaded = auth_client.post(
        "/app/labs/uploads", data={"category": "Marketing"},
        files={"file": ("data.csv", b"secret,target\nprivate,1\n", "text/csv")},
    )
    assert uploaded.status_code == 200
    # Make it a real legacy row: pre-ADR-0005 runs never recorded artifact_id,
    # so no publication can be found for the upload's artifact.
    from app.db.models import ClientLabUpload
    from app.db.session import get_session_factory
    with get_session_factory()() as db:
        upload = db.get(ClientLabUpload, uploaded.json()["id"])
        db.execute(text("UPDATE ingestion_runs SET artifact_id = NULL WHERE id = :id"), {"id": upload.ingestion_run_id})
        db.commit()
    monkeypatch.setattr("app.services.client_lab_upload_service.publication_enforced", lambda _settings: True)
    result = auth_client.get(f"/app/labs/uploads/{uploaded.json()['id']}")
    assert result.status_code == 200
    body = result.json()
    assert body["dataset_id"] is None
    assert body["fields_noticed"] == []
    assert body["record_count"] == 0
    assert body["insights"] == [] and body["outcome"] is None
    assert "quarantined" in body["message"] and "Upload it again" in body["message"]


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


def test_enforced_publication_upload_trains_end_to_end(auth_client, db_session, monkeypatch, tmp_path):
    """P1.3-A: with publication enforced (production), an own-workspace upload
    trains end to end; run outputs are published to object storage and the
    prepared table is a derived_dataset artifact, not a repo file."""
    import random

    from app.config import REPO_ROOT, get_settings
    from app.db.models import Artifact, ClientLabUpload, Dataset, Experiment
    from app.services.auto_train_service import run_auto_train_job

    enforced = lambda _settings: True  # noqa: E731
    for target in (
        "app.config.publication_enforced",
        "app.services.artifact_service.publication_enforced",
        "app.services.dataset_materialization.publication_enforced",
        "app.services.client_lab_upload_service.publication_enforced",
    ):
        monkeypatch.setattr(target, enforced)
    monkeypatch.setattr(get_settings(), "run_scratch_root", tmp_path)
    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)
    rng = random.Random(7)
    lines = ["age,income,region,defaulted"]
    for _ in range(160):
        income = rng.randint(20, 150)
        lines.append(f"{rng.randint(18, 70)},{income},{rng.choice('NSEW')},{int(income < 50 or rng.random() < 0.1)}")
    response = auth_client.post(
        "/app/labs/uploads", data={"category": "Sales", "target_column": "defaulted"},
        files={"file": ("loans.csv", ("\n".join(lines) + "\n").encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    upload_id = response.json()["id"]
    run_auto_train_job(db_session, upload_id)
    db_session.expire_all()
    upload = db_session.get(ClientLabUpload, upload_id)
    experiment = db_session.get(Experiment, upload.experiment_id)
    assert experiment.status == "COMPLETED", (experiment.status, experiment.failure_reason)
    types = set(db_session.scalars(
        select(Artifact.artifact_type).where(Artifact.pipeline_run_id == experiment.id)
    ))
    assert {"report", "result_json", "predictions"} <= types
    prepared = db_session.get(Dataset, experiment.dataset_id)
    assert db_session.get(Artifact, prepared.artifact_id).artifact_type == "derived_dataset"
    assert not (REPO_ROOT / "data" / "client_lab_datasets" / f"{upload_id}.csv").exists()
    assert str(tmp_path) in (experiment.artifact_dir or "")

    # The derived table inherits its source upload's gate and is never signable.
    from app.services.ingestion_run_service import require_published_artifact
    from app.services.reproducibility_service import read_run_file, signed_url_for_artifact
    from app.db.models import User

    uploader = db_session.get(User, upload.requested_by)
    prepared_artifact = db_session.get(Artifact, prepared.artifact_id)
    assert require_published_artifact(db_session, prepared_artifact) is not None
    with pytest.raises(IdentityError) as exc:
        signed_url_for_artifact(
            db_session, uploader,
            artifact_id=prepared_artifact.id, workspace_id=prepared_artifact.workspace_id,
        )
    assert exc.value.status_code == 409
    orphan = Artifact(
        workspace_id=prepared_artifact.workspace_id, artifact_type="derived_dataset",
        provider=prepared_artifact.provider, object_key=f"{prepared_artifact.object_key}.orphan",
        content_digest=prepared_artifact.content_digest, size_bytes=1,
        extra_metadata={"derived_from_upload_id": str(uuid4())},
    )
    db_session.add(orphan)
    db_session.flush()
    with pytest.raises(IdentityError) as exc:
        require_published_artifact(db_session, orphan)
    assert exc.value.status_code == 409
    db_session.rollback()

    # A stored run file whose object vanished degrades instead of raising.
    from app.storage.factory import storage_for_artifact

    report = db_session.scalar(
        select(Artifact).where(Artifact.pipeline_run_id == experiment.id, Artifact.artifact_type == "report")
    )
    storage_for_artifact(report).delete(report.object_key)
    # Falls back to the worker's local scratch copy while it exists ...
    assert read_run_file(db_session, experiment, "report.md")
    # ... and degrades to None (no 500) once that is gone too.
    from pathlib import Path

    (Path(experiment.artifact_dir) / "report.md").unlink()
    assert read_run_file(db_session, experiment, "report.md") is None
