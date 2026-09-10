"""Control-plane execution_requests: tenant FKs, bounded payload, Labs link."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import DEFAULT_WORKSPACE_ID, ClientLabUpload, ExecutionRequest, User
from app.domain.execution_requests import (
    OPERATION_MODEL_BUILD,
    REQUEST_ACCEPTED,
    REQUEST_SPEC_MAX_BYTES,
    SOURCE_LEGACY_LABS,
)
from app.services.execution_request_service import (
    ExecutionRequestSpecError,
    attach_legacy_labs_model_build_request,
    bound_request_spec,
    create_execution_request,
)
from app.translation.banned_terms import find_banned_terms


def test_bound_request_spec_rejects_rows_and_credentials():
    with pytest.raises(ExecutionRequestSpecError, match="rows"):
        bound_request_spec({"rows": [{"a": 1}]})
    with pytest.raises(ExecutionRequestSpecError, match="password"):
        bound_request_spec({"password": "secret"})
    with pytest.raises(ExecutionRequestSpecError, match="unsupported"):
        bound_request_spec({"model_pickle": "nope"})


def test_labs_upload_creates_linked_execution_request_without_changing_client_payload(
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
    assert "execution_request_id" not in body
    assert body["status"] == "queued"
    assert find_banned_terms(response.text) == []

    upload_id = body["id"]
    row = db_session.scalar(
        select(ExecutionRequest).where(
            ExecutionRequest.workspace_id == DEFAULT_WORKSPACE_ID,
            ExecutionRequest.idempotency_key == f"legacy_labs_upload:{upload_id}",
        )
    )
    assert row is not None
    assert row.operation == OPERATION_MODEL_BUILD
    assert row.source_surface == SOURCE_LEGACY_LABS
    assert row.status == REQUEST_ACCEPTED
    assert row.workflow_run_id is not None
    assert row.pipeline_run_id is not None
    assert str(row.pipeline_run_id) == body["pipeline_run_id"]
    assert row.request_spec["upload_id"] == upload_id
    assert row.request_spec["filename"] == "customers.csv"
    assert "rows" not in row.request_spec
    assert "password" not in row.request_spec
    upload = db_session.get(ClientLabUpload, UUID(upload_id))
    actor = db_session.get(User, row.requested_by_user_id)
    again = attach_legacy_labs_model_build_request(
        db_session,
        upload=upload,
        actor=actor,
        project_id=row.project_id,
        workflow_run_id=row.workflow_run_id,
        pipeline_run_id=row.pipeline_run_id,
    )
    assert again.id == row.id


def test_idempotency_key_is_unique_per_workspace_when_present(db_session):
    first = create_execution_request(
        db_session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        operation=OPERATION_MODEL_BUILD,
        source_surface=SOURCE_LEGACY_LABS,
        idempotency_key="once",
        request_spec={"filename": "a.csv"},
    )
    second = create_execution_request(
        db_session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        operation=OPERATION_MODEL_BUILD,
        source_surface=SOURCE_LEGACY_LABS,
        idempotency_key="once",
        request_spec={"filename": "b.csv"},
    )
    assert first.id == second.id
    create_execution_request(
        db_session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        operation=OPERATION_MODEL_BUILD,
        source_surface=SOURCE_LEGACY_LABS,
        request_spec={"filename": "c.csv"},
    )
    create_execution_request(
        db_session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        operation=OPERATION_MODEL_BUILD,
        source_surface=SOURCE_LEGACY_LABS,
        request_spec={"filename": "d.csv"},
    )
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO execution_requests (
                    id, workspace_id, operation, source_surface, status,
                    idempotency_key, request_spec
                ) VALUES (
                    gen_random_uuid(), :workspace, 'model_build', 'legacy_labs',
                    'accepted', 'once', '{}'::jsonb
                )
                """
            ),
            {"workspace": DEFAULT_WORKSPACE_ID},
        )
        db_session.commit()
    db_session.rollback()


def test_postgres_rejects_oversized_and_secret_request_spec(db_session):
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO execution_requests (
                    id, workspace_id, operation, source_surface, status, request_spec
                ) VALUES (
                    gen_random_uuid(), :workspace, 'model_build', 'api',
                    'accepted', CAST(:spec AS jsonb)
                )
                """
            ),
            {
                "workspace": DEFAULT_WORKSPACE_ID,
                "spec": '{"filename":"%s"}' % ("x" * (REQUEST_SPEC_MAX_BYTES + 8)),
            },
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO execution_requests (
                    id, workspace_id, operation, source_surface, status, request_spec
                ) VALUES (
                    gen_random_uuid(), :workspace, 'model_build', 'api',
                    'accepted', '{"password":"n"}'::jsonb
                )
                """
            ),
            {"workspace": DEFAULT_WORKSPACE_ID},
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO execution_requests (
                    id, workspace_id, operation, source_surface, status, request_spec
                ) VALUES (
                    gen_random_uuid(), :workspace, 'model_build', 'api',
                    'accepted', '{"rows":[]}'::jsonb
                )
                """
            ),
            {"workspace": DEFAULT_WORKSPACE_ID},
        )
        db_session.commit()
    db_session.rollback()


def test_concurrent_idempotency_resolves_to_one_canonical_request(db_session, test_engine):
    key = f"race-{uuid4().hex}"
    db_session.commit()
    SessionLocal = sessionmaker(bind=test_engine)
    barrier = threading.Barrier(2)

    def _create() -> str:
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            row = create_execution_request(
                session,
                workspace_id=DEFAULT_WORKSPACE_ID,
                operation=OPERATION_MODEL_BUILD,
                source_surface=SOURCE_LEGACY_LABS,
                idempotency_key=key,
                request_spec={"filename": "race.csv"},
            )
            session.commit()
            return str(row.id)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: _create(), range(2)))
    assert ids[0] == ids[1]
    db_session.expire_all()
    rows = list(
        db_session.scalars(
            select(ExecutionRequest).where(
                ExecutionRequest.workspace_id == DEFAULT_WORKSPACE_ID,
                ExecutionRequest.idempotency_key == key,
            )
        )
    )
    assert len(rows) == 1
    assert str(rows[0].id) == ids[0]


def test_unrelated_integrity_error_is_not_swallowed_as_idempotency(db_session):
    with pytest.raises(IntegrityError):
        create_execution_request(
            db_session,
            workspace_id=DEFAULT_WORKSPACE_ID,
            operation=OPERATION_MODEL_BUILD,
            source_surface=SOURCE_LEGACY_LABS,
            idempotency_key=f"fk-{uuid4().hex[:12]}",
            parent_request_id=uuid4(),
            request_spec={"filename": "x.csv"},
        )
