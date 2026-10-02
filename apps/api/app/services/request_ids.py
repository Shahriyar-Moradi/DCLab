"""Correlate API requests. Values are never treated as credentials."""

from __future__ import annotations

import re
from uuid import uuid4

from fastapi import Request

REQUEST_ID_HEADER = "X-Request-Id"
MAX_REQUEST_ID_LENGTH = 128
# Inbound ids are echoed in headers, logs and /v1 error bodies: printable token
# characters only (UUIDs, W3C trace ids, ULIDs); anything else gets a fresh id.
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,%d}$" % MAX_REQUEST_ID_LENGTH)


def bind_request_id(request: Request) -> str:
    raw = (request.headers.get(REQUEST_ID_HEADER) or "").strip()
    if not _REQUEST_ID.fullmatch(raw):
        raw = str(uuid4())
    request.state.request_id = raw
    return raw


def request_id_of(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str) and existing:
        return existing
    return bind_request_id(request)
