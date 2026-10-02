"""P3.2-A service tokens: lifecycle, bearer resolution and adversarial cases (wrong
workspace, revoked, expired, scope escalation, cookie paths, agent actor, re-auth,
revocation on credential/authority changes)."""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import get_settings
from app.db.models import IdempotencyKey, Project, ServiceToken, UserRole, Workspace, WorkspaceMembership
from app.domain.decision_records import DecisionActor
from app.domain.service_tokens import HUMAN_ONLY_ROUTES, TOKEN_ROUTE_SCOPES
from app.main import app
from app.services import decision_record_service as drs
from app.services.auth_hashing import token_hash
from app.services.auth_service import create_access_token, create_user
from app.services.project_service import create_project
from app.services.recovery_service import PURPOSE_PASSWORD_RESET, confirm_password_reset, issue_recovery_token
from app.services.service_token_service import (
    create_service_token,
    request_token_bound,
    revoke_service_token,
    revoke_tokens_created_by,
    utcnow,
    verify_agent_binding,
)
from conftest import browser_login, csrf_headers
from test_v1_contract_conventions import _assert_envelope, _operations

PASSWORD = "test-password-123"
SECRET = re.compile(r"^dclab_st_[0-9a-f]{32}_[A-Za-z0-9_-]{43}$")


@pytest.fixture
def st(db_session):
    db = db_session
    alpha = Workspace(slug=f"st-alpha-{uuid4().hex[:8]}", name="ST Alpha")
    beta = Workspace(slug=f"st-beta-{uuid4().hex[:8]}", name="ST Beta")
    db.add_all([alpha, beta])
    db.flush()

    def user(tag: str, role: UserRole, workspace: Workspace | None):
        return create_user(db, email=f"st-{tag}-{uuid4().hex[:6]}@test.invalid", password=PASSWORD,
                           role=role, workspace_id=workspace.id if workspace else None)

    s = SimpleNamespace(
        alpha=alpha, beta=beta,
        admin=user("admin", UserRole.BUSINESS_ADMIN, alpha),
        engineer=user("eng", UserRole.ML_ENGINEER, alpha),
        viewer=user("viewer", UserRole.VIEWER, alpha),
        beta_admin=user("beta", UserRole.BUSINESS_ADMIN, beta),
        platform=user("platform", UserRole.DCLAB_ADMIN, None),
    )
    s.project = create_project(db, actor=s.admin, workspace_id=alpha.id, name="Alpha", slug=f"a-{uuid4().hex[:6]}")
    s.beta_project = create_project(db, actor=s.beta_admin, workspace_id=beta.id, name="Beta", slug=f"b-{uuid4().hex[:6]}")
    db.commit()
    return s


@contextmanager
def _browser(user, workspace):
    with TestClient(app) as browser:
        assert browser_login(browser, user.email, PASSWORD).status_code == 200
        browser.headers["X-Workspace-Id"] = str(workspace.id)
        yield browser


def _mint(browser, scopes, *, name="ci", days=30, key=None, password=PASSWORD):
    headers = {**csrf_headers(browser), "Idempotency-Key": key or f"st-{uuid4().hex}"}
    body = {"name": name, "scopes": scopes, "expires_in_days": days}
    if password is not None:
        body["current_password"] = password
    return browser.post("/v1/service-tokens", json=body, headers=headers)


def _token(db, s, user=None, scopes=("read",), workspace=None, **kwargs) -> str:
    _row, raw = create_service_token(db, creator=user or s.admin, workspace_id=(workspace or s.alpha).id,
                                     name="t", scopes=scopes, expires_in_days=kwargs.pop("days", 30),
                                     current_password=PASSWORD, **kwargs)
    db.commit()
    return raw


def _id(db, raw: str):
    return db.scalar(select(ServiceToken.id).where(ServiceToken.secret_hash == token_hash(raw)))


def _b(raw: str, **extra) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}", **extra}


def _dead(client, raw: str) -> None:
    _assert_envelope(client.get("/v1/projects", headers=_b(raw)), 401, "unauthenticated")


