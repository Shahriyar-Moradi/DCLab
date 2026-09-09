"""Bounded HTTP transport for /v1. No SQLAlchemy, sessions, or engine imports."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from dclab_client.errors import DCLabAPIError, DCLabClientError

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 120.0
V1_PREFIX = "/v1"


def bound_timeout(timeout: float) -> float:
    if type(timeout) is bool or not isinstance(timeout, (int, float)):
        raise DCLabClientError("timeout must be a positive number of seconds")
    if timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise DCLabClientError(
            f"timeout must be in (0, {MAX_TIMEOUT_SECONDS}] seconds"
        )
    return float(timeout)


def _as_id(value: UUID | str) -> str:
    return str(value)


def _detail_from_response(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict) and "detail" in payload:
        return payload["detail"]
    return payload


class V1Transport:
    """httpx wrapper that only issues /v1 paths."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        workspace_id: UUID | str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        if not (base_url or "").strip():
            raise DCLabClientError("base_url is required")
        self._timeout = bound_timeout(timeout)
        self._token = (token or "").strip() or None
        self._workspace_id = (
            _as_id(workspace_id) if workspace_id is not None else None
        )
        self._request_id = (request_id or "").strip() or None
        self._idempotency_key = (idempotency_key or "").strip() or None
        self._owns_http = http is None
        root = base_url.strip().rstrip("/")
        self._http = http or httpx.Client(base_url=root, timeout=self._timeout)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        url_path = self._v1_path(path)
        headers = self._headers(
            method=method,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        query = None
        if params:
            query = {key: value for key, value in params.items() if value is not None}
        request_kwargs: dict[str, Any] = {
            "json": json,
            "params": query or None,
            "headers": headers,
        }
        if not type(self._http).__module__.startswith("starlette."):
            request_kwargs["timeout"] = self._timeout
        response = self._http.request(method, url_path, **request_kwargs)
        rid = request_id or self._request_id or response.headers.get("x-request-id")
        if response.status_code >= 400:
            raise DCLabAPIError(
                response.status_code,
                _detail_from_response(response),
                method=method.upper(),
                path=url_path,
                request_id=rid,
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise DCLabAPIError(
                response.status_code,
                response.text,
                method=method.upper(),
                path=url_path,
                request_id=rid,
            ) from exc

    def _v1_path(self, path: str) -> str:
        cleaned = "/" + path.lstrip("/")
        if cleaned == V1_PREFIX or cleaned.startswith(V1_PREFIX + "/"):
            return cleaned
        raise DCLabClientError("dclab_client only calls /v1 paths")

    def _headers(
        self,
        *,
        method: str,
        request_id: str | None,
        idempotency_key: str | None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._workspace_id is not None:
            headers["X-Workspace-Id"] = self._workspace_id
        rid = (request_id or "").strip() or self._request_id
        if rid:
            headers["X-Request-Id"] = rid
        key = (idempotency_key or "").strip() or self._idempotency_key
        if key and method.upper() in {"POST", "PUT", "PATCH"}:
            headers["Idempotency-Key"] = key
        return headers
