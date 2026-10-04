"""Provider adapters: the ONLY modules that import a model SDK, call a provider
host or read a ``DCLAB_<PROVIDER>_API_KEY`` variable (ADR 0009 §1 CI rules a, e;
enforced by ``tests/test_ai_gateway.py``). Adapters raise ``ProviderError`` and
never retry; the gateway service owns retries, limits, budgets and the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel

ProviderErrorKind = Literal[
    "timeout",  # counts for the circuit breaker
    "server_error",  # provider 5xx / connection failure: counts for the circuit breaker
    "client_error",  # provider 4xx other than 429
    "rate_limited",  # provider 429
    "invalid_output",  # no parseable structured output
    "not_configured",  # adapter missing or no key
]


class ProviderError(Exception):
    def __init__(self, kind: ProviderErrorKind, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail[:200]


@dataclass(frozen=True)
class ProviderCall:
    model: str
    instructions: str  # the prompt release text plus the untrusted-text notice
    input_json: str  # canonical JSON of the redacted payload
    output_schema: type[BaseModel]
    max_output_tokens: int
    temperature: float | None
    timeout_s: float
    purpose: str
    agent_key: str | None


@dataclass(frozen=True)
class ProviderResult:
    output: dict[str, Any]
    input_tokens: int
    output_tokens: int
    request_id: str | None
    resolved_model: str | None


class Provider(Protocol):
    name: str

    def complete(self, call: ProviderCall) -> ProviderResult: ...


def default_providers() -> dict[str, Provider]:
    """Production adapters. TypeSafe Jev arrives in P6.7-A (``typesafe_jev.py``)."""

    from app.agents.gateway.providers.openai import OpenAIProvider

    return {"openai": OpenAIProvider()}
