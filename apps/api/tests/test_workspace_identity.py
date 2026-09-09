from __future__ import annotations

from uuid import uuid4

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    ProblemSpec,
    User,
    UserRole,
    WorkspaceKind,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.domain.errors import IdentityError, ProjectNotFoundError
from app.services.auth_service import create_access_token, create_user
from app.services.authorization_service import (
    can_manage_workspace_members,
    can_perform_ml_write,
    can_write_platform,
    can_write_workspace,
    canonical_workspace_role,
    consumes_ml_engineer_seat,
)
from app.services.problem_spec_service import create_problem_spec, get_problem_spec
from app.services.project_service import create_project, get_project, list_projects
from app.services.workspace_entitlement_service import (
    max_members_for,
    max_ml_engineer_seats_for,
    member_count,
    technical_seat_count,
)
from app.services.workspace_service import (
    add_workspace_member,
    create_business_workspace,
    create_personal_workspace,
)


def _headers(user, workspace_id=None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {create_access_token(user)}"}
    if workspace_id is not None:
        headers["X-Workspace-Id"] = str(workspace_id)
    return headers


def _owner(db, email_prefix: str, role: UserRole = UserRole.WORKSPACE_OWNER):
    return create_user(
        db,
        email=f"{email_prefix}-{uuid4().hex}@test.invalid",
        password="test-password",
        role=role,
        full_name=email_prefix,
    )


def test_personal_workspace_has_owner_and_max_members_one(db_session):
    owner = _owner(db_session, "personal-owner")
    workspace = create_personal_workspace(db_session, owner=owner, name="Personal Lab")
    db_session.commit()

    assert workspace.kind == WorkspaceKind.PERSONAL.value
    memberships = (
        db_session.query(WorkspaceMembership)
        .filter(WorkspaceMembership.workspace_id == workspace.id)
        .all()
    )
    assert len(memberships) == 1
    assert memberships[0].user_id == owner.id
    assert memberships[0].role == WorkspaceRole.WORKSPACE_OWNER.value
    assert max_members_for(db_session, workspace.id) == 1
    assert max_ml_engineer_seats_for(db_session, workspace.id) == 0
    assert member_count(db_session, workspace.id) == 1

    with pytest.raises(IdentityError) as exceeded:
        add_workspace_member(
            db_session,
            actor=owner,
            workspace_id=workspace.id,
            email=f"extra-{uuid4().hex}@test.invalid",
            password="test-password",
            role=WorkspaceRole.ML_ENGINEER.value,
        )
    assert exceeded.value.status_code == 409


def test_business_workspace_technical_seats_do_not_include_owner_or_admin(
    db_session,
):
    owner = _owner(db_session, "business-owner")
    workspace = create_business_workspace(db_session, owner=owner, name="Acme Analytics")
    db_session.commit()

    assert workspace.kind == WorkspaceKind.BUSINESS.value
    assert max_members_for(db_session, workspace.id) is None
    assert max_ml_engineer_seats_for(db_session, workspace.id) == 5
    assert technical_seat_count(db_session, workspace.id) == 0

    admin = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"biz-admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.WORKSPACE_ADMIN.value,
        full_name="Workspace Admin",
    )
    db_session.commit()
    assert admin.role == WorkspaceRole.WORKSPACE_ADMIN.value
    assert technical_seat_count(db_session, workspace.id) == 0

    admin_user = db_session.get(User, admin.user_id)
    engineer = add_workspace_member(
        db_session,
        actor=admin_user,
        workspace_id=workspace.id,
        email=f"ml-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.ML_ENGINEER.value,
        full_name="ML Engineer",
    )
    db_session.commit()
    assert engineer.role == WorkspaceRole.ML_ENGINEER.value
    assert member_count(db_session, workspace.id) == 3
    assert technical_seat_count(db_session, workspace.id) == 1


