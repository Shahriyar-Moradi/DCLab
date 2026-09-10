"""Browser CSRF, origin, and security-header middleware."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings, is_production_env
from app.services.csrf_service import validate_csrf
from app.services.request_ids import REQUEST_ID_HEADER, bind_request_id

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
HSTS = "max-age=31536000; includeSubDomains"


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        detail = validate_csrf(request)
        if detail is not None:
            return JSONResponse({"detail": detail}, status_code=403)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = bind_request_id(request)
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = getattr(
            request.state, "request_id", request_id
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault("Content-Security-Policy", API_CSP)
        if request.url.path.startswith("/auth"):
            response.headers["Cache-Control"] = "no-store"
        if is_production_env(get_settings()):
            response.headers.setdefault("Strict-Transport-Security", HSTS)
        return response
