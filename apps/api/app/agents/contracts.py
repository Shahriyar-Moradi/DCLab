"""Shared agent contracts (ADR 0009 §1, §8; ADR 0008 §2b).

Every value an AI call may see travels as a ``ContextField`` tagged with its data
class, its outcome scope and its sources (dataset / column ids). The gateway
redacts by these tags, never by regex. Free text (user messages, column names,
descriptions) travels wrapped as ``Untrusted``; a plain ``str`` value is refused.
``holdout`` is not an outcome scope: the final holdout never enters an AI call.
``AgentRunSpec`` / ``RunLimits`` / ``AgentRunResult`` / ``Citation`` are the harness
contract (ADR 0009 §5, §7.1; P6.10-A); P6.3-B adds ``AssistantStep`` (the only thing the
lead model may return) and ``LeadTurn`` (one assistant turn's input on the spec).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.governance.platform_default import DataClass
from app.domain.agent_records import AGENT_SUBJECT_KINDS, KEY_PATTERN

OutcomeScope = Literal["none", "cv"]
UNTRUSTED_TEXT_MAX_CHARS = 4000


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Untrusted(_Frozen):
    """Text from a user or a dataset; never instructions. Wire form ``{"untrusted_text": …}``."""

    untrusted_text: str = Field(max_length=UNTRUSTED_TEXT_MAX_CHARS)


class FieldSource(_Frozen):
    """Where a value comes from.

    ``dataset`` / ``column`` carry ids whose ADR 0005 ``llm_exposure_policy`` labels
    bound the call. ``system`` = DCLab-generated numbers, flags and ids with no
    dataset content: never text, capped at ``metadata``. ``workspace_text`` =
    tenant-written text that is not dataset content (project / experiment names,
    thread titles, user messages): always ``Untrusted``, capped at ``metadata``.
    """

    kind: Literal["dataset", "column", "system", "workspace_text"]
    dataset_id: UUID | None = None
    column_id: UUID | None = None

    @model_validator(mode="after")
    def _ids_match_kind(self) -> "FieldSource":
        expected = {"system": (False, False), "workspace_text": (False, False), "dataset": (True, False),
                    "column": (True, True)}.get(self.kind)
        if (self.dataset_id is not None, self.column_id is not None) != expected:
            raise ValueError(f"{self.kind} source ids do not match its kind")
        return self


SYSTEM_SOURCE = FieldSource(kind="system")
WORKSPACE_TEXT_SOURCE = FieldSource(kind="workspace_text")


class ContextField(_Frozen):
    """One tagged value. ``value`` may hold numbers, booleans, ``None``, UUIDs,
    ``Untrusted`` and lists / key-named objects of those (checked by the gateway;
    ``Untrusted`` needs a dataset, column or workspace_text source).
    An empty ``sources`` means "unrecorded" and gets the most restrictive treatment
    (dropped). ``required`` fields that redaction would drop refuse the call."""

    key: str = Field(pattern=KEY_PATTERN)
    value: Any
    data_class: DataClass
    outcome_scope: OutcomeScope
    sources: tuple[FieldSource, ...] = Field(default=(), max_length=64)
    required: bool = False


class ContextEnvelope(_Frozen):
    fields: tuple[ContextField, ...] = Field(default=(), max_length=500)


class TranscriptItem(_Frozen):
    """A prior turn or tool result; its fields are redacted exactly like the envelope."""

    kind: Literal["user_message", "assistant_message", "tool_result"]
    tool_name: str | None = Field(default=None, pattern=KEY_PATTERN)
    fields: tuple[ContextField, ...] = Field(default=(), max_length=200)


# --- the harness contract (ADR 0009 §5; P6.10-A) ------------------------------------------

RunKind = Literal["assistant", "lead", "specialist", "ops"]
RuntimeName = Literal["fake", "nooa_predict", "lead_loop"]
CitationKind = Literal["experiment", "dataset_version", "problem_spec", "split_plan", "model_version",
                       "candidate", "finding", "decision", "proposal", "prediction"]


class RunLimits(_Frozen):
    """``agent_runs.limits`` (≤ 2 KB): policy ⊓ spec; hooks may only narrow it."""

    steps: int = Field(ge=0, le=1000)
    tokens: int = Field(ge=0)
    wall_s: int = Field(ge=1, le=3600)
    cost_micros: int = Field(ge=0)
    tool_calls: int = Field(ge=0, le=1000)

    def narrow(self, other: "RunLimits | None") -> "RunLimits":
        if other is None:
            return self
        return RunLimits(**{name: min(getattr(self, name), getattr(other, name)) for name in type(self).model_fields})


class LeadTurn(_Frozen):
    """One lead-agent turn's input (P6.3-B): the user's message (untrusted) and the bounded,
    tagged transcript of the thread's earlier items; the graph, re-read every turn, stays
    the memory. Never persisted on the run row (the turn runs in the API process)."""

    user_text: Untrusted
    transcript: tuple[TranscriptItem, ...] = Field(default=(), max_length=20)


class AgentRunSpec(_Frozen):
    """What to run, for whom. Exactly one principal: a user, or a service token (which
    acts as its creator, re-checked from the token row on every run). ``run_id`` names
    an existing queued run (the ``agents.run`` job rebuilds the spec from the row);
    ``turn`` is the ``lead_loop`` runtime's input."""

    workspace_id: UUID
    project_id: UUID | None = None
    kind: RunKind
    agent_key: str = Field(pattern=KEY_PATTERN)
    agent_version: str = Field(min_length=1, max_length=32)
    runtime: RuntimeName
    runtime_version: str = Field(min_length=1, max_length=64)
    purpose: str = Field(pattern=KEY_PATTERN)
    decision_point_key: str | None = Field(default=None, pattern=KEY_PATTERN)
    subject_kind: str | None = None
    subject_id: UUID | None = None
    prompt_release_id: UUID | None = None
    user_id: UUID | None = None
    service_token_id: UUID | None = None
    outcome_scope: OutcomeScope = "none"
    limits: RunLimits | None = None
    estimate_micros: int | None = Field(default=None, ge=0)
    tool_surface: Literal["assistant"] | None = None  # the catalog surface the runtime may call
    may_propose: bool = True  # ADR 0009 §5.1 step 1: ML-write for any run that may propose
    parent_run_id: UUID | None = None
    run_id: UUID | None = None
    turn: LeadTurn | None = None

    @model_validator(mode="after")
    def _principal_and_subject(self) -> "AgentRunSpec":
        if (self.user_id is None) == (self.service_token_id is None):
            raise ValueError("exactly one principal: user_id or service_token_id")
        if self.subject_kind is not None and self.subject_kind not in AGENT_SUBJECT_KINDS:
            raise ValueError("unknown subject kind")
        if self.turn is not None and self.runtime != "lead_loop":
            raise ValueError("a turn is the lead_loop runtime's input")
        return self


