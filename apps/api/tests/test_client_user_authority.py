"""A ``client_user`` is an external reader of the shared ML core (founder decision, 2026-10-13).

Its ``business_admin`` membership still opens the workspace for reading and for the legacy
``/app`` Labs trials (``workspace_write``), but grants no ML writes, member administration,
AI-policy approval or service tokens. Real ``business_admin`` accounts keep their authority.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.domain.data_plane import LABS_PROJECT_SLUG
from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    ClientLabUpload,
    ProblemSpec,
    Project,
    ServiceToken,
    User,
    UserRole,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.main import app
from app.services.auth_service import create_access_token, create_user
from app.services.authorization_service import (
    assignable_workspace_roles_for,
    can_approve_ai_policy,
    can_execute_workspace_ml,
    can_manage_workspace_members,
    can_perform_ml_write,
    can_read_workspace,
    can_write_workspace,
    explicit_workspace_role,
    is_ml_write_role,
)
from app.services.project_service import create_project
from app.services.service_token_service import create_service_token
from app.services.workspace_capability_service import (
    APPLICATION_ACCESS,
    BUSINESS_ACCESS,
    DEVELOPMENT_ACCESS,
    WORKSPACE_ADD_MEMBER,
    WORKSPACE_EXECUTE_ML,
    WORKSPACE_MANAGE_MEMBERS,
    WORKSPACE_READ,
    WORKSPACE_WRITE,
    effective_capability_matrix,
)
from app.services.workspace_service import add_workspace_member
from conftest import CLIENT_PASSWORD, browser_login, csrf_headers
from test_v1_contract_conventions import UUID_ZERO, _assert_envelope, _call, _concrete, _operations

WS = DEFAULT_WORKSPACE_ID
PASSWORD = "authority-pass-123"
READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _bearer(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user)}", "X-Workspace-Id": str(WS)}


def _member(db_session, admin_user, role: UserRole) -> User:
    membership = add_workspace_member(
        db_session, actor=admin_user, workspace_id=WS, email=f"{role.value}-{uuid4().hex[:8]}@authority.test",
        password=PASSWORD, role=role.value,
    )
    db_session.commit()
    return db_session.get(User, membership.user_id)


# --- the predicates and the capability matrix -------------------------------------------------


def _assert_external_reader(db_session, person: User) -> None:
    assert can_read_workspace(db_session, person, WS)
    assert can_write_workspace(db_session, person, WS)  # the legacy /app gate (require_client) stays open
    assert not can_perform_ml_write(db_session, person, WS)
    assert not can_execute_workspace_ml(db_session, person, WS)
    assert not can_manage_workspace_members(db_session, person, WS)
    assert not can_approve_ai_policy(db_session, person, WS)
    assert explicit_workspace_role(db_session, person, WS) in (WorkspaceRole.VIEWER, None)
    assert not is_ml_write_role(explicit_workspace_role(db_session, person, WS))
    assert assignable_workspace_roles_for(db_session, person, WS) == frozenset()
    matrix = effective_capability_matrix(db_session, person, WS)
    assert matrix[WORKSPACE_READ] and matrix[WORKSPACE_WRITE] and matrix[APPLICATION_ACCESS]
    assert not matrix[WORKSPACE_EXECUTE_ML]
    assert not matrix[WORKSPACE_MANAGE_MEMBERS] and not matrix[WORKSPACE_ADD_MEMBER]
    assert not matrix[DEVELOPMENT_ACCESS] and not matrix[BUSINESS_ACCESS]


def test_client_user_is_an_external_reader(db_session, client_user):
    _assert_external_reader(db_session, client_user)


def test_client_user_without_a_membership_row_is_an_external_reader_too(db_session, client_user):
    db_session.query(WorkspaceMembership).filter_by(user_id=client_user.id).delete()
    db_session.commit()
    _assert_external_reader(db_session, client_user)
    assert explicit_workspace_role(db_session, client_user, WS) is None


def test_business_admin_accounts_keep_their_authority(db_session):
    admin = create_user(db_session, email="biz-admin@authority.test", password=PASSWORD,
                        role=UserRole.BUSINESS_ADMIN, workspace_id=WS)
    db_session.commit()
    assert can_perform_ml_write(db_session, admin, WS) and can_execute_workspace_ml(db_session, admin, WS)
    assert can_manage_workspace_members(db_session, admin, WS) and can_approve_ai_policy(db_session, admin, WS)
    assert explicit_workspace_role(db_session, admin, WS) is WorkspaceRole.WORKSPACE_ADMIN
    assert effective_capability_matrix(db_session, admin, WS)[WORKSPACE_EXECUTE_ML]


# --- /v1 writes ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/projects"),
        ("POST", "/v1/datasets"),
        ("POST", "/v1/execution-requests"),
        ("POST", "/v1/experiments"),
        ("POST", "/v1/experiments/{experiment_id}/branches"),
        ("POST", "/v1/model-versions/{model_version_id}/predictions"),
        ("PUT", "/v1/projects/{project_id}/scoring-schedule"),
        ("POST", "/v1/projects/{project_id}/decisions"),
        ("POST", "/v1/decisions/{decision_id}/accept"),
        ("POST", "/v1/projects/{project_id}/refs/{ref_kind}"),  # moving the champion = approving a model for scoring
        ("POST", "/v1/proposals/{proposal_id}/accept"),
        ("POST", "/v1/proposals/{proposal_id}/reject"),
        ("POST", "/v1/projects/{project_id}/monitoring/outcomes"),
    ],
)
def test_client_user_cannot_write_the_shared_ml_core(client, client_user, method, path):
    response = _call(client, method, _concrete(path), headers=_bearer(client_user))
    _assert_envelope(response, 403, "forbidden")


def test_client_user_does_what_a_viewer_does_on_every_v1_write(client, db_session, client_user, admin_user):
    """Any mutating /v1 operation a client can reach but a viewer cannot is a new hole: decide it on purpose."""

    viewer = _member(db_session, admin_user, UserRole.VIEWER)
    writes = [(method, path) for method, path in _operations() if method not in READ_METHODS]
    assert len(writes) > 25
    codes = {
        who: {
            f"{method} {path}": _call(client, method, _concrete(path), headers=_bearer(person)).status_code
            for method, path in writes
        }
        for who, person in (("client", client_user), ("viewer", viewer))
    }
    assert codes["client"] == codes["viewer"]
    assert sum(code == 403 for code in codes["client"].values()) > 25


def test_ml_engineer_still_writes(client, db_session, admin_user):
    engineer = _member(db_session, admin_user, UserRole.ML_ENGINEER)
    created = client.post("/v1/projects", json={"name": "Engineer"}, headers={**_bearer(engineer), "Idempotency-Key": f"e-{uuid4().hex}"})
    assert created.status_code == 201, created.text


# --- session-only surfaces: members and service tokens -----------------------------------------------


def test_client_user_cannot_list_or_add_members_or_mint_tokens(client, db_session, client_user):
    people_before = db_session.scalar(select(func.count()).select_from(User))
    with TestClient(app) as browser:
        assert browser_login(browser, client_user.email, CLIENT_PASSWORD).status_code == 200
        browser.headers["X-Workspace-Id"] = str(WS)

        listed = browser.get(f"/v1/workspaces/{WS}/members")
        _assert_envelope(listed, 403, "members_not_permitted")
        assert client_user.email not in listed.text

        added = browser.post(
            f"/workspaces/{WS}/members", headers=csrf_headers(browser),
            json={"email": "planted@authority.test", "password": PASSWORD, "role": "viewer"},
        )
        assert added.status_code == 403, added.text

        minted = browser.post(
            "/v1/service-tokens", headers={**csrf_headers(browser), "Idempotency-Key": f"t-{uuid4().hex}"},
            json={"name": "ci", "scopes": ["read", "projects:write"], "expires_in_days": 30,
                  "current_password": CLIENT_PASSWORD},
        )
        _assert_envelope(minted, 403, "token_creation_not_permitted")
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(User)) == people_before
    assert db_session.scalar(select(func.count()).select_from(ServiceToken)) == 0


def test_a_token_minted_before_the_cap_is_read_only(client, db_session):
    """Tokens a client minted while it still had admin authority keep working for reads, never for writes."""

    creator = create_user(db_session, email="was-engineer@authority.test", password=PASSWORD,
                          role=UserRole.ML_ENGINEER, workspace_id=WS)
    _row, raw = create_service_token(
        db_session, creator=creator, workspace_id=WS, name="old", scopes=["read", "projects:write"],
        expires_in_days=30, current_password=PASSWORD,
    )
    db_session.commit()
    token = {"Authorization": f"Bearer {raw}"}
    assert client.post("/v1/projects", json={"name": "Before"}, headers={**token, "Idempotency-Key": f"b-{uuid4().hex}"}).status_code == 201

    # The creator is (now) a client_user: a core UPDATE, as the ORM path would revoke the token on a role change.
    db_session.execute(update(User).where(User.id == creator.id).values(role=UserRole.CLIENT_USER.value))
    db_session.commit()
    db_session.expire_all()

    assert client.get("/v1/projects", headers=token).status_code == 200
    denied = client.post("/v1/projects", json={"name": "After"}, headers={**token, "Idempotency-Key": f"a-{uuid4().hex}"})
    assert denied.status_code == 403, denied.text
    assert client.get(f"/v1/projects/{UUID_ZERO}", headers=token).status_code == 404  # reads still resolve


# --- what stays open to a client ----------------------------------------------------------------------------


def test_client_user_still_runs_a_labs_trial(auth_client):
    """The legacy /app Labs gate (require_client -> workspace_write) is unchanged."""

    listed = auth_client.get("/app/labs/problems")
    assert listed.status_code == 200, listed.text
    run = auth_client.post("/app/labs/runs", data={"use_case": "cross_sell"})
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "completed"


# --- Client Labs: its own project only, never a shared one ----------------------------------------------------

CSV = b"tenure,spend,churn\n1,10.5,yes\n2,20.0,no\n3,7.25,yes\n4,3.0,no\n5,9.5,yes\n6,1.0,no\n"


def _labs_upload(auth_client, monkeypatch, **form):
    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)
    monkeypatch.setattr("app.services.auto_train_service.enqueue_auto_train", lambda _id: None)
    return auth_client.post(
        "/app/labs/uploads", data={"category": "Revenue", **form}, files={"file": ("rows.csv", CSV, "text/csv")}
    )


def test_client_labs_upload_goes_to_its_own_project(auth_client, db_session, monkeypatch):
    response = _labs_upload(auth_client, monkeypatch)
    assert response.status_code == 200, response.text
    assert db_session.get(ClientLabUpload, response.json()["run_id"]) is not None
    assert db_session.scalars(select(Project.slug)).all() == [LABS_PROJECT_SLUG]  # only the stable Labs project


def test_client_labs_upload_cannot_choose_a_shared_project_or_problem_spec(
    auth_client, engineer_user, db_session, monkeypatch
):
    shared = create_project(db_session, actor=engineer_user, workspace_id=WS, name="Shared", slug="shared-core")
    db_session.commit()
    datasets_before = db_session.scalar(select(func.count()).select_from(ClientLabUpload))

    chosen = _labs_upload(auth_client, monkeypatch, project_id=str(shared.id))
    assert chosen.status_code == 403, chosen.text
    spec_chosen = _labs_upload(auth_client, monkeypatch, problem_spec_id=UUID_ZERO)
    assert spec_chosen.status_code == 403, spec_chosen.text
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(ClientLabUpload)) == datasets_before
    assert db_session.scalar(select(func.count()).select_from(ProblemSpec)) == 0


def test_an_engineer_may_still_choose_a_project_in_labs(engineer_client, engineer_user, db_session, monkeypatch):
    shared = create_project(db_session, actor=engineer_user, workspace_id=WS, name="Shared", slug="shared-core")
    db_session.commit()
    response = _labs_upload(engineer_client, monkeypatch, project_id=str(shared.id))
    assert response.status_code == 200, response.text


# --- identity guards ----------------------------------------------------------------------------------------------


def test_client_user_cannot_create_a_workspace(client, client_user, db_session):
    workspaces_before = db_session.scalar(select(func.count()).select_from(Workspace))
    for kind in ("personal", "business"):
        created = client.post(f"/workspaces/{kind}", json={"name": "Mine"}, headers=_bearer(client_user))
        assert created.status_code == 403, created.text
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Workspace)) == workspaces_before


def test_an_existing_client_account_can_only_be_added_as_a_viewer(db_session, client_user, admin_user):
    other = create_user(db_session, email="other-ws-owner@authority.test", password=PASSWORD, role=UserRole.WORKSPACE_OWNER)
    from app.services.workspace_service import create_business_workspace

    workspace = create_business_workspace(db_session, owner=other, name="Second tenant")
    db_session.commit()
    for role in ("ml_engineer", "workspace_admin"):
        with pytest.raises(Exception, match="external reader"):
            add_workspace_member(
                db_session, actor=admin_user, workspace_id=workspace.id, email=client_user.email, password=PASSWORD, role=role
            )
        db_session.rollback()
    added = add_workspace_member(
        db_session, actor=admin_user, workspace_id=workspace.id, email=client_user.email, password=PASSWORD, role="viewer"
    )
    assert added.role == "viewer"
