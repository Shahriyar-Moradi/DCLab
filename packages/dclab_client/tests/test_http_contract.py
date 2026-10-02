"""HTTP contract for DCLabClient without a live API process."""

from __future__ import annotations

import io
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from dclab_client import DCLabAPIError, DCLabClient, DCLabClientError, MAX_TIMEOUT_SECONDS
from dclab_client._http import V1Transport


def _client(handler, **kwargs) -> DCLabClient:
    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://api.test",
    )
    return DCLabClient(base_url="http://api.test", http=http, **kwargs)


def test_timeout_must_be_bounded():
    with pytest.raises(DCLabClientError, match="timeout"):
        DCLabClient("http://api.test", timeout=0)
    with pytest.raises(DCLabClientError, match="timeout"):
        DCLabClient("http://api.test", timeout=MAX_TIMEOUT_SECONDS + 1)
    with pytest.raises(DCLabClientError, match="timeout"):
        DCLabClient("http://api.test", timeout=None)  # type: ignore[arg-type]


def test_rejects_non_v1_paths():
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        base_url="http://api.test",
    )
    transport = V1Transport(base_url="http://api.test", http=http)
    with pytest.raises(DCLabClientError, match="/v1"):
        transport.request("GET", "/app/labs/problems")
    with pytest.raises(DCLabClientError, match="/v1"):
        transport.request("GET", "/auth/me")


def test_injects_bearer_workspace_and_request_id():
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(401, json={"detail": "missing bearer token"})

    api = _client(
        handler,
        token="secret-token",
        workspace_id="11111111-1111-1111-1111-111111111111",
        request_id="trace-1",
    )
    with pytest.raises(DCLabAPIError) as caught:
        api.identity.me()
    assert caught.value.status_code == 401
    assert caught.value.detail == "missing bearer token"
    assert caught.value.path == "/v1/me"
    request = recorded[0]
    assert request.url.path == "/v1/me"
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert request.headers["X-Workspace-Id"] == "11111111-1111-1111-1111-111111111111"
    assert request.headers["X-Request-Id"] == "trace-1"


def test_create_sends_idempotency_key_header_and_body():
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(400, json={"detail": "unsupported operation"})

    api = _client(
        handler,
        token="secret-token",
        workspace_id="11111111-1111-1111-1111-111111111111",
        idempotency_key="client-default",
    )
    with pytest.raises(DCLabAPIError) as caught:
        api.execution_requests.create(
            operation="model_build",
            idempotency_key="once",
            request_id="trace-create",
        )
    assert caught.value.status_code == 400
    request = recorded[0]
    assert request.url.path == "/v1/execution-requests"
    assert request.method == "POST"
    assert request.headers["Idempotency-Key"] == "once"
    assert request.headers["X-Request-Id"] == "trace-create"
    body = json.loads(request.content.decode("utf-8"))
    assert body["idempotency_key"] == "once"
    assert body["operation"] == "model_build"


def test_confirm_target_posts_column_to_v1():
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200,
            json={
                "id": "11111111-1111-1111-1111-111111111111",
                "workspace_id": "11111111-1111-1111-1111-111111111111",
                "project_id": None,
                "operation": "model_build",
                "source_surface": "legacy_labs",
                "requested_by_user_id": None,
                "idempotency_key": None,
                "external_request_id": None,
                "parent_request_id": None,
                "status": "running",
                "request_spec": {"target_column": "Churn"},
                "result_summary": {"code": "target_confirmed", "target_column": "Churn"},
                "workflow_run_id": None,
                "pipeline_run_id": None,
                "created_at": "2026-09-09T00:00:00Z",
                "started_at": "2026-09-09T00:00:01Z",
                "completed_at": None,
                "failure_code": None,
                "failure_summary": None,
            },
        )

    api = _client(
        handler, token="secret-token", workspace_id="11111111-1111-1111-1111-111111111111"
    )
    row = api.execution_requests.confirm_target(
        "11111111-1111-1111-1111-111111111111",
        target_column="Churn",
        request_id="trace-confirm",
    )
    assert row.status == "running"
    assert row.request_spec["target_column"] == "Churn"
    request = recorded[0]
    assert request.url.path == (
        "/v1/execution-requests/11111111-1111-1111-1111-111111111111/target-confirmation"
    )
    assert request.method == "POST"
    assert request.headers["X-Request-Id"] == "trace-confirm"
    body = json.loads(request.content.decode("utf-8"))
    assert body == {"target_column": "Churn"}


