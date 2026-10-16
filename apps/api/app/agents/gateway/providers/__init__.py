"""Provider adapters: the ONLY modules that import a model SDK, call a provider
host or read a ``DCLAB_<PROVIDER>_API_KEY`` variable (ADR 0009 §1 CI rules a, e;
enforced by ``tests/test_ai_gateway.py``; the Anthropic adapter also falls back to
the plain ``ANTHROPIC_API_KEY``, see its docstring). Adapters raise ``ProviderError``
and never retry; the gateway service owns retries, limits, budgets and the ledger.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

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
    """A failed call. ``input_tokens`` / ``output_tokens``: usage the provider reported for a
    reply that was billed but unusable (refusal, length stop, unparsable or invalid output);
    ``billed_unknown``: the request left the process and may have been billed, but no usage
    came back (a read timeout, a 5xx, a dropped or oversized reply): the gateway charges the
    call's worst case then."""

    def __init__(self, kind: ProviderErrorKind, detail: str = "", *, input_tokens: int = 0,
                 output_tokens: int = 0, billed_unknown: bool = False) -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail[:200]
        self.input_tokens = max(0, int(input_tokens))
        self.output_tokens = max(0, int(output_tokens))
        self.billed_unknown = bool(billed_unknown)


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
    # The call's ledger row and cache key (``llm_invocations.id`` / ``input_evidence_digest``);
    # adapters never send them. The offline AI harness (P6.10-B) joins calls to rows with them.
    input_digest: str | None = None
    invocation_id: UUID | None = None


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


def default_providers(settings: Any = None) -> dict[str, Provider]:
    """Production adapters (each imports its SDK / HTTP client lazily, at call time).

    Every model routes to Anthropic since 2026-10-16. LEGACY (switched to Anthropic
    2026-10-16; kept for now): the OpenAI and TypeSafe adapters are registered only with
    ``AI_LEGACY_PROVIDERS_ENABLED=true``; their model ids are commented out of the router,
    the platform allowlist and the price table, so the switch alone routes nothing to them."""

    from app.agents.gateway.providers.anthropic import AnthropicProvider

    providers: dict[str, Provider] = {"anthropic": AnthropicProvider()}
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    if getattr(settings, "ai_legacy_providers_enabled", False) is True:
        from app.agents.gateway.providers.openai import OpenAIProvider
        from app.agents.gateway.providers.typesafe_jev import TypeSafeJevProvider

        providers |= {"openai": OpenAIProvider(), "typesafe": TypeSafeJevProvider()}
    return providers


def ambient_provider_keys(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Names (never values) of provider credentials a third-party library would read on its
    own: any ``*_API_KEY`` / ``*_AUTH_TOKEN`` / ``*_AD_TOKEN`` outside DCLab's ``DCLAB_``
    namespace (``OPENAI_API_KEY``, ``ANTHROPIC_AUTH_TOKEN``, ``AZURE_OPENAI_AD_TOKEN`` …),
    ``HF_TOKEN`` and ``GOOGLE_APPLICATION_CREDENTIALS``. The NOOA runtime refuses to start
    while one is set (P6.3-A): LiteLLM and NOOA's model registry would pick it up if they
    were ever called, and DCLab's own keys are only ever ``DCLAB_<PROVIDER>_API_KEY``.
    ``AWS_*`` credentials are left out on purpose: object storage may use them."""

    names = os.environ if environ is None else environ
    return tuple(sorted(name for name in names if not name.upper().startswith("DCLAB_") and (
        name.upper().endswith(_AMBIENT_SUFFIXES) or name.upper() in _AMBIENT_NAMES)))


_AMBIENT_SUFFIXES = ("_API_KEY", "_AUTH_TOKEN", "_AD_TOKEN")
_AMBIENT_NAMES = frozenset({"HF_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS"})
