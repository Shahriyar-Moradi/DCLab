"""S0-P02A: HttpOnly browser sessions, distinct API bearer path, fail-closed production boot."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import (
    INSECURE_JWT_SECRET,
    Settings,
    cookie_secure,
    validate_runtime_settings,
)
from app.db.models import AuthSession, UserRole
from app.services.session_service import hash_session_token, revoke_sessions_for_user
from conftest import browser_login, csrf_headers

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_SESSION = REPO_ROOT / "apps" / "web" / "lib" / "infrastructure" / "session.ts"
WEB_CSRF = REPO_ROOT / "apps" / "web" / "lib" / "infrastructure" / "csrf.ts"
WEB_CLIENT = REPO_ROOT / "apps" / "web" / "lib" / "infrastructure" / "api-client.ts"
WEB_MIDDLEWARE = REPO_ROOT / "apps" / "web" / "middleware.ts"


def _cookie_header(response) -> str:
    if hasattr(response.headers, "get_list"):
        return "\n".join(response.headers.get_list("set-cookie"))
    return response.headers.get("set-cookie") or ""


def _session_value(client) -> str:
    value = client.cookies.get("dclab_session")
    assert value, "expected dclab_session cookie"
    return value


def test_browser_login_sets_httponly_cookie_without_bearer(client, client_user):
    response = browser_login(client, client_user.email, "client-pass-123")
    assert response.status_code == 200
    body = response.json()
    assert "access_token" not in body
    assert body["user"]["email"] == client_user.email
    cookie = _cookie_header(response)
    assert "dclab_session=" in cookie
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert "secure" not in cookie.lower()
    csrf_lines = [
        line for line in cookie.splitlines() if "dclab_csrf=" in line.lower()
    ]
    assert csrf_lines
    assert all("httponly" not in line.lower() for line in csrf_lines)
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == client_user.email
    assert me.json()["role"] == "client_user"


def test_javascript_sources_do_not_read_or_attach_bearer():
    session_src = WEB_SESSION.read_text(encoding="utf-8")
    csrf_src = WEB_CSRF.read_text(encoding="utf-8")
    client_src = WEB_CLIENT.read_text(encoding="utf-8")
    middleware_src = WEB_MIDDLEWARE.read_text(encoding="utf-8")
    assert "document.cookie" not in session_src
    assert "atob" not in session_src
    assert "dclab_token" not in session_src
    assert "dclab_session" not in csrf_src
    assert "dclab_token" not in csrf_src
    assert "document.cookie" in csrf_src
    assert "dclab_csrf" in csrf_src
    assert "Authorization" not in client_src
    assert "readToken" not in client_src
    assert "jose" not in middleware_src
    assert "jwtVerify" not in middleware_src
    assert "X-DCLab-Session" in WEB_MIDDLEWARE.read_text(encoding="utf-8")
    package = (REPO_ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8")
    assert '"jose"' not in package
    assert "localStorage" not in session_src
    assert "localStorage" not in client_src


def test_tokens_path_issues_bearer_without_cookie(client, client_user):
    response = client.post(
        "/auth/tokens",
        json={"email": client_user.email, "password": "client-pass-123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert "dclab_session=" not in _cookie_header(response)
    me = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == client_user.email


def test_session_header_authenticates_without_cookie(client, client_user):
    login = browser_login(client, client_user.email, "client-pass-123")
    raw = login.cookies.get("dclab_session")
    client.cookies.clear()
    denied = client.get("/auth/me")
    assert denied.status_code == 401
    ok = client.get("/auth/me", headers={"X-DCLab-Session": raw})
    assert ok.status_code == 200
    assert ok.json()["email"] == client_user.email


def test_logout_revokes_server_session(client, client_user, db_session):
    browser_login(client, client_user.email, "client-pass-123")
    raw = _session_value(client)
    assert client.get("/auth/me").status_code == 200
    logout = client.post("/auth/logout", headers=csrf_headers(client))
    assert logout.status_code == 204
    cookie = _cookie_header(logout).lower()
    assert "dclab_session=" in cookie
    assert "dclab_csrf=" in cookie
    assert "max-age=0" in cookie
    assert client.get("/auth/me").status_code == 401
    replay = client.get("/auth/me", headers={"X-DCLab-Session": raw})
    assert replay.status_code == 401
    row = db_session.query(AuthSession).one()
    assert row.revoked_at is not None
    assert row.token_hash == hash_session_token(raw)
    assert raw not in row.token_hash


def test_login_rotates_and_revokes_previous_cookie(client, client_user, db_session):
    first = browser_login(client, client_user.email, "client-pass-123")
    first_raw = first.cookies.get("dclab_session")
    second = browser_login(client, client_user.email, "client-pass-123")
    second_raw = second.cookies.get("dclab_session")
    assert first_raw != second_raw
    assert client.get("/auth/me").status_code == 200
    assert (
        client.get("/auth/me", headers={"X-DCLab-Session": first_raw}).status_code
        == 401
    )
    db_session.expire_all()
    predecessor = (
        db_session.query(AuthSession)
        .filter(AuthSession.token_hash == hash_session_token(first_raw))
        .one()
    )
    successor = (
        db_session.query(AuthSession)
        .filter(AuthSession.token_hash == hash_session_token(second_raw))
        .one()
    )
    assert predecessor.revoked_at is not None
    assert successor.revoked_at is None
    assert successor.rotated_from_id == predecessor.id


def test_unknown_session_fails_safely_without_echoing_token(client):
    raw = "unknown-session-secret-that-must-not-be-echoed"
    response = client.get("/auth/me", headers={"X-DCLab-Session": raw})
    assert response.status_code == 401
    assert raw not in response.text


def test_idle_expiry_with_injected_clock(client, client_user, monkeypatch):
    from app.services import session_service

    start = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(session_service, "utcnow", lambda: start)
    browser_login(client, client_user.email, "client-pass-123")
    monkeypatch.setattr(
        session_service, "utcnow", lambda: start + timedelta(hours=13)
    )
    assert client.get("/auth/me").status_code == 401


def test_absolute_expiry_with_injected_clock(client, client_user, monkeypatch):
    from app.services import session_service

    start = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(session_service, "utcnow", lambda: start)
    browser_login(client, client_user.email, "client-pass-123")
    monkeypatch.setattr(
        session_service, "utcnow", lambda: start + timedelta(days=8)
    )
    assert client.get("/auth/me").status_code == 401


def test_sliding_idle_extends_with_injected_clock(client, client_user, monkeypatch):
    from app.services import session_service

    start = datetime(2026, 1, 1, tzinfo=UTC)
    current = {"now": start}

    def _now():
        return current["now"]

    monkeypatch.setattr(session_service, "utcnow", _now)
    browser_login(client, client_user.email, "client-pass-123")
    current["now"] = start + timedelta(hours=11)
    assert client.get("/auth/me").status_code == 200
    current["now"] = start + timedelta(hours=22)
    assert client.get("/auth/me").status_code == 200
    current["now"] = start + timedelta(hours=36)
    assert client.get("/auth/me").status_code == 401


def test_privilege_change_revokes_sessions(client, client_user, db_session):
    browser_login(client, client_user.email, "client-pass-123")
    assert client.get("/auth/me").status_code == 200
    client_user.role = UserRole.DCLAB_ADMIN.value
    revoke_sessions_for_user(db_session, client_user.id)
    db_session.commit()
    assert client.get("/auth/me").status_code == 401


def test_bearer_authorization_uses_database_role_not_token_claim(
    client, client_user, db_session
):
    tokens = client.post(
        "/auth/tokens",
        json={"email": client_user.email, "password": "client-pass-123"},
    )
    token = tokens.json()["access_token"]
    denied = client.get(
        "/admin/experiments", headers={"Authorization": f"Bearer {token}"}
    )
    assert denied.status_code == 403
    client_user.role = UserRole.DCLAB_ADMIN.value
    db_session.commit()
    allowed = client.get(
        "/admin/experiments", headers={"Authorization": f"Bearer {token}"}
    )
    assert allowed.status_code == 200


def test_session_stores_hash_not_raw_token(client, client_user, db_session):
    browser_login(client, client_user.email, "client-pass-123")
    raw = _session_value(client)
    row = db_session.query(AuthSession).one()
    assert row.token_hash == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert len(row.token_hash) == 64
    assert raw != row.token_hash


def test_register_issues_session_not_bearer(client):
    response = client.post(
        "/auth/register",
        json={
            "email": "session-owner@test.invalid",
            "password": "test-password",
            "full_name": "Owner",
        },
        headers=csrf_headers(client),
    )
    assert response.status_code == 200
    assert "access_token" not in response.json()
    assert "httponly" in _cookie_header(response).lower()
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "session-owner@test.invalid"


def test_production_boot_rejects_insecure_jwt():
    settings = Settings.model_construct(
        dclab_env="production",
        jwt_secret=INSECURE_JWT_SECRET,
        session_cookie_secure=True,
        session_cookie_samesite="lax",
        session_cookie_path="/",
    )
    try:
        validate_runtime_settings(settings)
        raise AssertionError("expected fail-closed production boot")
    except RuntimeError as exc:
        assert "JWT_SECRET" in str(exc)


def test_production_boot_rejects_insecure_cookie():
    settings = Settings.model_construct(
        dclab_env="production",
        jwt_secret="deployed-secret-not-the-default",
        session_cookie_secure=False,
        session_cookie_samesite="lax",
        session_cookie_path="/",
    )
    try:
        validate_runtime_settings(settings)
        raise AssertionError("expected fail-closed production boot")
    except RuntimeError as exc:
        assert "Secure" in str(exc)


def test_production_boot_accepts_secure_configuration():
    settings = Settings.model_construct(
        dclab_env="production",
        jwt_secret="deployed-secret-not-the-default",
        auth_token_hash_secret="deployed-hash-secret-not-the-default",
        auth_csrf_secret="deployed-csrf-secret-not-the-default",
        session_cookie_secure=True,
        session_cookie_samesite="lax",
        session_cookie_path="/",
        cors_origins="https://app.example.test",
    )
    validate_runtime_settings(settings)
    assert cookie_secure(settings) is True


def test_development_boot_allows_default_secret():
    settings = Settings.model_construct(
        dclab_env="development",
        jwt_secret=INSECURE_JWT_SECRET,
        session_cookie_secure=None,
        session_cookie_samesite="lax",
        session_cookie_path="/",
    )
    validate_runtime_settings(settings)
    assert cookie_secure(settings) is False
