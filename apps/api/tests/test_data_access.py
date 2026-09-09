"""DataAccess is the executable path to a DataSource. Secrets stay out of PostgreSQL."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    ClientLabUpload,
    DataAccess,
    DataSource,
    IngestionRun,
    User,
    UserRole,
    Workspace,
)
from app.domain.data_access import (
    ACCESS_TYPE_UPLOAD,
    EXECUTION_MODE_COPY,
    EXECUTION_MODE_QUERY,
    LOCATOR_MAX_BYTES,
)
from app.domain.errors import DataAccessConfigurationError, IdentityError
from app.services.auth_service import create_user
from app.services.data_access_service import (
    assert_opaque_credential_reference,
    create_data_access,
)
from app.services.data_source_service import create_data_source
from app.services.ingestion_run_service import start_ingestion_run
from app.services.project_service import create_project
from app.translation.banned_terms import find_banned_terms


def _user(db, *, workspace_id, prefix: str = "access"):
    return create_user(
        db,
        email=f"{prefix}-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name=prefix,
        workspace_id=workspace_id,
    )


def _workspace(db, name: str) -> Workspace:
    row = Workspace(slug=f"{name}-{uuid4().hex[:10]}", name=name)
    db.add(row)
    db.flush()
    return row


def _source_and_project(db, workspace, actor, prefix: str):
    project = create_project(
        db,
        actor=actor,
        workspace_id=workspace.id,
        name=f"{prefix} case",
        slug=f"{prefix}-case",
    )
    source = create_data_source(
        db,
        workspace_id=workspace.id,
        project_id=project.id,
        name=f"{prefix} files",
        source_type="upload",
        provider="local",
        created_by=actor.id,
    )
    return project, source


def test_assert_opaque_credential_reference_rejects_raw_secrets():
    assert assert_opaque_credential_reference(None) is None
    assert assert_opaque_credential_reference("vault:uploads") == "vault:uploads"
    assert (
        assert_opaque_credential_reference("sm:projects/x/secrets/y")
        == "sm:projects/x/secrets/y"
    )
    with pytest.raises(DataAccessConfigurationError, match="opaque"):
        assert_opaque_credential_reference("postgres://user:pass@db/app")
    with pytest.raises(DataAccessConfigurationError, match="opaque"):
        assert_opaque_credential_reference("password=super-secret")
    with pytest.raises(DataAccessConfigurationError, match="opaque"):
        assert_opaque_credential_reference("-----BEGIN PRIVATE KEY-----")


def test_create_data_access_rejects_locator_secrets_and_upload_non_copy(db_session):
    workspace = _workspace(db_session, "locator")
    actor = _user(db_session, workspace_id=workspace.id)
    project, source = _source_and_project(db_session, workspace, actor, "locator")
    create_data_access(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        data_source_id=source.id,
        name="Uploads",
        access_type=ACCESS_TYPE_UPLOAD,
        provider="local",
        execution_mode=EXECUTION_MODE_COPY,
        created_by=actor.id,
        resource_locator={"object_key": "ws/file.csv"},
        credential_reference="vault:uploads",
    )
    db_session.commit()
    with pytest.raises(DataAccessConfigurationError, match="secret"):
        create_data_access(
            db_session,
            workspace_id=workspace.id,
            project_id=project.id,
            data_source_id=source.id,
            name="Bad",
            access_type=ACCESS_TYPE_UPLOAD,
            provider="local",
            execution_mode=EXECUTION_MODE_COPY,
            created_by=actor.id,
            resource_locator={"object_key": "ws/file.csv", "password": "nopenope"},
        )
    with pytest.raises(DataAccessConfigurationError, match="copy"):
        create_data_access(
            db_session,
            workspace_id=workspace.id,
            project_id=project.id,
            data_source_id=source.id,
            name="Query upload",
            access_type=ACCESS_TYPE_UPLOAD,
            provider="local",
            execution_mode=EXECUTION_MODE_QUERY,
            created_by=actor.id,
        )


def test_postgres_rejects_secret_locator_and_raw_credential(db_session):
    workspace = _workspace(db_session, "sql-access")
    actor = _user(db_session, workspace_id=workspace.id)
    _project, source = _source_and_project(db_session, workspace, actor, "sql")
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO data_accesses (
                    id, workspace_id, data_source_id, name, access_type, provider,
                    execution_mode, resource_locator, created_by
                ) VALUES (
                    gen_random_uuid(), :workspace, :source, 'bad', 'upload', 'local',
                    'copy', '{"password":"n"}'::jsonb, :actor
                )
                """
            ),
            {"workspace": workspace.id, "source": source.id, "actor": actor.id},
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO data_accesses (
                    id, workspace_id, data_source_id, name, access_type, provider,
                    execution_mode, credential_reference, created_by
                ) VALUES (
                    gen_random_uuid(), :workspace, :source, 'conn', 'upload', 'local',
                    'copy', 'postgres://user:pass@db/app', :actor
                )
                """
            ),
            {"workspace": workspace.id, "source": source.id, "actor": actor.id},
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO data_accesses (
                    id, workspace_id, data_source_id, name, access_type, provider,
                    execution_mode, created_by
                ) VALUES (
                    gen_random_uuid(), :workspace, :source, 'mode', 'upload', 'local',
                    'query', :actor
                )
                """
            ),
            {"workspace": workspace.id, "source": source.id, "actor": actor.id},
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO data_accesses (
                    id, workspace_id, data_source_id, name, access_type, provider,
                    execution_mode, resource_locator, created_by
                ) VALUES (
                    gen_random_uuid(), :workspace, :source, 'huge', 'upload', 'local',
                    'copy', CAST(:locator AS jsonb), :actor
                )
                """
            ),
            {
                "workspace": workspace.id,
                "source": source.id,
                "actor": actor.id,
                "locator": '{"object_key":"%s"}' % ("x" * (LOCATOR_MAX_BYTES + 8)),
            },
        )
        db_session.commit()
    db_session.rollback()


def test_ingestion_run_keeps_working_without_data_access(db_session):
    workspace = _workspace(db_session, "compat")
    actor = _user(db_session, workspace_id=workspace.id)
    project, source = _source_and_project(db_session, workspace, actor, "compat")
    run = start_ingestion_run(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        data_source_id=source.id,
    )
    assert run.data_access_id is None
    assert run.execution_request_id is None
    assert run.data_source_id == source.id


def test_ingestion_run_rejects_access_from_another_source_or_workspace(db_session):
    alpha = _workspace(db_session, "alpha")
    beta = _workspace(db_session, "beta")
    alpha_actor = _user(db_session, workspace_id=alpha.id, prefix="alpha")
    beta_actor = _user(db_session, workspace_id=beta.id, prefix="beta")
    alpha_project, alpha_source = _source_and_project(
        db_session, alpha, alpha_actor, "alpha"
    )
    beta_project, beta_source = _source_and_project(db_session, beta, beta_actor, "beta")
    spare_source = create_data_source(
        db_session,
        workspace_id=alpha.id,
        project_id=alpha_project.id,
        name="spare",
        source_type="upload",
        provider="local",
        created_by=alpha_actor.id,
    )
    alpha_access = create_data_access(
        db_session,
        workspace_id=alpha.id,
        project_id=alpha_project.id,
        data_source_id=alpha_source.id,
        name="alpha access",
        access_type=ACCESS_TYPE_UPLOAD,
        provider="local",
        execution_mode=EXECUTION_MODE_COPY,
        created_by=alpha_actor.id,
    )
    beta_access = create_data_access(
        db_session,
        workspace_id=beta.id,
        project_id=beta_project.id,
        data_source_id=beta_source.id,
        name="beta access",
        access_type=ACCESS_TYPE_UPLOAD,
        provider="local",
        execution_mode=EXECUTION_MODE_COPY,
        created_by=beta_actor.id,
    )
    db_session.commit()
    with pytest.raises(IdentityError, match="data source"):
        start_ingestion_run(
            db_session,
            workspace_id=alpha.id,
            project_id=alpha_project.id,
            data_source_id=spare_source.id,
            data_access_id=alpha_access.id,
        )
    with pytest.raises(IdentityError, match="workspace"):
        start_ingestion_run(
            db_session,
            workspace_id=alpha.id,
            project_id=alpha_project.id,
            data_source_id=alpha_source.id,
            data_access_id=beta_access.id,
        )
    run = start_ingestion_run(
        db_session,
        workspace_id=alpha.id,
        project_id=alpha_project.id,
        data_source_id=alpha_source.id,
        data_access_id=alpha_access.id,
    )
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE ingestion_runs SET data_access_id = :access WHERE id = :id"),
            {"access": beta_access.id, "id": run.id},
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO data_accesses (
                    id, workspace_id, data_source_id, name, access_type, provider,
                    execution_mode, created_by
                ) VALUES (
                    gen_random_uuid(), :workspace, :source, 'cross', 'upload', 'local',
                    'copy', :actor
                )
                """
            ),
            {
                "workspace": alpha.id,
                "source": beta_source.id,
                "actor": alpha_actor.id,
            },
        )
        db_session.commit()
    db_session.rollback()