class Citation(_Frozen):
    """A graph node a claim rests on (ADR 0009 §7.1); validated against the run's project."""

    kind: CitationKind
    id: UUID


class ToolCall(_Frozen):
    tool: str = Field(max_length=64)  # a catalog name; anything else rejects the step
    arguments: dict[str, Any] = Field(default_factory=dict)  # validated by the tool's input schema
    reason: str = Field(default="", max_length=200)


class AssistantStep(_Frozen):
    """One lead-agent step (ADR 0009 §7.1): tool calls, or a final answer / clarification /
    done. ``message`` is the Markdown subset (no raw HTML, images or external links; links
    only ``dclab://<kind>/<id>`` to cited nodes); every claim cites a node of the project."""

    kind: Literal["tool_calls", "answer", "clarify", "done"]
    tool_calls: tuple[ToolCall, ...] = Field(default=(), max_length=6)
    message: str | None = Field(default=None, max_length=4000)
    citations: tuple[Citation, ...] = Field(default=(), max_length=32)


class AgentRunResult(_Frozen):
    run_id: UUID | None
    status: str  # an agent_runs status, or ``refused`` when no run row was created
    error_code: str | None = None
    output_digest: str | None = None
    proposal_ids: tuple[UUID, ...] = ()
    cost_micros: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
