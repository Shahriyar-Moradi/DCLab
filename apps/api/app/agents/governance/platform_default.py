"""Code-owned ``AiPolicyV1`` platform default, CAPS and code constants (ADR 0009 §3).

Never editable at runtime: the default is seeded as the platform ``ai_policies``
row (``change_kind = 'seed'``); CAPS bound every effective policy; the constants
below are not policy at all. Founder decisions of 2026-10-04 are filled in
(OpenAI ``gpt-6.1-sol`` / ``gpt-6-luna``, Jev ``jev-1.13.0``, budgets, no sample values).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DATA_CLASS_ORDER = ("metadata", "aggregates", "sample_values")
DataClass = Literal["metadata", "aggregates", "sample_values"]  # raw_rows is never valid
ROLE_NAMES = ("lead", "specialist", "legacy_decision", "verifier", "jev")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RoleModels(_Strict):
    default: str = Field(min_length=1, max_length=64)
    allowed: tuple[str, ...] = Field(min_length=1, max_length=16)
    fallback: Literal["rule_path", "rule_answer"]
    per_agent: dict[str, str] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def _defaults_are_allowed(self) -> "RoleModels":
        if self.default not in self.allowed or not set(self.per_agent.values()) <= set(self.allowed):
            raise ValueError("default and per_agent models must be in allowed")
        return self


class Roles(_Strict):
    lead: RoleModels
    specialist: RoleModels
    legacy_decision: RoleModels
    verifier: RoleModels
    jev: RoleModels


class Models(_Strict):
    roles: Roles


class DataPolicy(_Strict):
    max_class: DataClass
    sample_values_per_column: int = Field(ge=0, le=50)
    user_text_to_jev: bool


class RollbackRule(_Strict):
    enabled: bool
    metric: Literal["precision", "recall", "roc_auc", "pr_auc", "f1"]
    drop: float = Field(gt=0, le=1)
    windows: int = Field(ge=1, le=12)


class OpsAutonomy(_Strict):
    auto_retrain_per_week: int = Field(ge=0, le=14)
    auto_release: bool
    rollback_rule: RollbackRule


class Autonomy(_Strict):
    ops: OpsAutonomy


class Budgets(_Strict):
    workspace_month_micros: int = Field(ge=0)
    project_month_micros: int = Field(ge=0)
    assistant_turn_micros: int = Field(ge=0)
    assistant_thread_micros: int = Field(ge=0)
    specialist_run_micros: int = Field(ge=0)
    alert_fraction: float = Field(gt=0, le=1)
    hard_stop: bool


class TurnLimits(_Strict):
    steps: int = Field(ge=1)
    tokens: int = Field(ge=1)
    wall_s: int = Field(ge=1)
    tool_calls: int = Field(ge=0)


class ThreadLimits(_Strict):
    steps: int = Field(ge=1)
    tokens: int = Field(ge=1)
    wall_s: int = Field(ge=1)


class UserLimits(_Strict):
    turns_per_hour: int = Field(ge=0)
    concurrent_turns: int = Field(ge=0)


class SpecialistLimits(_Strict):
    calls: int = Field(ge=0)
    tokens: int = Field(ge=1)
    wall_s: int = Field(ge=1)


class JevLimits(_Strict):
    timeout_ms: int = Field(ge=1)
    batch: int = Field(ge=1)


class Limits(_Strict):
    assistant_turn: TurnLimits
    assistant_thread: ThreadLimits
    assistant_user: UserLimits
    specialist: SpecialistLimits
    jev: JevLimits


class Proposals(_Strict):
    ttl_days: int = Field(ge=1, le=90)


class Retention(_Strict):
    prompts_days: int = Field(ge=1, le=3650)


class Incidents(_Strict):
    validator_rejections_24h: int = Field(ge=1)
    revert_rate_30d: float = Field(gt=0, le=1)


class AiPolicyV1(_Strict):
    """The governance document (ADR 0009 §3). Stored in ``ai_policies.policy``."""

    schema_version: Literal[1]
    models: Models
    data: DataPolicy
    autonomy: Autonomy
    budgets: Budgets
    limits: Limits
    proposals: Proposals
    retention: Retention
    incidents: Incidents


_SOL, _LUNA, _JEV = "gpt-6.1-sol", "gpt-6-luna", "jev-1.13.0"

PLATFORM_DEFAULT = AiPolicyV1.model_validate(
    {
        "schema_version": 1,
        "models": {
            "roles": {
                "lead": {"default": _SOL, "allowed": [_SOL, _LUNA], "fallback": "rule_path"},
                "specialist": {
                    "default": _SOL,
                    "allowed": [_SOL, _LUNA],
                    "fallback": "rule_answer",
                    "per_agent": {"dataset_investigator": _LUNA},
                },
                "legacy_decision": {"default": _LUNA, "allowed": [_LUNA], "fallback": "rule_answer"},
                "verifier": {"default": _LUNA, "allowed": [_LUNA, _SOL], "fallback": "rule_answer"},
                "jev": {"default": _JEV, "allowed": [_JEV], "fallback": "rule_answer"},
            }
        },
        "data": {"max_class": "aggregates", "sample_values_per_column": 0, "user_text_to_jev": False},
        "autonomy": {
            "ops": {
                "auto_retrain_per_week": 2,
                "auto_release": False,
                "rollback_rule": {"enabled": False, "metric": "precision", "drop": 0.05, "windows": 2},
            }
        },
        "budgets": {
            "workspace_month_micros": 25_000_000,
            "project_month_micros": 12_000_000,
            "assistant_turn_micros": 250_000,
            "assistant_thread_micros": 1_000_000,
            "specialist_run_micros": 100_000,
            "alert_fraction": 0.8,
            "hard_stop": True,
        },
        "limits": {
            "assistant_turn": {"steps": 8, "tokens": 60000, "wall_s": 120, "tool_calls": 20},
            "assistant_thread": {"steps": 40, "tokens": 300000, "wall_s": 900},
            "assistant_user": {"turns_per_hour": 60, "concurrent_turns": 2},
            "specialist": {"calls": 2, "tokens": 24000, "wall_s": 60},
            "jev": {"timeout_ms": 1000, "batch": 50},
        },
        "proposals": {"ttl_days": 7},
        "retention": {"prompts_days": 365},
        "incidents": {"validator_rejections_24h": 5, "revert_rate_30d": 0.10},
    }
)

# CAPS: the code bound of every effective policy (ADR 0009 §3, "caps only narrow").
# max_class <= aggregates and no sample values (founder Q3); models within the
# platform allowlist; limits and budgets <= these values (P8 ties budgets to plan
# entitlements); auto_release false in the MVP; incidents only stricter.
CAPS = PLATFORM_DEFAULT
PLATFORM_MODEL_ALLOWLIST = frozenset({_SOL, _LUNA, _JEV})

# Code constants, never policy.
UNTRUSTED_TEXT_MARKING = True
RAW_PROMPTS_STORED = False
DATA_EXPOSURE_AUTO_DEMOTION_DISABLEABLE = False
FAKE_PROVIDER_ALLOWED_IN_PRODUCTION = False
AI_ENABLED_DEFAULT = False
SEED_ACTOR_RULE = "governance.seed.v1"
APPROVER_WORKSPACE_ROLES = ("workspace_owner", "workspace_admin")


# Fail safe: anything not named here (production, prod, staging, a typo) counts as production.
DEVELOPMENT_ENV_VALUES = frozenset({"development", "dev", "test", "local"})


def is_development_env(environment: str) -> bool:
    return environment.strip().lower() in DEVELOPMENT_ENV_VALUES


def fake_provider_allowed(environment: str) -> bool:
    return FAKE_PROVIDER_ALLOWED_IN_PRODUCTION or is_development_env(environment)
