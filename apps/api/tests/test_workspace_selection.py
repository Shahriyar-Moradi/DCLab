"""S0-P03A: active workspace selector is never proof of access."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.config import get_settings
from app.db.models import AuthSession, UserRole, WorkspaceMembership
from app.services.auth_service import create_access_token, create_user, register_customer
from app.services.csrf_service import csrf_token_for_session
from app.services.project_service import create_project
from app.services.workspace_service import create_business_workspace
from conftest import TRUSTED_ORIGIN, browser_login, csrf_headers

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_CLIENT = REPO_ROOT / "apps" / "web" / "lib" / "infrastructure" / "api-client.ts"
SDK_HTTP = REPO_ROOT / "packages" / "dclab_client" / "dclab_client" / "_http.py"


def _session_csrf(raw: str) -> dict[str, str]:
    token = csrf_token_for_session(raw, get_settings())
    return {
        "Origin": TRUSTED_ORIGIN,
        "X-CSRF-Token": token,
        "X-DCLab-Session": raw,
    }


def _two_workspaces(db_session, *, home: bool = True):
    owner = create_user(
        db_session,
        email=f"multi-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Multi",
    )
    first = create_business_workspace(db_session, owner=owner, name="Alpha Select")
    second = create_business_workspace(db_session, owner=owner, name="Beta Select")
    if not home:
        owner.workspace_id = None
    db_session.commit()
    return owner, first, second


def test_zero_memberships_have_no_active_workspace(client, db_session):
    user = register_customer(
        db_session,
        email=f"zero-{uuid4().hex}@test.invalid",
        password="test-password",
        full_name="Zero",
    )
    db_session.commit()
    login = browser_login(client, user.email, "test-password")
    assert login.status_code == 200, login.text
    me = client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["active_workspace_id"] is None
    assert body["workspaces"] == []
    assert me.headers.get("x-request-id")
    denied = client.get("/v1/projects")
    assert denied.status_code == 403
    assert "not authorized for a workspace" in denied.json()["detail"]


def test_single_membership_session_selects_home_without_header(client, client_user):
    login = browser_login(client, client_user.email, "client-pass-123")
    assert login.status_code == 200
    me = client.get("/auth/me")
    assert me.json()["active_workspace_id"] == str(client_user.workspace_id)
    listed = client.get("/v1/workspaces")
    ids = {row["id"] for row in listed.json()}
    assert str(client_user.workspace_id) in ids
    projects = client.get("/v1/projects")
    assert projects.status_code == 200, projects.text


def test_multiple_memberships_require_selection_when_home_is_unset(
    client, db_session
):
    owner, first, second = _two_workspaces(db_session, home=False)
    browser_login(client, owner.email, "test-password")
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["active_workspace_id"] is None
    assert {row["id"] for row in me.json()["workspaces"]} == {
        str(first.id),
        str(second.id),
    }
    missing = client.get("/v1/projects")
    assert missing.status_code == 400
    assert "X-Workspace-Id" in missing.json()["detail"]

    chosen = client.put(
        "/auth/workspace",
        json={"workspace_id": str(second.id)},
        headers=csrf_headers(client),
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["active_workspace_id"] == str(second.id)
    assert client.get("/v1/projects").status_code == 200
    row = db_session.query(AuthSession).filter(AuthSession.revoked_at.is_(None)).one()
    assert row.selected_workspace_id == second.id


def test_header_cannot_grant_a_workspace_the_user_does_not_have(
    client, db_session, client_user
):
    owner, first, _second = _two_workspaces(db_session)
    browser_login(client, client_user.email, "client-pass-123")
    denied = client.get(
        "/v1/projects", headers={"X-Workspace-Id": str(first.id)}
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "not authorized for this workspace"
    unknown = client.put(
        "/auth/workspace",
        json={"workspace_id": str(uuid4())},
        headers=csrf_headers(client),
    )
    assert unknown.status_code == 404
    forbidden = client.put(
        "/auth/workspace",
        json={"workspace_id": str(first.id)},
        headers=csrf_headers(client),
    )
    assert forbidden.status_code == 403


def test_cross_workspace_project_stays_not_found(client, db_session):
    owner, first, second = _two_workspaces(db_session)
    hidden = create_project(
        db_session, actor=owner, workspace_id=second.id, name="Secret"
    )
    db_session.commit()
    token = create_access_token(owner)
    visible_headers = {
        "Authorization": f"Bearer {token}",
        "X-Workspace-Id": str(first.id),
    }
    hidden_get = client.get(f"/v1/projects/{hidden.id}", headers=visible_headers)
    assert hidden_get.status_code == 404


def test_suspended_membership_is_not_selectable(client, db_session):
    owner, first, second = _two_workspaces(db_session, home=False)
    membership = (
        db_session.query(WorkspaceMembership)
        .filter(
            WorkspaceMembership.user_id == owner.id,
            WorkspaceMembership.workspace_id == second.id,
        )
        .one()
    )
    membership.suspended_at = datetime.now(UTC)
    db_session.commit()
    browser_login(client, owner.email, "test-password")
    denied = client.put(
        "/auth/workspace",
        json={"workspace_id": str(second.id)},
        headers=csrf_headers(client),
    )
    assert denied.status_code == 403
    ok = client.put(
        "/auth/workspace",
        json={"workspace_id": str(first.id)},
        headers=csrf_headers(client),
    )
    assert ok.status_code == 200


def test_sessions_keep_independent_workspace_selection(client, db_session):
    owner, first, second = _two_workspaces(db_session, home=False)
    first_login = browser_login(client, owner.email, "test-password")
    raw_one = first_login.cookies.get("dclab_session")
    client.cookies.clear()
    second_login = browser_login(client, owner.email, "test-password")
    raw_two = second_login.cookies.get("dclab_session")
    assert raw_one != raw_two

    one = client.put(
        "/auth/workspace",
        json={"workspace_id": str(first.id)},
        headers=_session_csrf(raw_one),
    )
    assert one.status_code == 200, one.text
    two = client.put(
        "/auth/workspace",
        json={"workspace_id": str(second.id)},
        headers=_session_csrf(raw_two),
    )
    assert two.status_code == 200, two.text
    me_one = client.get("/auth/me", headers={"X-DCLab-Session": raw_one})
    me_two = client.get("/auth/me", headers={"X-DCLab-Session": raw_two})
    assert me_one.json()["active_workspace_id"] == str(first.id)
    assert me_two.json()["active_workspace_id"] == str(second.id)


def test_bearer_clients_cannot_use_session_workspace_endpoint(
    client, client_user, client_token
):
    response = client.put(
        "/auth/workspace",
        json={"workspace_id": str(client_user.workspace_id)},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert response.status_code == 400
    assert "X-Workspace-Id" in response.json()["detail"]


def test_v1_me_is_additive_and_echoes_request_id(auth_client, client_user):
    response = auth_client.get("/v1/me", headers={"X-Request-Id": "trace-workspace-1"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(client_user.id)
    assert "workspace_id" in body
    assert "active_workspace_id" in body
    assert "workspaces" in body
    assert body["request_id"] == "trace-workspace-1"
    assert response.headers.get("x-request-id") == "trace-workspace-1"


def test_web_client_propagates_workspace_header_and_sdk_does_not_use_sessions():
    client_src = WEB_CLIENT.read_text(encoding="utf-8")
    header_src = (
        REPO_ROOT / "apps" / "web" / "lib" / "infrastructure" / "active-workspace.ts"
    ).read_text(encoding="utf-8")
    sdk_src = SDK_HTTP.read_text(encoding="utf-8")
    assert "WORKSPACE_HEADER" in client_src
    assert 'WORKSPACE_HEADER = "X-Workspace-Id"' in header_src
    assert "Authorization" not in client_src
    assert "Cookie" not in sdk_src
    assert "dclab_session" not in sdk_src
    assert "/auth/workspace" not in sdk_src
    assert "X-Workspace-Id" in sdk_src
