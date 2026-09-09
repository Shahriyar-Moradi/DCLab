"""Structured errors for /v1 HTTP responses."""

from __future__ import annotations

from typing import Any


class DCLabAPIError(Exception):
    """A /v1 response that is not a successful resource payload."""

    def __init__(
        self,
        status_code: int,
        detail: Any,
        *,
        method: str,
        path: str,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.detail = detail
        self.method = method
        self.path = path
        self.request_id = request_id
        super().__init__(f"{method} {path} -> {status_code}: {detail}")


class DCLabClientError(ValueError):
    """Invalid client configuration (timeout, URL) before a request is sent."""
