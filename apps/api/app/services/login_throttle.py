"""In-process login failure throttle. Not shared across workers."""

from __future__ import annotations

import hashlib
import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import Request

from app.config import get_settings

_lock = threading.Lock()
_failures: dict[str, list[datetime]] = defaultdict(list)


class ThrottleError(Exception):
    """Too many credential attempts in the window."""


def utcnow() -> datetime:
    return datetime.now(UTC)


def reset_login_throttle() -> None:
    with _lock:
        _failures.clear()


def client_ip(request: Request) -> str:
    settings = get_settings()
    if settings.auth_trust_forwarded:
        forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if forwarded:
            return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def throttle_key(email: str, ip: str) -> str:
    raw = f"{email.strip().lower()}|{ip}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _prune(entries: list[datetime], cutoff: datetime) -> list[datetime]:
    return [moment for moment in entries if moment > cutoff]


def check_login_throttle(email: str, ip: str, *, now: datetime | None = None) -> None:
    settings = get_settings()
    moment = now or utcnow()
    cutoff = moment - timedelta(minutes=settings.login_throttle_window_minutes)
    key = throttle_key(email, ip)
    with _lock:
        recent = _prune(_failures.get(key, []), cutoff)
        _failures[key] = recent
        if len(recent) >= settings.login_throttle_attempts:
            raise ThrottleError("too many attempts")


def record_login_failure(email: str, ip: str, *, now: datetime | None = None) -> None:
    moment = now or utcnow()
    key = throttle_key(email, ip)
    with _lock:
        _failures[key].append(moment)


def record_login_success(email: str, ip: str) -> None:
    key = throttle_key(email, ip)
    with _lock:
        _failures.pop(key, None)
