"""Fail-closed HTTP projections for legacy diagnostic and failure fields.

Stored evidence remains untouched. Protected operator telemetry retains the
original diagnostic; API, SDK and browser payloads receive this bounded view.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.observability import LlmInvocationRead, MlRunEventRead
from app.domain.reproducibility import ArtifactRead, CodeSnapshotRead

_BLOCKED_KEY_PARTS = (
    "secret", "password", "credential", "authorization", "access_token",
    "refresh_token", "session_token", "csrf_token", "api_key",
    "access_key", "private_key", "signature", "cookie", "jwt",
    "path", "directory", "object_key", "storage_key", "object_store", "bucket", "handler",
    "storage_uri", "source_uri", "filesystem",
    "stack", "traceback", "exception", "prompt", "provider_body", "request_body",
    "response_body", "provider_response", "raw_body", "raw_payload",
    "raw_response", "raw_request", "raw_provider", "raw_output", "raw_prompt",
    "raw_trace", "output_text", "choices", "sample_rows",
)
# DCLab service-token bearer strings (P3.2-A). The one shared pattern: embedded in
# SECRET_TEXT / _UNSAFE_TEXT here and used by observability_service and
# verification_evidence. A lookbehind instead of ``\b`` so ``x_dclab_st_...`` matches too.
SERVICE_TOKEN_TEXT = r"(?<![A-Za-z0-9])dclab_st_[A-Za-z0-9_-]{8,}"
SERVICE_TOKEN_PATTERN = re.compile(SERVICE_TOKEN_TEXT)
_UNSAFE_TEXT = re.compile(
    r"(?i)(traceback \(most recent call last\)|(?:^|\s)(?:/users/|/private/|/tmp/|/var/|/home/|/app/)|"
    r"[a-z]:\\|(?:s3|gs|object|file)://|\b(?:workspaces|private|artifacts)/[a-z0-9._/-]+|"
    r"\bbearer\s+\S+|\bsk-[a-z0-9_-]{8,}|" + SERVICE_TOKEN_TEXT + "|"
    r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})"
)
# Credential-shaped text (the secret subset of _UNSAFE_TEXT). Append-only
# writers reject it outright instead of redacting it on read.
SECRET_TEXT = re.compile(
    r"(?i)(\bbearer\s+\S+|\bsk-[a-z0-9_-]{8,}|" + SERVICE_TOKEN_TEXT + "|"
    r"-----begin [a-z ]*private key-----|\bakia[0-9a-z]{16}\b)"
)
BLOCKED_KEY_PARTS = _BLOCKED_KEY_PARTS
_MAX_DEPTH = 6
_MAX_ITEMS = 200
_MAX_STRING = 1000
PUBLIC_FAILURE = "The operation failed. See the request ID for support."


def public_failure(value: str | None) -> str | None:
    """Never expose persisted exception text as a customer/API failure reason."""

    return PUBLIC_FAILURE if value else None


def public_diagnostic(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded copy without operational identifiers or raw bodies."""

    if depth > _MAX_DEPTH:
        return "[REDACTED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:_MAX_ITEMS]:
            key = str(raw_key)[:128]
            lowered = key.lower()
            if lowered in {"failure_reason", "failure_summary", "error"}:
                result[key] = public_failure(str(item)) if item else None
            elif not any(part in lowered for part in _BLOCKED_KEY_PARTS):
                result[key] = public_diagnostic(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [public_diagnostic(item, depth=depth + 1) for item in value[:_MAX_ITEMS]]
    if isinstance(value, str):
        if _UNSAFE_TEXT.search(value):
            return "[REDACTED]"
        return value[:_MAX_STRING]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return value


def event_read(
    row: Any, *, include_payload: bool = True, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    result = MlRunEventRead.model_validate(row).model_dump()
    result["payload"] = public_diagnostic(
        result["payload"] if payload is None else payload
    ) if include_payload else {}
    if result["status"] in {"failed", "error"} and "reason" in result["payload"]:
        result["payload"]["reason"] = PUBLIC_FAILURE
    return result


def invocation_read(row: Any) -> dict[str, Any]:
    result = LlmInvocationRead.model_validate(row).model_dump()
    result["reason"] = public_failure(result["reason"]) if result["status"] == "failed" else public_diagnostic(result["reason"])
    for key in ("safe_output", "final_decision", "redaction_summary"):
        if result[key] is not None:
            result[key] = public_diagnostic(result[key])
    return result


def artifact_read(row: Any) -> ArtifactRead:
    """Keep the stable contract while withholding the internal object locator."""

    return ArtifactRead.model_validate(row).model_copy(update={"object_key": ""})


def code_snapshot_read(row: Any) -> CodeSnapshotRead:
    """Expose digest/lineage, not the internal execution entrypoint."""

    return CodeSnapshotRead.model_validate(row).model_copy(update={"entrypoint": ""})