def test_lifecycle_secret_once_hashed_replay_audit_and_revoke(client, db_session, st, caplog):
    db = db_session
    caplog.set_level(logging.INFO, logger="dclab.service_tokens")
    with _browser(st.admin, st.alpha) as browser:
        key = f"st-{uuid4().hex}"
        created = _mint(browser, ["experiments:write", "read"], key=key)
        assert created.status_code == 201, created.text
        body = created.json()
        raw = body["secret"]
        assert SECRET.fullmatch(raw) and created.headers["Cache-Control"] == "no-store"
        assert (body["status"], body["scopes"], body["prefix"]) == (
            "active", ["read", "experiments:write"], "dclab_st_" + body["id"].replace("-", "")[:8])
        row = db.get(ServiceToken, body["id"])
        assert row.secret_hash == token_hash(raw)
        # The password is not part of the digest: a replay under the key answers without a secret.
        replay = _mint(browser, ["experiments:write", "read"], key=key, password="not-checked-on-replay")
        assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
        assert replay.json()["id"] == body["id"] and replay.json()["secret"] is None
        stored = db.scalar(select(IdempotencyKey).where(IdempotencyKey.idempotency_key == key))
        assert PASSWORD not in json.dumps({c.name: str(getattr(stored, c.name)) for c in stored.__table__.columns})
        listed = browser.get("/v1/service-tokens").json()
        assert [item["id"] for item in listed] == [body["id"]] and "secret" not in listed[0]

        me = client.get("/v1/me", headers=_b(raw))
        assert me.status_code == 200, me.text
        principal = me.json()
        assert principal["service_token"]["id"] == body["id"] and principal["active_workspace_id"] == str(st.alpha.id)
        assert [w["id"] for w in principal["workspaces"]] == [str(st.alpha.id)]
        assert principal["capabilities"]["workspace_read"] and principal["capabilities"]["workspace_execute_ml"]
        assert not principal["capabilities"]["workspace_manage_members"]
        assert [w["id"] for w in client.get("/v1/workspaces", headers=_b(raw)).json()] == [str(st.alpha.id)]
        assert client.get("/v1/projects", headers=_b(raw)).status_code == 200
        db.expire_all()
        assert db.get(ServiceToken, body["id"]).last_used_at is not None

        revoke = browser.post(f"/v1/service-tokens/{body['id']}/revoke", headers=csrf_headers(browser))
        assert revoke.status_code == 200 and revoke.json()["status"] == "revoked"
        again = browser.post(f"/v1/service-tokens/{body['id']}/revoke", headers=csrf_headers(browser))
        assert again.json()["revoked_at"] == revoke.json()["revoked_at"]
    _dead(client, raw)
    audit = [r.getMessage() for r in caplog.records if "service_token_audit" in r.getMessage()]
    assert any('"created"' in line for line in audit) and any('"revoked"' in line for line in audit)
    failed = [r.getMessage() for r in caplog.records if "service_token_auth_failed" in r.getMessage()]
    assert failed and f"token_id={body['id']}" in failed[-1] and "reason=revoked" in failed[-1]
    assert raw not in caplog.text and raw.rsplit("_", 1)[1] not in caplog.text and PASSWORD not in caplog.text


def test_create_requires_reauthentication_ml_write_membership_and_valid_input(client, db_session, st, monkeypatch):
    with _browser(st.engineer, st.alpha) as engineer:
        _assert_envelope(_mint(engineer, ["read"], password=None), 422, "validation_failed")
        wrong = _assert_envelope(_mint(engineer, ["read"], password="wrong-password"), 403, "reauthentication_failed")
        assert "wrong-password" not in json.dumps(wrong)
        for name in ("evil‮name", "bell\x07"):  # Unicode Cf / Cc
            _assert_envelope(_mint(engineer, ["read"], name=name), 422, "invalid_name")
        _assert_envelope(_mint(engineer, ["admin"]), 422, "validation_failed")
        for days in (0, 91):
            _assert_envelope(_mint(engineer, ["read"], days=days), 422, "validation_failed")
        monkeypatch.setattr(get_settings(), "service_tokens_enabled", False)
        _assert_envelope(_mint(engineer, ["read"]), 403, "service_tokens_disabled")
        monkeypatch.setattr(get_settings(), "service_tokens_enabled", True)
        for _ in range(get_settings().login_throttle_attempts):
            _mint(engineer, ["read"], password="wrong-password")
        _assert_envelope(_mint(engineer, ["read"]), 429, "rate_limited")
    # Founder default: minting ANY token needs ML write as an explicit member.
    with _browser(st.viewer, st.alpha) as viewer:
        for scopes in (["read"], ["read", "experiments:write"]):
            _assert_envelope(_mint(viewer, scopes), 403, "token_creation_not_permitted")
    with _browser(st.platform, st.alpha) as platform:  # dclab_admin, not a member of alpha
        _assert_envelope(_mint(platform, ["read"]), 403, "token_creation_not_permitted")
    assert db_session.scalar(select(func.count()).select_from(ServiceToken)) == 0


