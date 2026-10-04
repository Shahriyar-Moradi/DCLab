"""Rate limits and circuit breakers (ADR 0009 §4 step 5).

Token buckets per workspace (default 60 calls / minute) and per provider (a
requests-per-second ceiling), and circuit breakers keyed by (provider, purpose)
and by (provider, workspace). Breakers count only provider 5xx responses and
timeouts (never 4xx, validation or policy refusals), open after 5 such failures
within 60 s and go half-open after 15 minutes: one probe at a time, a success
closes, a failure reopens.

State is in-process (MVP): each API / worker process limits on its own, so N
processes allow up to N× the configured rate. A shared store (Postgres or
Redis) and ``ai_switches`` / ``ai_incidents`` rows for an open breaker
(``limits.breaker.v1``) are follow-ups.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from app.agents.gateway.contract import GatewayRefusal

WORKSPACE_CALLS_PER_MINUTE = 60
PROVIDER_REQUESTS_PER_SECOND = {"openai": 50, "typesafe": 80}
DEFAULT_PROVIDER_RPS = 20
BREAKER_FAILURES = 5
BREAKER_WINDOW_S = 60.0
BREAKER_COOL_DOWN_S = 15 * 60.0


@dataclass
class TokenBucket:
    capacity: float
    refill_per_s: float
    tokens: float = -1.0
    updated: float = 0.0

    def _refill(self, now: float) -> None:
        if self.tokens < 0:
            self.tokens, self.updated = self.capacity, now
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill_per_s)
        self.updated = now

    def wait_s(self, now: float) -> float:
        self._refill(now)
        return 0.0 if self.tokens >= 1 else (1 - self.tokens) / self.refill_per_s

    def take(self) -> None:
        self.tokens -= 1


@dataclass
class CircuitBreaker:
    failures: deque = field(default_factory=deque)
    opened_at: float | None = None
    probing: bool = False

    def wait_s(self, now: float) -> float:
        """0 when a call may go ahead (closed, or the single half-open probe)."""

        if self.opened_at is None:
            return 0.0
        remaining = self.opened_at + BREAKER_COOL_DOWN_S - now
        if remaining > 0:
            return remaining
        return 1.0 if self.probing else 0.0

    def admit(self) -> None:
        if self.opened_at is not None:
            self.probing = True

    def record(self, now: float, *, failure: bool) -> None:
        if failure:
            if self.opened_at is not None:  # failed probe (or straggler): reopen
                self.opened_at, self.probing = now, False
                return
            self.failures.append(now)
            while self.failures and self.failures[0] <= now - BREAKER_WINDOW_S:
                self.failures.popleft()
            if len(self.failures) >= BREAKER_FAILURES:
                self.opened_at = now
                self.failures.clear()
        elif self.opened_at is not None and self.probing:
            self.opened_at, self.probing = None, False
            self.failures.clear()

    def release_probe(self) -> None:
        self.probing = False


class GatewayLimits:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        workspace_calls_per_minute: int = WORKSPACE_CALLS_PER_MINUTE,
        provider_rps: dict[str, int] | None = None,
    ) -> None:
        self._clock = clock
        self._workspace_per_minute = workspace_calls_per_minute
        self._provider_rps = provider_rps if provider_rps is not None else dict(PROVIDER_REQUESTS_PER_SECOND)
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], TokenBucket] = {}
        self._breakers: dict[tuple[str, str, str], CircuitBreaker] = {}

    def _bucket(self, kind: str, key: str) -> TokenBucket:
        if (kind, key) not in self._buckets:
            if kind == "workspace":
                per_s = self._workspace_per_minute / 60.0
                capacity = float(self._workspace_per_minute)
            else:
                per_s = float(self._provider_rps.get(key, DEFAULT_PROVIDER_RPS))
                capacity = per_s
            self._buckets[(kind, key)] = TokenBucket(capacity=capacity, refill_per_s=per_s)
        return self._buckets[(kind, key)]

    def _breaker_keys(self, provider: str, workspace_id: UUID, purpose: str) -> list[tuple[str, str, str]]:
        return [(provider, "purpose", purpose), (provider, "workspace", str(workspace_id))]

    def _check_breakers(self, now: float, provider: str, workspace_id: UUID, purpose: str) -> list[CircuitBreaker]:
        breakers = []
        for key in self._breaker_keys(provider, workspace_id, purpose):
            breaker = self._breakers.setdefault(key, CircuitBreaker())
            wait = breaker.wait_s(now)
            if wait > 0:
                raise GatewayRefusal("breaker_open", "provider circuit breaker open",
                                     scope=f"provider:{provider}/{key[1]}", retry_after_s=math.ceil(wait))
            breakers.append(breaker)
        return breakers

    def admit(self, *, provider: str, workspace_id: UUID, purpose: str) -> None:
        """Step 5: refuse while a breaker is open, else take one token from both buckets."""

        with self._lock:
            now = self._clock()
            self._check_breakers(now, provider, workspace_id, purpose)
            buckets = [("workspace", self._bucket("workspace", str(workspace_id))),
                       ("provider", self._bucket("provider", provider))]
            for kind, bucket in buckets:
                wait = bucket.wait_s(now)
                if wait > 0:
                    raise GatewayRefusal("rate_limited", f"{kind} rate limit", scope=kind,
                                         retry_after_s=max(1, math.ceil(wait)))
            for _kind, bucket in buckets:
                bucket.take()

    def begin_call(self, *, provider: str, workspace_id: UUID, purpose: str) -> None:
        """Right before the provider call: claims the single half-open probe (a call
        refused or served from the cache after ``admit`` never holds a probe)."""

        with self._lock:
            for breaker in self._check_breakers(self._clock(), provider, workspace_id, purpose):
                breaker.admit()

    def record(self, *, provider: str, workspace_id: UUID, purpose: str, outcome: str) -> None:
        """``outcome``: ``failure`` (5xx / timeout), ``success`` (any response) or ``neutral``."""

        with self._lock:
            now = self._clock()
            for key in self._breaker_keys(provider, workspace_id, purpose):
                breaker = self._breakers.setdefault(key, CircuitBreaker())
                if outcome == "neutral":
                    breaker.release_probe()
                else:
                    breaker.record(now, failure=outcome == "failure")


LIMITS = GatewayLimits()
