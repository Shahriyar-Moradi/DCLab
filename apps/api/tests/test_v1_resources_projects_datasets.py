"""P3.1-B1: POST /v1/projects, /v1/projects/{id}/problem-specs and /v1/datasets.

Required Idempotency-Key bound in ``idempotency_keys`` with the resource, tenant
isolation, ML-write authorization, and the Labs ingestion path for uploads.
"""

from __future__ import annotations

import threading
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.models import (
    ClientLabUpload,
    DataAccessEvent,
    Dataset,
    DatasetColumn,
    ExecutionRequest,
    IdempotencyKey,
    IngestionRun,
    MlJob,
    ProblemSpec,
    Project,
    User,
    WorkspaceRole,
)
from app.services import idempotency_service
from app.services.auth_service import create_access_token
from app.services.workspace_service import add_workspace_member
from test_data_model_lineage import make_lineage_setup
from test_v1_contract_conventions import _assert_envelope, _headers

CSV = b"tenure,spend,churn\n1,10.5,yes\n2,20.0,no\n3,7.25,yes\n"


@pytest.fixture()
def setup(db_session, tmp_path):
    return make_lineage_setup(db_session, tmp_path)


def _key() -> str:
    return f"p31b1-{uuid4().hex}"


def _h(setup, user=None, key: str | None = None, **extra) -> dict[str, str]:
    headers = _headers(user or setup["alpha_admin"], setup["alpha"].id, **extra)
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _member(db_session, setup, role: WorkspaceRole) -> User:
    membership = add_workspace_member(
        db_session,
        actor=setup["alpha_admin"],
        workspace_id=setup["alpha"].id,
        email=f"p31b1-{role.value}-{uuid4().hex}@test.invalid",
        password="test-password",
        role=role.value,
    )
    db_session.commit()
    return db_session.get(User, membership.user_id)


def _count(db_session, model) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model)) or 0


def _upload(client, setup, *, key=None, project_id=None, name="churn.csv", body=CSV, mime="text/csv", user=None):
    return client.post(
        "/v1/datasets",
        headers=_h(setup, user, key=key if key is not None else _key()),
        data={"project_id": str(project_id or setup["alpha_project"].id)},
        files={"file": (name, body, mime)},
    )


def _snapshot(db_session) -> tuple[int, int]:
    """(datasets, stored objects): uploads must add both or neither."""

    root = get_settings().object_storage_root
    objects = sum(1 for path in root.rglob("*") if path.is_file()) if root.exists() else 0
    return _count(db_session, Dataset), objects


# --- POST /v1/projects -------------------------------------------------------------------


def test_create_project_returns_201_etag_location_and_replays(client, db_session, setup):
    key = _key()
    payload = {"name": "Churn study", "slug": "churn-study", "description": "Q4"}
    created = client.post("/v1/projects", json=payload, headers=_h(setup, key=key))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["slug"] == "churn-study" and body["workspace_id"] == str(setup["alpha"].id)
    assert created.headers["Location"] == f"/v1/projects/{body['id']}"
    assert "Idempotent-Replayed" not in created.headers
    read = client.get(f"/v1/projects/{body['id']}", headers=_h(setup))
    assert read.headers["ETag"] == created.headers["ETag"]
    before = _count(db_session, Project)

    replay = client.post("/v1/projects", json=payload, headers=_h(setup, key=key))
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == body and replay.headers["ETag"] == created.headers["ETag"]
    assert _count(db_session, Project) == before

    conflict = client.post("/v1/projects", json={**payload, "name": "Other"}, headers=_h(setup, key=key))
    assert _assert_envelope(conflict, 409, "idempotency_key_conflict")["retryable"] is False
    assert _count(db_session, Project) == before

    # A taken slug under a new key is suffixed, never reused.
    again = client.post("/v1/projects", json=payload, headers=_h(setup, key=_key()))
    assert again.status_code == 201 and again.json()["slug"].startswith("churn-study-")
    stored = db_session.scalar(select(IdempotencyKey).where(IdempotencyKey.idempotency_key == key))
    assert (stored.principal_kind, stored.principal_id, stored.resource_kind) == (
        "user", setup["alpha_admin"].id, "project"
    )
    assert stored.operation == "POST /v1/projects" and stored.response_status == 201


