"""V7-B6: GET /v1/workspaces/{id}/members: administrators of that workspace only, people only."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.models import User, UserRole, Workspace, WorkspaceMembership
from app.domain.service_tokens import HUMAN_ONLY_ROUTES
from app.main import app
from app.services import workspace_service
from app.services.auth_service import create_user
from conftest import browser_login

PASSWORD = "test-password-123"
ROUTE = "/v1/workspaces/{}/members"
FIELDS = {"full_name", "email", "role", "joined_at", "is_you", "legacy_access"}


@pytest.fixture
def ws(db_session):
    db = db_session
    alpha = Workspace(slug=f"wm-a-{uuid4().hex[:8]}", name="WM Alpha")
    beta = Workspace(slug=f"wm-b-{uuid4().hex[:8]}", name="WM Beta")
    db.add_all([alpha, beta])
    db.flush()

    def user(tag, role, workspace, name=""):
        return create_user(db, email=f"wm-{tag}-{uuid4().hex[:6]}@test.invalid", password=PASSWORD,
                           role=role, workspace_id=workspace.id, full_name=name)

    s = SimpleNamespace(
        alpha=alpha, beta=beta,
        owner=user("owner", UserRole.WORKSPACE_OWNER, alpha, "Olive Owner"),
        admin=user("admin", UserRole.BUSINESS_ADMIN, alpha, "Ada Admin"),  # legacy role -> workspace_admin
        engineer=user("eng", UserRole.ML_ENGINEER, alpha, "Eli Engineer"),
        viewer=user("viewer", UserRole.VIEWER, alpha, "Vi Viewer"),
        beta_admin=user("beta", UserRole.BUSINESS_ADMIN, beta, "Bea Beta"),
        platform=create_user(db, email=f"wm-platform-{uuid4().hex[:6]}@test.invalid", password=PASSWORD,
                             role=UserRole.DCLAB_ADMIN, workspace_id=None),
    )
    db.commit()
    return s


def _get(user, workspace, path_workspace=None, headers=None):
    with TestClient(app) as browser:
        assert browser_login(browser, user.email, PASSWORD).status_code == 200
        browser.headers["X-Workspace-Id"] = str(workspace.id)
        return browser.get(ROUTE.format((path_workspace or workspace).id), headers=headers or {})


def test_admin_lists_members_with_only_the_listed_fields(client, ws):
    res = _get(ws.admin, ws.alpha)
    assert res.status_code == 200, res.text
    assert res.headers["Cache-Control"] == "private, no-store"
    assert "Authorization" in res.headers["Vary"] and "Cookie" in res.headers["Vary"]
    body = res.json()
    assert set(body) == {"members", "more", "roles"} and body["more"] is False
    assert all(set(m) == FIELDS for m in body["members"])
    by_email = {m["email"]: m for m in body["members"]}
    assert {e: m["role"] for e, m in by_email.items()} == {
        ws.owner.email: "workspace_owner", ws.admin.email: "workspace_admin",  # legacy business_admin shown canonically
        ws.engineer.email: "ml_engineer", ws.viewer.email: "viewer"}
    assert [e for e, m in by_email.items() if m["is_you"]] == [ws.admin.email]
    assert by_email[ws.admin.email]["full_name"] == "Ada Admin"
    assert {m["email"] for m in body["members"]} == {ws.owner.email, ws.admin.email, ws.engineer.email, ws.viewer.email}
    assert ws.beta_admin.email not in res.text and "password" not in res.text.lower() and "$" not in res.text
    roles = {r["role"]: r for r in body["roles"]}
    assert roles["viewer"]["can_run_ml"] is False and roles["viewer"]["can_manage_members"] is False
    assert roles["ml_engineer"]["can_run_ml"] is True and roles["ml_engineer"]["can_manage_members"] is False
    assert roles["workspace_admin"]["can_manage_members"] is True and roles["workspace_owner"]["can_manage_members"] is True
    assert _get(ws.admin, ws.alpha).json() == body  # stable order


def test_owner_and_platform_admin_may_list(client, ws):
    assert _get(ws.owner, ws.alpha).status_code == 200
    assert _get(ws.platform, ws.alpha).status_code == 200


@pytest.mark.parametrize("who", ["engineer", "viewer"])
def test_non_administrators_get_a_stable_403(client, ws, who):
    res = _get(getattr(ws, who), ws.alpha)
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "members_not_permitted"
    assert "@test.invalid" not in res.text


def test_other_workspace_is_not_found_or_forbidden_and_leaks_nothing(client, ws):
    mismatched = _get(ws.admin, ws.alpha, path_workspace=ws.beta)
    assert mismatched.status_code == 404 and ws.beta_admin.email not in mismatched.text
    foreign = _get(ws.beta_admin, ws.alpha)  # selecting a workspace one is not in
    assert foreign.status_code == 403 and ws.admin.email not in foreign.text


def test_service_tokens_and_any_bearer_are_refused(client, db_session, ws):
    from app.services.service_token_service import create_service_token

    _row, raw = create_service_token(db_session, creator=ws.admin, workspace_id=ws.alpha.id, name="t",
                                     scopes=("read",), expires_in_days=30, current_password=PASSWORD)
    db_session.commit()
    res = client.get(ROUTE.format(ws.alpha.id), headers={"Authorization": f"Bearer {raw}"})
    assert res.status_code == 403 and "@test.invalid" not in res.text
    assert ("GET", "/v1/workspaces/{workspace_id}/members") in HUMAN_ONLY_ROUTES
    assert client.get(ROUTE.format(ws.alpha.id)).status_code in (401, 403)  # anonymous


def test_suspended_and_inactive_people_are_not_listed(client, db_session, ws):
    from datetime import UTC, datetime

    membership = db_session.query(WorkspaceMembership).filter_by(user_id=ws.viewer.id).one()
    membership.suspended_at = datetime.now(UTC)
    db_session.get(User, ws.engineer.id).is_active = False
    db_session.commit()
    emails = {m["email"] for m in _get(ws.admin, ws.alpha).json()["members"]}
    assert emails == {ws.owner.email, ws.admin.email}


def test_list_is_capped_with_a_more_flag(db_session, ws, monkeypatch):
    monkeypatch.setattr(workspace_service, "MEMBER_LIST_CAP", 2)
    out = workspace_service.list_workspace_members(db_session, actor=ws.admin, workspace_id=ws.alpha.id)
    assert len(out.members) == 2 and out.more is True


def test_a_suspended_administrator_is_refused(client, db_session, ws):
    from datetime import UTC, datetime

    with TestClient(app) as browser:
        assert browser_login(browser, ws.admin.email, PASSWORD).status_code == 200
        browser.headers["X-Workspace-Id"] = str(ws.alpha.id)
        db_session.query(WorkspaceMembership).filter_by(user_id=ws.admin.id).one().suspended_at = datetime.now(UTC)
        db_session.commit()
        res = browser.get(ROUTE.format(ws.alpha.id))
    assert res.status_code in (403, 404) and ws.owner.email not in res.text


def test_a_user_bearer_is_session_required(client, ws):
    from app.services.auth_service import create_access_token

    res = client.get(ROUTE.format(ws.alpha.id), headers={
        "Authorization": f"Bearer {create_access_token(ws.admin)}", "X-Workspace-Id": str(ws.alpha.id)})
    assert res.status_code == 403 and res.json()["error"]["code"] == "session_required"
    assert ws.owner.email not in res.text


def test_create_user_always_writes_a_membership(db_session, ws):
    for person in (ws.owner, ws.admin, ws.engineer, ws.viewer):
        assert db_session.query(WorkspaceMembership).filter_by(user_id=person.id).count() == 1


def test_legacy_client_without_membership_is_listed_and_marked(client, db_session, ws):
    legacy = create_user(db_session, email=f"wm-legacy-{uuid4().hex[:6]}@test.invalid", password=PASSWORD,
                         role=UserRole.CLIENT_USER, workspace_id=ws.alpha.id, full_name="Lee Legacy")
    db_session.query(WorkspaceMembership).filter_by(user_id=legacy.id).delete()
    db_session.commit()
    members = {m["email"]: m for m in _get(ws.admin, ws.alpha).json()["members"]}
    assert members[legacy.email]["legacy_access"] is True and members[legacy.email]["role"] == "workspace_admin"
    assert members[ws.admin.email]["legacy_access"] is False
