"""HTTP request telemetry (P8.5-A): a pure ASGI middleware installed innermost, so the request
id bound by ``SecurityHeadersMiddleware`` is already in ``scope["state"]``.

Labels are the route template (``<unmatched>`` for 404s), the method and the status class:
never the raw path, query string, ids or headers. With ``LOG_FORMAT=json`` it also writes the
access line (``dclab.access``) that replaces uvicorn's raw-path access log.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.observability import metrics, tracing
from app.observability.labels import http_method, status_class
from app.observability.logging import json_access_logs, request_id_var

access_logger = logging.getLogger("dclab.access")
UNMATCHED = "<unmatched>"


def route_template(scope: dict[str, Any]) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else UNMATCHED


class ObservabilityMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        state = scope.get("state") or {}
        token = request_id_var.set(state.get("request_id") if isinstance(state, dict) else None)
        method = http_method(scope.get("method", ""))
        started = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                status["code"] = int(message.get("status", 500))
            await send(message)

        try:
            with tracing.span("http.request", method=method) as current:
                try:
                    await self.app(scope, receive, send_wrapper)
                finally:
                    route = route_template(scope)
                    current.rename(f"{method} {route}")
                    current.set(route=route, status_code=status["code"], status_class=status_class(status["code"]))
        finally:
            elapsed = time.perf_counter() - started
            route = route_template(scope)
            labels = {"route": route, "method": method, "status_class": status_class(status["code"])}
            metrics.add("dclab_http_requests", **labels)
            metrics.observe("dclab_http_request_duration_seconds", elapsed, **labels)
            if json_access_logs():
                access_logger.info(
                    "http_request",
                    extra={"method": method, "route": route, "status": status["code"],
                           "duration_ms": round(elapsed * 1000, 1)},
                )
            request_id_var.reset(token)