def test_deleting_data_access_nulls_ingestion_pointer_only(db_session):
    workspace = _workspace(db_session, "setnull")
    actor = _user(db_session, workspace_id=workspace.id)
    project, source = _source_and_project(db_session, workspace, actor, "setnull")
    access = create_data_access(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        data_source_id=source.id,
        name="copy path",
        access_type=ACCESS_TYPE_UPLOAD,
        provider="local",
        execution_mode=EXECUTION_MODE_COPY,
        created_by=actor.id,
    )
    run = start_ingestion_run(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        data_source_id=source.id,
        data_access_id=access.id,
    )
    db_session.commit()
    access_id = access.id
    run_id = run.id
    source_id = source.id
    workspace_id = workspace.id
    db_session.execute(text("DELETE FROM data_accesses WHERE id = :id"), {"id": access_id})
    db_session.commit()
    leftover = db_session.execute(
        text(
            "SELECT workspace_id, data_source_id, data_access_id "
            "FROM ingestion_runs WHERE id = :id"
        ),
        {"id": run_id},
    ).one()
    assert leftover == (workspace_id, source_id, None)


def test_labs_upload_creates_copy_access_and_links_ingestion_without_client_ids(
    auth_client, db_session, monkeypatch
):
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None
    )
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": ("customers.csv", b"tenure,churn\n1,Yes\n2,No\n", "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "data_access_id" not in body
    assert "execution_request_id" not in body
    assert find_banned_terms(response.text) == []
    upload = db_session.get(ClientLabUpload, UUID(body["id"]))
    ingestion = db_session.get(IngestionRun, upload.ingestion_run_id)
    source = db_session.get(DataSource, upload.data_source_id)
    access = db_session.get(DataAccess, ingestion.data_access_id)
    assert ingestion.data_source_id == source.id
    assert access is not None
    assert access.data_source_id == source.id
    assert access.access_type == ACCESS_TYPE_UPLOAD
    assert access.execution_mode == EXECUTION_MODE_COPY
    assert access.credential_reference is None
    assert "password" not in access.resource_locator
    assert access.resource_locator["object_key"]
    assert ingestion.execution_request_id is not None
    request = db_session.execute(
        text("SELECT workspace_id FROM execution_requests WHERE id = :id"),
        {"id": ingestion.execution_request_id},
    ).one()
    assert request[0] == DEFAULT_WORKSPACE_ID
    actor = db_session.get(User, access.created_by)
    assert actor is not None
    again = db_session.scalar(
        select(DataAccess).where(DataAccess.data_source_id == source.id)
    )
    assert again.id == access.id