def test_legacy_business_admin_can_add_ml_engineer(db_session):
    owner = _owner(db_session, "legacy-owner")
    workspace = create_business_workspace(db_session, owner=owner, name="Legacy Co")
    legacy_admin = create_user(
        db_session,
        email=f"legacy-admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.BUSINESS_ADMIN,
        workspace_id=workspace.id,
    )
    db_session.commit()
    assert canonical_workspace_role(WorkspaceRole.BUSINESS_ADMIN) is WorkspaceRole.WORKSPACE_ADMIN
    membership = add_workspace_member(
        db_session,
        actor=legacy_admin,
        workspace_id=workspace.id,
        email=f"legacy-ml-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.ML_ENGINEER.value,
    )
    db_session.commit()
    assert membership.role == WorkspaceRole.ML_ENGINEER.value


def test_business_owner_admin_and_five_ml_engineers_then_sixth_fails(db_session):
    owner = _owner(db_session, "cap-owner")
    workspace = create_business_workspace(db_session, owner=owner, name="Capped Co")
    add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.WORKSPACE_ADMIN.value,
    )
    for index in range(5):
        add_workspace_member(
            db_session,
            actor=owner,
            workspace_id=workspace.id,
            email=f"seat-{index}-{uuid4().hex}@test.invalid",
            password="test-password",
            role=WorkspaceRole.ML_ENGINEER.value,
        )
    db_session.commit()
    assert member_count(db_session, workspace.id) == 7
    assert technical_seat_count(db_session, workspace.id) == 5
    with pytest.raises(IdentityError) as exceeded:
        add_workspace_member(
            db_session,
            actor=owner,
            workspace_id=workspace.id,
            email=f"overflow-{uuid4().hex}@test.invalid",
            password="test-password",
            role=WorkspaceRole.ML_ENGINEER.value,
        )
    assert exceeded.value.status_code == 409
    assert "ML engineer seat" in str(exceeded.value)


def test_business_viewer_and_admin_do_not_consume_technical_seats(db_session):
    owner = _owner(db_session, "viewer-owner")
    workspace = create_business_workspace(db_session, owner=owner, name="Viewer Co")
    for index in range(5):
        add_workspace_member(
            db_session,
            actor=owner,
            workspace_id=workspace.id,
            email=f"ml-{index}-{uuid4().hex}@test.invalid",
            password="test-password",
            role=WorkspaceRole.ML_ENGINEER.value,
        )
    db_session.commit()
    assert technical_seat_count(db_session, workspace.id) == 5
    viewer = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"viewer-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.VIEWER.value,
    )
    admin = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.WORKSPACE_ADMIN.value,
    )
    db_session.commit()
    assert viewer.role == WorkspaceRole.VIEWER.value
    assert admin.role == WorkspaceRole.WORKSPACE_ADMIN.value
    assert technical_seat_count(db_session, workspace.id) == 5
    with pytest.raises(IdentityError) as exceeded:
        add_workspace_member(
            db_session,
            actor=owner,
            workspace_id=workspace.id,
            email=f"sixth-ml-{uuid4().hex}@test.invalid",
            password="test-password",
            role=WorkspaceRole.ML_ENGINEER.value,
        )
    assert exceeded.value.status_code == 409


