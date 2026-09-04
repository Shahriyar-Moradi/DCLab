"""Rebuild planning dataclasses from persisted dicts without a second planning pass."""

from __future__ import annotations

from dataclasses import fields
from typing import Any, TypeVar

T = TypeVar("T")


def from_mapping(cls: type[T], payload: Any) -> T | None:
    """Return `cls` from a mapping, pass an instance through, or None if absent."""
    if payload is None:
        return None
    if isinstance(payload, cls):
        return payload
    if not isinstance(payload, dict) or not payload:
        return None
    allowed = {item.name for item in fields(cls)}
    return cls(**{key: value for key, value in payload.items() if key in allowed})
