"""Structured errors for /v1 HTTP responses.

/v1 answers errors with ``{"error": {code, message, retryable, request_id,
details}}`` (P3.1-A). ``DCLabAPIError`` carries those fields; subclasses let
callers branch on the class (or on the stable ``code``) instead of parsing
messages. Older servers answering ``{"detail": ...}`` still map onto the same
classes with a code derived from the status.
"""

from __future__ import annotations

from typing import Any

RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    412: "precondition_failed",
    422: "validation_failed",
    428: "precondition_required",
    429: "rate_limited",
}


class DCLabAPIError(Exception):
    """A /v1 response that is not a successful resource payload.

    ``detail`` is the server's error object (the envelope's ``error`` dict, or
    a legacy ``detail`` value); ``code``/``message``/``retryable``/``details``
    are the typed envelope fields. ``idempotency_key`` is the key the client
    sent, so a retryable failure can be resent safely with the same key.
    """

    def __init__(
        self,
        status_code: int,
        detail: Any,
        *,
        method: str,
        path: str,
        request_id: str | None = None,
        code: str | None = None,
        message: str | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.detail = detail
        self.method = method
        self.path = path
        self.request_id = request_id
        self.code = code or _STATUS_CODES.get(
            status_code, "internal_error" if status_code >= 500 else "error"
        )
        self.message = message if message is not None else (detail if isinstance(detail, str) else "")
        self.retryable = (status_code in RETRYABLE_STATUSES) if retryable is None else bool(retryable)
        self.details = dict(details or {})
        self.idempotency_key = idempotency_key
        suffix = f" (request_id={request_id})" if request_id else ""
        super().__init__(f"{method} {path} -> {status_code} {self.code}: {self.message or detail}{suffix}")


class BadRequestError(DCLabAPIError):
    """400: malformed request (bad cursor, bad Idempotency-Key, invalid spec...)."""


class AuthenticationError(DCLabAPIError):
    """401: missing, expired or revoked credentials."""


class PermissionDeniedError(DCLabAPIError):
    """403: authenticated but not allowed in this workspace."""


class NotFoundError(DCLabAPIError):
    """404: unknown resource, or one in another workspace (indistinguishable)."""


class ConflictError(DCLabAPIError):
    """409: the resource state forbids this command."""


class IdempotencyConflictError(ConflictError):
    """409 ``idempotency_key_conflict``: the key was used for a different request."""


class PreconditionFailedError(DCLabAPIError):
    """412: ``If-Match`` no longer matches; re-read the resource and retry."""


class UnprocessableEntityError(DCLabAPIError):
    """422: the body or parameters failed validation (``details['errors']``)."""


class PreconditionRequiredError(DCLabAPIError):
    """428: this command requires ``If-Match``."""


class RateLimitedError(DCLabAPIError):
    """429: slow down; retryable."""


class ServerError(DCLabAPIError):
    """5xx: server-side failure; retryable unless the server says otherwise."""


_BY_STATUS: dict[int, type[DCLabAPIError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    412: PreconditionFailedError,
    422: UnprocessableEntityError,
    428: PreconditionRequiredError,
    429: RateLimitedError,
}


def error_class_for(status_code: int, code: str | None) -> type[DCLabAPIError]:
    if status_code == 409 and code == "idempotency_key_conflict":
        return IdempotencyConflictError
    if status_code >= 500:
        return ServerError
    return _BY_STATUS.get(status_code, DCLabAPIError)


class DCLabClientError(ValueError):
    """Invalid client configuration (timeout, URL) before a request is sent."""
