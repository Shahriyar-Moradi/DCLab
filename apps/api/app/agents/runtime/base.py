"""The runtime protocol (ADR 0009 §5.1 step 6; P6.10-A).

A runtime gets a ``RuntimeSession`` — the redacted envelope, the run's limits, the
tool names it may call and two calls back into the harness — and returns a
``RuntimeOutput``. It never receives a database session, a storage client or a
provider key: every model call is ``session.complete`` (pre-call hooks, then the
gateway, then the ledger and an event) and every tool call is ``session.call_tool``
(registry, capability, validator, consumer-mode service, shaping, events; write
tools become L1 proposals and never act). One runtime instance per run; the harness
builds it from ``AgentRunSpec`` with the factory registered for ``spec.runtime``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.agents.contracts import AgentRunSpec, Citation, ContextEnvelope, ContextField, RunLimits, TranscriptItem
from app.agents.gateway.contract import CompletionResponse

ToolStatus = Literal["result", "proposed", "denied", "rejected_by_validator", "error", "stub"]


@dataclass(frozen=True)
class ToolOutcome:
    """What a tool call gives back to the runtime. ``fields`` are tagged, redaction-ready
    (holdout-free, within the run's data class and outcome scope); a write tool returns
    the pending proposal id instead ("pending your confirmation")."""

    tool: str
    ok: bool
    status: ToolStatus
    code: str | None = None
    fields: tuple[ContextField, ...] = ()
    result_digest: str | None = None
    proposal_id: UUID | None = None
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True)
class ProposalDraft:
    """A typed proposal from a runtime's output (``ExperimentReviewProposal`` …); the
    harness validates it and stores it at the decision point's level (L0 shadow …)."""

    proposal_type: str
    decision_point_key: str
    payload: Mapping[str, Any]
    answer_kind: str | None = None
    rationale: str | None = None


@dataclass(frozen=True)
class RuntimeOutput:
    output: BaseModel | None = None
    citations: tuple[Citation, ...] = ()
    message: str | None = None
    proposal: ProposalDraft | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)


class RuntimeSession(Protocol):
    envelope: ContextEnvelope
    limits: RunLimits
    tools: tuple[str, ...]

    def complete(self, *, output_schema: type[BaseModel], max_output_tokens: int,
                 transcript: tuple[TranscriptItem, ...] = (), user_text: tuple[Any, ...] = ()) -> CompletionResponse:
        ...

    def call_tool(self, name: str, arguments: Mapping[str, Any], *, reason: str = "") -> ToolOutcome:
        ...


class Runtime(Protocol):
    name: str  # agent_runs.runtime
    version: str
    output_schema: type[BaseModel] | None

    def run(self, session: RuntimeSession) -> RuntimeOutput:
        ...


RuntimeFactory = Callable[[AgentRunSpec], Runtime]
