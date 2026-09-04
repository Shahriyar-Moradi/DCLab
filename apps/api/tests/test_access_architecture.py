from __future__ import annotations

import re
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from app.api.deps import require_admin, require_client
from app.db.models import (
    BusinessProfile,
    Opportunity,
    PlatformMembership,
    UserRole,
    Workspace,
    WorkspaceCapability,
    WorkspaceMembership,
)
from app.services.auth_service import create_access_token, create_user
from app.services.authorization_service import can_write_platform, can_write_workspace
from app.main import app


def _unsafe_operations(prefix: str) -> list[tuple[str, str]]:
    operations: list[tuple[str, str]] = []
    for path, definitions in app.openapi()["paths"].items():
        if not path.startswith(prefix):
            continue
        concrete = re.sub(
            r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", path
        )
        operations.extend(
            (method.upper(), concrete)
            for method in definitions
            if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
        )
    return operations


def _headers(user, workspace_id=None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {create_access_token(user)}"}
    if workspace_id is not None:
        headers["X-Workspace-Id"] = str(workspace_id)
    return headers


def _request(method: str, workspace_id=None) -> Request:
    headers = []
    if workspace_id is not None:
        headers.append((b"x-workspace-id", str(workspace_id).encode()))
    return Request({"type": "http", "method": method, "headers": headers})


@pytest.fixture()
def access_setup(db_session):
    alpha = Workspace(slug=f"alpha-{uuid4().hex}", name="Alpha LLC")
    beta = Workspace(slug=f"beta-{uuid4().hex}", name="Beta LLC")
    db_session.add_all([alpha, beta])
    db_session.flush()
    db_session.add_all(
        [
            BusinessProfile(workspace_id=alpha.id, legal_name="Alpha LLC", profile_data={}),
            BusinessProfile(workspace_id=beta.id, legal_name="Beta LLC", profile_data={}),
            WorkspaceCapability(
                workspace_id=alpha.id,
                capability="labs",
                enabled=True,
                configuration={"tier": "standard"},
            ),
        ]
    )
    platform_admin = create_user(
        db_session,
        email=f"platform-admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.DCLAB_ADMIN,
    )
    platform_developer = create_user(
        db_session,
        email=f"platform-developer-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.DCLAB_DEVELOPER,
    )
    business_admin = create_user(
        db_session,
        email=f"business-admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.BUSINESS_ADMIN,
        workspace_id=alpha.id,
    )
    business_developer = create_user(
        db_session,
        email=f"business-developer-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.BUSINESS_DEVELOPER,
        workspace_id=alpha.id,
    )
    alpha_opportunity = Opportunity(
        workspace_id=alpha.id,
        external_id="shared-business-id",
        customer_id="alpha-customer",
        amount=100,
        currency="AED",
        stage="new",
        source="test",
        owner_id="alpha-owner",
    )
    beta_opportunity = Opportunity(
        workspace_id=beta.id,
        external_id="shared-business-id",
        customer_id="beta-customer",
        amount=200,
        currency="AED",
        stage="new",
        source="test",
        owner_id="beta-owner",
    )
    db_session.add_all([alpha_opportunity, beta_opportunity])
    db_session.commit()
    return {
        "alpha": alpha,
        "beta": beta,
        "platform_admin": platform_admin,
        "platform_developer": platform_developer,
        "business_admin": business_admin,
        "business_developer": business_developer,
        "alpha_opportunity": alpha_opportunity,
        "beta_opportunity": beta_opportunity,
    }


def test_memberships_and_business_metadata_are_persisted(db_session, access_setup):
    setup = access_setup
    assert db_session.scalar(
        select(PlatformMembership).where(
            PlatformMembership.user_id == setup["platform_admin"].id
        )
    ).role == "dclab_admin"
    assert db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.user_id == setup["business_developer"].id
        )
    ).role == "business_developer"
    assert setup["alpha"].business_profile.legal_name == "Alpha LLC"
    assert setup["alpha"].capabilities[0].capability == "labs"


def test_business_memberships_are_isolated_by_backend_queries(client, access_setup):
    setup = access_setup
    own = client.get(
        "/app/opportunities",
        headers=_headers(setup["business_admin"], setup["alpha"].id),
    )
    assert own.status_code == 200
    assert [row["customer_id"] for row in own.json()["items"]] == ["alpha-customer"]

    foreign_selector = client.get(
        "/app/opportunities",
        headers=_headers(setup["business_admin"], setup["beta"].id),
    )
    assert foreign_selector.status_code == 403

    foreign_object = client.get(
        f"/app/opportunities/{setup['beta_opportunity'].id}",
        headers=_headers(setup["business_admin"], setup["alpha"].id),
    )
    assert foreign_object.status_code == 404


