"""Bounded HTTP transport for /v1. No SQLAlchemy, sessions, or engine imports."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx

from dclab_client._version import USER_AGENT
from dclab_client.errors import DCLabAPIError, DCLabClientError, error_class_for

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 120.0
V1_PREFIX = "/v1"
IDEMPOTENT_METHODS_NEEDING_KEY = frozenset({"POST", "PUT", "PATCH"})


def bound_timeout(timeout: float) -> float:
    if type(timeout) is bool or not isinstance(timeout, (int, float)):
        raise DCLabClientError("timeout must be a positive number of seconds")
    if timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise DCLabClientError(
            f"timeout must be in (0, {MAX_TIMEOUT_SECONDS}] seconds"
        )
    return float(timeout)


def _as_id(value: UUID | str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise DCLabClientError("workspace_id must be a UUID") from exc


def _api_error(
    response: httpx.Response,
    *,
    method: str,
    path: str,
    request_id: str | None,
    idempotency_key: str | None,
) -> DCLabAPIError:
    """Typed error from the /v1 envelope; legacy ``{"detail": ...}`` bodies still map."""

    try:
        payload = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        code = error["code"]
        details = error.get("details")
        retryable = error.get("retryable")
        return error_class_for(response.status_code, code)(
            response.status_code,
            error,
            method=method,
            path=path,
            request_id=str(error.get("request_id") or "") or request_id,
            code=code,
            message=str(error.get("message") or ""),
            retryable=retryable if isinstance(retryable, bool) else None,
            details=details if isinstance(details, dict) else None,
            idempotency_key=idempotency_key,
        )
    if isinstance(payload, dict) and "detail" in payload:
        detail: Any = payload["detail"]
    elif payload is not None:
        detail = payload
    else:
        detail = response.text
    return error_class_for(response.status_code, None)(
        response.status_code,
        detail,
        method=method,
        path=path,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


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
        """``idempotency_key`` (deprecated): one fixed key for every POST of this
        client. Each command then needs a distinct body or the server answers 409;
        prefer per-call keys, or none (a fresh key is generated per POST)."""

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
        if_match: str | None = None,
    ) -> Any:
        payload, _headers = self.request_with_headers(
            method,
            path,
            json=json,
            params=params,
            request_id=request_id,
            idempotency_key=idempotency_key,
            if_match=if_match,
        )
        return payload

    def request_with_headers(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        if_match: str | None = None,
        data: dict[str, str] | None = None,
        files: dict[str, Any] | None = None,
    ) -> tuple[Any, httpx.Headers]:
        """Payload plus response headers (``ETag``, ``Idempotent-Replayed``, ``X-Request-Id``).

        ``data`` + ``files`` send ``multipart/form-data`` instead of ``json``."""

        url_path = self._v1_path(path)
        if url_path not in {"/v1/me", "/v1/workspaces"} and self._workspace_id is None:
            raise DCLabClientError("workspace_id is required for workspace resources")
        headers = self._headers(
            method=method,
            request_id=request_id,
            idempotency_key=idempotency_key,
            if_match=if_match,
        )
        query = None
        if params:
            query = {key: value for key, value in params.items() if value is not None}
        request_kwargs: dict[str, Any] = {
            "params": query or None,
            "headers": headers,
        }
        if files is not None:
            request_kwargs.update(data=data, files=files)
        else:
            request_kwargs["json"] = json
        if not type(self._http).__module__.startswith("starlette."):
            request_kwargs["timeout"] = self._timeout
        response = self._http.request(method, url_path, **request_kwargs)
        rid = response.headers.get("x-request-id") or headers.get("X-Request-Id")
        sent_key = headers.get("Idempotency-Key")
        if response.status_code >= 400:
            raise _api_error(
                response,
                method=method.upper(),
                path=url_path,
                request_id=rid,
                idempotency_key=sent_key,
            )
        if not response.content:
            return None, response.headers
        try:
            return response.json(), response.headers
        except ValueError as exc:
            raise DCLabAPIError(
                response.status_code,
                response.text,
                method=method.upper(),
                path=url_path,
                request_id=rid,
                idempotency_key=sent_key,
            ) from exc

    def _v1_path(self, path: str) -> str:
        cleaned = "/" + path.lstrip("/")
        # Defense in depth: no dot segments, escapes or separators that an HTTP
        # client could normalise into a path outside /v1.
        if (
            "%" in cleaned
            or "\\" in cleaned
            or any(segment in {".", ".."} for segment in cleaned.split("/"))
        ):
            raise DCLabClientError("dclab_client only calls /v1 paths")
        if cleaned == V1_PREFIX or cleaned.startswith(V1_PREFIX + "/"):
            return cleaned
        raise DCLabClientError("dclab_client only calls /v1 paths")

    def _headers(
        self,
        *,
        method: str,
        request_id: str | None,
        idempotency_key: str | None,
        if_match: str | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._workspace_id is not None:
            headers["X-Workspace-Id"] = self._workspace_id
        rid = (request_id or "").strip() or self._request_id
        if rid:
            headers["X-Request-Id"] = rid
        if method.upper() in IDEMPOTENT_METHODS_NEEDING_KEY:
            # Every command carries a key (P3.1-A). A generated key is exposed as
            # ``DCLabAPIError.idempotency_key``: resend with it to retry safely
            # (httpx does not retry on its own).
            key = (idempotency_key or "").strip() or self._idempotency_key or str(uuid4())
            headers["Idempotency-Key"] = key
        if if_match is not None and if_match.strip():
            headers["If-Match"] = if_match.strip()
        return headers
