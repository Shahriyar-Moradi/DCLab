"""Model and provider routing (ADR 0009 §4 step 3; ADR 0008 § Founder decisions).

The model comes from the effective policy: ``models.roles[role]`` (its
``per_agent`` entry first, then ``default``); a request may name a model only
from that role's ``allowed`` list, which caps keep inside the platform
allowlist. Since 2026-10-16 every role, Jev included, routes to Claude on
Anthropic; bare model ids are routed and the ledger records the
provider-reported resolved model. Aliases (``-latest``, ``-preview``) are never
routed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.gateway.contract import GatewayRefusal
from app.agents.governance.platform_default import PLATFORM_MODEL_ALLOWLIST, AiPolicyV1

MODEL_PROVIDERS = (("claude-", "anthropic"),)
# LEGACY (switched to Anthropic 2026-10-16; kept for now): routable only with
# AI_LEGACY_PROVIDERS_ENABLED, TypeSafe only for the jev role and OpenAI never for it. Their ids
# are also out of the platform allowlist and the price table, so the flag alone routes nothing.
LEGACY_MODEL_PROVIDERS = (("gpt-", "openai"), ("jev-", "typesafe"))
_ALIAS_SUFFIXES = ("-latest", "-preview")


@dataclass(frozen=True)
class Route:
    provider: str
    model: str


def provider_for(model: str, *, legacy: bool = False) -> str | None:
    table = (*MODEL_PROVIDERS, *LEGACY_MODEL_PROVIDERS) if legacy else MODEL_PROVIDERS
    return next((provider for prefix, provider in table if model.startswith(prefix)), None)


def select_model(policy: AiPolicyV1, *, role: str, agent_key: str | None, requested: str | None) -> str:
    models = getattr(policy.models.roles, role)
    return requested or (models.per_agent.get(agent_key) if agent_key else None) or models.default


def route(policy: AiPolicyV1, *, role: str, agent_key: str | None, requested: str | None,
          legacy: bool = False) -> Route:
    from app.agents.gateway.budget import PRICES_MICROS_PER_MTOK

    model = select_model(policy, role=role, agent_key=agent_key, requested=requested)
    allowed = getattr(policy.models.roles, role).allowed
    provider = provider_for(model, legacy=legacy)
    if (
        model not in allowed
        or model not in PLATFORM_MODEL_ALLOWLIST
        or model.endswith(_ALIAS_SUFFIXES)
        or provider is None
        or (provider == "typesafe" and role != "jev")  # LEGACY: TypeSafe answered Jev only
        or (provider == "openai" and role == "jev")
        or model not in PRICES_MICROS_PER_MTOK
    ):
        raise GatewayRefusal("model_not_allowed", f"{model} is not allowed for role {role}", scope=f"role:{role}")
    return Route(provider=provider, model=model)
