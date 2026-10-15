"""The one allow-list for span attributes and metric labels (P8.5-A).

Every attribute or label passes ``clean``: unknown keys are dropped, and values must be
short code-owned tokens (route templates, job types, handler keys, status classes, outcome
codes, model names). Ids, emails, file names, error text and any customer value never
become a label: their keys are not allowed, and values that look like ids are dropped.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

ALLOWED_KEYS = frozenset(
    {
        "route",  # route template (``/v1/projects/{project_id}``), never the raw path
        "method",
        "status_code",
        "status_class",  # 2xx / 4xx / 5xx
        "job_type",
        "handler_key",
        "status",
        "attempt",
        "outcome",
        "provider",
        "model",
        "llm_kind",
        "agent_kind",
        "process",
        "check",
        "runs_queued",
        "runs_skipped",
    }
)
# Documentation and tests: none of these may ever be allowed.
FORBIDDEN_KEYS = frozenset(
    {
        "workspace_id", "user_id", "project_id", "dataset_id", "job_id", "request_id", "dataset_name",
        "email", "file_name", "filename", "path", "url", "query", "error", "error_message", "exception",
        "prompt", "content", "name",
    }
)
MAX_ATTRIBUTES = 16
_VALUE = re.compile(r"^[A-Za-z0-9_.:/{}<>\-]{1,128}$")
# UUID fragments, long hex runs and long digit runs (ids, timestamps) are never labels.
_ID_LIKE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}|[0-9a-fA-F]{16,}|\d{5,}")

Value = str | int | float | bool


def clean_value(value: object) -> Value | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if -1_000_000 < value < 1_000_000 else None
    if isinstance(value, float):
        return value if abs(value) < 1e12 else None
    if isinstance(value, str) and _VALUE.fullmatch(value) and not _ID_LIKE.search(value):
        return value
    return None


def clean(attributes: Mapping[str, object]) -> dict[str, Value]:
    out: dict[str, Value] = {}
    for key, value in attributes.items():
        if key not in ALLOWED_KEYS or len(out) >= MAX_ATTRIBUTES:
            continue
        cleaned = clean_value(value)
        if cleaned is not None:
            out[key] = cleaned
    return out


def status_class(status_code: int) -> str:
    return f"{int(status_code) // 100}xx" if 100 <= int(status_code) < 600 else "other"


_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


def http_method(method: str) -> str:
    upper = (method or "").upper()
    return upper if upper in _METHODS else "OTHER"