def test_malformed_unknown_wrong_secret_revoked_expired_are_one_uniform_401(client, db_session, st, monkeypatch):
    db = db_session
    good = _token(db, st)
    token_id = good.split("_")[2]
    revoked = _token(db, st)
    db.execute(update(ServiceToken).where(ServiceToken.id == _id(db, revoked)).values(revoked_at=utcnow()))
    db.commit()
    expired = _token(db, st, days=1, now=utcnow() - timedelta(days=2))
    variants = [
        "dclab_st_garbage",
        f"dclab_st_{uuid4().hex}_{'A' * 43}",           # well-formed, unknown id
        f"dclab_st_{token_id}_{'B' * 43}",              # known id, wrong secret
        good + "x", revoked, expired,
    ]
    bodies = []
    for raw in variants:
        response = client.get("/v1/projects", headers=_b(raw))
        error = _assert_envelope(response, 401, "unauthenticated")
        assert response.headers["WWW-Authenticate"] == "Bearer"
        bodies.append({k: v for k, v in error.items() if k != "request_id"})
    assert all(body == bodies[0] for body in bodies) and bodies[0]["message"] == "invalid service token"
    assert client.get("/v1/projects", headers=_b(good)).status_code == 200
    monkeypatch.setattr(get_settings(), "service_tokens_enabled", False)
    _dead(client, good)


def test_tokens_die_on_password_reset_logout_all_and_authority_changes(client, db_session, st):
    db = db_session
    # Deactivation (ORM); reactivating does not resurrect the token.
    deactivated = _token(db, st, user=st.engineer)
    st.engineer.is_active = False
    db.commit()
    st.engineer.is_active = True
    db.commit()
    _dead(client, deactivated)
    # Password reset.
    reset = _token(db, st, user=st.engineer)
    confirm_password_reset(db, issue_recovery_token(db, st.engineer, PURPOSE_PASSWORD_RESET), "new-password-456")
    db.commit()
    _dead(client, reset)
    assert db.get(ServiceToken, _id(db, reset)).revoked_by_user_id is None  # system revocation
    # Logout-all from a session.
    logout = _token(db, st)
    with _browser(st.admin, st.alpha) as browser:
        assert browser.post("/auth/logout-all", headers=csrf_headers(browser)).status_code == 204
    _dead(client, logout)
    # Role downgrade (ORM) revokes the creator's tokens in that workspace only.
    beta_membership = WorkspaceMembership(workspace_id=st.beta.id, user_id=st.admin.id, role="ml_engineer")
    db.add(beta_membership)
    db.commit()
    alpha_token, beta_token = _token(db, st), _token(db, st, workspace=st.beta)
    membership = db.scalar(select(WorkspaceMembership).where(
        WorkspaceMembership.user_id == st.admin.id, WorkspaceMembership.workspace_id == st.alpha.id))
    membership.role = "viewer"
    db.commit()
    _dead(client, alpha_token)
    assert client.get("/v1/projects", headers=_b(beta_token)).status_code == 200
    # Suspension and removal (ORM).
    suspended = _token(db, st, user=st.beta_admin, workspace=st.beta)
    db.scalar(select(WorkspaceMembership).where(WorkspaceMembership.user_id == st.beta_admin.id)).suspended_at = utcnow()
    db.commit()
    _dead(client, suspended)
    db.delete(beta_membership)
    db.commit()
    _dead(client, beta_token)


