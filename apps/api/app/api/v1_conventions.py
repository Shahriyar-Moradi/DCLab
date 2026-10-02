"""/v1 contract conventions (P3.1-A). Transport only; /v1 paths only.

* **Error envelope.** Every /v1 error body is
  ``{"error": {"code", "message", "retryable", "request_id", "details"}}``
  (``V1ErrorEnvelope``). ``code`` is a stable lower_snake identifier,
  ``retryable`` says whether resending the *same* request may succeed, and
  ``request_id`` equals the ``X-Request-Id`` response header. Non-/v1
  surfaces keep FastAPI's ``{"detail": ...}`` shape: the handlers below
  delegate to FastAPI's defaults outside /v1.
* **Request ids.** ``SecurityHeadersMiddleware`` binds one per request (a
  validated inbound ``X-Request-Id`` or a fresh UUID) and echoes it; /v1
  errors carry it in the body and in logs.
* **ETag / If-Match.** Mutable single resources return a strong ``ETag``:
  ``"<version>"`` for rows with an optimistic ``version`` (project refs,
  ADR 0006 §2) and ``"<sha256-128 of the JSON representation>"`` otherwise.
  Mutations accept ``If-Match`` (strong comparison, ``*`` matches any
  current state): a mismatch is ``412 precondition_failed``. ``If-Match`` is
  optional on existing commands (back-compat); a command declared
  ``required`` answers ``428 precondition_required`` when it is missing.
* **Idempotency-Key.** Every /v1 POST command accepts ``Idempotency-Key``
  (``[A-Za-z0-9._:-]{1,128}``). The key is bound to a request digest
  (``app.domain.idempotency``) stored on the resource the command creates; a
  replay returns the stored resource (``Idempotent-Replayed: true``), a
  different request under the same key is ``409 idempotency_key_conflict``.
  POST without a key executes once per call (not replay-protected); commands
  may declare the key ``required`` (``400 idempotency_key_required``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Header, Request, Response
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.idempotency import (
    IDEMPOTENCY_KEY_HEADER,
    is_reserved_idempotency_key,
    IDEMPOTENCY_KEY_MAX_CHARS,
    IDEMPOTENCY_KEY_PATTERN,
    IDEMPOTENT_REPLAY_HEADER,
    IdempotencyBinding,
    request_digest,
)
from app.services.request_ids import REQUEST_ID_HEADER, request_id_of

logger = logging.getLogger("dclab.api.v1")

V1_PREFIX = "/v1"
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
VALIDATION_ERRORS_MAX = 20
_CODE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")

STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    408: "request_timeout",
    409: "conflict",
    412: "precondition_failed",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_failed",
    428: "precondition_required",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "unavailable",
    504: "gateway_timeout",
}


def is_v1_path(path: str) -> bool:
    return path == V1_PREFIX or path.startswith(V1_PREFIX + "/")


# --- envelope -------------------------------------------------------------------


class V1Error(BaseModel):
    code: str = Field(description="Stable lower_snake error code; branch on this, not on message.")
    message: str = Field(description="Human-readable summary. Not stable; never parse it.")
    retryable: bool = Field(description="True when resending the same request (same Idempotency-Key) may succeed.")
    request_id: str = Field(description="Equals the X-Request-Id response header; quote it in support requests.")
    details: dict[str, Any] = Field(
        default_factory=dict, description="Code-specific structured context (bounded; never secrets or rows)."
    )


class V1ErrorEnvelope(BaseModel):
    error: V1Error


class V1APIError(Exception):
    """Raised by /v1 routes and helpers; rendered as the envelope."""

    def __init__(
        self,
        status_code: int,
        code: str | None = None,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = int(status_code)
        self.code = normalize_code(code, self.status_code)
        self.message = message or _phrase(self.status_code)
        self.details = dict(details or {})
        self.retryable = self.status_code in RETRYABLE_STATUSES if retryable is None else retryable
        self.headers = headers
        super().__init__(f"{self.status_code} {self.code}: {self.message}")


def _phrase(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase.lower()
    except ValueError:
        return "error"


def normalize_code(code: Any, status_code: int) -> str:
    text = str(code).strip().lower() if isinstance(code, str) else ""
    if _CODE.fullmatch(text):
        return text
    return STATUS_CODES.get(status_code, "error" if status_code < 500 else "internal_error")


def domain_error(exc: Exception, *, status_code: int | None = None, code: str | None = None) -> V1APIError:
    """Envelope for a typed domain error (``status_code`` / ``code`` / ``public_detail()``)."""

    status = int(status_code or getattr(exc, "status_code", None) or 400)
    public = exc.public_detail() if callable(getattr(exc, "public_detail", None)) else None
    if isinstance(public, dict):
        details = dict(public)
        found = details.pop("code", None)
        message = details.pop("message", None) or str(exc)
        return V1APIError(status, code or found, str(message), details=details)
    return V1APIError(status, code or getattr(exc, "code", None), str(exc) or None)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", None) or "<unmatched>"


def v1_error_response(
    request: Request,
    status_code: int,
    code: str | None = None,
    message: str | None = None,
    *,
    details: dict[str, Any] | None = None,
    retryable: bool | None = None,
    headers: dict[str, str] | None = None,
    exc: BaseException | None = None,
) -> JSONResponse:
    error = V1APIError(status_code, code, message, details=details, retryable=retryable)
    request_id = request_id_of(request)
    body = V1ErrorEnvelope(
        error=V1Error(
            code=error.code,
            message=error.message,
            retryable=error.retryable,
            request_id=request_id,
            details=error.details,
        )
    )
    # Starlette re-raises unhandled errors (one traceback); this line correlates them.
    log = logger.error if error.status_code >= 500 else logger.info
    log(
        "v1_error status=%s code=%s request_id=%s method=%s route=%s exception=%s",
        error.status_code,
        error.code,
        request_id,
        request.method,
        _route_template(request),
        type(exc).__name__ if exc is not None else "-",
    )
    response_headers = dict(headers or {})
    response_headers[REQUEST_ID_HEADER] = request_id
    response_headers["Cache-Control"] = "no-store"
    # 500s are sent outside SecurityHeadersMiddleware; keep the essential header.
    response_headers["X-Content-Type-Options"] = "nosniff"
    return JSONResponse(
        body.model_dump(mode="json"), status_code=error.status_code, headers=response_headers
    )


# --- exception handlers -----------------------------------------------------------------


async def _v1_api_error_handler(request: Request, exc: V1APIError) -> Response:
    return v1_error_response(
        request,
        exc.status_code,
        exc.code,
        exc.message,
        details=exc.details,
        retryable=exc.retryable,
        headers=exc.headers,
    )


async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> Response:
    if not is_v1_path(request.url.path):
        return await http_exception_handler(request, exc)
    detail = exc.detail
    details: dict[str, Any] = {}
    code = None
    if isinstance(detail, dict):
        details = dict(detail)
        code = details.pop("code", None)
        message = details.pop("message", None) or details.pop("detail", None)
    else:
        message = detail
    return v1_error_response(
        request,
        exc.status_code,
        code,
        str(message) if message else None,
        details=details,
        headers=getattr(exc, "headers", None),
    )


def _validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    # Never echo ``input``/``ctx``: they can carry the caller's payload.
    errors = []
    for item in list(exc.errors())[:VALIDATION_ERRORS_MAX]:
        errors.append(
            {
                "loc": [part if isinstance(part, int) else str(part)[:64] for part in item.get("loc", ())],
                "msg": str(item.get("msg", ""))[:256],
                "type": str(item.get("type", ""))[:64],
            }
        )
    return errors


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> Response:
    if not is_v1_path(request.url.path):
        return await request_validation_exception_handler(request, exc)
    return v1_error_response(
        request,
        422,
        "validation_failed",
        "request validation failed",
        details={"errors": _validation_errors(exc)},
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> Response:
    if not is_v1_path(request.url.path):
        # Starlette's own production response for non-/v1 surfaces (unchanged).
        return PlainTextResponse("Internal Server Error", status_code=500)
    return v1_error_response(request, 500, "internal_error", "internal error", exc=exc)


def install_v1_conventions(app: FastAPI) -> None:
    app.add_exception_handler(V1APIError, _v1_api_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)


# --- OpenAPI ------------------------------------------------------------------------------

COMMON_ERROR_STATUSES = (400, 401, 403, 404, 422, 500)
ETAG_HEADER_DOC = {
    "ETag": {
        "description": "Strong entity tag of this representation; send it as If-Match on mutations.",
        "schema": {"type": "string"},
    }
}
REPLAY_HEADER_DOC = {
    IDEMPOTENT_REPLAY_HEADER: {
        "description": "`true` when this response replays an earlier request with the same Idempotency-Key.",
        "schema": {"type": "string"},
    }
}


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    return {
        status: {"model": V1ErrorEnvelope, "description": f"{_phrase(status).capitalize()} (error envelope)"}
        for status in statuses
    }


# --- ETag / If-Match -----------------------------------------------------------------------


def representation_etag(model: BaseModel) -> str:
    encoded = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return '"' + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32] + '"'


def version_etag(version: int) -> str:
    """ETag of a row with an optimistic ``version`` column (ADR 0006 §2 refs)."""

    return f'"{int(version)}"'


def set_etag(response: Response, etag: str) -> None:
    response.headers["ETag"] = etag


def _if_match_tags(value: str) -> list[str]:
    return [tag.strip() for tag in value.split(",") if tag.strip()]


def check_if_match(if_match: str | None, current_etag: str, *, required: bool = False) -> None:
    """Strong If-Match evaluation (RFC 9110 §13.1.1). Weak tags never match."""

    if if_match is None or not if_match.strip():
        if required:
            raise V1APIError(428, "precondition_required", "If-Match is required for this command")
        return
    tags = _if_match_tags(if_match)
    if "*" in tags or current_etag in tags:
        return
    raise V1APIError(
        412,
        "precondition_failed",
        "the resource changed; re-read it and retry with its current ETag",
        details={"current_etag": current_etag},
    )


def if_match_version(if_match: str | None, *, required: bool = False) -> int | None:
    """``If-Match: "<version>"`` → expected version (refs, P3.1-B); ``*``/absent → None."""

    if if_match is None or not if_match.strip():
        if required:
            raise V1APIError(428, "precondition_required", "If-Match is required for this command")
        return None
    tags = _if_match_tags(if_match)
    if tags == ["*"]:
        return None
    if len(tags) == 1 and re.fullmatch(r'"[1-9][0-9]{0,9}"', tags[0]):
        return int(tags[0].strip('"'))
    raise V1APIError(412, "precondition_failed", 'If-Match must be one strong version tag such as "3"')


def if_match_header(
    if_match: str | None = Header(
        None,
        alias="If-Match",
        max_length=512,
        description="Strong ETag from a previous read; a mismatch is 412 precondition_failed.",
    ),
) -> str | None:
    return if_match


# --- Idempotency-Key ----------------------------------------------------------------------------


def idempotency_key_header(
    idempotency_key: str | None = Header(
        None,
        alias=IDEMPOTENCY_KEY_HEADER,
        max_length=IDEMPOTENCY_KEY_MAX_CHARS + 64,
        description=(
            "Client-chosen key ([A-Za-z0-9._:-]{1,128}) binding this POST to its request digest: "
            "a replay returns the original result, a different request under the key is 409."
        ),
    ),
) -> str | None:
    if idempotency_key is None:
        return None
    key = idempotency_key.strip()
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise V1APIError(
            400,
            "invalid_idempotency_key",
            f"Idempotency-Key must be 1-{IDEMPOTENCY_KEY_MAX_CHARS} characters of [A-Za-z0-9._:-]",
        )
    return key


def idempotency_binding(
    *,
    operation: str,
    principal_id: Any,
    header_key: str | None,
    body: Any,
    path_params: dict[str, Any] | None = None,
    body_key: str | None = None,
    required: bool = False,
) -> IdempotencyBinding:
    """Key (header, or a legacy body field) + request digest for one POST command."""

    legacy = (body_key or "").strip() or None
    for candidate in (header_key, legacy):
        if candidate is not None and is_reserved_idempotency_key(candidate):
            raise V1APIError(400, "invalid_idempotency_key", "this Idempotency-Key namespace is reserved")
    if header_key is not None and legacy is not None and header_key != legacy:
        raise V1APIError(
            400,
            "idempotency_key_mismatch",
            "the Idempotency-Key header and the body idempotency_key differ",
        )
    key = header_key or legacy
    if key is None and required:
        raise V1APIError(400, "idempotency_key_required", "this command requires an Idempotency-Key header")
    digest = request_digest(
        operation=operation, principal_id=principal_id, path_params=path_params, body=body
    )
    source = "header" if header_key is not None else ("body" if legacy is not None else "none")
    return IdempotencyBinding(key=key, digest=digest, source=source)


def mark_replayed(response: Response, replayed: bool) -> None:
    if replayed:
        response.headers[IDEMPOTENT_REPLAY_HEADER] = "true"
