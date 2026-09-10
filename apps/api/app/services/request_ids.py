"""Correlate API requests. Values are never treated as credentials."""

from __future__ import annotations

from uuid import uuid4

from fastapi import Request

REQUEST_ID_HEADER = "X-Request-Id"
MAX_REQUEST_ID_LENGTH = 128


def bind_request_id(request: Request) -> str:
    raw = (request.headers.get(REQUEST_ID_HEADER) or "").strip()
    if (
        not raw
        or len(raw) > MAX_REQUEST_ID_LENGTH
        or any(char in raw for char in "\r\n")
    ):
        raw = str(uuid4())
    request.state.request_id = raw
    return raw


def request_id_of(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str) and existing:
        return existing
    return bind_request_id(request)
