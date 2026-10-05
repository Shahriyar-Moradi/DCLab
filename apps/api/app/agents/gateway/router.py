"""Model and provider routing (ADR 0009 §4 step 3; ADR 0008 § Founder decisions).

The model comes from the effective policy: ``models.roles[role]`` (its
``per_agent`` entry first, then ``default``); a request may name a model only
from that role's ``allowed`` list, which caps keep inside the platform
allowlist. OpenAI publishes no dated snapshots for the pinned ids, so bare ids
are routed and the ledger records the provider-reported resolved model.
Aliases (``-latest``, ``-preview``) are never routed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.gateway.contract import GatewayRefusal
from app.agents.governance.platform_default import PLATFORM_MODEL_ALLOWLIST, AiPolicyV1

MODEL_PROVIDERS = (("gpt-", "openai"), ("jev-", "typesafe"))
_ALIAS_SUFFIXES = ("-latest", "-preview")


@dataclass(frozen=True)
class Route:
    provider: str
    model: str


def provider_for(model: str) -> str | None:
    return next((provider for prefix, provider in MODEL_PROVIDERS if model.startswith(prefix)), None)


def select_model(policy: AiPolicyV1, *, role: str, agent_key: str | None, requested: str | None) -> str:
    models = getattr(policy.models.roles, role)
    return requested or (models.per_agent.get(agent_key) if agent_key else None) or models.default


def route(policy: AiPolicyV1, *, role: str, agent_key: str | None, requested: str | None) -> Route:
    from app.agents.gateway.budget import PRICES_MICROS_PER_MTOK

    model = select_model(policy, role=role, agent_key=agent_key, requested=requested)
    allowed = getattr(policy.models.roles, role).allowed
    provider = provider_for(model)
    if (
        model not in allowed
        or model not in PLATFORM_MODEL_ALLOWLIST
        or model.endswith(_ALIAS_SUFFIXES)
        or provider is None
        or (provider == "typesafe") != (role == "jev")
        or model not in PRICES_MICROS_PER_MTOK
    ):
        raise GatewayRefusal("model_not_allowed", f"{model} is not allowed for role {role}", scope=f"role:{role}")
    return Route(provider=provider, model=model)