def test_create_project_key_rules_and_validation(client, db_session, setup):
    payload = {"name": "Keyless"}
    _assert_envelope(client.post("/v1/projects", json=payload, headers=_h(setup)), 400, "idempotency_key_required")
    _assert_envelope(
        client.post("/v1/projects", json=payload, headers=_h(setup, key="bad key!")), 400, "invalid_idempotency_key"
    )
    _assert_envelope(
        client.post("/v1/projects", json=payload, headers=_h(setup, key="legacy_labs_upload:x")),
        400,
        "invalid_idempotency_key",
    )
    invalid = client.post("/v1/projects", json={"name": ""}, headers=_h(setup, key=_key()))
    error = _assert_envelope(invalid, 422, "validation_failed")
    assert error["details"]["errors"][0]["loc"] == ["body", "name"]
    assert _count(db_session, IdempotencyKey) == 0


def test_create_project_authz_and_principal_scoped_keys(client, db_session, setup, auth_client):
    viewer = _member(db_session, setup, WorkspaceRole.VIEWER)
    denied = client.post("/v1/projects", json={"name": "x"}, headers=_h(setup, viewer, key=_key()))
    _assert_envelope(denied, 403, "forbidden")
    foreign = client.post(
        "/v1/projects",
        json={"name": "x"},
        headers={**_h(setup, key=_key()), "X-Workspace-Id": str(setup["beta"].id)},
    )
    _assert_envelope(foreign, 403, "forbidden")
    # Client users write /v1 like POST /v1/execution-requests does.
    assert auth_client.post("/v1/projects", json={"name": "Client"}, headers={"Idempotency-Key": _key()}).status_code == 201

    # Keys are scoped per principal: another engineer's identical key is independent.
    engineer = _member(db_session, setup, WorkspaceRole.ML_ENGINEER)
    key = _key()
    mine = client.post("/v1/projects", json={"name": "Shared"}, headers=_h(setup, key=key))
    theirs = client.post("/v1/projects", json={"name": "Shared"}, headers=_h(setup, engineer, key=key))
    assert mine.status_code == theirs.status_code == 201
    assert mine.json()["id"] != theirs.json()["id"] and "Idempotent-Replayed" not in theirs.headers


def test_idempotency_key_rows_are_immutable(client, db_session, setup):
    created = client.post("/v1/projects", json={"name": "Frozen"}, headers=_h(setup, key=_key()))
    row_id = db_session.scalar(select(IdempotencyKey.id).where(IdempotencyKey.resource_id == UUID(created.json()["id"])))
    with pytest.raises(DBAPIError, match="immutable"):
        db_session.execute(
            IdempotencyKey.__table__.update().where(IdempotencyKey.id == row_id).values(response_status=200)
        )
    db_session.rollback()


def test_concurrent_duplicate_project_creates_one_and_replays(test_engine, db_session, setup):
    """Two simultaneous requests, own sessions: one project, both 201, same id."""

    from app.db.session import get_db
    from app.main import app

    factory = sessionmaker(bind=test_engine)

    def _fresh_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    key, payload = _key(), {"name": "Race", "slug": "race"}
    headers = _h(setup, key=key)
    barrier, results = threading.Barrier(2), []

    def _post() -> None:
        with TestClient(app) as racer:
            barrier.wait()
            results.append(racer.post("/v1/projects", json=payload, headers=headers))

    app.dependency_overrides[get_db] = _fresh_session
    try:
        threads = [threading.Thread(target=_post) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        app.dependency_overrides.clear()
    assert [r.status_code for r in results] == [201, 201], [r.text for r in results]
    assert results[0].json()["id"] == results[1].json()["id"]
    assert sorted(r.headers.get("Idempotent-Replayed", "") for r in results) == ["", "true"]
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Project).where(Project.slug.like("race%"))) == 1


def test_lost_key_race_rolls_back_and_replays_the_winner(client, db_session, setup, monkeypatch):
    """The loser inserts its key after the winner committed: unique violation → replay."""

    key, payload = _key(), {"name": "Loser"}
    winner = client.post("/v1/projects", json=payload, headers=_h(setup, key=key))
    before = _count(db_session, Project)
    real, calls = idempotency_service.find_bound, []

    def _first_miss(db, scope, binding):
        calls.append(1)
        return None if len(calls) == 1 else real(db, scope, binding)

    monkeypatch.setattr(idempotency_service, "find_bound", _first_miss)
    loser = client.post("/v1/projects", json=payload, headers=_h(setup, key=key))
    assert loser.status_code == 201 and loser.headers["Idempotent-Replayed"] == "true"
    assert loser.json()["id"] == winner.json()["id"] and len(calls) == 2
    assert _count(db_session, Project) == before