def test_omits_workspace_header_unless_constructed_with_one():
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200,
            json={
                "id": "11111111-1111-1111-1111-111111111111",
                "email": "a@b.test",
                "role": "viewer",
                "full_name": "A",
                "workspace_id": None,
            },
        )

    api = _client(handler, token="secret-token")
    me = api.identity.me()
    assert me.email == "a@b.test"
    assert me.active_workspace_id is None
    assert me.workspaces == []
    request = recorded[0]
    assert "X-Workspace-Id" not in request.headers
    assert "cookie" not in request.headers
    assert "x-dclab-session" not in request.headers


def test_workspace_resource_requires_explicit_sdk_scope():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    api = _client(handler, token="secret-token")
    with pytest.raises(DCLabClientError, match="workspace_id is required"):
        api.projects.list()
    assert requests == []
    with pytest.raises(DCLabClientError, match="workspace_id must be a UUID"):
        _client(handler, token="secret-token", workspace_id="not-a-uuid")


def test_source_never_calls_browser_session_workspace_selection():
    source = (
        Path(__file__).resolve().parents[1] / "dclab_client" / "_http.py"
    ).read_text(encoding="utf-8")
    assert "dclab_session" not in source
    assert "/auth/workspace" not in source
    assert "Cookie" not in source


def test_project_graph_and_node_impact_use_v1_paths():
    recorded: list[httpx.Request] = []
    node = {"kind": "split_plan", "id": "22222222-2222-2222-2222-222222222222",
            "key": "split_plan:22222222-2222-2222-2222-222222222222"}
    exp = {"kind": "experiment", "id": "33333333-3333-3333-3333-333333333333",
           "key": "experiment:33333333-3333-3333-3333-333333333333"}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if request.url.path.endswith("/impact"):
            return httpx.Response(200, json={
                "node": node, "project_id": "11111111-1111-1111-1111-111111111111",
                "items": [exp], "counts_by_kind": {"experiment": 1}, "total": 1,
                "truncated": False, "graph_truncated": False,
            })
        return httpx.Response(200, json={
            "project": {
                "id": "11111111-1111-1111-1111-111111111111",
                "workspace_id": "44444444-4444-4444-4444-444444444444",
                "name": "P", "slug": "p", "description": "", "status": "active",
                "created_by": None, "provenance": "user",
                "created_at": "2026-10-02T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z",
            },
            "refs_initialized": False, "refs": [],
            "nodes": [{**exp, "label": "run #1"}],
            "edges": [{"from": exp, "to": node, "relation": "uses_split_plan", "attribute": False}],
            "counts_by_kind": {"experiment": 1}, "stale_counts_by_kind": {},
            "experiment_limit": 200, "truncated": True, "next_cursor": "abc",
        })

    api = _client(handler, token="t", workspace_id="44444444-4444-4444-4444-444444444444")
    graph = api.projects.graph("11111111-1111-1111-1111-111111111111", cursor="prev", limit=200)
    assert graph.edges[0].from_.key == exp["key"] and graph.next_cursor == "abc"
    impact = api.nodes.impact("split_plan", node["id"])
    assert impact.items[0].key == exp["key"]
    assert recorded[0].url.path == "/v1/projects/11111111-1111-1111-1111-111111111111/graph"
    assert dict(recorded[0].url.params) == {"cursor": "prev", "limit": "200"}
    assert recorded[1].url.path == f"/v1/nodes/split_plan/{node['id']}/impact"


def test_path_ids_and_node_kinds_cannot_escape_v1():
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={})

    api = _client(handler, token="secret-token", workspace_id="44444444-4444-4444-4444-444444444444")
    node_id = "22222222-2222-2222-2222-222222222222"
    for kind in ("../../auth/me/x", "candidate", "split_plan/..", ""):
        with pytest.raises(DCLabClientError, match="kind"):
            api.nodes.impact(kind, node_id)
    for bad_id in ("../../auth/me", "abc", "11111111-1111-1111-1111-111111111111/../x"):
        with pytest.raises(DCLabClientError, match="UUID"):
            api.projects.get(bad_id)
        with pytest.raises(DCLabClientError, match="UUID"):
            api.nodes.impact("experiment", bad_id)
        with pytest.raises(DCLabClientError, match="UUID"):
            api.model_builds.get(bad_id)
    assert recorded == []


def test_transport_rejects_dot_segments_and_escapes():
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        base_url="http://api.test",
    )
    transport = V1Transport(base_url="http://api.test", http=http, workspace_id="44444444-4444-4444-4444-444444444444")
    for path in ("/v1/../auth/me", "/v1/projects/./x", "/v1/%2e%2e/auth/me", "/v1/projects\\..\\auth", "/v1/.."):
        with pytest.raises(DCLabClientError, match="/v1"):
            transport.request("GET", path)
    assert transport.request("GET", "/v1/projects") == {}


