"""Deterministic fake provider for tests and local development (ADR 0009 §3, §9).

Refuses to load or answer outside a development environment
(``fake_provider_allowed`` on the process setting ``DCLAB_ENV`` and on any
``environment`` argument: anything not named development/dev/test/local counts
as production, including ``""``, ``prod`` and ``staging``). Outputs are scripted (a queue of dicts,
``ProviderResult`` or exceptions) or produced by a handler, e.g. one replaying
recorded outputs keyed by the input digest. Every call is kept in ``calls`` so
tests can assert exactly what would have left the process.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

from app.agents.gateway.providers import ProviderCall, ProviderResult
from app.agents.governance.platform_default import fake_provider_allowed


class FakeProviderForbidden(RuntimeError):
    pass


Scripted = dict[str, Any] | ProviderResult | BaseException


def _require_development(environment: str | None) -> None:
    """The process environment must be a development one; an explicit argument can
    only make this stricter, never allow the fake where the settings forbid it."""

    from app.config import get_settings

    for value in (get_settings().dclab_env, environment):
        if value is not None and not fake_provider_allowed(value):
            raise FakeProviderForbidden(f"the fake provider is refused in environment {value!r}")


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        responses: Iterable[Scripted] = (),
        *,
        handler: Callable[[ProviderCall], Scripted] | None = None,
        resolved_model: str | None = None,
        environment: str | None = None,
    ) -> None:
        self._environment = environment
        _require_development(environment)
        self._queue: deque[Scripted] = deque(responses)
        self._handler = handler
        self._resolved_model = resolved_model
        self.calls: list[ProviderCall] = []

    def complete(self, call: ProviderCall) -> ProviderResult:
        _require_development(self._environment)
        self.calls.append(call)
        if self._handler is not None:
            item = self._handler(call)
        elif self._queue:
            item = self._queue.popleft()
        else:
            raise AssertionError("fake provider has no scripted response left")
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, ProviderResult):
            return item
        return ProviderResult(
            output=dict(item),
            input_tokens=max(1, len(call.instructions + call.input_json) // 4),
            output_tokens=max(1, len(str(item)) // 4),
            request_id=f"fake-{len(self.calls)}",
            resolved_model=self._resolved_model or call.model,
        )