def test_lost_key_race_with_a_different_request_is_409(client, db_session, setup, monkeypatch):
    key = _key()
    client.post("/v1/projects", json={"name": "Winner"}, headers=_h(setup, key=key))
    before = _count(db_session, Project)
    real, calls = idempotency_service.find_bound, []

    def _first_miss(db, scope, binding):
        calls.append(1)
        return None if len(calls) == 1 else real(db, scope, binding)

    monkeypatch.setattr(idempotency_service, "find_bound", _first_miss)
    loser = client.post("/v1/projects", json={"name": "Different"}, headers=_h(setup, key=key))
    _assert_envelope(loser, 409, "idempotency_key_conflict")
    assert _count(db_session, Project) == before


# --- POST /v1/projects/{id}/problem-specs --------------------------------------------------------


def _spec_path(project) -> str:
    return f"/v1/projects/{project.id}/problem-specs"


SPEC = {"task_type": "binary", "business_objective": "Reduce churn", "target_column": "churn"}


def test_create_problem_spec_versions_replay_and_conflict(client, db_session, setup):
    path, key = _spec_path(setup["alpha_project"]), _key()
    first = client.post(path, json=SPEC, headers=_h(setup, key=key))
    assert first.status_code == 201, first.text
    body = first.json()
    assert (body["version"], body["status"], body["locked_at"]) == (1, "draft", None)
    assert first.headers["ETag"]

    replay = client.post(path, json=SPEC, headers=_h(setup, key=key))
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == body and _count(db_session, ProblemSpec) == 1

    _assert_envelope(
        client.post(path, json={**SPEC, "business_objective": "Other"}, headers=_h(setup, key=key)),
        409,
        "idempotency_key_conflict",
    )
    # The same key on another project's path is a different request.
    other = client.post("/v1/projects", json={"name": "Second"}, headers=_h(setup, key=_key())).json()
    _assert_envelope(
        client.post(f"/v1/projects/{other['id']}/problem-specs", json=SPEC, headers=_h(setup, key=key)),
        409,
        "idempotency_key_conflict",
    )
    locked = client.post(path, json={**SPEC, "status": "locked"}, headers=_h(setup, key=_key()))
    assert locked.status_code == 201 and locked.json()["version"] == 2 and locked.json()["locked_at"]
    _assert_envelope(client.post(path, json=SPEC, headers=_h(setup)), 400, "idempotency_key_required")


def test_create_problem_spec_validation_isolation_and_authz(client, db_session, setup):
    path = _spec_path(setup["alpha_project"])
    bad_metric = client.post(path, json={**SPEC, "primary_metric": "rmse"}, headers=_h(setup, key=_key()))
    _assert_envelope(bad_metric, 422, "validation_failed")
    _assert_envelope(client.post(path, json={"task_type": "x"}, headers=_h(setup, key=_key())), 422, "validation_failed")
    oversized = {**SPEC, "constraints": {"note": "x" * 20_000}}
    _assert_envelope(client.post(path, json=oversized, headers=_h(setup, key=_key())), 422, "validation_failed")
    _assert_envelope(
        client.post(path, json={**SPEC, "status": "archived"}, headers=_h(setup, key=_key())), 400, "bad_request"
    )
    # The other tenant's project is indistinguishable from a missing one.
    for project_id in (setup["beta_project"].id, uuid4()):
        hidden = client.post(f"/v1/projects/{project_id}/problem-specs", json=SPEC, headers=_h(setup, key=_key()))
        _assert_envelope(hidden, 404, "not_found")
    viewer = _member(db_session, setup, WorkspaceRole.VIEWER)
    _assert_envelope(client.post(path, json=SPEC, headers=_h(setup, viewer, key=_key())), 403, "forbidden")
    assert _count(db_session, ProblemSpec) == 0 and _count(db_session, IdempotencyKey) == 0


# --- POST /v1/datasets ---------------------------------------------------------------------------