def test_token_only_acts_inside_its_workspace_with_explicit_membership(client, db_session, st):
    db = db_session
    raw = _token(db, st, scopes=("read", "projects:write"))
    _assert_envelope(client.get("/v1/projects", headers=_b(raw, **{"X-Workspace-Id": str(st.beta.id)})), 403)
    assert client.get("/v1/projects", headers=_b(raw, **{"X-Workspace-Id": str(st.alpha.id)})).status_code == 200
    ids = {p["id"] for p in client.get("/v1/projects", headers=_b(raw)).json()}
    assert str(st.project.id) in ids and str(st.beta_project.id) not in ids
    _assert_envelope(client.get(f"/v1/projects/{st.beta_project.id}", headers=_b(raw)), 404, "not_found")
    created = client.post("/v1/projects", json={"name": "Via token"},
                          headers=_b(raw, **{"Idempotency-Key": "st-k1", "X-Workspace-Id": str(st.beta.id)}))
    _assert_envelope(created, 403)
    created = client.post("/v1/projects", json={"name": "Via token"}, headers=_b(raw, **{"Idempotency-Key": "st-k1"}))
    assert created.status_code == 201 and created.json()["workspace_id"] == str(st.alpha.id)
    key = db.scalar(select(IdempotencyKey).where(IdempotencyKey.idempotency_key == "st-k1"))
    assert (key.principal_kind, key.workspace_id) == ("service_token", st.alpha.id)
    # A creator who lost the membership (even outside the ORM hooks) makes the token unusable.
    db.execute(delete(WorkspaceMembership).where(WorkspaceMembership.user_id == st.admin.id))
    db.commit()
    _dead(client, raw)


def test_scope_escalation_is_refused_on_use(client, db_session, st, caplog):
    db = db_session
    caplog.set_level(logging.INFO, logger="dclab.workspace_access")
    read_only = _token(db, st, user=st.engineer, scopes=("read",))
    projects = db.scalar(select(func.count()).select_from(Project))
    error = _assert_envelope(client.post("/v1/projects", json={"name": "x"},
                                         headers=_b(read_only, **{"Idempotency-Key": "st-k2"})), 403, "insufficient_scope")
    assert error["details"]["required_scope"] == "projects:write"
    event = json.loads([r.getMessage() for r in caplog.records if "workspace_audit" in r.getMessage()][-1].split(" ", 1)[1])
    assert (event["principal_kind"], event["service_token_id"]) == ("service_token", str(_id(db, read_only)))
    write_only = _token(db, st, user=st.engineer, scopes=("projects:write",))
    _assert_envelope(client.get("/v1/projects", headers=_b(write_only)), 403, "insufficient_scope")
    assert db.scalar(select(func.count()).select_from(Project)) == projects
    # A role change that bypasses the ORM hooks still cannot write: writes need the
    # creator's explicit ML-write membership on every request.
    writer = _token(db, st, user=st.engineer, scopes=("read", "projects:write"))
    db.execute(update(WorkspaceMembership).where(WorkspaceMembership.user_id == st.engineer.id).values(role="viewer"))
    db.commit()
    _assert_envelope(client.post("/v1/projects", json={"name": "x"},
                                 headers=_b(writer, **{"Idempotency-Key": "st-k3"})), 403, "forbidden")
    assert client.get("/v1/projects", headers=_b(writer)).status_code == 200


