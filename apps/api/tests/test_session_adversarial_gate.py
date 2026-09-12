"""S0-P02F: two-user/two-workspace adversarial completion gate."""

from __future__ import annotations

import hashlib
import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config import get_settings, validate_runtime_settings
from app.db.models import AuthRecoveryToken, AuthSession, UserRole
from app.services.auth_service import create_user
from app.services.csrf_service import (
    csrf_token_for_session,
    generate_anonymous_csrf_token,
    request_origin,
)
from app.services.login_throttle import reset_login_throttle
from app.services.session_cleanup_service import cleanup_expired_auth_state
from app.services.session_service import (
    hash_session_token,
    issue_session,
    revoke_session_token,
)
from app.services.workspace_service import create_business_workspace
from conftest import TRUSTED_ORIGIN, csrf_headers

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_PASSWORD = "gate-password-1"
WEB_SESSION = REPO_ROOT / "apps" / "web" / "lib" / "infrastructure" / "session.ts"
WEB_CSRF = REPO_ROOT / "apps" / "web" / "lib" / "infrastructure" / "csrf.ts"
CSRF_SERVICE = REPO_ROOT / "apps" / "api" / "app" / "services" / "csrf_service.py"

PROD_ENV = {
    "DCLAB_ENV": "production",
    "JWT_SECRET": "p02f-prod-jwt-not-the-default-value",
    "AUTH_TOKEN_HASH_SECRET": "p02f-prod-hash-not-the-default-value",
    "AUTH_CSRF_SECRET": "p02f-prod-csrf-not-the-default-value",
    "SESSION_COOKIE_SECURE": "true",
    "CORS_ORIGINS": f"{TRUSTED_ORIGIN},https://app.example.test",
    "AUTH_EMAIL_DELIVERY_ENABLED": "false",
    "AUTH_BROWSER_SESSIONS_ENABLED": "true",
}


def _owners(db_session):
    suffix = uuid4().hex
    alice = create_user(
        db_session,
        email=f"alice-{suffix}@gate.invalid",
        password=GATE_PASSWORD,
        role=UserRole.WORKSPACE_OWNER,
        full_name="Alice Gate",
    )
    bob = create_user(
        db_session,
        email=f"bob-{suffix}@gate.invalid",
        password=GATE_PASSWORD,
        role=UserRole.WORKSPACE_OWNER,
        full_name="Bob Gate",
    )
    alice_ws = create_business_workspace(db_session, owner=alice, name="Alice Gate Co")
    bob_ws = create_business_workspace(db_session, owner=bob, name="Bob Gate Co")
    db_session.commit()
    return alice, bob, alice_ws, bob_ws


def _csrf(raw: str | None = None) -> dict[str, str]:
    if raw:
        return {
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token_for_session(raw),
            "X-DCLab-Session": raw,
        }
    token = generate_anonymous_csrf_token()
    name = get_settings().csrf_cookie_name
    return {
        "Origin": TRUSTED_ORIGIN,
        "X-CSRF-Token": token,
        "Cookie": f"{name}={token}",
    }


def _login(client, email: str, password: str):
    headers = csrf_headers(client)
    return client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=headers,
    )


def _raw_session(response) -> str:
    raw = response.cookies.get("dclab_session")
    assert raw, "login did not issue dclab_session"
    return raw


@contextmanager
def _restarted_client(db_session):
    from app.db.session import get_db
    from app.main import app

    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as extra:
        yield extra


def _apply_production_env(monkeypatch, extra: dict[str, str] | None = None) -> None:
    values = dict(PROD_ENV)
    if extra:
        values.update(extra)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    validate_runtime_settings(get_settings())


