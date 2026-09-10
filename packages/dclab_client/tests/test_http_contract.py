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

    api = _client(handler, token="secret-token", idempotency_key="client-default")
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

    api = _client(handler, token="secret-token")
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


def test_source_never_calls_browser_session_workspace_selection():
    source = (
        Path(__file__).resolve().parents[1] / "dclab_client" / "_http.py"
    ).read_text(encoding="utf-8")
    assert "dclab_session" not in source
    assert "/auth/workspace" not in source
    assert "Cookie" not in source