def test_experiment_code_uses_v1_path_and_validates_ids():
    recorded: list[httpx.Request] = []
    document = {"filename": "e.py", "media_type": "text/x-python", "content_digest": "a" * 64, "source": "x = 1\n"}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={
            "experiment_id": "33333333-3333-3333-3333-333333333333",
            "workspace_id": "44444444-4444-4444-4444-444444444444",
            "generator_version": "dclab.model_build_reproduction.v2",
            "spec_digest": "b" * 64,
            "split_plan_id": "22222222-2222-2222-2222-222222222222",
            "parent_experiment_id": None,
            "is_branch": False,
            "standalone_cv": True,
            "script": document,
            "notebook": {**document, "filename": "e.ipynb", "media_type": "application/x-ipynb+json"},
            "inputs": [{"name": "split_assignment", "placeholder": "<p>", "env_var": "DCLAB_SPLIT_ASSIGNMENT_PATH",
                        "artifact_id": None, "content_digest": "c" * 64, "description": "map"}],
            "helper_requirements": [],
        })

    api = _client(handler, token="t", workspace_id="44444444-4444-4444-4444-444444444444")
    code = api.experiments.code("33333333-3333-3333-3333-333333333333", request_id="trace-code")
    assert code.standalone_cv is True and code.script.source == "x = 1\n"
    assert code.inputs[0].env_var == "DCLAB_SPLIT_ASSIGNMENT_PATH"
    assert recorded[0].method == "GET"
    assert recorded[0].url.path == "/v1/experiments/33333333-3333-3333-3333-333333333333/code"
    assert recorded[0].headers["X-Request-Id"] == "trace-code"
    with pytest.raises(DCLabClientError, match="UUID"):
        api.experiments.code("../../auth/me")
    assert len(recorded) == 1


def test_project_decisions_use_v1_path_filters_and_untrusted_fields():
    from datetime import UTC, datetime

    recorded: list[httpx.Request] = []
    project = "11111111-1111-1111-1111-111111111111"
    record = {
        "id": "55555555-5555-5555-5555-555555555555", "project_id": project,
        "decision_type": "experiment_accepted", "state": "proposed", "effective_state": "superseded",
        "supersedes_id": None, "superseded_by_id": "66666666-6666-6666-6666-666666666666",
        "subject": {"kind": "experiment", "id": "33333333-3333-3333-3333-333333333333",
                    "key": "experiment:33333333-3333-3333-3333-333333333333"},
        "actor": {"kind": "agent", "agent_run_id": "77777777-7777-7777-7777-777777777777"},
        "rationale": "[REDACTED]", "rationale_untrusted": True,
        "rationale_label": "unverified agent rationale", "rationale_truncated": False,
        "content_origin": "agent",
        "facts": {}, "evidence_refs": [{"kind": "experiment", "id": "33333333-3333-3333-3333-333333333333",
                                        "key": "experiment:33333333-3333-3333-3333-333333333333"}],
        "details": {}, "schema_version": 1, "policy_version": "dclab.decisions.v1",
        "event_at": "2026-10-02T00:00:00Z", "recorded_at": "2026-10-02T00:00:00Z",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"items": [record], "next_cursor": "c2", "limit": 10})

    api = _client(handler, token="t", workspace_id="44444444-4444-4444-4444-444444444444")
    page = api.projects.decisions(
        project, effective_state="superseded", actor_kind="agent", subject_kind="experiment",
        subject_id="33333333-3333-3333-3333-333333333333",
        recorded_after=datetime(2026, 10, 1, tzinfo=UTC), cursor="c1", limit=10,
    )
    assert page.next_cursor == "c2" and page.items[0].rationale_untrusted is True
    assert page.items[0].rationale_label == "unverified agent rationale"
    assert page.items[0].content_origin == "agent" and page.items[0].details_truncated is False
    assert recorded[0].method == "GET" and recorded[0].url.path == f"/v1/projects/{project}/decisions"
    assert dict(recorded[0].url.params) == {
        "effective_state": "superseded", "actor_kind": "agent", "subject_kind": "experiment",
        "subject_id": "33333333-3333-3333-3333-333333333333",
        "recorded_after": "2026-10-01T00:00:00+00:00", "cursor": "c1", "limit": "10",
    }
    for bad_id in ("../../auth/me", "abc"):
        with pytest.raises(DCLabClientError, match="UUID"):
            api.projects.decisions(bad_id)
        with pytest.raises(DCLabClientError, match="UUID"):
            api.projects.decisions(project, subject_id=bad_id)
    assert len(recorded) == 1


