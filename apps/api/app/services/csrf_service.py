"""CSRF tokens and trusted-origin checks. Raw session secrets are never logged."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from fastapi import Request, Response
from starlette.datastructures import Headers

from app.config import Settings, cookie_secure, csrf_hmac_secret, get_settings
from app.services.auth_metrics import record_auth_event

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
BEARER_EXEMPT_PATHS = frozenset({"/auth/tokens"})
ANONYMOUS_CSRF_PATH_PREFIXES = (
    "/auth/login",
    "/auth/register",
    "/auth/password-reset",
    "/auth/email-verification",
)


def trusted_origins(settings: Settings | None = None) -> set[str]:
    cfg = settings or get_settings()
    return {
        origin.strip().rstrip("/")
        for origin in cfg.cors_origins.split(",")
        if origin.strip()
    }


def request_origin(request: Request) -> str | None:
    origin = (request.headers.get("origin") or "").strip()
    if origin:
        return origin.rstrip("/")
    referer = (request.headers.get("referer") or "").strip()
    if not referer:
        return None
    parts = urlsplit(referer)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def tokens_match(left: str, right: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(left.encode("utf-8")).digest(),
        hashlib.sha256(right.encode("utf-8")).digest(),
    )


def csrf_token_for_session(raw_session: str, settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    return hmac.new(
        csrf_hmac_secret(cfg).encode("utf-8"),
        f"csrf-v1:{raw_session}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_anonymous_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def attach_csrf_cookie(
    response: Response, token: str, settings: Settings | None = None
) -> None:
    cfg = settings or get_settings()
    response.set_cookie(
        key=cfg.csrf_cookie_name,
        value=token,
        max_age=int(cfg.session_absolute_minutes * 60),
        path=cfg.session_cookie_path,
        httponly=False,
        secure=cookie_secure(cfg),
        samesite=cfg.session_cookie_samesite,
    )


def clear_csrf_cookie(response: Response, settings: Settings | None = None) -> None:
    cfg = settings or get_settings()
    response.delete_cookie(
        key=cfg.csrf_cookie_name,
        path=cfg.session_cookie_path,
        httponly=False,
        secure=cookie_secure(cfg),
        samesite=cfg.session_cookie_samesite,
    )


def _bearer_present(headers: Headers) -> bool:
    header = headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    return scheme.lower() == "bearer" and bool(token.strip())


def _session_raw(request: Request, settings: Settings) -> str | None:
    header = (request.headers.get("x-dclab-session") or "").strip()
    if header:
        return header
    cookie = request.cookies.get(settings.session_cookie_name)
    if cookie:
        return cookie.strip()
    return None


def csrf_required(request: Request, settings: Settings | None = None) -> bool:
    if request.method not in UNSAFE_METHODS:
        return False
    path = request.url.path
    if path in BEARER_EXEMPT_PATHS:
        return False
    if _bearer_present(request.headers):
        return False
    cfg = settings or get_settings()
    if any(path == prefix or path.startswith(prefix + "/") for prefix in ANONYMOUS_CSRF_PATH_PREFIXES):
        return True
    if path == "/auth/logout" or path.startswith("/auth/logout"):
        return True
    return _session_raw(request, cfg) is not None


def validate_csrf(request: Request, settings: Settings | None = None) -> str | None:
    """Return an error detail or None when the request is allowed."""
    cfg = settings or get_settings()
    if not csrf_required(request, cfg):
        return None
    origin = request_origin(request)
    trusted = trusted_origins(cfg)
    if origin is None or origin not in trusted:
        record_auth_event("csrf", "untrusted_origin")
        return "untrusted origin"
    header_name = cfg.csrf_header_name
    presented = (request.headers.get(header_name) or "").strip()
    cookie = (request.cookies.get(cfg.csrf_cookie_name) or "").strip()
    raw_session = _session_raw(request, cfg)
    if raw_session:
        expected = csrf_token_for_session(raw_session, cfg)
        if not presented or not tokens_match(presented, expected):
            record_auth_event("csrf", "mismatch" if presented else "missing_token")
            return "csrf validation failed"
        return None
    if not presented or not cookie or not tokens_match(presented, cookie):
        record_auth_event("csrf", "mismatch" if presented and cookie else "missing_token")
        return "csrf validation failed"
    return None
