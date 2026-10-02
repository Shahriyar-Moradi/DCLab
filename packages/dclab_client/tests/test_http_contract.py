"""HTTP contract for DCLabClient without a live API process."""

from __future__ import annotations

import json
from pathlib import Path

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