# --- P3.1-A: versioned client, error envelope, Idempotency-Key, ETag ----------------------

WS = "11111111-1111-1111-1111-111111111111"

_EXECUTION_REQUEST = {
    "id": WS,
    "workspace_id": WS,
    "project_id": None,
    "operation": "model_build",
    "source_surface": "api",
    "requested_by_user_id": None,
    "idempotency_key": None,
    "external_request_id": None,
    "parent_request_id": None,
    "status": "accepted",
    "request_spec": {},
    "result_summary": None,
    "workflow_run_id": None,
    "pipeline_run_id": None,
    "created_at": "2026-10-02T00:00:00Z",
    "started_at": None,
    "completed_at": None,
    "failure_code": None,
    "failure_summary": None,
}


def _envelope(status: int, code: str, *, retryable: bool = False, details=None) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "error": {
                "code": code,
                "message": f"{code} happened",
                "retryable": retryable,
                "request_id": "srv-rid-1",
                "details": details or {},
            }
        },
        headers={"X-Request-Id": "srv-rid-1"},
    )


def test_client_is_versioned_and_identifies_itself():
    import dclab_client

    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=[])

    assert dclab_client.__version__ == "0.2.0"
    assert DCLabClient.version == dclab_client.__version__
    assert dclab_client.USER_AGENT == f"dclab-client/{dclab_client.__version__}"
    _client(handler, token="t", workspace_id=WS).projects.list()
    assert recorded[0].headers["User-Agent"] == dclab_client.USER_AGENT


@pytest.mark.parametrize(
    ("status", "code", "error_type"),
    [
        (400, "invalid_cursor", "BadRequestError"),
        (401, "unauthenticated", "AuthenticationError"),
        (403, "forbidden", "PermissionDeniedError"),
        (404, "not_found", "NotFoundError"),
        (409, "execution_not_waiting", "ConflictError"),
        (409, "idempotency_key_conflict", "IdempotencyConflictError"),
        (412, "precondition_failed", "PreconditionFailedError"),
        (422, "validation_failed", "UnprocessableEntityError"),
        (428, "precondition_required", "PreconditionRequiredError"),
        (429, "rate_limited", "RateLimitedError"),
        (503, "unavailable", "ServerError"),
    ],
)
def test_envelope_parses_into_typed_errors(status, code, error_type):
    import dclab_client

    api = _client(lambda request: _envelope(status, code, retryable=status >= 429, details={"k": "v"}),
                  token="t", workspace_id=WS)
    with pytest.raises(getattr(dclab_client, error_type)) as caught:
        api.projects.list()
    error = caught.value
    assert isinstance(error, DCLabAPIError)
    assert (error.status_code, error.code, error.message) == (status, code, f"{code} happened")
    assert error.retryable is (status >= 429)
    assert error.request_id == "srv-rid-1" and error.details == {"k": "v"}
    assert error.detail["code"] == code
    assert "srv-rid-1" in str(error)


def test_legacy_detail_bodies_still_map_to_typed_errors():
    import dclab_client

    api = _client(lambda request: httpx.Response(404, json={"detail": "not found"}), token="t", workspace_id=WS)
    with pytest.raises(dclab_client.NotFoundError) as caught:
        api.projects.list()
    assert caught.value.code == "not_found" and caught.value.detail == "not found"
    assert caught.value.retryable is False


def test_every_post_carries_an_idempotency_key_and_gets_never_do():
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_EXECUTION_REQUEST)
        return _envelope(503, "unavailable", retryable=True)

    api = _client(handler, token="t", workspace_id=WS)
    with pytest.raises(DCLabAPIError) as first:
        api.execution_requests.create(request_spec={"filename": "a.csv"})
    with pytest.raises(DCLabAPIError):
        api.execution_requests.confirm_target(WS, target_column="y")
    api.execution_requests.get(WS)
    create, confirm, get = recorded
    generated = create.headers["Idempotency-Key"]
    assert str(UUID(generated)) == generated
    assert first.value.idempotency_key == generated and first.value.retryable is True
    assert "idempotency_key" not in json.loads(create.content)  # header only unless given
    assert confirm.headers["Idempotency-Key"] and confirm.headers["Idempotency-Key"] != generated
    assert "Idempotency-Key" not in get.headers


