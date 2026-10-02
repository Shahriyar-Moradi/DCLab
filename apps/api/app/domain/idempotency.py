"""Idempotency-Key + request-digest binding for /v1 POST commands (P3.1-A).

Generalizes the ``execution_requests`` digest binding (P2.4-A branch replay):
a key names exactly one request. The request digest covers the route
template, path parameters, the canonical JSON body (minus any legacy body
key) and the authenticated principal. Each command stores ``(key, digest)``
on the resource it creates; a replay with the same digest returns that
resource, a different digest is ``IdempotencyKeyReusedError`` (409).
Pure functions only; transports parse the header, services persist.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
IDEMPOTENCY_KEY_MAX_CHARS = 128
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,%d}$" % IDEMPOTENCY_KEY_MAX_CHARS)
IDEMPOTENT_REPLAY_HEADER = "Idempotent-Replayed"
REQUEST_DIGEST_VERSION = "v1"

# request_spec key that carries the digest on ``execution_requests`` (server-owned).
REQUEST_DIGEST_SPEC_KEY = "request_digest"

# Key namespaces owned by internal surfaces (they replay by key alone); a /v1
# caller can never claim them, so it can never plant a row they would adopt.
LEGACY_LABS_UPLOAD_IDEMPOTENCY_PREFIX = "legacy_labs_upload:"
RESERVED_IDEMPOTENCY_KEY_PREFIXES = (LEGACY_LABS_UPLOAD_IDEMPOTENCY_PREFIX,)


def is_reserved_idempotency_key(key: str) -> bool:
    return key.startswith(RESERVED_IDEMPOTENCY_KEY_PREFIXES)


def request_digest(
    *,
    operation: str,
    principal_id: Any,
    path_params: dict[str, Any] | None = None,
    body: Any = None,
) -> str:
    """sha256 over the canonical request; equal requests give equal digests."""

    canonical = json.dumps(
        {
            "v": REQUEST_DIGEST_VERSION,
            "op": operation,
            "principal": str(principal_id),
            "params": {key: str(value) for key, value in sorted((path_params or {}).items())},
            "body": body,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IdempotencyBinding:
    """One POST's idempotency identity. ``key`` is None when the caller sent none."""

    key: str | None
    digest: str
    source: str  # "header" | "body" | "none"

    @property
    def keyed(self) -> bool:
        return self.key is not None
