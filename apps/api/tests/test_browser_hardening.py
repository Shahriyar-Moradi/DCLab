"""S0-P02B: CSRF, origin, throttling, CSP, recovery hooks, concurrent sessions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import INSECURE_JWT_SECRET, Settings, get_settings, validate_runtime_settings
from app.db.models import AuthRecoveryToken, AuthSession, WorkspaceMembership
from app.services.login_throttle import reset_login_throttle
from app.services.observability_service import sanitize_observability_payload
from app.services.recovery_service import (
    PURPOSE_EMAIL_VERIFICATION,
    PURPOSE_PASSWORD_RESET,
    issue_recovery_token,
)
from conftest import browser_login, csrf_headers

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_ROOT = REPO_ROOT / "apps" / "web"


def test_login_without_origin_or_csrf_is_forbidden(client, client_user):
    response = client.post(
        "/auth/login",
        json={"email": client_user.email, "password": "client-pass-123"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] in {"untrusted origin", "csrf validation failed"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_csrf_from_untrusted_origin_is_forbidden(client, client_user):
    headers = csrf_headers(client)
    headers["Origin"] = "https://evil.example"
    response = client.post(
        "/auth/login",
        json={"email": client_user.email, "password": "client-pass-123"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "untrusted origin"


def test_wrong_csrf_token_is_forbidden(client, client_user):
    headers = csrf_headers(client)
    headers["X-CSRF-Token"] = "not-the-csrf-token"
    response = client.post(
        "/auth/login",
        json={"email": client_user.email, "password": "client-pass-123"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "csrf validation failed"


def test_bearer_logout_all_skips_csrf(client, client_token):
    response = client.post(
        "/auth/logout-all",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert response.status_code == 204


def test_tokens_path_skips_csrf(client, client_user):
    response = client.post(
        "/auth/tokens",
        json={"email": client_user.email, "password": "client-pass-123"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_security_headers_on_health(client):
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_auth_responses_are_not_stored(client, client_user):
    response = browser_login(client, client_user.email, "client-pass-123")
    assert "no-store" in response.headers.get("cache-control", "")
    me = client.get("/auth/me")
    assert "no-store" in me.headers.get("cache-control", "")


def test_uniform_credential_errors_for_disabled_and_unknown(client, client_user, db_session):
    unknown = client.post(
        "/auth/login",
        json={"email": "missing@test.invalid", "password": "nope-nope-1"},
        headers=csrf_headers(client),
    )
    wrong = client.post(
        "/auth/login",
        json={"email": client_user.email, "password": "nope-nope-1"},
        headers=csrf_headers(client),
    )
    client_user.is_active = False
    db_session.commit()
    disabled = client.post(
        "/auth/login",
        json={"email": client_user.email, "password": "client-pass-123"},
        headers=csrf_headers(client),
    )
    assert unknown.status_code == wrong.status_code == disabled.status_code == 401
    assert unknown.json()["detail"] == "invalid email or password"
    assert wrong.json()["detail"] == "invalid email or password"
    assert disabled.json()["detail"] == "invalid email or password"


def test_login_error_does_not_echo_password(client, client_user):
    secret = "super-secret-password-xyz"
    response = client.post(
        "/auth/login",
        json={"email": client_user.email, "password": secret},
        headers=csrf_headers(client),
    )
    assert response.status_code == 401
    assert secret not in response.text


def test_login_throttle_then_recovers_with_injected_clock(client, client_user, monkeypatch):
    from app.services import login_throttle

    reset_login_throttle()
    start = datetime(2026, 3, 1, tzinfo=UTC)
    current = {"now": start}

    def _now():
        return current["now"]

    monkeypatch.setattr(login_throttle, "utcnow", _now)
    for _ in range(5):
        failed = client.post(
            "/auth/login",
            json={"email": client_user.email, "password": "wrong-password"},
            headers=csrf_headers(client),
        )
        assert failed.status_code == 401
    blocked = client.post(
        "/auth/login",
        json={"email": client_user.email, "password": "client-pass-123"},
        headers=csrf_headers(client),
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "too many attempts"
    current["now"] = start + timedelta(minutes=16)
    recovered = browser_login(client, client_user.email, "client-pass-123")
    assert recovered.status_code == 200
    reset_login_throttle()


def test_forwarded_for_ignored_unless_trusted(client, client_user, monkeypatch):
    reset_login_throttle()
    monkeypatch.setenv("AUTH_TRUST_FORWARDED", "false")
    get_settings.cache_clear()
    try:
        for _ in range(5):
            client.post(
                "/auth/login",
                json={"email": client_user.email, "password": "wrong-password"},
                headers={
                    **csrf_headers(client),
                    "X-Forwarded-For": "203.0.113.9",
                },
            )
        blocked = client.post(
            "/auth/login",
            json={"email": client_user.email, "password": "wrong-password"},
            headers={
                **csrf_headers(client),
                "X-Forwarded-For": "198.51.100.10",
            },
        )
        assert blocked.status_code == 429
    finally:
        get_settings.cache_clear()
        reset_login_throttle()


def test_concurrent_session_cap_revokes_oldest(
    client, client_user, db_session, monkeypatch
):
    monkeypatch.setenv("SESSION_MAX_CONCURRENT", "2")
    get_settings.cache_clear()
    try:
        raws: list[str] = []
        for _ in range(3):
            client.cookies.clear()
            response = browser_login(client, client_user.email, "client-pass-123")
            assert response.status_code == 200
            raws.append(response.cookies.get("dclab_session"))
        db_session.expire_all()
        active = (
            db_session.query(AuthSession)
            .filter(AuthSession.revoked_at.is_(None))
            .all()
        )
        assert len(active) == 2
        assert (
            client.get("/auth/me", headers={"X-DCLab-Session": raws[0]}).status_code
            == 401
        )
        assert (
            client.get("/auth/me", headers={"X-DCLab-Session": raws[2]}).status_code
            == 200
        )
    finally:
        get_settings.cache_clear()


def test_logout_all_revokes_every_session(client, client_user, db_session):
    first = browser_login(client, client_user.email, "client-pass-123")
    first_raw = first.cookies.get("dclab_session")
    client.cookies.clear()
    browser_login(client, client_user.email, "client-pass-123")
    done = client.post("/auth/logout-all", headers=csrf_headers(client))
    assert done.status_code == 204
    assert client.get("/auth/me").status_code == 401
    assert (
        client.get("/auth/me", headers={"X-DCLab-Session": first_raw}).status_code
        == 401
    )
    db_session.expire_all()
    assert all(row.revoked_at is not None for row in db_session.query(AuthSession).all())


def test_suspended_membership_does_not_fall_through(
    client, client_user, db_session, client_token
):
    membership = (
        db_session.query(WorkspaceMembership)
        .filter(WorkspaceMembership.user_id == client_user.id)
        .one()
    )
    membership.suspended_at = datetime.now(UTC)
    db_session.commit()
    response = client.get(
        "/app/opportunities",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert response.status_code == 403
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {client_token}"})
    assert me.status_code == 200


def test_password_reset_hooks_do_not_leak_token(client, client_user, db_session):
    unknown = client.post(
        "/auth/password-reset/request",
        json={"email": "nobody@test.invalid"},
        headers=csrf_headers(client),
    )
    known = client.post(
        "/auth/password-reset/request",
        json={"email": client_user.email},
        headers=csrf_headers(client),
    )
    assert unknown.status_code == known.status_code == 204
    assert unknown.content == known.content == b""
    row = db_session.query(AuthRecoveryToken).one()
    assert row.purpose == PURPOSE_PASSWORD_RESET
    assert len(row.token_hash) == 64
    assert "token" not in known.text.lower() or known.content == b""


def test_password_reset_confirm_revokes_sessions(client, client_user, db_session):
    raw = issue_recovery_token(db_session, client_user, PURPOSE_PASSWORD_RESET)
    db_session.commit()
    assert raw not in row_hashes(db_session)
    browser_login(client, client_user.email, "client-pass-123")
    confirm = client.post(
        "/auth/password-reset/confirm",
        json={"token": raw, "password": "replacement-pass-1"},
        headers=csrf_headers(client),
    )
    assert confirm.status_code == 204
    assert client.get("/auth/me").status_code == 401
    old = client.post(
        "/auth/login",
        json={"email": client_user.email, "password": "client-pass-123"},
        headers=csrf_headers(client),
    )
    assert old.status_code == 401
    new = browser_login(client, client_user.email, "replacement-pass-1")
    assert new.status_code == 200


def test_email_verification_confirm_sets_timestamp(client, client_user, db_session):
    raw = issue_recovery_token(db_session, client_user, PURPOSE_EMAIL_VERIFICATION)
    db_session.commit()
    confirm = client.post(
        "/auth/email-verification/confirm",
        json={"token": raw},
        headers=csrf_headers(client),
    )
    assert confirm.status_code == 204
    db_session.refresh(client_user)
    assert client_user.email_verified_at is not None
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {client_user_token(client_user)}"})
    assert me.status_code == 200
    assert me.json()["email_verified_at"] is not None


def client_user_token(user):
    from app.services.auth_service import create_access_token

    return create_access_token(user)


def row_hashes(db_session) -> str:
    return " ".join(row.token_hash for row in db_session.query(AuthRecoveryToken).all())


def test_recovery_confirm_rejects_unknown_token(client):
    response = client.post(
        "/auth/password-reset/confirm",
        json={"token": "no-such-token", "password": "replacement-pass-1"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid or expired token"


def test_email_delivery_flag_fails_closed():
    settings = Settings.model_construct(
        dclab_env="development",
        jwt_secret=INSECURE_JWT_SECRET,
        auth_email_delivery_enabled=True,
    )
    try:
        validate_runtime_settings(settings)
        raise AssertionError("expected fail-closed email delivery flag")
    except RuntimeError as exc:
        assert "AUTH_EMAIL_DELIVERY_ENABLED" in str(exc)


def test_xss_payload_stays_json_text(client, client_user, db_session):
    client_user.full_name = "<script>alert(1)</script>"
    db_session.commit()
    me = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {client_user_token(client_user)}"},
    )
    assert me.status_code == 200
    assert me.json()["full_name"] == "<script>alert(1)</script>"
    assert me.headers["content-type"].startswith("application/json")


def test_web_sources_have_no_html_injection_sinks():
    hits: list[str] = []
    for path in WEB_ROOT.rglob("*"):
        if path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        if "node_modules" in path.parts or ".next" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "dangerouslySetInnerHTML" in text or ".innerHTML" in text:
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []


def test_csrf_and_session_secrets_are_redacted():
    safe, summary = sanitize_observability_payload(
        {
            "csrf_token": "abc123csrf",
            "dclab_csrf": "cookie-value",
            "dclab_session": "session-value",
            "recovery_token": "reset-raw",
        }
    )
    serialized = str(safe)
    assert "abc123csrf" not in serialized
    assert "cookie-value" not in serialized
    assert "session-value" not in serialized
    assert "reset-raw" not in serialized
    assert summary["redacted_fields"] >= 3


def test_next_csp_header_is_configured():
    config = (WEB_ROOT / "next.config.mjs").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in config
    assert "frame-ancestors 'none'" in config
    assert "form-action 'self'" in config