def test_concurrent_ml_engineer_adds_cannot_exceed_five_seats(db_session, test_engine):
    owner = _owner(db_session, "race-owner")
    workspace = create_business_workspace(db_session, owner=owner, name="Race Co")
    for index in range(4):
        add_workspace_member(
            db_session,
            actor=owner,
            workspace_id=workspace.id,
            email=f"ml-{index}-{uuid4().hex}@test.invalid",
            password="test-password",
            role=WorkspaceRole.ML_ENGINEER.value,
        )
    db_session.commit()
    assert technical_seat_count(db_session, workspace.id) == 4
    owner_id = owner.id
    workspace_id = workspace.id
    emails = [f"race-{index}-{uuid4().hex}@test.invalid" for index in range(2)]
    SessionLocal = sessionmaker(bind=test_engine)

    def _add(email: str) -> str | int:
        session = SessionLocal()
        try:
            actor = session.get(User, owner_id)
            add_workspace_member(
                session,
                actor=actor,
                workspace_id=workspace_id,
                email=email,
                password="test-password",
                role=WorkspaceRole.ML_ENGINEER.value,
            )
            session.commit()
            return "ok"
        except IdentityError as exc:
            session.rollback()
            return exc.status_code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_add, emails))
    db_session.expire_all()
    assert results.count("ok") == 1
    assert results.count(409) == 1
    assert technical_seat_count(db_session, workspace.id) == 5
    owner = _owner(db_session, "perm-owner")
    workspace = create_business_workspace(db_session, owner=owner, name="Perm Co")
    engineer_membership = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"engineer-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.ML_ENGINEER.value,
    )
    viewer_membership = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"viewer-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.VIEWER.value,
    )
    db_session.commit()
    engineer = db_session.get(User, engineer_membership.user_id)
    viewer = db_session.get(User, viewer_membership.user_id)

    assert can_perform_ml_write(db_session, engineer, workspace.id)
    assert can_write_workspace(db_session, engineer, workspace.id)
    assert not can_manage_workspace_members(db_session, engineer, workspace.id)
    project = create_project(
        db_session, actor=engineer, workspace_id=workspace.id, name="Churn"
    )
    db_session.commit()
    assert project.workspace_id == workspace.id

    assert not can_perform_ml_write(db_session, viewer, workspace.id)
    assert not can_write_workspace(db_session, viewer, workspace.id)
    with pytest.raises(IdentityError) as denied:
        create_project(db_session, actor=viewer, workspace_id=workspace.id, name="Secret")
    assert denied.value.status_code == 403
    with pytest.raises(IdentityError) as member_denied:
        add_workspace_member(
            db_session,
            actor=engineer,
            workspace_id=workspace.id,
            email=f"blocked-{uuid4().hex}@test.invalid",
            password="test-password",
            role=WorkspaceRole.VIEWER.value,
        )
    assert member_denied.value.status_code == 403


def test_legacy_business_developer_stays_read_only_viewer(db_session):
    owner = _owner(db_session, "compat-owner")
    workspace = create_business_workspace(db_session, owner=owner, name="Compat Co")
    developer = create_user(
        db_session,
        email=f"biz-dev-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.BUSINESS_DEVELOPER,
        workspace_id=workspace.id,
    )
    db_session.commit()
    stored = (
        db_session.query(WorkspaceMembership)
        .filter(WorkspaceMembership.user_id == developer.id)
        .one()
    )
    assert stored.role == WorkspaceRole.BUSINESS_DEVELOPER.value
    assert canonical_workspace_role(WorkspaceRole.BUSINESS_DEVELOPER) is WorkspaceRole.VIEWER
    assert not consumes_ml_engineer_seat(WorkspaceRole.BUSINESS_DEVELOPER)
    assert not consumes_ml_engineer_seat(WorkspaceRole.BUSINESS_ADMIN)
    assert consumes_ml_engineer_seat(WorkspaceRole.ML_ENGINEER)
    assert not can_perform_ml_write(db_session, developer, workspace.id)
    assert technical_seat_count(db_session, workspace.id) == 0


def test_platform_roles_remain_internal(db_session, client):
    admin = create_user(
        db_session,
        email=f"platform-admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.DCLAB_ADMIN,
    )
    developer = create_user(
        db_session,
        email=f"platform-dev-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.DCLAB_DEVELOPER,
    )
    db_session.commit()
    assert can_write_platform(db_session, admin)
    assert not can_write_platform(db_session, developer)
    assert client.get("/admin/experiments", headers=_headers(developer)).status_code == 200
    assert (
        client.post("/admin/environments/dogfood", headers=_headers(developer)).status_code
        == 403
    )
    created = client.post(
        "/workspaces/personal",
        headers=_headers(developer),
        json={"name": "Developer should not create"},
    )
    assert created.status_code == 403


