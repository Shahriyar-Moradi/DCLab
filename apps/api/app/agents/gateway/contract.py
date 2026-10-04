"""Gateway request / response types (ADR 0009 §4).

Callers never see an exception from the gateway: every failure is a typed
``Refusal`` (and a ledger row). Deviations from the ADR sketch, all additive:
``max_data_class`` / ``outcome_scope`` declare the purpose's allowed class and
scope (narrowed further by the decision point and the workspace); ``model`` is
an optional request for a model from the role's allowlist; ``provider``,
``model`` and ``invocation_id`` are ``None`` when the call was refused before
routing or the ledger itself could not be written.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.contracts import ContextEnvelope, OutcomeScope, TranscriptItem, Untrusted
from app.agents.governance.platform_default import DataClass
from app.domain.agent_records import KEY_PATTERN

RefusalCode = Literal[
    "kill_switch",
    "policy_unavailable",
    "model_not_allowed",
    "data_class_exceeded",
    "outcome_scope_exceeded",
    "budget_exhausted",
    "rate_limited",
    "breaker_open",
    "provider_error",
    "timeout",
    "invalid_output",
    "policy_denied",
]
AgentRole = Literal["lead", "specialist", "legacy_decision", "verifier"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Refusal(_Frozen):
    code: RefusalCode
    scope: str | None = None
    message: str = Field(default="", max_length=500)
    retry_after_s: int | None = None


class GatewayRefusal(Exception):
    """Internal control flow only; converted to a ``Refusal`` before returning."""

    def __init__(self, code: RefusalCode, message: str = "", *, scope: str | None = None,
                 retry_after_s: int | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.refusal = Refusal(code=code, scope=scope, message=message[:500], retry_after_s=retry_after_s)


class Usage(_Frozen):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class BudgetReservation(_Frozen):
    """A hold on the locked counter rows (``budget.reserve``). ``seal`` is an HMAC
    over every other field, so a hand-built or altered reservation is refused;
    ``expires_at`` (issue time + the run kind's policy wall limit) stops replay."""

    id: UUID
    workspace_id: UUID
    project_id: UUID | None = None
    run_kind: str | None = None
    agent_run_id: UUID | None = None
    counter_ids: tuple[UUID, ...]
    held_micros: int = Field(ge=0)
    currency: str = "USD"
    expires_at: datetime
    seal: str = Field(min_length=64, max_length=64)


class _Attributed(_Frozen):
    workspace_id: UUID
    project_id: UUID | None = None
    experiment_id: UUID | None = None
    workflow_run_id: UUID | None = None
    agent_run_id: UUID | None = None

    @model_validator(mode="after")
    def _attributed(self):
        # ck_llm_invocations_attributed: the ledger row must point at something.
        if not any((self.project_id, self.experiment_id, self.workflow_run_id, self.agent_run_id)):
            raise ValueError("a gateway call needs a project, experiment, workflow run or agent run")
        return self


class CompletionRequest(_Attributed):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    agent_role: AgentRole
    agent_key: str = Field(pattern=KEY_PATTERN)
    purpose: str = Field(pattern=KEY_PATTERN)
    decision_point_key: str | None = Field(default=None, pattern=KEY_PATTERN)
    prompt_release_id: UUID
    envelope: ContextEnvelope = ContextEnvelope()
    transcript: tuple[TranscriptItem, ...] = Field(default=(), max_length=200)
    user_text: tuple[Any, ...] = Field(default=(), max_length=50)  # Untrusted; checked by redaction
    output_schema: type[BaseModel]
    max_output_tokens: int = Field(ge=1, le=128_000)
    temperature: float | None = Field(default=0.0, ge=0, le=2)
    max_data_class: DataClass = "metadata"
    outcome_scope: OutcomeScope = "none"
    model: str | None = Field(default=None, max_length=64)
    budget: BudgetReservation
    timeout_s: float = Field(default=60.0, gt=0, le=300)
    cache: bool = True


class CompletionResponse(_Frozen):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    ok: bool
    output: BaseModel | None = None
    refusal: Refusal | None = None
    provider: str | None = None
    model: str | None = None
    resolved_model: str | None = None
    usage: Usage = Usage()
    cost_micros: int = 0
    latency_ms: int = 0
    cache_hit: bool = False
    invocation_id: UUID | None = None
    prompt_release_id: UUID | None = None
    data_class: DataClass = "metadata"
    outcome_scope: OutcomeScope = "none"


class SemanticQuestion(_Frozen):
    question_key: str = Field(min_length=1, max_length=200)  # travels as untrusted text
    primitive: Literal["noul", "choice", "score"]
    choices: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def _choices(self) -> "SemanticQuestion":
        if any(not re.fullmatch(KEY_PATTERN, choice) for choice in self.choices):
            raise ValueError("choices are code labels")
        if (self.primitive == "choice") != bool(self.choices):
            raise ValueError("choice questions (and only they) list choices")
        return self


class SemanticAnswer(_Frozen):
    question_key: str = Field(max_length=200)
    answer: dict[str, Any]
    confidence: float | None = Field(default=None, ge=0, le=1)
    probabilities: dict[str, float] | None = None


class SemanticAnswers(_Frozen):
    """Output schema of a Jev call."""

    answers: tuple[SemanticAnswer, ...]


class SemanticDecisionRequest(_Attributed):
    purpose: str = Field(pattern=KEY_PATTERN)
    release_version: str = Field(pattern="^[1-9][0-9]{0,8}$")
    decision_point_key: str = Field(pattern=KEY_PATTERN)
    dataset_id: UUID | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    # Top-level state keys about a source column: the only place ``Untrusted`` may appear in state.
    column_keys: dict[str, UUID] = Field(default_factory=dict, max_length=512)
    # User free text reaches Jev only when the policy's ``data.user_text_to_jev`` is on.
    user_text: tuple[Any, ...] = Field(default=(), max_length=10)
    data_class: DataClass = "metadata"  # declared class of ``state``
    questions: tuple[SemanticQuestion, ...] = Field(min_length=1, max_length=50)
    source_datasets: tuple[UUID, ...] = Field(default=(), max_length=64)
    source_columns: tuple[UUID, ...] = Field(default=(), max_length=512)
    budget: BudgetReservation
    timeout_ms: int = Field(default=1000, ge=1, le=60_000)


class SemanticDecisionResponse(_Frozen):
    ok: bool
    answers: tuple[SemanticAnswer, ...] = ()
    refusal: Refusal | None = None
    cache_hit: bool = False
    invocation_id: UUID | None = None
    latency_ms: int = 0
    cost_micros: int = 0


__all__ = [
    "BudgetReservation",
    "CompletionRequest",
    "CompletionResponse",
    "GatewayRefusal",
    "Refusal",
    "RefusalCode",
    "SemanticAnswer",
    "SemanticAnswers",
    "SemanticDecisionRequest",
    "SemanticDecisionResponse",
    "SemanticQuestion",
    "TranscriptItem",
    "Untrusted",
    "Usage",
]