def test_tokens_never_use_cookie_paths_or_manage_tokens(client, db_session, st):
    raw = _token(db_session, st, scopes=("read", "decisions:propose"))
    for method, path in (("GET", "/v1/service-tokens"), ("POST", "/v1/service-tokens"),
                         ("POST", f"/v1/service-tokens/{uuid4()}/revoke")):
        response = client.request(method, path, headers=_b(raw, **{"Idempotency-Key": "st-k4"}),
                                  json={"name": "x", "scopes": ["read"]} if method == "POST" else None)
        _assert_envelope(response, 403, "session_required")
    jwt = _b(create_access_token(st.admin), **{"X-Workspace-Id": str(st.alpha.id)})
    _assert_envelope(client.get("/v1/service-tokens", headers=jwt), 403, "session_required")
    _assert_envelope(client.post(f"/v1/decisions/{uuid4()}/accept", json={"rationale": "x"},
                                 headers=_b(raw, **{"Idempotency-Key": "st-k5"})), 403, "service_token_not_permitted")
    legacy = client.get("/app/opportunities", headers=_b(raw))
    assert legacy.status_code == 401 and legacy.json() == {"detail": "service tokens authenticate /v1 only"}
    with _browser(st.admin, st.alpha) as browser:
        # Token + session cookie: refused before either credential is resolved (no escalation).
        _assert_envelope(browser.get("/v1/projects", headers=_b(raw)), 400, "ambiguous_credentials")
        _assert_envelope(browser.get("/v1/projects", headers=_b("dclab_st_bogus")), 400, "ambiguous_credentials")
        # Off /v1 a service token never exempts a cookie request from CSRF.
        assert browser.post("/auth/logout", headers=_b(raw, Origin="http://evil.test")).status_code == 403
        assert browser.get("/auth/session").status_code == 200


def test_token_is_an_agent_that_proposes_but_never_accepts(client, db_session, st):
    db = db_session
    raw = _token(db, st, scopes=("read", "decisions:propose"))
    token_id = _id(db, raw)
    path = f"/v1/projects/{st.project.id}/decisions"
    body = {"action": "propose", "decision_type": "experiment_accepted", "subject": {"kind": "project"},
            "rationale": "agent: promote the branch"}
    proposed = client.post(path, json=body, headers=_b(raw, **{"Idempotency-Key": "st-k6"}))
    assert proposed.status_code == 201, proposed.text
    record = proposed.json()
    assert record["actor"]["kind"] == "agent" and record["actor"]["service_token_id"] == str(token_id)
    assert record["rationale_untrusted"] is True
    recorded = client.post(path, json={**body, "action": "record"}, headers=_b(raw, **{"Idempotency-Key": "st-k7"}))
    _assert_envelope(recorded, 403, "decision_actor_not_permitted")
    leaked = client.post(path, json={**body, "rationale": f"use {raw}"}, headers=_b(raw, **{"Idempotency-Key": "st-k7b"}))
    assert _assert_envelope(leaked, 422, "invalid_decision_record")["details"]["reason"] == "secret_like_text"
    no_scope = _token(db, st, scopes=("read",))
    _assert_envelope(client.post(path, json=body, headers=_b(no_scope, **{"Idempotency-Key": "st-k8"})), 403,
                     "insufficient_scope")
    human = _b(create_access_token(st.admin), **{"X-Workspace-Id": str(st.alpha.id), "Idempotency-Key": "st-k9"})
    accepted = client.post(f"/v1/decisions/{record['id']}/accept", json={"rationale": "reviewed"}, headers=human)
    assert accepted.status_code == 201 and accepted.json()["actor"]["kind"] == "human"
    # The installed binding verifier: only the request's own active token of the same
    # workspace that may propose, whose creator still has explicit ML write.
    actor = DecisionActor.agent(service_token_id=token_id)
    assert drs.agent_binding_verifier is verify_agent_binding
    assert not verify_agent_binding(db, actor, st.alpha.id)  # no authenticated token in this context
    with request_token_bound(_id(db, no_scope)):
        assert not verify_agent_binding(db, actor, st.alpha.id)  # another token than the request's
    with request_token_bound(token_id):
        assert verify_agent_binding(db, actor, st.alpha.id)
        assert not verify_agent_binding(db, actor, st.beta.id)
        assert not verify_agent_binding(db, DecisionActor.agent(agent_run_id=uuid4()), st.alpha.id)
        db.execute(update(WorkspaceMembership).where(WorkspaceMembership.user_id == st.admin.id).values(role="viewer"))
        assert not verify_agent_binding(db, actor, st.alpha.id)
        db.rollback()
        db.execute(update(ServiceToken).where(ServiceToken.id == token_id).values(revoked_at=utcnow()))
        assert not verify_agent_binding(db, actor, st.alpha.id)
        db.rollback()
    # Composite FK: a record cannot name another workspace's token.
    beta_token = _id(db, _token(db, st, user=st.beta_admin, workspace=st.beta))
    with pytest.raises(IntegrityError, match="fk_pdr_actor_service_token"):
        db.execute(text(
            "INSERT INTO project_decision_records (id, workspace_id, project_id, decision_type, state, subject_kind,"
            " actor_kind, actor_service_token_id, rationale, rationale_untrusted, schema_version, policy_version)"
            " VALUES (:id, :ws, :project, 'experiment_accepted', 'proposed', 'project', 'agent', :token, 'x', true,"
            " 1, 'dclab.decisions.v1')"
        ), {"id": uuid4(), "ws": st.alpha.id, "project": st.project.id, "token": beta_token})
    db.rollback()