def test_workspace_admin_cannot_create_workspace_owner(db_session, client):
    owner = _owner(db_session, "esc-owner")
    workspace = create_business_workspace(db_session, owner=owner, name="Escalation Co")
    admin = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"esc-admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.WORKSPACE_ADMIN.value,
    )
    db_session.commit()
    admin_user = db_session.get(User, admin.user_id)
    with pytest.raises(IdentityError) as denied:
        add_workspace_member(
            db_session,
            actor=admin_user,
            workspace_id=workspace.id,
            email=f"new-owner-{uuid4().hex}@test.invalid",
            password="test-password",
            role=WorkspaceRole.WORKSPACE_OWNER.value,
        )
    assert denied.value.status_code == 403
    assert "workspace_owner" in str(denied.value)
    http_denied = client.post(
        f"/workspaces/{workspace.id}/members",
        headers=_headers(admin_user, workspace.id),
        json={
            "email": f"http-owner-{uuid4().hex}@test.invalid",
            "password": "test-password",
            "role": WorkspaceRole.WORKSPACE_OWNER.value,
        },
    )
    assert http_denied.status_code == 403, http_denied.text


def test_workspace_admin_can_add_ml_engineer(db_session):
    owner = _owner(db_session, "admin-ml-owner")
    workspace = create_business_workspace(db_session, owner=owner, name="Admin ML Co")
    admin = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.WORKSPACE_ADMIN.value,
    )
    db_session.commit()
    admin_user = db_session.get(User, admin.user_id)
    engineer = add_workspace_member(
        db_session,
        actor=admin_user,
        workspace_id=workspace.id,
        email=f"ml-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.ML_ENGINEER.value,
    )
    db_session.commit()
    assert engineer.role == WorkspaceRole.ML_ENGINEER.value


def test_workspace_owner_can_add_workspace_admin(db_session):
    owner = _owner(db_session, "owner-admin")
    workspace = create_business_workspace(db_session, owner=owner, name="Owner Admin Co")
    admin = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.WORKSPACE_ADMIN.value,
    )
    db_session.commit()
    assert admin.role == WorkspaceRole.WORKSPACE_ADMIN.value


def test_ml_engineer_and_viewer_cannot_manage_memberships(db_session):
    owner = _owner(db_session, "deny-owner")
    workspace = create_business_workspace(db_session, owner=owner, name="Deny Co")
    engineer = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"ml-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.ML_ENGINEER.value,
    )
    viewer = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"viewer-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.VIEWER.value,
    )
    db_session.commit()
    engineer_user = db_session.get(User, engineer.user_id)
    viewer_user = db_session.get(User, viewer.user_id)
    assert not can_manage_workspace_members(db_session, engineer_user, workspace.id)
    assert not can_manage_workspace_members(db_session, viewer_user, workspace.id)
    with pytest.raises(IdentityError) as engineer_denied:
        add_workspace_member(
            db_session,
            actor=engineer_user,
            workspace_id=workspace.id,
            email=f"blocked-ml-{uuid4().hex}@test.invalid",
            password="test-password",
            role=WorkspaceRole.VIEWER.value,
        )
    assert engineer_denied.value.status_code == 403
    with pytest.raises(IdentityError) as viewer_denied:
        add_workspace_member(
            db_session,
            actor=viewer_user,
            workspace_id=workspace.id,
            email=f"blocked-viewer-{uuid4().hex}@test.invalid",
            password="test-password",
            role=WorkspaceRole.ML_ENGINEER.value,
        )
    assert viewer_denied.value.status_code == 403


