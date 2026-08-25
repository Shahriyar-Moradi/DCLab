"""Deterministic candidate fingerprint for cache hits."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def candidate_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]
