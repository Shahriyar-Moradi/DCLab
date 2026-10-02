"""P3.1-A /v1 contract conventions: error envelope, request ids, opaque cursors,
ETag / If-Match and Idempotency-Key, applied to every /v1 operation."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.v1_conventions import (
    V1APIError,
    check_if_match,
    if_match_version,
    representation_etag,
    version_etag,
)
from app.db.models import DEFAULT_WORKSPACE_ID, ExecutionRequest
from app.domain.execution_requests import OPERATION_MODEL_BUILD, SOURCE_LEGACY_LABS
from app.main import app
from app.services.auth_service import create_access_token
from app.services.cursor_codec import open_cursor, sign_cursor
from app.services.execution_request_service import create_execution_request
from app.services.graph_service import encode_cursor
from app.services.lineage_service import create_pipeline_run, create_workflow_run
from app.services.observability_service import append_ml_run_event
from app.services.project_service import create_project
from app.domain.errors import InvalidCursorError
from test_data_model_lineage import make_lineage_setup

ENVELOPE_KEYS = {"code", "message", "retryable", "request_id", "details"}
UUID_ZERO = "00000000-0000-0000-0000-000000000000"
WORKSPACE_FREE = {"/v1/me", "/v1/workspaces"}


def _operations() -> list[tuple[str, str]]:
    """Every /v1 (method, path template), from the router as FastAPI exposes it."""

    ops = [
        (method.upper(), path)
        for path, methods in app.openapi()["paths"].items()
        if path.startswith("/v1/")
        for method in methods
    ]
    assert ops, "the /v1 router must not be empty"
    return sorted(ops, key=lambda item: (item[1], item[0]))


def _concrete(path: str, fill: str = UUID_ZERO) -> str:
    path = path.replace("{kind}", "experiment").replace("{ref_kind}", "champion_model")
    return re.sub(r"\{[^}]+\}", fill, path)


def _call(client, method: str, path: str, **kwargs):
    if method == "POST":
        body = {}
        if "confirmation" in path:
            body = {"target_column": "y"}
        elif path.endswith("/problem-specs"):
            body = {"task_type": "binary", "business_objective": "x"}
        elif path.endswith("/branches"):
            body = {"intent": "x", "changes": [{"kind": "family_exclude", "family": "xgboost"}]}
        elif path.endswith("/decisions"):
            body = {"action": "propose", "decision_type": "experiment_accepted",
                    "subject": {"kind": "project"}, "rationale": "x"}
        elif path.endswith(("/accept", "/reject", "/supersede")):
            body = {"rationale": "x"}
        elif "/refs/" in path:
            body = {"target_id": UUID_ZERO, "rationale": "x", "evidence_refs": [{"kind": "experiment", "id": UUID_ZERO}]}
        kwargs.setdefault("json", body)
        # P3.1-B commands require a key; supply one so the resource checks answer.
        kwargs["headers"] = {"Idempotency-Key": f"conv-{uuid4().hex}", **(kwargs.get("headers") or {})}
    return client.request(method, path, **kwargs)


def _assert_envelope(response, status: int, code: str | None = None) -> dict:
    assert response.status_code == status, (response.request.url, response.status_code, response.text)
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == ENVELOPE_KEYS, error
    assert isinstance(error["retryable"], bool) and isinstance(error["details"], dict)
    assert re.fullmatch(r"[a-z][a-z0-9_.]*", error["code"]), error["code"]
    assert error["request_id"] and error["request_id"] == response.headers["X-Request-Id"]
    if code is not None:
        assert error["code"] == code, error
    return error


def _headers(user, workspace_id, **extra) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user)}",
        "X-Workspace-Id": str(workspace_id),
        **extra,
    }


# --- the envelope on every /v1 operation ----------------------------------------------------


def test_inventory_covers_every_current_v1_operation():
    ops = {f"{method} {path}" for method, path in _operations()}
    assert ops == {
        "GET /v1/me",
        "GET /v1/workspaces",
        "GET /v1/projects",
        "POST /v1/projects",
        "GET /v1/projects/{project_id}",
        "POST /v1/projects/{project_id}/problem-specs",
        "GET /v1/projects/{project_id}/graph",
        "GET /v1/projects/{project_id}/decisions",
        "GET /v1/nodes/{kind}/{node_id}/impact",
        "GET /v1/datasets",
        "POST /v1/datasets",
        "GET /v1/datasets/{dataset_id}",
        "POST /v1/execution-requests",
        "POST /v1/execution-requests/{request_id}/target-confirmation",
        "GET /v1/execution-requests/{request_id}",
        "GET /v1/model-builds/{pipeline_run_id}",
        "GET /v1/model-builds/{pipeline_run_id}/events",
        "GET /v1/model-builds/{pipeline_run_id}/visualizations",
        "GET /v1/model-builds/{pipeline_run_id}/artifacts",
        "GET /v1/experiments/{experiment_id}/code",
        "GET /v1/experiments",
        "POST /v1/experiments",
        "GET /v1/experiments/compare",
        "GET /v1/experiments/{experiment_id}",
        "POST /v1/experiments/{experiment_id}/branches",
        "POST /v1/experiments/{experiment_id}/cancel",
        "POST /v1/projects/{project_id}/decisions",
        "GET /v1/decisions/{decision_id}",
        "POST /v1/decisions/{decision_id}/accept",
        "POST /v1/decisions/{decision_id}/reject",
        "POST /v1/decisions/{decision_id}/supersede",
        "GET /v1/projects/{project_id}/refs",
        "GET /v1/projects/{project_id}/refs/{ref_kind}",
        "POST /v1/projects/{project_id}/refs/{ref_kind}",
        "GET /v1/model-versions/{model_version_id}",
    }


def test_openapi_documents_the_envelope_on_every_v1_operation():
    schema = app.openapi()
    assert set(schema["components"]["schemas"]["V1Error"]["required"]) == {
        "code", "message", "retryable", "request_id",
    }
    for path, methods in schema["paths"].items():
        if not path.startswith("/v1/"):
            continue
        for method, operation in methods.items():
            responses = operation["responses"]
            for status in ("400", "401", "403", "404", "422", "500"):
                ref = responses[status]["content"]["application/json"]["schema"]["$ref"]
                assert ref.endswith("/V1ErrorEnvelope"), (method, path, status)
            if method == "post":
                headers = {p["name"] for p in operation["parameters"] if p["in"] == "header"}
                assert "Idempotency-Key" in headers, path
                assert "409" in responses, path


def test_every_v1_operation_401_uses_envelope_with_request_id(client):
    for method, path in _operations():
        response = _call(client, method, _concrete(path))
        error = _assert_envelope(response, 401, "unauthenticated")
        assert error["retryable"] is False


def test_every_workspace_operation_400_without_selector_and_403_foreign(client, db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    bearer = {"Authorization": f"Bearer {create_access_token(setup['alpha_admin'])}"}
    for method, path in _operations():
        if path in WORKSPACE_FREE:
            continue
        missing = _call(client, method, _concrete(path), headers=bearer)
        assert "X-Workspace-Id" in _assert_envelope(missing, 400, "bad_request")["message"]
        foreign = _call(
            client, method, _concrete(path), headers={**bearer, "X-Workspace-Id": str(setup["beta"].id)}
        )
        _assert_envelope(foreign, 403, "forbidden")


def test_every_resource_operation_404_and_422_use_envelope(client, db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    headers = _headers(setup["alpha_admin"], setup["alpha"].id)
    for method, path in _operations():
        if "{" not in path:
            continue
        missing = _call(client, method, _concrete(path, str(uuid4())), headers=headers)
        _assert_envelope(missing, 404, "not_found")
        malformed = _call(client, method, _concrete(path, "not-a-uuid"), headers=headers)
        error = _assert_envelope(malformed, 422, "validation_failed")
        assert error["details"]["errors"] and all(
            set(item) == {"loc", "msg", "type"} for item in error["details"]["errors"]
        ), "validation errors never echo the caller's input"
    # The other tenant's resource is indistinguishable from a missing one.
    hidden = client.get(f"/v1/projects/{setup['beta_project'].id}", headers=headers)
    _assert_envelope(hidden, 404, "not_found")


def test_request_id_is_echoed_or_replaced_and_unknown_v1_paths_use_envelope(auth_client):
    echoed = auth_client.get("/v1/projects/not-a-uuid", headers={"X-Request-Id": "trace-p31a.1"})
    assert _assert_envelope(echoed, 422)["request_id"] == "trace-p31a.1"
    assert auth_client.get("/v1/me", headers={"X-Request-Id": "trace-ok"}).headers["X-Request-Id"] == "trace-ok"
    replaced = auth_client.get("/v1/projects/not-a-uuid", headers={"X-Request-Id": "bad id <script>"})
    rid = _assert_envelope(replaced, 422)["request_id"]
    assert rid != "bad id <script>" and UUID(rid)
    _assert_envelope(auth_client.get("/v1/no-such-resource"), 404, "not_found")
    _assert_envelope(auth_client.delete("/v1/projects"), 405, "method_not_allowed")


def test_non_v1_surfaces_keep_their_error_shape(client, auth_client):
    legacy = client.get("/app/labs/problems")
    assert legacy.status_code == 401 and "detail" in legacy.json() and "error" not in legacy.json()
    unknown = auth_client.get("/no-such-route")
    assert unknown.status_code == 404 and unknown.json() == {"detail": "Not Found"}


def test_csrf_failure_on_v1_uses_envelope(client):
    response = client.post(
        "/v1/execution-requests",
        json={},
        headers={"Cookie": "dclab_session=forged-session", "Origin": "https://evil.example"},
    )
    _assert_envelope(response, 403, "csrf_failed")


def test_unhandled_error_is_a_retryable_500_envelope_without_internals(client, monkeypatch, client_user):
    def boom(*_args, **_kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("app.api.v1.list_workspaces_for_actor", boom)
    with TestClient(app, raise_server_exceptions=False) as quiet:
        response = quiet.get(
            "/v1/workspaces", headers={"Authorization": f"Bearer {create_access_token(client_user)}"}
        )
    error = _assert_envelope(response, 500, "internal_error")
    assert error["retryable"] is True and "secret" not in response.text


# --- opaque cursors -------------------------------------------------------------------------


def _pipeline_with_events(db_session, setup, count: int = 3):
    workflow_run = create_workflow_run(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    pipeline = create_pipeline_run(
        db_session,
        workflow_run=workflow_run,
        environment=setup["env"],
        dataset=setup["alpha_dataset"],
        task=setup["task"],
        commit=False,
    )
    for index in range(count):
        append_ml_run_event(
            db_session,
            workspace_id=setup["alpha"].id,
            workflow_run_id=workflow_run.id,
            experiment_id=pipeline.id,
            stage="training",
            event_type=f"step_{index}",
            status="completed",
            commit=False,
        )
    return pipeline


def test_cursors_are_opaque_tamper_evident_and_scope_bound(client, db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    first = _pipeline_with_events(db_session, setup)
    second = _pipeline_with_events(db_session, setup)
    other_project = create_project(
        db_session, actor=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="Other"
    )
    db_session.commit()
    headers = _headers(setup["alpha_admin"], setup["alpha"].id)
    events = f"/v1/model-builds/{first.id}/events"

    page = client.get(events, headers=headers, params={"limit": 2}).json()
    cursor = page["next_cursor"]
    assert cursor.startswith("c1.") and "2" != cursor
    following = client.get(events, headers=headers, params={"cursor": cursor, "limit": 2})
    assert [row["sequence"] for row in following.json()["items"]] == [3]

    version, body, mac = cursor.split(".")
    forged_body = sign_cursor("events:x:y", [0]).split(".")[1]
    for tampered in (
        f"{version}.{forged_body}.{mac}",  # payload swapped, old MAC
        f"{version}.{body}.{mac[:-2]}AA",  # MAC altered
        f"c0.{body}.{mac}",  # unknown version
        "2",  # the pre-P3.1-A bare sequence
        "not-a-cursor",
    ):
        response = client.get(events, headers=headers, params={"cursor": tampered})
        _assert_envelope(response, 400, "invalid_cursor")

    # Cross-resource and cross-route reuse fail verification.
    _assert_envelope(
        client.get(f"/v1/model-builds/{second.id}/events", headers=headers, params={"cursor": cursor}),
        400,
        "invalid_cursor",
    )
    project_id = setup["alpha_project"].id
    for path in (f"/v1/projects/{project_id}/graph", f"/v1/projects/{project_id}/decisions"):
        _assert_envelope(client.get(path, headers=headers, params={"cursor": cursor}), 400, "invalid_cursor")

    # A graph cursor works for its project only.
    graph_cursor = encode_cursor(setup["alpha_project"], datetime.now(UTC), uuid4())
    own = client.get(f"/v1/projects/{project_id}/graph", headers=headers, params={"cursor": graph_cursor})
    assert own.status_code == 200, own.text
    _assert_envelope(
        client.get(f"/v1/projects/{other_project.id}/graph", headers=headers, params={"cursor": graph_cursor}),
        400,
        "invalid_cursor",
    )
    # A decisions cursor is bound to its filter set.
    from app.services.decision_record_service import decision_cursor_scope, encode_decision_cursor

    unfiltered = {
        key: None
        for key in (
            "state", "effective_state", "decision_type", "subject_kind",
            "subject_id", "actor_kind", "recorded_after", "recorded_before",
        )
    }
    decision_cursor = encode_decision_cursor(
        decision_cursor_scope(setup["alpha"].id, project_id, unfiltered), datetime.now(UTC), uuid4()
    )
    decisions = f"/v1/projects/{project_id}/decisions"
    assert client.get(decisions, headers=headers, params={"cursor": decision_cursor}).status_code == 200
    _assert_envelope(
        client.get(decisions, headers=headers, params={"cursor": decision_cursor, "state": "accepted"}),
        400,
        "invalid_cursor",
    )


def test_cursor_codec_rejects_foreign_scope_and_garbage():
    token = sign_cursor("events:a:b", [7])
    assert open_cursor(token, "events:a:b") == [7]
    for scope, value in (("events:a:c", token), ("events:a:b", token + "x"), ("events:a:b", "c1..")):
        with pytest.raises(InvalidCursorError):
            open_cursor(value, scope)


# --- ETag / If-Match --------------------------------------------------------------------------


def _create_request(auth_client, **extra):
    return auth_client.post(
        "/v1/execution-requests",
        json={"operation": OPERATION_MODEL_BUILD, "request_spec": {"filename": "a.csv"}, **extra.pop("body", {})},
        **extra,
    )


def test_mutable_gets_return_strong_etags(client, db_session, tmp_path, auth_client):
    setup = make_lineage_setup(db_session, tmp_path)
    pipeline = _pipeline_with_events(db_session, setup, count=1)
    db_session.commit()
    headers = _headers(setup["alpha_admin"], setup["alpha"].id)
    for path in (f"/v1/projects/{setup['alpha_project'].id}", f"/v1/model-builds/{pipeline.id}"):
        first = client.get(path, headers=headers)
        assert first.status_code == 200, first.text
        etag = first.headers["ETag"]
        assert re.fullmatch(r'"[0-9a-f]{32}"', etag), etag
        assert client.get(path, headers=headers).headers["ETag"] == etag

    created = _create_request(auth_client)
    assert created.status_code == 202, created.text
    fetched = auth_client.get(f"/v1/execution-requests/{created.json()['id']}")
    assert fetched.headers["ETag"] == created.headers["ETag"]


def test_if_match_mismatch_is_412_and_match_reaches_the_command(auth_client):
    created = _create_request(auth_client)
    request_id = created.json()["id"]
    etag = created.headers["ETag"]
    confirm = f"/v1/execution-requests/{request_id}/target-confirmation"

    stale = auth_client.post(confirm, json={"target_column": "y"}, headers={"If-Match": '"deadbeef"'})
    error = _assert_envelope(stale, 412, "precondition_failed")
    assert error["details"]["current_etag"] == etag and error["retryable"] is False
    weak = auth_client.post(confirm, json={"target_column": "y"}, headers={"If-Match": f"W/{etag}"})
    _assert_envelope(weak, 412, "precondition_failed")

    # Matching (or `*`) preconditions pass; the state machine then answers.
    for value in (etag, "*", f'"other", {etag}'):
        response = auth_client.post(confirm, json={"target_column": "y"}, headers={"If-Match": value})
        error = _assert_envelope(response, 409, "execution_not_waiting")
        assert error["details"]["status"] == "accepted"


def test_if_match_helpers_define_412_and_428_rules():
    check_if_match(None, '"a"')
    check_if_match('"a"', '"a"')
    with pytest.raises(V1APIError) as required:
        check_if_match(None, '"a"', required=True)
    assert required.value.status_code == 428 and required.value.code == "precondition_required"
    with pytest.raises(V1APIError) as failed:
        check_if_match('"b"', '"a"')
    assert failed.value.status_code == 412
    # Refs (ADR 0006 §2): ETag is the optimistic version, If-Match maps to expected_version.
    assert version_etag(3) == '"3"' and if_match_version('"3"') == 3
    assert if_match_version("*") is None and if_match_version(None) is None
    with pytest.raises(V1APIError) as missing:
        if_match_version(None, required=True)
    assert missing.value.status_code == 428
    for bad in ('"0"', "3", 'W/"3"', '"3", "4"'):
        with pytest.raises(V1APIError) as malformed:
            if_match_version(bad)
        assert malformed.value.status_code == 412
    assert representation_etag(_Etagged(a=1)) == representation_etag(_Etagged(a=1))
    assert representation_etag(_Etagged(a=1)) != representation_etag(_Etagged(a=2))


class _Etagged(BaseModel):
    a: int


# --- Idempotency-Key -------------------------------------------------------------------------


def _count(db_session) -> int:
    return db_session.scalar(select(func.count(ExecutionRequest.id))) or 0


def test_idempotency_key_replays_and_conflicts(auth_client, db_session):
    key = f"p31a-{uuid4().hex}"
    first = _create_request(auth_client, headers={"Idempotency-Key": key})
    assert first.status_code == 202 and "Idempotent-Replayed" not in first.headers
    before = _count(db_session)

    replay = _create_request(auth_client, headers={"Idempotency-Key": key})
    assert replay.status_code == 202 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == first.json() and _count(db_session) == before
    # Header and the legacy body key are the same key.
    same = _create_request(auth_client, headers={"Idempotency-Key": key}, body={"idempotency_key": key})
    assert same.json()["id"] == first.json()["id"]

    conflict = auth_client.post(
        "/v1/execution-requests",
        json={"operation": OPERATION_MODEL_BUILD, "request_spec": {"filename": "b.csv"}},
        headers={"Idempotency-Key": key},
    )
    error = _assert_envelope(conflict, 409, "idempotency_key_conflict")
    assert error["retryable"] is False and _count(db_session) == before


def test_idempotency_key_is_bound_to_the_principal(auth_client, admin_client, db_session):
    key = f"p31a-shared-{uuid4().hex}"
    assert _create_request(auth_client, headers={"Idempotency-Key": key}).status_code == 202
    other = _create_request(admin_client, headers={"Idempotency-Key": key})
    _assert_envelope(other, 409, "idempotency_key_conflict")


def test_idempotency_key_validation_and_keyless_posts(auth_client, db_session):
    _assert_envelope(
        _create_request(auth_client, headers={"Idempotency-Key": "has spaces!"}), 400, "invalid_idempotency_key"
    )
    _assert_envelope(
        _create_request(auth_client, headers={"Idempotency-Key": "a"}, body={"idempotency_key": "b"}),
        400,
        "idempotency_key_mismatch",
    )
    # Without a key every POST executes (documented: not replay-protected).
    one, two = _create_request(auth_client), _create_request(auth_client)
    assert one.status_code == two.status_code == 202 and one.json()["id"] != two.json()["id"]


def test_v1_key_never_replays_an_internal_surface_row(auth_client, db_session, client_user):
    # Internal namespaces are reserved: a /v1 caller can neither claim nor plant them.
    for spelling in ({"headers": {"Idempotency-Key": f"legacy_labs_upload:{uuid4()}"}},
                     {"body": {"idempotency_key": f"legacy_labs_upload:{uuid4()}"}}):
        _assert_envelope(_create_request(auth_client, **spelling), 400, "invalid_idempotency_key")
    # A row written without a digest (internal surface) never replays to a /v1 key.
    key = f"internal-{uuid4().hex}"
    create_execution_request(
        db_session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        operation=OPERATION_MODEL_BUILD,
        source_surface=SOURCE_LEGACY_LABS,
        requested_by_user_id=client_user.id,
        idempotency_key=key,
        request_spec={"filename": "a.csv"},
    )
    db_session.commit()
    _assert_envelope(_create_request(auth_client, headers={"Idempotency-Key": key}), 409, "idempotency_key_conflict")


def test_server_owned_spec_keys_are_refused_and_the_digest_is_not_exposed(auth_client, db_session):
    for key in ("request_digest", "parent_experiment_id", "change_set_digest"):
        response = auth_client.post(
            "/v1/execution-requests",
            json={"operation": OPERATION_MODEL_BUILD, "request_spec": {key: "0" * 64}},
        )
        _assert_envelope(response, 400, "invalid_request_spec")
    created = _create_request(auth_client, headers={"Idempotency-Key": f"p31a-{uuid4().hex}"})
    assert "request_digest" not in created.json()["request_spec"]
    stored = db_session.get(ExecutionRequest, UUID(created.json()["id"]))
    assert len(stored.request_spec["request_digest"]) == 64


def test_confirmation_accepts_idempotency_key_and_validates_it(auth_client):
    created = _create_request(auth_client).json()
    confirm = f"/v1/execution-requests/{created['id']}/target-confirmation"
    _assert_envelope(
        auth_client.post(confirm, json={"target_column": "y"}, headers={"Idempotency-Key": "bad key"}),
        400,
        "invalid_idempotency_key",
    )
    keyed = auth_client.post(confirm, json={"target_column": "y"}, headers={"Idempotency-Key": "k-1"})
    _assert_envelope(keyed, 409, "execution_not_waiting")
