"""S0-P03B: current server state is the only capability authority."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.db.models import User, UserRole, WorkspaceMembership, WorkspaceRole
from app.services.auth_service import create_access_token, create_user
from app.services.workspace_capability_service import (
    BUSINESS_ACCESS,
    CAPABILITY_MATRIX_VERSION,
    PIPELINE_MONITOR,
    WORKSPACE_ADD_ML_ENGINEER,
    WORKSPACE_MANAGE_MEMBERS,
    WORKSPACE_WRITE,
    effective_capability_matrix,
    set_workspace_capability,
)
from app.services.workspace_entitlement_service import (
    MAX_ML_ENGINEER_SEATS,
    set_entitlement,
)
from app.services.workspace_service import add_workspace_member, create_business_workspace


def _headers(user: User, workspace_id) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user)}",
        "X-Workspace-Id": str(workspace_id),
    }


def _workspace_with_owner(db_session):
    owner = create_user(
        db_session,
        email=f"owner-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
    )
    workspace = create_business_workspace(
        db_session, owner=owner, name=f"Capability {uuid4().hex[:8]}"
    )
    db_session.commit()
    return owner, workspace


def test_v1_me_returns_versioned_display_capabilities(client, db_session):
    owner, workspace = _workspace_with_owner(db_session)

    response = client.get("/v1/me", headers=_headers(owner, workspace.id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["capability_matrix_version"] == CAPABILITY_MATRIX_VERSION
    assert body["capabilities"][WORKSPACE_WRITE] is True
    assert body["capabilities"][WORKSPACE_MANAGE_MEMBERS] is True
    assert body["capabilities"][PIPELINE_MONITOR] is False
    assert "access_token" not in body


def test_legacy_client_user_cannot_call_business_admin_directly(client, client_user):
    headers = {"Authorization": f"Bearer {create_access_token(client_user)}"}
    me = client.get("/v1/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["capabilities"][BUSINESS_ACCESS] is False
    assert client.get("/business/workspaces", headers=headers).status_code == 403


def test_feature_and_entitlement_changes_invalidate_same_transaction_cache(
    db_session,
):
    owner, workspace = _workspace_with_owner(db_session)
    before = effective_capability_matrix(db_session, owner, workspace.id)
    assert before[PIPELINE_MONITOR] is False
    assert before[WORKSPACE_ADD_ML_ENGINEER] is True

    set_workspace_capability(
        db_session,
        workspace.id,
        PIPELINE_MONITOR,
        True,
        configuration={"source": "test"},
    )
    set_entitlement(
        db_session,
        workspace.id,
        MAX_ML_ENGINEER_SEATS,
        0,
        source="test",
    )
    after = effective_capability_matrix(db_session, owner, workspace.id)
    assert after[PIPELINE_MONITOR] is True
    assert after[WORKSPACE_ADD_ML_ENGINEER] is False


def test_membership_role_change_replaces_stale_token_role(client, db_session):
    owner, workspace = _workspace_with_owner(db_session)
    membership = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"viewer-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.VIEWER.value,
    )
    db_session.commit()
    viewer = db_session.get(User, membership.user_id)
    headers = _headers(viewer, workspace.id)

    first = client.get("/v1/me", headers=headers)
    assert first.status_code == 200
    assert first.json()["capabilities"][WORKSPACE_WRITE] is False
    denied = client.post(
        "/v1/execution-requests",
        headers=headers,
        json={"operation": "model_build", "request_spec": {}},
    )
    assert denied.status_code == 403

    # The bearer was issued while the display role was viewer. Current
    # membership, not the role claim in that token, changes authority.
    membership.role = WorkspaceRole.WORKSPACE_ADMIN.value
    db_session.commit()
    promoted = client.get("/v1/me", headers=headers)
    assert promoted.status_code == 200
    assert promoted.json()["capabilities"][WORKSPACE_WRITE] is True
    assert promoted.json()["capabilities"][WORKSPACE_MANAGE_MEMBERS] is True


def test_role_change_invalidates_cached_capabilities_before_commit(db_session):
    owner, workspace = _workspace_with_owner(db_session)
    membership = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"viewer-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.VIEWER.value,
    )
    db_session.commit()
    viewer = db_session.get(User, membership.user_id)
    assert effective_capability_matrix(db_session, viewer, workspace.id)[WORKSPACE_WRITE] is False

    membership.role = WorkspaceRole.WORKSPACE_ADMIN.value
    db_session.flush()
    assert effective_capability_matrix(db_session, viewer, workspace.id)[WORKSPACE_WRITE] is True

    membership.suspended_at = datetime.now(UTC)
    db_session.flush()
    assert effective_capability_matrix(db_session, viewer, workspace.id)[WORKSPACE_WRITE] is False


def test_suspension_removes_navigation_hints_and_direct_api_access(client, db_session):
    owner, workspace = _workspace_with_owner(db_session)
    membership = (
        db_session.query(WorkspaceMembership)
        .filter(
            WorkspaceMembership.user_id == owner.id,
            WorkspaceMembership.workspace_id == workspace.id,
        )
        .one()
    )
    headers = _headers(owner, workspace.id)
    assert client.get("/v1/projects", headers=headers).status_code == 200

    membership.suspended_at = datetime.now(UTC)
    db_session.commit()
    me = client.get("/v1/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["active_workspace_id"] is None
    assert me.json()["capabilities"][WORKSPACE_WRITE] is False
    assert client.get("/v1/projects", headers=headers).status_code == 403
    assert client.get("/business/workspaces", headers=headers).status_code == 403