def test_list_visibility_and_revoke_permissions(client, db_session, st):
    with _browser(st.engineer, st.alpha) as engineer, _browser(st.admin, st.alpha) as admin:
        own = _mint(engineer, ["read"]).json()
        admins = _mint(admin, ["read"]).json()
        assert [t["id"] for t in engineer.get("/v1/service-tokens").json()] == [own["id"]]
        assert {t["id"] for t in admin.get("/v1/service-tokens").json()} == {own["id"], admins["id"]}
        _assert_envelope(engineer.post(f"/v1/service-tokens/{admins['id']}/revoke", headers=csrf_headers(engineer)),
                         404, "not_found")
        _assert_envelope(engineer.post("/v1/service-tokens/not-a-uuid/revoke", headers=csrf_headers(engineer)),
                         422, "validation_failed")
        assert admin.post(f"/v1/service-tokens/{own['id']}/revoke", headers=csrf_headers(admin)).status_code == 200
        _assert_envelope(admin.post("/v1/service-tokens", json={"name": "x", "scopes": ["read"]},
                                    headers={"Idempotency-Key": "st-k10"}), 403, "csrf_failed")
    with _browser(st.beta_admin, st.beta) as other:
        assert other.get("/v1/service-tokens").json() == []
        _assert_envelope(other.post(f"/v1/service-tokens/{admins['id']}/revoke", headers=csrf_headers(other)),
                         404, "not_found")
        _assert_envelope(other.get("/v1/service-tokens", headers={"X-Workspace-Id": str(st.alpha.id)}), 403)


def test_db_freezes_token_identity_and_makes_revocation_final(db_session, st):
    db = db_session
    token_id = _id(db, _token(db, st))
    for values in ({"scopes": ["read", "projects:write"]}, {"expires_at": utcnow() + timedelta(days=900)},
                   {"secret_hash": "f" * 64}, {"workspace_id": st.beta.id}):
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(update(ServiceToken).where(ServiceToken.id == token_id).values(**values))
        db.rollback()
    with pytest.raises(IntegrityError, match="ck_service_tokens_revoked_by"):
        db.execute(update(ServiceToken).where(ServiceToken.id == token_id).values(revoked_by_user_id=st.admin.id))
    db.rollback()
    db.execute(update(ServiceToken).where(ServiceToken.id == token_id)
               .values(revoked_at=utcnow(), revoked_by_user_id=st.admin.id))
    db.commit()
    for values in ({"revoked_at": None}, {"revoked_by_user_id": st.engineer.id}):
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(update(ServiceToken).where(ServiceToken.id == token_id).values(**values))
        db.rollback()
    db.execute(update(ServiceToken).where(ServiceToken.id == token_id).values(revoked_by_user_id=None))  # SET NULL
    db.rollback()
    now = utcnow()
    for values, constraint in (
        ({"scopes": ["admin"]}, "ck_service_tokens_scopes"),
        ({"scopes": ["read", "read"]}, "ck_service_tokens_scopes"),
        ({"expires_at": now + timedelta(days=91)}, "ck_service_tokens_expiry"),
    ):
        row = {"scopes": ["read"], "expires_at": now + timedelta(days=1), **values}
        with pytest.raises(IntegrityError, match=constraint):
            db.add(ServiceToken(workspace_id=st.alpha.id, created_by_user_id=st.admin.id, name="x",
                                secret_hash=uuid4().hex * 2, created_at=now, **row))
            db.flush()
        db.rollback()