def test_legacy_roles_cannot_be_assigned_as_membership_targets(db_session, client):
    owner = _owner(db_session, "legacy-target-owner")
    workspace = create_business_workspace(db_session, owner=owner, name="Legacy Target Co")
    db_session.commit()
    for legacy_role in (
        WorkspaceRole.BUSINESS_ADMIN.value,
        WorkspaceRole.BUSINESS_DEVELOPER.value,
    ):
        with pytest.raises(IdentityError) as denied:
            add_workspace_member(
                db_session,
                actor=owner,
                workspace_id=workspace.id,
                email=f"{legacy_role}-{uuid4().hex}@test.invalid",
                password="test-password",
                role=legacy_role,
            )
        assert denied.value.status_code == 400
        http_denied = client.post(
            f"/workspaces/{workspace.id}/members",
            headers=_headers(owner, workspace.id),
            json={
                "email": f"http-{legacy_role}-{uuid4().hex}@test.invalid",
                "password": "test-password",
                "role": legacy_role,
            },
        )
        assert http_denied.status_code == 400, http_denied.text
    stored = (
        db_session.query(WorkspaceMembership)
        .filter(WorkspaceMembership.workspace_id == workspace.id)
        .all()
    )
    assert {row.role for row in stored} == {WorkspaceRole.WORKSPACE_OWNER.value}


def test_dclab_developer_cannot_add_workspace_members(db_session, client):
    owner = _owner(db_session, "dev-mem-owner")
    workspace = create_business_workspace(db_session, owner=owner, name="Dev Members Co")
    developer = create_user(
        db_session,
        email=f"platform-dev-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.DCLAB_DEVELOPER,
    )
    db_session.commit()
    assert not can_write_platform(db_session, developer)
    assert not can_manage_workspace_members(db_session, developer, workspace.id)
    with pytest.raises(IdentityError) as denied:
        add_workspace_member(
            db_session,
            actor=developer,
            workspace_id=workspace.id,
            email=f"dev-add-{uuid4().hex}@test.invalid",
            password="test-password",
            role=WorkspaceRole.ML_ENGINEER.value,
        )
    assert denied.value.status_code == 403
    http_denied = client.post(
        f"/workspaces/{workspace.id}/members",
        headers=_headers(developer, workspace.id),
        json={
            "email": f"http-dev-{uuid4().hex}@test.invalid",
            "password": "test-password",
            "role": WorkspaceRole.ML_ENGINEER.value,
        },
    )
    assert http_denied.status_code == 403, http_denied.text


def test_cross_workspace_project_access_rejected(db_session):
    owner_a = _owner(db_session, "alpha-owner")
    owner_b = _owner(db_session, "beta-owner")
    workspace_a = create_business_workspace(db_session, owner=owner_a, name="Alpha")
    workspace_b = create_business_workspace(db_session, owner=owner_b, name="Beta")
    project = create_project(
        db_session, actor=owner_a, workspace_id=workspace_a.id, name="Alpha project"
    )
    db_session.commit()

    with pytest.raises(IdentityError) as denied:
        get_project(
            db_session,
            actor=owner_b,
            workspace_id=workspace_a.id,
            project_id=project.id,
        )
    assert denied.value.status_code == 403
    with pytest.raises(ProjectNotFoundError):
        get_project(
            db_session,
            actor=owner_b,
            workspace_id=workspace_b.id,
            project_id=project.id,
        )
    assert list_projects(db_session, actor=owner_b, workspace_id=workspace_b.id) == []


