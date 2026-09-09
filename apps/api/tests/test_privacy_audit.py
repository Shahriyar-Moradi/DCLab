"""Column policy metadata and append-only data_access_events. Not a classifier."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    ClientLabUpload,
    DataAccessEvent,
    DatasetColumn,
    IngestionRun,
    UserRole,
    Workspace,
)
from app.domain.errors import DataAccessEventSpecError, IdentityError
from app.domain.privacy_audit import (
    ACTOR_USER,
    EVENT_COMPLETED,
    OPERATION_COPY,
    PURPOSE_INGEST,
    SUMMARY_MAX_BYTES,
)
from app.services.auth_service import create_user
from app.services.data_access_event_service import (
    append_data_access_event,
    bound_column_summary,
    bound_resource_summary,
)
from app.services.data_access_service import create_data_access
from app.services.data_source_service import create_data_source
from app.services.dataset_column_service import set_dataset_column_policy
from app.services.ingestion_run_service import start_ingestion_run
from app.services.project_service import create_project
from app.translation.banned_terms import find_banned_terms


def _user(db, *, workspace_id, prefix: str = "audit"):
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


def _access_path(db, workspace, actor, prefix: str):
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
    access = create_data_access(
        db,
        workspace_id=workspace.id,
        project_id=project.id,
        data_source_id=source.id,
        name=f"{prefix} access",
        access_type="upload",
        provider="local",
        execution_mode="copy",
        created_by=actor.id,
    )
    run = start_ingestion_run(
        db,
        workspace_id=workspace.id,
        project_id=project.id,
        data_source_id=source.id,
        data_access_id=access.id,
    )
    return project, source, access, run


def test_bound_summaries_reject_raw_rows_and_credentials():
    with pytest.raises(DataAccessEventSpecError, match="rows"):
        bound_column_summary({"rows": [{"a": 1}]})
    with pytest.raises(DataAccessEventSpecError, match="password"):
        bound_resource_summary({"password": "n"})
    with pytest.raises(DataAccessEventSpecError, match="unsupported"):
        bound_resource_summary({"connection_string": "postgres://x"})


def test_labs_upload_leaves_columns_unclassified_and_records_copy_event(
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
    assert "data_access_event_id" not in body
    assert find_banned_terms(response.text) == []
    upload = db_session.get(ClientLabUpload, UUID(body["id"]))
    columns = (
        db_session.query(DatasetColumn)
        .filter(DatasetColumn.dataset_id == upload.dataset_id)
        .order_by(DatasetColumn.ordinal_position)
        .all()
    )
    assert [column.name for column in columns] == ["tenure", "churn"]
    assert all(column.sensitivity_class is None for column in columns)
    assert all(column.classification_source is None for column in columns)
    assert all(column.model_use_policy is None for column in columns)
    assert all(column.llm_exposure_policy is None for column in columns)

    tenure = columns[0]
    labeled = set_dataset_column_policy(
        db_session,
        workspace_id=upload.workspace_id,
        column_id=tenure.id,
        sensitivity_class="identifier",
        classification_source="manual",
        model_use_policy="deny",
        llm_exposure_policy="deny",
    )
    db_session.commit()
    db_session.refresh(labeled)
    assert labeled.sensitivity_class == "identifier"
    assert labeled.classification_source == "manual"

    ingestion = db_session.get(IngestionRun, upload.ingestion_run_id)
    event = db_session.scalar(
        select(DataAccessEvent).where(DataAccessEvent.ingestion_run_id == ingestion.id)
    )
    assert event is not None
    assert event.actor_type == ACTOR_USER
    assert event.purpose == PURPOSE_INGEST
    assert event.operation == OPERATION_COPY
    assert event.status == EVENT_COMPLETED
    assert event.data_access_id == ingestion.data_access_id
    assert event.execution_request_id == ingestion.execution_request_id
    assert "rows" not in event.resource_summary
    assert "password" not in event.resource_summary
    assert event.column_summary["column_names"] == ["tenure", "churn"]
    assert event.rows_read == 2

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "UPDATE dataset_columns SET sensitivity_class = 'secret' WHERE id = :id"
            ),
            {"id": tenure.id},
        )
        db_session.commit()
    db_session.rollback()


def test_postgres_rejects_raw_rows_and_oversized_event_summaries(db_session):
    workspace = _workspace(db_session, "ck")
    actor = _user(db_session, workspace_id=workspace.id)
    _project, _source, access, run = _access_path(db_session, workspace, actor, "ck")
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO data_access_events (
                    id, workspace_id, data_access_id, actor_type, purpose, operation,
                    resource_summary, status, started_at, completed_at
                ) VALUES (
                    gen_random_uuid(), :workspace, :access, 'user', 'ingest', 'copy',
                    '{"rows":[]}'::jsonb, 'completed', now(), now()
                )
                """
            ),
            {"workspace": workspace.id, "access": access.id},
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO data_access_events (
                    id, workspace_id, data_access_id, ingestion_run_id, actor_type,
                    purpose, operation, column_summary, status, started_at, completed_at
                ) VALUES (
                    gen_random_uuid(), :workspace, :access, :run, 'user', 'ingest',
                    'copy', CAST(:summary AS jsonb), 'completed', now(), now()
                )
                """
            ),
            {
                "workspace": workspace.id,
                "access": access.id,
                "run": run.id,
                "summary": '{"column_names":["%s"]}' % ("x" * (SUMMARY_MAX_BYTES + 8)),
            },
        )
        db_session.commit()
    db_session.rollback()


def test_append_only_blocks_update_and_delete(db_session):
    workspace = _workspace(db_session, "append")
    actor = _user(db_session, workspace_id=workspace.id)
    _project, _source, access, run = _access_path(db_session, workspace, actor, "append")
    event = append_data_access_event(
        db_session,
        workspace_id=workspace.id,
        data_access_id=access.id,
        ingestion_run_id=run.id,
        actor_type=ACTOR_USER,
        actor_user_id=actor.id,
        purpose=PURPOSE_INGEST,
        operation=OPERATION_COPY,
        resource_summary={"filename": "a.csv"},
        column_summary={"column_names": ["a"], "column_count": 1},
        rows_read=1,
        bytes_read=4,
    )
    db_session.commit()
    with pytest.raises(Exception, match="append-only"):
        event.status = "failed"
        db_session.commit()
    db_session.rollback()
    with pytest.raises(Exception, match="append-only"):
        db_session.execute(
            text("UPDATE data_access_events SET status = 'failed' WHERE id = :id"),
            {"id": event.id},
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises(Exception, match="append-only"):
        db_session.execute(
            text("DELETE FROM data_access_events WHERE id = :id"),
            {"id": event.id},
        )
        db_session.commit()
    db_session.rollback()


def test_event_rejects_cross_tenant_data_access(db_session):
    alpha = _workspace(db_session, "alpha")
    beta = _workspace(db_session, "beta")
    alpha_actor = _user(db_session, workspace_id=alpha.id, prefix="alpha")
    beta_actor = _user(db_session, workspace_id=beta.id, prefix="beta")
    _ap, _as, _alpha_access, alpha_run = _access_path(
        db_session, alpha, alpha_actor, "alpha"
    )
    _bp, _bs, beta_access, _br = _access_path(db_session, beta, beta_actor, "beta")
    db_session.commit()
    with pytest.raises(IdentityError, match="workspace"):
        append_data_access_event(
            db_session,
            workspace_id=alpha.id,
            data_access_id=beta_access.id,
            ingestion_run_id=alpha_run.id,
            actor_type=ACTOR_USER,
            purpose=PURPOSE_INGEST,
            operation=OPERATION_COPY,
        )
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO data_access_events (
                    id, workspace_id, data_access_id, actor_type, purpose, operation,
                    status, started_at, completed_at
                ) VALUES (
                    gen_random_uuid(), :workspace, :access, 'user', 'ingest', 'copy',
                    'completed', now(), now()
                )
                """
            ),
            {"workspace": alpha.id, "access": beta_access.id},
        )
        db_session.commit()
    db_session.rollback()