def test_every_v1_operation_declares_its_token_policy():
    ops = set(_operations())
    assert set(TOKEN_ROUTE_SCOPES) <= ops and HUMAN_ONLY_ROUTES <= ops
    assert not set(TOKEN_ROUTE_SCOPES) & HUMAN_ONLY_ROUTES
    assert ops == set(TOKEN_ROUTE_SCOPES) | HUMAN_ONLY_ROUTES


def test_revocations_tolerate_a_concurrent_revoke(client, db_session, st, test_engine):
    db = db_session
    first, second = _token(db, st), _token(db, st)
    ids = [_id(db, first), _id(db, second)]
    assert all(db.get(ServiceToken, token_id).revoked_at is None for token_id in ids)  # loaded, now stale
    earlier = utcnow()  # the competing revocation (after creation, before ours)
    with test_engine.begin() as other:  # another request revoked them first and committed
        other.execute(update(ServiceToken).where(ServiceToken.id.in_(ids))
                      .values(revoked_at=earlier, revoked_by_user_id=st.admin.id))
    with _browser(st.admin, st.alpha) as browser:
        single = browser.post(f"/v1/service-tokens/{ids[0]}/revoke", headers=csrf_headers(browser))
        assert single.status_code == 200, single.text
        assert browser.post("/auth/logout-all", headers=csrf_headers(browser)).status_code == 204
    db.expire_all()
    for token_id in ids:
        row = db.get(ServiceToken, token_id)
        assert (row.revoked_at, row.revoked_by_user_id) == (earlier, st.admin.id)  # first revocation kept
    # Same race at the service level with a genuinely stale loaded object (no commit between).
    third = db.get(ServiceToken, _id(db, _token(db, st)))
    assert third.revoked_at is None
    with test_engine.begin() as other:
        other.execute(update(ServiceToken).where(ServiceToken.id == third.id).values(revoked_at=utcnow()))
    assert revoke_service_token(db, actor=st.admin, workspace_id=st.alpha.id, token_id=third.id).revoked_by_user_id is None
    db.commit()
    # System revocation keeps loaded objects truthful without another UPDATE.
    fourth = db.get(ServiceToken, _id(db, _token(db, st)))
    assert revoke_tokens_created_by(db, st.admin.id, reason="test") == 1
    assert fourth.revoked_at is not None and fourth not in db.dirty
    db.commit()


def test_audit_lines_are_emitted_once_per_root_commit(db_session, st, caplog):
    db = db_session
    caplog.set_level(logging.INFO, logger="dclab.service_tokens")

    def audits() -> list[str]:
        return [r.getMessage() for r in caplog.records if "service_token_audit" in r.getMessage()]

    create_service_token(db, creator=st.admin, workspace_id=st.alpha.id, name="t", scopes=["read"],
                         expires_in_days=1, current_password=PASSWORD)
    with db.begin_nested():  # savepoint release (idempotency_service.bind) must not emit
        pass
    with pytest.raises(ValueError):
        with db.begin_nested():  # a savepoint rollback must not drop the queue
            raise ValueError
    assert audits() == []
    db.commit()
    assert len(audits()) == 1 and '"created"' in audits()[0]
    create_service_token(db, creator=st.admin, workspace_id=st.alpha.id, name="t", scopes=["read"],
                         expires_in_days=1, current_password=PASSWORD)
    db.rollback()  # a rolled-back creation is never audited
    db.commit()
    assert len(audits()) == 1


def test_sanitizers_redact_service_tokens():
    from collections import defaultdict

    from app.services.observability_service import sanitize_observability_payload
    from app.services.verification_evidence import _clean_string

    raw = "dclab_st_" + "a" * 32 + "_" + "Zz0-_" * 8 + "abc"
    for text_value in (f"token {raw} leaked", f"x_{raw}", f"({raw})"):
        cleaned, _summary = sanitize_observability_payload({"note": text_value})
        assert raw[:20] not in json.dumps(cleaned), text_value
        counts: dict[str, int] = defaultdict(int)
        assert raw[:20] not in _clean_string(text_value, counts) and counts["secret_like_values_redacted"] == 1
