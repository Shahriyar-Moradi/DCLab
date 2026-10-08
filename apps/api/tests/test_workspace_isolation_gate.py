"""S0-P03E: live route inventory, tenant isolation, and safe operations signals."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.db.models import UserRole, WorkspaceMembership
from app.main import app
from app.services.auth_service import create_access_token, create_user
from app.services.project_service import create_project
from app.services.workspace_access_metrics import (
    reset_workspace_access_metrics,
    workspace_access_metric_counts,
)
from app.services import workspace_access_metrics
from app.services.workspace_service import create_business_workspace
from conftest import browser_login, csrf_headers


# Bearers refused outright with this code (P3.2-A service tokens; P6.3-B2 assistant).
SESSION_ONLY_GETS = {"/v1/service-tokens": "session_required", "/v1/assistant/threads": "human_session_required",
                     "/v1/assistant/threads/{thread_id}": "human_session_required"}


def _owner_and_workspaces(db_session):
    owner = create_user(
        db_session,
        email=f"gate-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
    )
    first = create_business_workspace(db_session, owner=owner, name="Gate Alpha")
    second = create_business_workspace(db_session, owner=owner, name="Gate Beta")
    db_session.commit()
    return owner, first, second


def _tenant_get_routes() -> list[str]:
    excluded = {"/v1/me", "/v1/workspaces", "/workspaces/personal", "/workspaces/business"}
    excluded |= set(SESSION_ONLY_GETS)
    paths = app.openapi()["paths"]
    selected = [
        path
        for path, operations in paths.items()
        if "get" in operations
        and path not in excluded
        and path.startswith(("/v1/", "/app/", "/business/", "/development/", "/workspaces/"))
    ]
    return sorted(selected)


def test_route_inventory_bearer_gets_never_infer_a_workspace(client, db_session):
    owner, _first, _second = _owner_and_workspaces(db_session)
    token = create_access_token(owner)
    inventory = _tenant_get_routes()
    assert len(inventory) >= 35
    for prefix in ("/v1/", "/app/", "/business/", "/development/", "/workspaces/"):
        assert any(path.startswith(prefix) for path in inventory)
    assert any("/download" in path for path in inventory)
    assert any("/events" in path for path in inventory)

    for path in inventory:
        concrete = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", path)
        response = client.get(concrete, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 400, (path, response.status_code, response.text)
        body = response.json()
        # /v1 answers with the P3.1-A error envelope; legacy surfaces keep `detail`.
        message = body["error"]["message"] if path.startswith("/v1/") else body["detail"]
        assert "X-Workspace-Id" in message
    for path, code in SESSION_ONLY_GETS.items():  # refuse every bearer outright
        concrete = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", path)
        response = client.get(concrete, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 403 and response.json()["error"]["code"] == code


def test_two_workspace_read_mutation_file_event_and_revocation_matrix(
    client, db_session
):
    owner, first, second = _owner_and_workspaces(db_session)
    project_a = create_project(db_session, actor=owner, workspace_id=first.id, name="Alpha")
    project_b = create_project(db_session, actor=owner, workspace_id=second.id, name="Beta")
    db_session.commit()
    token = create_access_token(owner)
    a = {"Authorization": f"Bearer {token}", "X-Workspace-Id": str(first.id)}
    b = {"Authorization": f"Bearer {token}", "X-Workspace-Id": str(second.id)}

    assert [row["id"] for row in client.get("/v1/projects", headers=a).json()] == [str(project_a.id)]
    assert [row["id"] for row in client.get("/v1/projects", headers=b).json()] == [str(project_b.id)]
    assert client.get(f"/v1/projects/{project_b.id}", headers=a).status_code == 404
    assert client.get(f"/workspaces/{second.id}/projects", headers=a).status_code == 404
    assert client.post(f"/workspaces/{second.id}/projects", headers=a, json={"name": "No"}).status_code == 404
    assert client.get(f"/workspaces/{second.id}/artifacts/{uuid4()}/download", headers=a).status_code == 404
    assert client.get(f"/v1/model-builds/{uuid4()}/events", headers=a).status_code == 404

    membership = db_session.query(WorkspaceMembership).filter_by(
        user_id=owner.id, workspace_id=second.id
    ).one()
    membership.suspended_at = datetime.now(UTC)
    db_session.commit()
    db_session.expire_all()
    assert client.get("/v1/projects", headers=b).status_code == 403
    assert client.get("/v1/projects", headers=a).status_code == 200
    assert client.get("/v1/me", headers=b).json()["active_workspace_id"] == str(first.id)


def test_selection_and_denial_signals_are_bounded_and_safe(
    client, db_session, monkeypatch
):
    reset_workspace_access_metrics()
    emitted: list[str] = []
    monkeypatch.setattr(
        workspace_access_metrics.logger,
        "info",
        lambda _template, payload: emitted.append(payload),
    )
    owner, first, second = _owner_and_workspaces(db_session)
    browser_login(client, owner.email, "test-password")
    selected = client.put(
        "/auth/workspace",
        json={"workspace_id": str(first.id)},
        headers=csrf_headers(client),
    )
    assert selected.status_code == 200
    denied = client.get(
        "/v1/projects", headers={"X-Workspace-Id": str(uuid4())}
    )
    assert denied.status_code == 403
    mismatch = client.get(
        f"/workspaces/{second.id}/projects",
        headers={"X-Workspace-Id": str(first.id)},
    )
    assert mismatch.status_code == 404
    malformed = client.get("/v1/projects", headers={"X-Workspace-Id": "not-a-uuid"})
    assert malformed.status_code == 400

    counts = workspace_access_metric_counts()
    assert counts["selection.selected"] == 1
    assert counts["denial.unauthorized_selector"] == 1
    assert counts["denial.path_mismatch"] == 1
    assert counts["denial.malformed_selector"] == 1
    events = [json.loads(payload) for payload in emitted]
    assert len(events) == 4
    assert all(event["event"] == "workspace_access" and event["request_id"] for event in events)
    serialized = json.dumps(events)
    assert owner.email not in serialized
    assert "not-a-uuid" not in serialized
    assert "test-password" not in serialized


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/business/workspaces"),
        (
            "post",
            "/business/workspaces/00000000-0000-0000-0000-000000000000/"
            "lab-runs/00000000-0000-0000-0000-000000000000/verification/deep",
        ),
    ],
)
def test_capability_denials_are_observable_without_bypassing_policy(
    client, client_user, method, path
):
    reset_workspace_access_metrics()
    headers = {"Authorization": f"Bearer {create_access_token(client_user)}"}
    response = getattr(client, method)(path, headers=headers)
    assert response.status_code == 403
    assert workspace_access_metric_counts()["denial.capability_denied"] == 1
