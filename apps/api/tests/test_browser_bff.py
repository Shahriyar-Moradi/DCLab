"""S0-P02D: BFF contract — no bearer, no authz layer, bounds, request-id, cache."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB = REPO_ROOT / "apps" / "web"
BFF = WEB / "lib" / "infrastructure" / "bff-proxy.ts"
ROUTE = WEB / "app" / "api" / "backend" / "[...path]" / "route.ts"
CLIENT = WEB / "lib" / "infrastructure" / "api-client.ts"
SESSION = WEB / "lib" / "infrastructure" / "session.ts"
CSRF = WEB / "lib" / "infrastructure" / "csrf.ts"
MIDDLEWARE = WEB / "middleware.ts"
PACKAGE = WEB / "package.json"


def _web_sources() -> dict[str, str]:
    files: dict[str, str] = {}
    for path in WEB.rglob("*"):
        if path.suffix not in {".ts", ".tsx", ".js", ".mjs"}:
            continue
        if "node_modules" in path.parts or ".next" in path.parts:
            continue
        files[str(path.relative_to(REPO_ROOT))] = path.read_text(encoding="utf-8")
    return files


def test_bff_route_is_a_thin_proxy():
    route = ROUTE.read_text(encoding="utf-8")
    bff = BFF.read_text(encoding="utf-8")
    assert "proxyBackend" in route
    assert "export const HEAD = handle" in route
    assert "dclab_admin" not in route
    assert "Authorization" not in bff
    assert "console.log" not in bff
    assert "console.info" not in bff
    assert "backend unavailable" in bff
    assert "payload too large" in bff
    assert "no-store" in bff
    assert "X-Request-Id" in bff
    assert "crypto.randomUUID" in bff
    assert "256 * 1024 * 1024" in bff
    assert "512 * 1024 * 1024" in bff
    assert "Never a second authorization layer" in bff


def test_javascript_has_no_bearer_or_session_cookie_helpers():
    sources = _web_sources()
    session = sources["apps/web/lib/infrastructure/session.ts"]
    csrf = sources["apps/web/lib/infrastructure/csrf.ts"]
    client = sources["apps/web/lib/infrastructure/api-client.ts"]
    assert "document.cookie" not in session
    assert "localStorage" not in session
    assert "localStorage" not in client
    assert "Authorization" not in client
    assert "dclab_token" not in session
    assert "dclab_session" not in csrf
    assert "document.cookie" in csrf
    assert '"jose"' not in PACKAGE.read_text(encoding="utf-8")
    assert "jose" not in MIDDLEWARE.read_text(encoding="utf-8")
    login = (WEB / "app" / "login" / "page.tsx").read_text(encoding="utf-8")
    assert "access_token" not in login
    assert "Bearer" not in login


def test_bff_maps_upstream_failure_to_bounded_json():
    bff = BFF.read_text(encoding="utf-8")
    assert "return jsonError(502, BACKEND_UNAVAILABLE, requestId)" in bff
    assert "return jsonError(413, PAYLOAD_TOO_LARGE, requestId)" in bff
    assert "return jsonError(502, RESPONSE_TOO_LARGE, requestId)" in bff
    bff = BFF.read_text(encoding="utf-8")
    route = ROUTE.read_text(encoding="utf-8")
    for forbidden in ("require_admin", "dclab_admin", "user.role", "can_write"):
        assert forbidden not in bff
        assert forbidden not in route


def test_middleware_session_probe_is_not_jwt():
    src = MIDDLEWARE.read_text(encoding="utf-8")
    assert "jose" not in src
    assert "jwtVerify" not in src
    assert "atob" not in src
    assert "/auth/me" in src
    assert "X-DCLab-Session" in src
    assert "the API authorizes" in src.lower() or "API authorizes" in src
