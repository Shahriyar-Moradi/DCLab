"""Shared agent contracts (ADR 0009 §1, §8; ADR 0008 §2b).

Every value an AI call may see travels as a ``ContextField`` tagged with its data
class, its outcome scope and its sources (dataset / column ids). The gateway
redacts by these tags, never by regex. Free text (user messages, column names,
descriptions) travels wrapped as ``Untrusted``; a plain ``str`` value is refused.
``holdout`` is not an outcome scope: the final holdout never enters an AI call.
Later prompts add ``AgentRunSpec``, ``AssistantStep``, proposal payloads and
``Citation`` here.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.governance.platform_default import DataClass
from app.domain.agent_records import KEY_PATTERN

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
