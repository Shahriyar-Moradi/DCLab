"""Opaque, tamper-evident /v1 page cursors (P3.1-A). One codec for every /v1 list.

A cursor is ``c1.<payload>.<mac>``: ``payload`` is the base64url JSON list of
the keyset position, ``mac`` a truncated HMAC-SHA256 over the version, the
caller-supplied *scope* and the payload. The scope names the route and the
resource it pages (e.g. ``graph:<workspace>:<project>``), so a cursor minted
for one route, project, filter set or tenant fails verification anywhere else.
Clients must treat cursors as opaque strings; their layout may change with the
version prefix.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Sequence
from typing import Any

from app.config import cursor_hmac_secret, get_settings
from app.domain.errors import InvalidCursorError

CURSOR_VERSION = "c1"
CURSOR_MAX_CHARS = 256
_MAC_BYTES = 16
# NUL-delimited label: outside the token input space of auth_hashing.digest_token.
_KEY_LABEL = b"\x00dclab-kdf\x00v1-cursor"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key() -> bytes:
    secret = cursor_hmac_secret(get_settings()).encode("utf-8")
    return hmac.new(secret, _KEY_LABEL, hashlib.sha256).digest()


def _mac(scope: str, body: str) -> str:
    message = f"{CURSOR_VERSION}|{scope}|{body}".encode("utf-8")
    return _b64(hmac.new(_key(), message, hashlib.sha256).digest()[:_MAC_BYTES])


def scope_digest(parts: dict[str, Any]) -> str:
    """Short stable digest of filter values, for binding a cursor to a filter set."""

    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def sign_cursor(scope: str, position: Sequence[str | int]) -> str:
    body = _b64(json.dumps(list(position), separators=(",", ":")).encode("utf-8"))
    return f"{CURSOR_VERSION}.{body}.{_mac(scope, body)}"


def open_cursor(cursor: str, scope: str, *, message: str = "cursor is invalid for this list") -> list[Any]:
    """The position a cursor carries, or ``InvalidCursorError`` (400) when it was not
    minted by this server for exactly this ``scope``."""

    text = (cursor or "").strip()
    parts = text.split(".")
    if len(text) > CURSOR_MAX_CHARS or len(parts) != 3 or parts[0] != CURSOR_VERSION:
        raise InvalidCursorError(message)
    _version, body, mac = parts
    if not hmac.compare_digest(mac.encode("ascii", "replace"), _mac(scope, body).encode("ascii")):
        raise InvalidCursorError(message)
    try:
        position = json.loads(_unb64(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise InvalidCursorError(message) from exc
    if not isinstance(position, list):
        raise InvalidCursorError(message)
    return position
