"""S0-P02E: typed auth settings, kill switch, hashing secrets, bounded audit."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest

from app.config import (
    INSECURE_JWT_SECRET,
    Settings,
    cookie_secure,
    csrf_hmac_secret,
    get_settings,
    validate_runtime_settings,
)
from app.db.models import AuthRecoveryToken, AuthSession
from app.services.auth_hashing import digest_token, token_hash, token_hash_candidates
from app.services.auth_metrics import auth_metric_counts, record_auth_event, reset_auth_metrics
from app.services.login_throttle import reset_login_throttle
from app.services.observability_service import sanitize_observability_payload
from app.services.recovery_service import hash_recovery_token
from app.services.session_service import hash_session_token
from conftest import browser_login, csrf_headers


def _production_settings(**overrides):
    values = dict(
        dclab_env="production",
        jwt_secret="deployed-secret-not-the-default",
        auth_token_hash_secret="deployed-hash-secret-not-the-default",
        auth_csrf_secret="deployed-csrf-secret-not-the-default",
        session_cookie_secure=True,
        session_cookie_samesite="lax",
        session_cookie_path="/",
        cors_origins="https://app.example.test",
        login_throttle_attempts=5,
        login_throttle_window_minutes=15,
        session_idle_minutes=60,
        session_absolute_minutes=1440,
        session_max_concurrent=5,
        session_cleanup_batch_size=500,
        session_cleanup_max_batches=20,
        session_cookie_name="dclab_session",
        csrf_cookie_name="dclab_csrf",
        csrf_header_name="X-CSRF-Token",
        auth_email_delivery_enabled=False,
        auth_browser_sessions_enabled=True,
    )
    values.update(overrides)
    return Settings.model_construct(**values)


def test_ci_does_not_require_production_hash_secrets():
    settings = Settings.model_construct(
        dclab_env="development",
        jwt_secret=INSECURE_JWT_SECRET,
        auth_token_hash_secret="",
        auth_csrf_secret="",
        session_cookie_secure=None,
        session_cookie_samesite="lax",
        session_cookie_path="/",
    )
    validate_runtime_settings(settings)
    assert settings.auth_token_hash_secret == ""
    assert csrf_hmac_secret(settings) == INSECURE_JWT_SECRET
    assert token_hash("raw-token", settings) == hashlib.sha256(b"raw-token").hexdigest()


def test_production_requires_hash_and_csrf_secrets():
    settings = _production_settings(auth_token_hash_secret="", auth_csrf_secret="")
    with pytest.raises(RuntimeError) as exc:
        validate_runtime_settings(settings)
    message = str(exc.value)
    assert "AUTH_TOKEN_HASH_SECRET" in message
    assert "AUTH_CSRF_SECRET" in message


def test_production_rejects_insecure_hash_secret():
    settings = _production_settings(auth_token_hash_secret=INSECURE_JWT_SECRET)
    with pytest.raises(RuntimeError) as exc:
        validate_runtime_settings(settings)
    assert "AUTH_TOKEN_HASH_SECRET" in str(exc.value)


def test_production_accepts_complete_auth_settings():
    settings = _production_settings()
    validate_runtime_settings(settings)
    assert cookie_secure(settings) is True


def test_development_rejects_nonsensical_throttle_and_ttl():
    with pytest.raises(RuntimeError) as exc:
        validate_runtime_settings(
            Settings.model_construct(
                dclab_env="development",
                login_throttle_attempts=0,
                login_throttle_window_minutes=15,
                session_idle_minutes=60,
                session_absolute_minutes=10,
                session_cookie_name="dclab_session",
                csrf_cookie_name="dclab_csrf",
                csrf_header_name="X-CSRF-Token",
                session_cookie_samesite="lax",
            )
        )
    message = str(exc.value)
    assert "LOGIN_THROTTLE_ATTEMPTS" in message
    assert "SESSION_ABSOLUTE_MINUTES" in message


def test_hmac_hash_is_not_unkeyed_sha256():
    settings = Settings.model_construct(auth_token_hash_secret="unit-test-hash-secret")
    raw = "session-raw"
    digest = token_hash(raw, settings)
    assert digest == hmac.new(b"unit-test-hash-secret", raw.encode(), hashlib.sha256).hexdigest()
    assert digest != hashlib.sha256(raw.encode()).hexdigest()
    assert len(digest) == 64
    candidates = token_hash_candidates(raw, settings)
    assert digest in candidates
    assert hashlib.sha256(raw.encode()).hexdigest() in candidates


def test_legacy_sha256_session_still_looks_up_after_hmac_rollout(
    client, client_user, monkeypatch
):
    browser_login(client, client_user.email, "client-pass-123")
    assert client.get("/auth/me").status_code == 200
    monkeypatch.setenv("AUTH_TOKEN_HASH_SECRET", "unit-test-hash-secret")
    get_settings.cache_clear()
    try:
        raw = client.cookies.get("dclab_session")
        assert hash_session_token(raw) != hashlib.sha256(raw.encode()).hexdigest()
        assert client.get("/auth/me").status_code == 200
    finally:
        get_settings.cache_clear()


def test_kill_switch_disables_browser_sessions_not_tokens(
    client, client_user, monkeypatch
):
    reset_auth_metrics()
    monkeypatch.setenv("AUTH_BROWSER_SESSIONS_ENABLED", "false")
    get_settings.cache_clear()
    try:
        denied = browser_login(client, client_user.email, "client-pass-123")
        assert denied.status_code == 503
        assert denied.json()["detail"] == "browser sessions are disabled"
        tokens = client.post(
            "/auth/tokens",
            json={"email": client_user.email, "password": "client-pass-123"},
        )
        assert tokens.status_code == 200
        me = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {tokens.json()['access_token']}"},
        )
        assert me.status_code == 200
        assert auth_metric_counts()["session.kill_switch"] >= 1
    finally:
        get_settings.cache_clear()


def test_kill_switch_rejects_existing_cookie(client, client_user, monkeypatch):
    browser_login(client, client_user.email, "client-pass-123")
    monkeypatch.setenv("AUTH_BROWSER_SESSIONS_ENABLED", "false")
    get_settings.cache_clear()
    try:
        assert client.get("/auth/me").status_code == 401
        assert client.get("/auth/me").json()["detail"] == "browser sessions are disabled"
    finally:
        get_settings.cache_clear()


def test_login_metrics_are_reason_codes_only(client):
    reset_login_throttle()
    reset_auth_metrics()
    denied = client.post(
        "/auth/login",
        json={"email": "metrics-unknown@test.invalid", "password": "wrong-password"},
        headers=csrf_headers(client),
    )
    assert denied.status_code == 401
    body = denied.text
    assert "wrong-password" not in body
    assert "metrics-unknown@test.invalid" not in body
    counts = auth_metric_counts()
    assert counts.get("login.invalid_credentials") == 1
    record_auth_event("login", "user-specific-reason-must-collapse")
    assert auth_metric_counts()["login.other"] == 1


def test_csrf_metrics_do_not_include_origin(client, client_user):
    reset_auth_metrics()
    browser_login(client, client_user.email, "client-pass-123")
    denied = client.post("/auth/logout", headers={"Origin": "https://evil.example"})
    assert denied.status_code == 403
    assert denied.json()["detail"] == "untrusted origin"
    assert "evil.example" not in denied.text
    assert auth_metric_counts()["csrf.untrusted_origin"] >= 1


def test_recovery_hash_uses_the_same_secret(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_HASH_SECRET", "unit-test-hash-secret")
    get_settings.cache_clear()
    try:
        raw = "recovery-raw"
        assert hash_recovery_token(raw) == digest_token(raw, "unit-test-hash-secret")
        assert hash_recovery_token(raw) != hashlib.sha256(raw.encode()).hexdigest()
    finally:
        get_settings.cache_clear()


def test_hash_and_session_secrets_are_redacted():
    safe, summary = sanitize_observability_payload(
        {
            "jwt_secret": INSECURE_JWT_SECRET,
            "auth_token_hash_secret": "deployed-hash-secret-not-the-default",
            "auth_csrf_secret": "deployed-csrf-secret-not-the-default",
            "dclab_session": "session-value",
            "email": "person@example.test",
        }
    )
    serialized = str(safe)
    assert INSECURE_JWT_SECRET not in serialized
    assert "deployed-hash-secret-not-the-default" not in serialized
    assert "deployed-csrf-secret-not-the-default" not in serialized
    assert "session-value" not in serialized
    assert "person@example.test" not in serialized
    assert summary["redacted_fields"] >= 3
    assert summary["redacted_strings"] >= 1


def test_env_example_documents_session_operations():
    text = (Path(__file__).resolve().parents[3] / ".env.example").read_text(
        encoding="utf-8"
    )
    for key in (
        "SESSION_COOKIE_NAME",
        "SESSION_COOKIE_SECURE",
        "SESSION_IDLE_MINUTES",
        "SESSION_ABSOLUTE_MINUTES",
        "CSRF_COOKIE_NAME",
        "CSRF_HEADER_NAME",
        "CORS_ORIGINS",
        "LOGIN_THROTTLE_ATTEMPTS",
        "AUTH_TOKEN_HASH_SECRET",
        "AUTH_CSRF_SECRET",
        "AUTH_BROWSER_SESSIONS_ENABLED",
    ):
        assert key in text


def test_auth_tables_stay_identity_plane():
    assert AuthSession.__table__.c.user_id is not None
    assert "workspace_id" not in AuthSession.__table__.c
    assert "workspace_id" not in AuthRecoveryToken.__table__.c