def test_two_user_fixation_theft_rotation_and_tenant_isolation(client, db_session):
    alice, bob, alice_ws, bob_ws = _owners(db_session)
    bob_login = _login(client, bob.email, GATE_PASSWORD)
    assert bob_login.status_code == 200, bob_login.text
    bob_raw = _raw_session(bob_login)

    client.cookies.clear()
    client.cookies.set("dclab_session", "fixed-attacker-session")
    alice_login = _login(client, alice.email, GATE_PASSWORD)
    assert alice_login.status_code == 200, alice_login.text
    alice_raw = _raw_session(alice_login)
    assert alice_raw != "fixed-attacker-session"
    assert (
        client.get(
            "/auth/me", headers={"X-DCLab-Session": "fixed-attacker-session"}
        ).status_code
        == 401
    )

    client.cookies.clear()
    client.cookies.set("dclab_session", bob_raw)
    planted = _login(client, alice.email, GATE_PASSWORD)
    assert planted.status_code == 200, planted.text
    alice_after_plant = _raw_session(planted)
    assert alice_after_plant != bob_raw
    assert client.get("/auth/me", headers={"X-DCLab-Session": bob_raw}).status_code == 401
    planted_me = client.get("/auth/me", headers={"X-DCLab-Session": alice_after_plant})
    assert planted_me.status_code == 200
    assert planted_me.json()["email"] == alice.email
    predecessor = (
        db_session.query(AuthSession)
        .filter(AuthSession.token_hash == hash_session_token(alice_after_plant))
        .one()
    )
    assert predecessor.rotated_from_id is None

    thief_me = client.get("/auth/me", headers={"X-DCLab-Session": alice_after_plant})
    assert thief_me.json()["email"] == alice.email
    foreign = client.get(
        "/v1/projects",
        headers={
            "X-DCLab-Session": alice_after_plant,
            "X-Workspace-Id": str(bob_ws.id),
        },
    )
    assert foreign.status_code == 403
    assert "not authorized" in foreign.json()["detail"]
    own = client.get(
        "/v1/projects",
        headers={
            "X-DCLab-Session": alice_after_plant,
            "X-Workspace-Id": str(alice_ws.id),
        },
    )
    assert own.status_code == 200, own.text

    client.cookies.clear()
    client.cookies.set("dclab_session", alice_after_plant)
    rotated = _login(client, alice.email, GATE_PASSWORD)
    assert rotated.status_code == 200
    new_alice = _raw_session(rotated)
    assert new_alice != alice_after_plant
    assert (
        client.get("/auth/me", headers={"X-DCLab-Session": alice_after_plant}).status_code
        == 401
    )
    still_alice = client.get("/auth/me", headers={"X-DCLab-Session": new_alice})
    assert still_alice.json()["email"] == alice.email

    client.cookies.clear()
    bob_again = _login(client, bob.email, GATE_PASSWORD)
    bob_live = _raw_session(bob_again)
    assert client.get("/auth/me", headers={"X-DCLab-Session": bob_live}).json()["email"] == bob.email
    assert client.get("/auth/me", headers={"X-DCLab-Session": new_alice}).json()["email"] == alice.email


def test_origin_host_confusion_does_not_bypass_csrf(client, db_session):
    alice, _bob, _alice_ws, _bob_ws = _owners(db_session)
    login = _login(client, alice.email, GATE_PASSWORD)
    raw = _raw_session(login)
    token = csrf_token_for_session(raw)

    trusted_origin_evil_host = client.post(
        "/auth/logout",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "Host": "evil.example",
            "X-Forwarded-Host": "app.example.test",
            "X-CSRF-Token": token,
            "X-DCLab-Session": raw,
        },
    )
    assert trusted_origin_evil_host.status_code == 204, trusted_origin_evil_host.text

    login = _login(client, alice.email, GATE_PASSWORD)
    raw = _raw_session(login)
    token = csrf_token_for_session(raw)
    evil_origin_trusted_host = client.post(
        "/auth/logout",
        headers={
            "Origin": "https://evil.example",
            "Host": "localhost:3001",
            "X-Forwarded-Host": "localhost:3001",
            "X-CSRF-Token": token,
            "X-DCLab-Session": raw,
        },
    )
    assert evil_origin_trusted_host.status_code == 403
    assert evil_origin_trusted_host.json()["detail"] == "untrusted origin"
    assert client.get("/auth/me", headers={"X-DCLab-Session": raw}).status_code == 200

    missing_origin_evil_referer = client.post(
        "/auth/logout",
        headers={
            "Referer": "https://evil.example/phish",
            "Host": "localhost:3001",
            "X-Forwarded-Host": "localhost:3001",
            "X-CSRF-Token": token,
            "X-DCLab-Session": raw,
        },
    )
    assert missing_origin_evil_referer.status_code == 403

    source = inspect.getsource(request_origin)
    csrf_src = CSRF_SERVICE.read_text(encoding="utf-8")
    assert "headers.get(\"host\")" not in source.lower()
    assert "x-forwarded-host" not in csrf_src.lower()