def test_platform_admin_can_read_and_write_across_tenants(client, access_setup):
    setup = access_setup
    for workspace, customer_id in (
        (setup["alpha"], "alpha-customer"),
        (setup["beta"], "beta-customer"),
    ):
        response = client.get(
            "/app/opportunities",
            headers=_headers(setup["platform_admin"], workspace.id),
        )
        assert response.status_code == 200
        assert [row["customer_id"] for row in response.json()["items"]] == [customer_id]

    created = client.post(
        "/app/opportunities/upload",
        headers=_headers(setup["platform_admin"], setup["beta"].id),
        files={
            "file": (
                "opportunities.csv",
                b"external_id,customer_id,amount,stage,source,owner_id\n"
                b"beta-new,beta-new-customer,50,new,test,owner\n",
                "text/csv",
            )
        },
    )
    assert created.status_code == 200
    assert created.json()["inserted"] == 1


def test_platform_developer_has_visibility_but_no_writes(client, db_session, access_setup):
    setup = access_setup
    developer = setup["platform_developer"]
    assert client.get("/admin/experiments", headers=_headers(developer)).status_code == 200
    assert (
        client.get(
            "/app/opportunities",
            headers=_headers(developer, setup["beta"].id),
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/admin/environments/dogfood", headers=_headers(developer)
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/admin/lab/runs/{uuid4()}/verification/deep",
            headers=_headers(developer),
        ).status_code
        == 403
    )
    assert not can_write_platform(db_session, developer)
    assert not can_write_workspace(db_session, developer, setup["alpha"].id)


def test_business_admin_can_write_only_its_workspace(client, access_setup):
    setup = access_setup
    response = client.post(
        "/app/opportunities/upload",
        headers=_headers(setup["business_admin"], setup["alpha"].id),
        files={
            "file": (
                "opportunities.csv",
                b"external_id,customer_id,amount,stage,source,owner_id\n"
                b"alpha-new,alpha-new-customer,75,new,test,owner\n",
                "text/csv",
            )
        },
    )
    assert response.status_code == 200
    assert response.json()["inserted"] == 1
    assert (
        client.get(
            "/admin/experiments", headers=_headers(setup["business_admin"])
        ).status_code
        == 403
    )


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_readonly_roles_are_rejected_for_every_unsafe_verb(
    db_session, access_setup, method
):
    setup = access_setup
    with pytest.raises(HTTPException) as platform_denied:
        require_admin(_request(method), setup["platform_developer"], db_session)
    assert platform_denied.value.status_code == 403

    with pytest.raises(HTTPException) as business_denied:
        require_client(
            _request(method, setup["alpha"].id),
            setup["business_developer"],
            db_session,
        )
    assert business_denied.value.status_code == 403


def test_business_developer_reads_own_workspace_but_cannot_post(client, access_setup):
    setup = access_setup
    developer = setup["business_developer"]
    assert (
        client.get(
            "/app/opportunities",
            headers=_headers(developer, setup["alpha"].id),
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/app/decisions/generate",
            headers=_headers(developer, setup["alpha"].id),
            json={"generate_all": True},
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/app/opportunities",
            headers=_headers(developer, setup["beta"].id),
        ).status_code
        == 403
    )


def test_developer_roles_are_blocked_from_every_live_mutation(client, access_setup):
    setup = access_setup
    for method, path in _unsafe_operations("/admin"):
        response = client.request(
            method,
            path,
            headers=_headers(setup["platform_developer"]),
            json={},
        )
        assert response.status_code == 403, f"{method} {path} was not read-only"

    for method, path in _unsafe_operations("/app"):
        response = client.request(
            method,
            path,
            headers=_headers(setup["business_developer"], setup["alpha"].id),
            json={},
        )
        assert response.status_code == 403, f"{method} {path} was not read-only"

    for method, path in _unsafe_operations("/business"):
        response = client.request(
            method,
            path,
            headers=_headers(setup["business_developer"], setup["alpha"].id),
            json={},
        )
        assert response.status_code in {403, 404}, (
            f"{method} {path} was not read-only ({response.status_code})"
        )


def test_explicit_membership_overrides_legacy_role_string(db_session, access_setup):
    user = access_setup["platform_admin"]
    membership = db_session.scalar(
        select(PlatformMembership).where(PlatformMembership.user_id == user.id)
    )
    membership.role = "dclab_developer"
    db_session.commit()
    assert user.role == "dclab_admin"
    assert not can_write_platform(db_session, user)


def test_legacy_client_without_membership_remains_functional(db_session, access_setup):
    user = create_user(
        db_session,
        email=f"legacy-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.CLIENT_USER,
        workspace_id=access_setup["alpha"].id,
    )
    membership = db_session.scalar(
        select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
    )
    db_session.delete(membership)
    db_session.commit()
    assert can_write_workspace(db_session, user, access_setup["alpha"].id)
    assert not can_write_workspace(db_session, user, access_setup["beta"].id)
