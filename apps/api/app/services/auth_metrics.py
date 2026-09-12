"""Bounded login/session/CSRF counters. Labels are reason codes only."""

from __future__ import annotations

import logging
import threading
from collections import defaultdict

logger = logging.getLogger("dclab.auth")

FAMILIES = frozenset({"login", "session", "csrf", "recovery"})
REASONS = frozenset(
    {
        "success",
        "invalid_credentials",
        "throttled",
        "issued",
        "revoked",
        "expired",
        "invalid",
        "kill_switch",
        "capped",
        "logout",
        "logout_all",
        "rotation",
        "untrusted_origin",
        "missing_token",
        "mismatch",
        "ignored",
        "consumed",
        "other",
    }
)

_lock = threading.Lock()
_counts: dict[tuple[str, str], int] = defaultdict(int)


def reset_auth_metrics() -> None:
    with _lock:
        _counts.clear()


def auth_metric_counts() -> dict[str, int]:
    with _lock:
        return {f"{family}.{reason}": value for (family, reason), value in _counts.items()}


def record_auth_event(family: str, reason: str) -> None:
    fam = family if family in FAMILIES else "session"
    why = reason if reason in REASONS else "other"
    with _lock:
        _counts[(fam, why)] += 1
    logger.info("auth_event family=%s reason=%s", fam, why)