def test_javascript_cannot_read_session_cookie_from_sources():
    session_src = WEB_SESSION.read_text(encoding="utf-8")
    csrf_src = WEB_CSRF.read_text(encoding="utf-8")
    assert "document.cookie" not in session_src
    assert "dclab_session" not in csrf_src
    assert "document.cookie" in csrf_src
    assert "dclab_csrf" in csrf_src
    assert "localStorage" not in session_src


def test_password_and_reset_enumeration_is_uniform(client, db_session):
    reset_login_throttle()
    alice, _bob, _alice_ws, _bob_ws = _owners(db_session)
    unknown = client.post(
        "/auth/login",
        json={"email": "missing-gate@test.invalid", "password": GATE_PASSWORD},
        headers=csrf_headers(client),
    )
    wrong = client.post(
        "/auth/login",
        json={"email": alice.email, "password": "wrong-password-9"},
        headers=csrf_headers(client),
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"] == "invalid email or password"
    assert alice.email not in unknown.text
    assert "wrong-password-9" not in wrong.text

    unknown_reset = client.post(
        "/auth/password-reset/request",
        json={"email": "nobody-gate@test.invalid"},
        headers=csrf_headers(client),
    )
    known_reset = client.post(
        "/auth/password-reset/request",
        json={"email": alice.email},
        headers=csrf_headers(client),
    )
    assert unknown_reset.status_code == known_reset.status_code == 204
    assert unknown_reset.content == known_reset.content == b""
    row = (
        db_session.query(AuthRecoveryToken)
        .filter(AuthRecoveryToken.user_id == alice.id)
        .one()
    )
    assert len(row.token_hash) == 64
    assert "token" not in known_reset.headers.get("set-cookie", "").lower()

    confirm = client.post(
        "/auth/password-reset/confirm",
        json={"token": "not-a-real-reset-token", "password": "replacement-pass-1"},
        headers=csrf_headers(client),
    )
    assert confirm.status_code == 400
    assert confirm.json()["detail"] == "invalid or expired token"
    assert alice.email not in confirm.text
    reset_login_throttle()


def test_expired_cleanup_keeps_the_other_users_live_session(db_session):
    alice, bob, _alice_ws, _bob_ws = _owners(db_session)
    moment = datetime(2026, 6, 1, tzinfo=UTC)
    _row, bob_raw = issue_session(db_session, bob)
    stale_raw = "stale-alice-raw-not-stored"
    db_session.add(
        AuthSession(
            id=uuid4(),
            user_id=alice.id,
            token_hash=hash_session_token(stale_raw),
            created_at=moment - timedelta(days=40),
            last_seen_at=moment - timedelta(days=40),
            idle_expires_at=moment - timedelta(days=39),
            absolute_expires_at=moment - timedelta(days=38),
            revoked_at=moment - timedelta(days=35),
        )
    )
    db_session.commit()
    result = cleanup_expired_auth_state(db_session, now=moment, limit=50, max_batches=2)
    db_session.commit()
    assert result.sessions >= 1
    assert (
        db_session.query(AuthSession)
        .filter(AuthSession.token_hash == hash_session_token(stale_raw))
        .one_or_none()
        is None
    )
    live = (
        db_session.query(AuthSession)
        .filter(AuthSession.token_hash == hash_session_token(bob_raw))
        .one()
    )
    assert live.revoked_at is None


def test_concurrent_logout_of_two_users(db_session, test_engine):
    alice, bob, _alice_ws, _bob_ws = _owners(db_session)
    _alice_row, alice_raw = issue_session(db_session, alice)
    _bob_row, bob_raw = issue_session(db_session, bob)
    db_session.commit()
    SessionLocal = sessionmaker(bind=test_engine)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _revoke(raw: str) -> None:
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            revoke_session_token(session, raw)
            session.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_revoke, alice_raw)
        second = pool.submit(_revoke, bob_raw)
        first.result()
        second.result()
    assert errors == []
    db_session.expire_all()
    for raw in (alice_raw, bob_raw):
        row = (
            db_session.query(AuthSession)
            .filter(AuthSession.token_hash == hash_session_token(raw))
            .one()
        )
        assert row.revoked_at is not None


