"""The semantic port: what a decision point asks Jev, and what it gets back (ADR 0008 §5, §8).

A caller (P6.9-A's ``DecisionPoint`` hook; the assistant router later) builds one
``SemanticAsk`` per purpose: shared ``context`` fields, and one ``Subject`` per question
with the rule's answer. ``semantic_port()`` returns the deterministic port (rule value,
no gateway call, nothing written) when AI is off — ``AI_ENABLED`` false, no ``agents``
extra or key — and the Jev port otherwise; the Jev port also falls back to the
deterministic port, with zero calls, while a kill switch is off. State values are band
labels from the release (``releases.ratio_band`` …), column names (exactly the stored
name; ``releases.name_tokens`` for tokens) wrapped as ``Untrusted``; numbers never enter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents.contracts import Untrusted
from app.agents.gateway.contract import BudgetReservation
from app.domain.agent_records import KEY_PATTERN


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Subject(_Frozen):
    """One question: about a column (``column_id`` + its stored name as ``question_key``
    and its band ``fields``) or, for non-column purposes, the release's code key."""

    question_key: str = Field(min_length=1, max_length=200)
    column_id: UUID | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    rule_answer: bool | str | None = None


class SemanticAsk(_Frozen):
    """``workspace_id`` must already be resolved by the caller's authorization (the
    gateway re-checks that every attribution id lies inside it)."""

    purpose: str = Field(pattern=KEY_PATTERN)
    workspace_id: UUID
    project_id: UUID | None = None
    experiment_id: UUID | None = None
    dataset_id: UUID | None = None
    workflow_run_id: UUID | None = None
    agent_run_id: UUID | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    context_columns: dict[str, UUID] = Field(default_factory=dict)  # untrusted context field -> its column
    subjects: tuple[Subject, ...] = Field(min_length=1, max_length=512)
    choices: tuple[str, ...] = Field(default=(), max_length=32)  # caller-supplied choice keys (intent route)
    user_text: tuple[Untrusted, ...] = Field(default=(), max_length=10)
    source_datasets: tuple[UUID, ...] = Field(default=(), max_length=64)
    source_columns: tuple[UUID, ...] = Field(default=(), max_length=512)
    budget: BudgetReservation | None = None  # one pre-reservation per run; else one per ask
    # P6.9-A: the levels snapshotted at job claim, per §1b answer kind ("*" = no kind);
    # None reads the live ``effective_level``. Inside a run the evidence partition is
    # ``train`` (ADR 0008 §2c); None takes the registry's.
    levels: dict[str, int] | None = None
    evidence_partition: Literal["metadata", "train"] | None = None


@dataclass(frozen=True)
class Resolution:
    question_key: str
    column_id: UUID | None
    rule_answer: Any
    value_used: Any
    agreement: str  # off | agree | disagree | abstain | unavailable
    level: int = 0
    policy_outcome: str = "rule"  # rule | ai | review
    ai_answer: Any = None  # in the rule's vocabulary; None when abstaining or unavailable
    raw_answer: dict[str, Any] | None = None
    confidence: float | None = None
    in_acting_band: bool = False
    cache_hit: bool = False
    refusal: str | None = None
    answer_id: UUID | None = None  # the semantic_decision_answers row
    invocation_id: UUID | None = None  # the llm_invocations row


@dataclass(frozen=True)
class SemanticOutcome:
    purpose: str
    ai: str  # off | on
    resolutions: tuple[Resolution, ...]
    gateway_calls: int = 0


Validator = Callable[[Subject, Any], bool]  # accepts the Jev value of an L2 disagreement
NO_KIND = "*"  # ``SemanticAsk.levels`` key of an answer without a §1b kind


class SemanticPort(Protocol):
    def resolve(self, db: Session, ask: SemanticAsk, *, validator: Validator | None = None) -> SemanticOutcome: ...


def semantic_port(gateway: Any = None, *, settings: Any = None) -> SemanticPort:
    """The deterministic port unless AI is on and the Jev provider is usable."""

    from app.agents.semantic.deterministic import DeterministicSemanticPort

    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    if not bool(getattr(settings, "ai_enabled", False)):
        return DeterministicSemanticPort()
    if gateway is None:
        from app.agents.gateway.service import GatewayService

        gateway = GatewayService()
    if not gateway.provider_ready("typesafe"):
        return DeterministicSemanticPort()
    from app.agents.semantic.typesafe_jev import JevSemanticPort

    return JevSemanticPort(gateway, settings=settings)


__all__ = ["NO_KIND", "Resolution", "SemanticAsk", "SemanticOutcome", "SemanticPort", "Subject", "Validator",
           "semantic_port"]