def test_etag_is_captured_and_if_match_is_sent():
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_EXECUTION_REQUEST, headers={"ETag": '"abc"'})
        if request.headers.get("If-Match") != '"abc"':
            return _envelope(412, "precondition_failed", details={"current_etag": '"abc"'})
        return httpx.Response(
            200, json=_EXECUTION_REQUEST, headers={"ETag": '"def"', "Idempotent-Replayed": "true"}
        )

    api = _client(handler, token="t", workspace_id=WS)
    row = api.execution_requests.get(WS)
    assert row.etag == '"abc"' and row.idempotent_replay is False
    assert "etag" not in row.model_dump()
    confirmed = api.execution_requests.confirm_target(
        WS, target_column="y", if_match=row.etag, idempotency_key="confirm-1"
    )
    assert confirmed.etag == '"def"' and confirmed.idempotent_replay is True
    assert recorded[1].headers["If-Match"] == '"abc"'
    assert recorded[1].headers["Idempotency-Key"] == "confirm-1"
    from dclab_client import PreconditionFailedError

    with pytest.raises(PreconditionFailedError) as stale:
        api.execution_requests.confirm_target(WS, target_column="y", if_match='"old"')
    assert stale.value.details["current_etag"] == '"abc"'


# --- P3.1-B1 resource commands ----------------------------------------------------------------

_PROJECT = {
    "id": WS, "workspace_id": WS, "name": "Churn", "slug": "churn", "description": "",
    "status": "active", "created_by": None, "provenance": "user",
    "created_at": "2026-10-02T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z", "archived_at": None,
}
_SPEC = {
    "id": WS, "workspace_id": WS, "project_id": WS, "version": 1, "task_type": "binary",
    "target_column": "churn", "prediction_unit": None, "prediction_time_column": None,
    "prediction_horizon": None, "primary_metric": None, "business_objective": "Reduce churn",
    "constraints": {}, "success_criteria": {}, "status": "draft", "content_digest": "a" * 64,
    "created_by": WS, "created_at": "2026-10-02T00:00:00Z", "locked_at": None,
}
_UPLOAD = {
    "id": WS, "workspace_id": WS, "project_id": WS, "dataset_asset_id": WS, "name": "churn",
    "version": "v1", "source_type": "csv", "content_digest": "b" * 64, "schema_digest": None,
    "size_bytes": 12, "row_count": 2, "column_count": 2, "created_at": "2026-10-02T00:00:00Z",
    "ingestion": {"id": WS, "status": "completed", "publication_state": "published",
                  "rows_read": 2, "bytes_read": 12, "completed_at": None},
}


def test_create_project_spec_and_upload_post_to_v1_with_keys(tmp_path):
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        body = {"/v1/projects": _PROJECT, "/v1/datasets": _UPLOAD}.get(request.url.path, _SPEC)
        return httpx.Response(201, json=body, headers={"ETag": '"e1"', "Idempotent-Replayed": "true"})

    api = _client(handler, token="t", workspace_id=WS)
    project = api.projects.create(name="Churn", slug="churn", idempotency_key="p-1")
    assert project.etag == '"e1"' and project.idempotent_replay is True
    spec = api.projects.create_problem_spec(WS, task_type="binary", business_objective="Reduce churn")
    assert spec.version == 1
    data = tmp_path / "churn.csv"
    data.write_bytes(b"a,b\n1,2\n3,4\n")
    upload = api.datasets.upload(WS, data, content_type="text/csv", idempotency_key="d-1")
    assert upload.ingestion.publication_state == "published"
    create, spec_post, upload_post = recorded
    assert (create.url.path, create.headers["Idempotency-Key"]) == ("/v1/projects", "p-1")
    assert json.loads(create.content) == {"name": "Churn", "description": "", "slug": "churn"}
    assert spec_post.url.path == f"/v1/projects/{WS}/problem-specs"
    assert UUID(spec_post.headers["Idempotency-Key"])  # generated when not given
    assert upload_post.url.path == "/v1/datasets" and upload_post.headers["Idempotency-Key"] == "d-1"
    assert upload_post.headers["Content-Type"].startswith("multipart/form-data")
    content = upload_post.content
    assert b'name="project_id"' in content and WS.encode() in content
    assert b'filename="churn.csv"' in content and b"a,b\n1,2\n3,4\n" in content


def test_upload_validates_ids_and_stream_filenames():
    api = _client(lambda request: httpx.Response(500), token="t", workspace_id=WS)
    with pytest.raises(DCLabClientError, match="UUID"):
        api.datasets.upload("../x", io.BytesIO(b"x"), filename="a.csv")
    with pytest.raises(DCLabClientError, match="filename"):
        api.datasets.upload(WS, io.BytesIO(b"x"))