def test_session_survives_testclient_restart_and_bearer_needs_no_cookies(
    client, db_session
):
    alice, _bob, _alice_ws, _bob_ws = _owners(db_session)
    login = _login(client, alice.email, GATE_PASSWORD)
    raw = _raw_session(login)
    tokens = client.post(
        "/auth/tokens",
        json={"email": alice.email, "password": GATE_PASSWORD},
    )
    assert tokens.status_code == 200
    access = tokens.json()["access_token"]

    with _restarted_client(db_session) as restarted:
        cookie_me = restarted.get("/auth/me", headers={"X-DCLab-Session": raw})
        assert cookie_me.status_code == 200
        assert cookie_me.json()["email"] == alice.email
        restarted.cookies.clear()
        bearer_me = restarted.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert bearer_me.status_code == 200
        assert bearer_me.json()["email"] == alice.email
        anonymous = restarted.get("/auth/me")
        assert anonymous.status_code == 401


def test_bearer_clients_work_after_browser_cookies_are_cleared(client, db_session):
    alice, _bob, _alice_ws, _bob_ws = _owners(db_session)
    login = _login(client, alice.email, GATE_PASSWORD)
    assert login.status_code == 200
    tokens = client.post(
        "/auth/tokens",
        json={"email": alice.email, "password": GATE_PASSWORD},
    )
    access = tokens.json()["access_token"]
    client.cookies.clear()
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["email"] == alice.email
    assert client.cookies.get("dclab_session") in {None, ""}
    denied = client.get("/auth/me")
    assert denied.status_code == 401


def test_production_shaped_kill_switch_and_theft_runbook(
    client, db_session, monkeypatch
):
    alice, _bob, _alice_ws, _bob_ws = _owners(db_session)
    prior = _login(client, alice.email, GATE_PASSWORD)
    assert prior.status_code == 200, prior.text
    stolen = _raw_session(prior)
    assert hash_session_token(stolen) == hashlib.sha256(stolen.encode()).hexdigest()

    try:
        _apply_production_env(monkeypatch)
        with _restarted_client(db_session) as restarted:
            replay = restarted.get("/auth/me", headers={"X-DCLab-Session": stolen})
            assert replay.status_code == 200
            assert replay.json()["email"] == alice.email

            revoked = restarted.post("/auth/logout-all", headers=_csrf(stolen))
            assert revoked.status_code == 204, revoked.text
            assert (
                restarted.get("/auth/me", headers={"X-DCLab-Session": stolen}).status_code
                == 401
            )

            restarted.cookies.clear()
            fresh = restarted.post(
                "/auth/login",
                json={"email": alice.email, "password": GATE_PASSWORD},
                headers=_csrf(),
            )
            assert fresh.status_code == 200, fresh.text
            hmac_raw = fresh.cookies.get("dclab_session") or _raw_session(fresh)
            assert hash_session_token(hmac_raw) != hashlib.sha256(hmac_raw.encode()).hexdigest()
            assert (
                restarted.get("/auth/me", headers={"X-DCLab-Session": hmac_raw}).status_code
                == 200
            )

            monkeypatch.setenv("AUTH_BROWSER_SESSIONS_ENABLED", "false")
            get_settings.cache_clear()
            restarted.cookies.clear()
            denied_login = restarted.post(
                "/auth/login",
                json={"email": alice.email, "password": GATE_PASSWORD},
                headers=_csrf(),
            )
            assert denied_login.status_code == 503
            assert denied_login.json()["detail"] == "browser sessions are disabled"
            cookie_denied = restarted.get(
                "/auth/me", headers={"X-DCLab-Session": hmac_raw}
            )
            assert cookie_denied.status_code == 401
            tokens = restarted.post(
                "/auth/tokens",
                json={"email": alice.email, "password": GATE_PASSWORD},
            )
            assert tokens.status_code == 200
            bearer = restarted.get(
                "/auth/me",
                headers={"Authorization": f"Bearer {tokens.json()['access_token']}"},
            )
            assert bearer.status_code == 200

            monkeypatch.setenv("AUTH_BROWSER_SESSIONS_ENABLED", "true")
            get_settings.cache_clear()
            restarted.cookies.clear()
            restored = restarted.post(
                "/auth/login",
                json={"email": alice.email, "password": GATE_PASSWORD},
                headers=_csrf(),
            )
            assert restored.status_code == 200, restored.text
            assert get_settings().auth_email_delivery_enabled is False
    finally:
        get_settings.cache_clear()