def test_cross_workspace_problem_spec_link_is_rejected(db_session):
    owner_a = _owner(db_session, "spec-a")
    owner_b = _owner(db_session, "spec-b")
    workspace_a = create_business_workspace(db_session, owner=owner_a, name="Spec Alpha")
    workspace_b = create_business_workspace(db_session, owner=owner_b, name="Spec Beta")
    project = create_project(
        db_session, actor=owner_a, workspace_id=workspace_a.id, name="Alpha case"
    )
    db_session.commit()

    with pytest.raises(IdentityError) as denied:
        create_problem_spec(
            db_session,
            actor=owner_a,
            workspace_id=workspace_b.id,
            project_id=project.id,
            task_type="classification",
            business_objective="Should not attach to the other tenant",
        )
    assert denied.value.status_code == 403

    spec = ProblemSpec(
        workspace_id=workspace_b.id,
        project_id=project.id,
        version=1,
        task_type="classification",
        business_objective="cross tenant",
        constraints={},
        success_criteria={},
        status="draft",
        content_digest="0" * 64,
        created_by=owner_a.id,
    )
    db_session.add(spec)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_problem_spec_create_and_read_stay_on_project_tenant(db_session):
    owner = _owner(db_session, "spec-owner")
    workspace = create_business_workspace(db_session, owner=owner, name="Intent Co")
    project = create_project(
        db_session, actor=owner, workspace_id=workspace.id, name="Retention"
    )
    spec = create_problem_spec(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        project_id=project.id,
        task_type="classification",
        business_objective="Predict churn risk for existing accounts",
        target_column="churned",
        primary_metric="roc_auc",
        status="locked",
    )
    db_session.commit()
    loaded = get_problem_spec(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        project_id=project.id,
        spec_id=spec.id,
    )
    assert loaded.version == 1
    assert loaded.workspace_id == workspace.id
    assert loaded.project_id == project.id
    assert loaded.status == "locked"
    assert loaded.locked_at is not None
    assert loaded.content_digest


def test_workspace_project_problem_spec_http_routes(client, db_session):
    owner = _owner(db_session, "http-owner")
    db_session.commit()
    created = client.post(
        "/workspaces/business",
        headers=_headers(owner),
        json={"name": "HTTP Business"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["kind"] == "business"
    assert body["max_members"] is None
    assert body["max_ml_engineer_seats"] == 5
    workspace_id = body["id"]

    project_response = client.post(
        f"/workspaces/{workspace_id}/projects",
        headers=_headers(owner),
        json={"name": "Forecasting case", "description": "Monthly demand"},
    )
    assert project_response.status_code == 200, project_response.text
    project_id = project_response.json()["id"]
    listed = client.get(
        f"/workspaces/{workspace_id}/projects",
        headers=_headers(owner),
    )
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [project_id]
    fetched = client.get(
        f"/workspaces/{workspace_id}/projects/{project_id}",
        headers=_headers(owner),
    )
    assert fetched.status_code == 200
    spec_response = client.post(
        f"/workspaces/{workspace_id}/projects/{project_id}/problem-specs",
        headers=_headers(owner),
        json={
            "task_type": "forecasting",
            "business_objective": "Forecast weekly demand",
            "prediction_horizon": "7d",
        },
    )
    assert spec_response.status_code == 200, spec_response.text
    spec_id = spec_response.json()["id"]
    read_spec = client.get(
        f"/workspaces/{workspace_id}/projects/{project_id}/problem-specs/{spec_id}",
        headers=_headers(owner),
    )
    assert read_spec.status_code == 200
    assert read_spec.json()["task_type"] == "forecasting"


def test_labs_adapter_creates_one_workflow_per_project(db_session):
    from app.services.lineage_service import get_or_create_labs_workflow

    owner = create_user(
        db_session,
        email=f"labs-wf-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
    )
    workspace = create_personal_workspace(db_session, owner=owner, name="Multi-case Lab")
    first = create_project(
        db_session, actor=owner, workspace_id=workspace.id, name="Case One"
    )
    second = create_project(
        db_session, actor=owner, workspace_id=workspace.id, name="Case Two"
    )
    workflow_one = get_or_create_labs_workflow(
        db_session, workspace_id=workspace.id, actor=owner, project=first
    )
    workflow_two = get_or_create_labs_workflow(
        db_session, workspace_id=workspace.id, actor=owner, project=second
    )
    assert workflow_one.id != workflow_two.id
    assert workflow_one.project_id == first.id
    assert workflow_two.project_id == second.id
    assert workflow_one.slug == "client-lab-analysis"
    assert workflow_two.slug == f"client-lab-analysis-{second.slug}"