def test_upload_ingests_and_publishes_through_the_labs_path_without_training(client, db_session, setup):
    jobs, requests, uploads = (_count(db_session, model) for model in (MlJob, ExecutionRequest, ClientLabUpload))
    datasets, objects = _snapshot(db_session)
    key = _key()
    created = _upload(client, setup, key=key)
    assert created.status_code == 201, created.text
    body = created.json()
    assert created.headers["Location"] == f"/v1/datasets/{body['id']}"
    assert body["project_id"] == str(setup["alpha_project"].id)
    assert (body["row_count"], body["column_count"], body["source_type"]) == (3, 3, "csv")
    assert body["ingestion"]["status"] == "completed"
    assert body["ingestion"]["publication_state"] == "published"

    dataset = db_session.get(Dataset, UUID(body["id"]))
    run = db_session.get(IngestionRun, dataset.ingestion_run_id)
    assert run.execution_request_id is None and run.publication_state == "published"
    columns = db_session.scalars(select(DatasetColumn).where(DatasetColumn.dataset_id == dataset.id)).all()
    assert {c.llm_exposure_policy for c in columns} == {"deny"} and len(columns) == 3
    assert db_session.scalar(select(DataAccessEvent).where(DataAccessEvent.ingestion_run_id == run.id))
    # Ingestion only: training is POST /v1/experiments (B2).
    assert (_count(db_session, MlJob), _count(db_session, ExecutionRequest), _count(db_session, ClientLabUpload)) == (
        jobs, requests, uploads,
    )
    assert client.get(f"/v1/datasets/{body['id']}", headers=_h(setup)).status_code == 200

    after = _snapshot(db_session)
    assert after == (datasets + 1, objects + 1)
    replay = _upload(client, setup, key=key)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == body and _snapshot(db_session) == after
    changed = _upload(client, setup, key=key, body=CSV + b"4,1.0,no\n")
    _assert_envelope(changed, 409, "idempotency_key_conflict")
    assert _snapshot(db_session) == after


@pytest.mark.parametrize(
    ("name", "content", "mime"),
    [
        ("tool.exe", b"MZ\x90\x00", "application/octet-stream"),
        ("report.csv", b"%PDF-1.7 not a table", "text/csv"),
        ("data.csv", CSV, "application/json"),
        ("old.xls", b"\xd0\xcf\x11\xe0", "application/vnd.ms-excel"),
    ],
)
def test_structurally_invalid_upload_is_rejected_and_nothing_is_kept(client, db_session, setup, name, content, mime):
    before = _snapshot(db_session)
    rejected = _upload(client, setup, name=name, body=content, mime=mime)
    _assert_envelope(rejected, 422, "upload_rejected")
    assert _snapshot(db_session) == before and _count(db_session, IdempotencyKey) == 0


def test_upload_limits_form_validation_and_key(client, db_session, setup, monkeypatch):
    before = _snapshot(db_session)
    monkeypatch.setattr(get_settings(), "v1_dataset_upload_max_bytes", 16)
    _assert_envelope(_upload(client, setup), 413, "payload_too_large")
    monkeypatch.undo()
    missing_file = client.post(
        "/v1/datasets", headers=_h(setup, key=_key()), data={"project_id": str(setup["alpha_project"].id)},
        files={"other": ("x.txt", b"x", "text/plain")},
    )
    assert _assert_envelope(missing_file, 422, "validation_failed")["details"]["errors"][0]["loc"] == ["body", "file"]
    bad_project = client.post(
        "/v1/datasets", headers=_h(setup, key=_key()), data={"project_id": "nope"},
        files={"file": ("a.csv", CSV, "text/csv")},
    )
    _assert_envelope(bad_project, 422, "validation_failed")
    keyless = client.post(
        "/v1/datasets", headers=_h(setup), data={"project_id": str(setup["alpha_project"].id)},
        files={"file": ("a.csv", CSV, "text/csv")},
    )
    _assert_envelope(keyless, 400, "idempotency_key_required")
    assert _snapshot(db_session) == before


def test_upload_content_length_is_checked_before_the_body_is_read(client, db_session, setup, monkeypatch):
    before = _snapshot(db_session)
    monkeypatch.setattr(get_settings(), "v1_dataset_upload_max_bytes", 0)
    big = b"a,b\n" + b"1,2\n" * 20_000  # > 64 KiB multipart allowance
    _assert_envelope(_upload(client, setup, body=big), 413, "payload_too_large")
    monkeypatch.undo()

    def _chunks():
        yield b"--x\r\nContent-Disposition: form-data; name=\"project_id\"\r\n\r\n"
        yield str(setup["alpha_project"].id).encode() + b"\r\n--x--\r\n"

    chunked = client.post(
        "/v1/datasets",
        headers={**_h(setup, key=_key()), "Content-Type": "multipart/form-data; boundary=x"},
        content=_chunks(),
    )
    _assert_envelope(chunked, 411, "length_required")
    two_files = client.post(
        "/v1/datasets", headers=_h(setup, key=_key()), data={"project_id": str(setup["alpha_project"].id)},
        files=[("file", ("a.csv", CSV, "text/csv")), ("file", ("b.csv", CSV, "text/csv"))],
    )
    _assert_envelope(two_files, 400, "bad_request")
    assert _snapshot(db_session) == before


def test_upload_tenant_isolation_and_authz(client, db_session, setup):
    before = _snapshot(db_session)
    for project_id in (setup["beta_project"].id, uuid4()):
        _assert_envelope(_upload(client, setup, project_id=project_id), 404, "not_found")
    viewer = _member(db_session, setup, WorkspaceRole.VIEWER)
    _assert_envelope(_upload(client, setup, user=viewer), 403, "forbidden")
    foreign = client.post(
        "/v1/datasets",
        headers={**_h(setup, key=_key()), "X-Workspace-Id": str(setup["beta"].id)},
        data={"project_id": str(setup["beta_project"].id)},
        files={"file": ("a.csv", CSV, "text/csv")},
    )
    _assert_envelope(foreign, 403, "forbidden")
    assert _snapshot(db_session) == before
    # An ML engineer may upload; a key is not shared across principals.
    engineer = _member(db_session, setup, WorkspaceRole.ML_ENGINEER)
    assert _upload(client, setup, user=engineer).status_code == 201


def test_lost_dataset_key_race_discards_the_loser_and_its_object(client, db_session, setup, monkeypatch):
    key = _key()
    winner = _upload(client, setup, key=key)
    after = _snapshot(db_session)
    real, calls = idempotency_service.find_bound, []

    def _first_miss(db, scope, binding):
        calls.append(1)
        return None if len(calls) == 1 else real(db, scope, binding)

    monkeypatch.setattr(idempotency_service, "find_bound", _first_miss)
    loser = _upload(client, setup, key=key)
    assert loser.status_code == 201 and loser.headers["Idempotent-Replayed"] == "true"
    assert loser.json()["id"] == winner.json()["id"]
    assert _snapshot(db_session) == after


def test_bearer_token_for_principal_kind_user(client, db_session, setup):
    token = create_access_token(setup["alpha_admin"])
    response = client.post(
        "/v1/projects",
        json={"name": "Bearer"},
        headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": str(setup["alpha"].id), "Idempotency-Key": _key()},
    )
    assert response.status_code == 201


def test_sdk_runs_project_spec_and_upload_against_the_app(client, db_session, setup, tmp_path):
    from dclab_client import DCLabClient, IdempotencyConflictError

    api = DCLabClient(
        base_url=str(client.base_url),
        token=create_access_token(setup["alpha_admin"]),
        workspace_id=setup["alpha"].id,
        http=client,
    )
    project = api.projects.create(name="SDK loop", idempotency_key="sdk-project")
    assert api.projects.create(name="SDK loop", idempotency_key="sdk-project").idempotent_replay
    assert project.etag == api.projects.get(project.id).etag
    spec = api.projects.create_problem_spec(project.id, **SPEC)
    assert spec.version == 1 and spec.project_id == project.id
    data = tmp_path / "loop.csv"
    data.write_bytes(CSV)
    upload = api.datasets.upload(project.id, data, content_type="text/csv", idempotency_key="sdk-data")
    assert upload.project_id == project.id and upload.ingestion.publication_state == "published"
    assert api.datasets.upload(project.id, data, content_type="text/csv", idempotency_key="sdk-data").id == upload.id
    with pytest.raises(IdempotencyConflictError):
        api.projects.create(name="Different", idempotency_key="sdk-project")
