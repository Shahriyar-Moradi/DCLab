"""Gateway plumbing for the legacy LLM writers (ADR 0009 §4 "Callers"; until P6.9-A).

The legacy purposes (``semantic_target``, ``semantic_column_type``,
``semantic_missing_value``, ``semantic_leakage``, ``pipeline_audit_*``) call the gateway
through ``complete``: the released prompt of their agent key, the policy role's model
(``legacy_decision`` / ``verifier``), one budget hold per call (released right after)
and an envelope whose fields carry the run's dataset and column ids, so ADR 0005 labels
and the policy bound what leaves the process. Every refusal comes back as a
``CompletionResponse`` (never an exception); the callers map it onto their rule path.
``gateway_service`` is the seam tests replace to inject the fake provider.

Caller contract (``gateway/service.py``): commit (or flush) before calling and hold no
``FOR UPDATE`` lock on the run's workspace, project, experiment or workflow run.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.contracts import ContextEnvelope, ContextField, FieldSource, Untrusted, UNTRUSTED_TEXT_MAX_CHARS
from app.agents.gateway.contract import Annotate, CompletionRequest, CompletionResponse, Refusal
from app.domain.agent_records import KEY_PATTERN

# No prompt release synced yet: the gateway refuses the call (policy_denied) with a row.
NO_RELEASE = UUID(int=0)
_KEY = re.compile(KEY_PATTERN)
_MAX_DEPTH = 5  # the gateway renders at most 6 levels; deeper values travel as text


@dataclass(frozen=True)
class LegacyContext:
    """The attributable run a legacy call belongs to (workspace + lineage + dataset)."""

    db: Session
    workspace_id: UUID
    project_id: UUID | None = None
    experiment_id: UUID | None = None
    workflow_run_id: UUID | None = None
    dataset_id: UUID | None = None
    column_ids: Mapping[str, UUID] = field(default_factory=dict)

    def columns(self, names: Iterable[Any]) -> tuple[FieldSource, ...]:
        """Column sources for ``names``; a name without a recorded column makes the whole
        field unsourced (the gateway then drops it), never a narrower source list."""

        wanted = list(dict.fromkeys(str(name) for name in names))
        if (self.dataset_id is None or not wanted or len(wanted) > 64
                or any(name not in self.column_ids for name in wanted)):
            return ()
        return tuple(FieldSource(kind="column", dataset_id=self.dataset_id, column_id=self.column_ids[name])
                     for name in wanted)

    def dataset(self) -> tuple[FieldSource, ...]:
        return () if self.dataset_id is None else (FieldSource(kind="dataset", dataset_id=self.dataset_id),)


def context_for_upload(db: Session | None, upload_id: UUID | None) -> LegacyContext | None:
    """The upload's experiment, workflow run, project and published dataset; ``None``
    when the upload has no attributable lineage yet (no AI call is possible then)."""

    if db is None or upload_id is None:
        return None
    from app.db.models import DatasetColumn
    from app.services.observability_service import pipeline_context_for_upload

    lineage = pipeline_context_for_upload(db, upload_id)
    if lineage is None:
        return None
    upload, workflow_run, experiment = lineage
    dataset_id = upload.dataset_id or experiment.source_dataset_id or experiment.dataset_id
    columns: dict[str, UUID] = {}
    if dataset_id is not None:
        rows = db.execute(
            select(DatasetColumn.name, DatasetColumn.id).where(
                DatasetColumn.workspace_id == experiment.workspace_id, DatasetColumn.dataset_id == dataset_id
            )
        ).all()
        columns = {name: column_id for name, column_id in rows}
    return LegacyContext(
        db=db, workspace_id=experiment.workspace_id, project_id=experiment.project_id,
        experiment_id=experiment.id, workflow_run_id=workflow_run.id, dataset_id=dataset_id,
        column_ids=columns,
    )


# --- envelope values ------------------------------------------------------------------


def text(value: Any) -> Untrusted:
    return Untrusted(untrusted_text=str(value)[:UNTRUSTED_TEXT_MAX_CHARS])


def number(value: Any) -> int | float | bool | None:
    """A JSON-safe number (numpy included); NaN, infinities and non-numbers become ``None``."""

    if hasattr(value, "item") and not isinstance(value, (list, tuple, dict, str)):
        try:
            value = value.item()
        except (TypeError, ValueError):
            return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def scalar(value: Any) -> Any:
    """A raw dataset value: a finite number stays a number, anything else is untrusted text."""

    if value is None:
        return None
    converted = number(value)
    return converted if converted is not None else text(value)


def wire(value: Any, depth: int = 0) -> Any:
    """Report data as gateway values: strings become ``Untrusted``, objects whose keys are
    not code keys become ``[{"name": ..., "value": ...}]``, deep values become text."""

    if isinstance(value, str):
        return text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return number(value)  # NaN and infinities become None
    if hasattr(value, "item") and not isinstance(value, (dict, list, tuple)):  # numpy scalars
        try:
            return wire(value.item(), depth)
        except (TypeError, ValueError):
            return text(value)
    if depth >= _MAX_DEPTH and isinstance(value, (dict, list, tuple)):
        return text(json.dumps(value, sort_keys=True, default=str))
    if isinstance(value, dict):
        if all(isinstance(name, str) and _KEY.fullmatch(name) for name in value):
            return {name: wire(item, depth + 1) for name, item in value.items()}
        return [{"name": text(name), "value": wire(item, depth + 2)} for name, item in value.items()]
    if isinstance(value, (list, tuple)):
        return [wire(item, depth + 1) for item in value]
    return text(value)


def ctx_field(key: str, value: Any, data_class: str, sources: tuple[FieldSource, ...], *,
              required: bool = True, outcome_scope: str = "none") -> ContextField:
    return ContextField(key=key, value=value, data_class=data_class, outcome_scope=outcome_scope,
                        sources=sources, required=required)


# --- the call -------------------------------------------------------------------------


@dataclass(frozen=True)
class LegacyCall:
    agent_role: str  # legacy_decision | verifier
    agent_key: str
    purpose: str
    decision_point_key: str
    output_schema: type[BaseModel]
    prompt_version: int  # the released version this caller is written for; another one refuses
    max_output_tokens: int
    timeout_s: float
    max_data_class: str = "aggregates"
    outcome_scope: str = "none"
    model: str | None = None  # a model from the role's allowlist; None = the role default
    temperature: float | None = 0.0  # None: the provider default (the auditor never set one)
    # Legacy rows store a narrowed summary (never the model's rationale), which the gateway
    # cache cannot serve back, so legacy calls do not use it.
    cache: bool = False


def gateway_service() -> Any:
    from app.agents.gateway.service import GatewayService

    return GatewayService()


def _release_id(db: Session, agent_key: str, version: int) -> UUID:
    from app.db.models import PromptRelease

    found = db.scalar(
        select(PromptRelease.id).where(
            PromptRelease.agent_key == agent_key, PromptRelease.version == version,
            PromptRelease.status == "released",
        )
    )
    return found or NO_RELEASE


def _estimate_micros(call: LegacyCall, envelope: ContextEnvelope) -> int:
    """Upper bound of the call's worst case: the envelope JSON (with its sources) is larger
    than the redacted payload; priced at the dearest model the role may route to."""

    from app.agents.gateway import budget
    from app.agents.governance.platform_default import CAPS
    from app.agents.prompt_releases import prompt_text

    try:
        prompt = prompt_text(call.agent_key, call.prompt_version)
    except OSError:
        prompt = ""
    allowed = getattr(CAPS.models.roles, call.agent_role).allowed
    tokens = budget.estimate_tokens(prompt + envelope.model_dump_json()) + 200
    return max(budget.worst_case_micros(model, input_tokens=tokens, max_output_tokens=call.max_output_tokens)
               for model in allowed if model in budget.PRICES_MICROS_PER_MTOK)


def complete(
    context: LegacyContext,
    call: LegacyCall,
    fields: Iterable[ContextField],
    *,
    annotate: Annotate | None = None,
    gateway: Any = None,
) -> CompletionResponse:
    """Reserve, call and release. Never raises; a refusal before the gateway call (budget,
    malformed envelope) comes back without an ``invocation_id``."""

    service = gateway or gateway_service()
    try:
        envelope = ContextEnvelope(fields=tuple(fields))
        estimate = _estimate_micros(call, envelope)
    except Exception as exc:  # a malformed envelope is a refusal, never a pipeline failure
        return CompletionResponse(ok=False, refusal=Refusal(code="policy_denied", message=type(exc).__name__))
    held = service.reserve(context.db, workspace_id=context.workspace_id, estimate_micros=estimate,
                           project_id=context.project_id)
    if isinstance(held, Refusal):
        return CompletionResponse(ok=False, refusal=held)
    try:
        request = CompletionRequest(
            agent_role=call.agent_role, agent_key=call.agent_key, purpose=call.purpose,
            decision_point_key=call.decision_point_key, workspace_id=context.workspace_id,
            project_id=context.project_id, experiment_id=context.experiment_id,
            workflow_run_id=context.workflow_run_id,
            prompt_release_id=_release_id(context.db, call.agent_key, call.prompt_version),
            envelope=envelope, output_schema=call.output_schema, max_output_tokens=call.max_output_tokens,
            max_data_class=call.max_data_class, outcome_scope=call.outcome_scope, model=call.model,
            temperature=call.temperature,
            budget=held, timeout_s=min(max(call.timeout_s, 1.0), 300.0), cache=call.cache,
        )
        return service.complete(context.db, request, annotate=annotate)
    except Exception as exc:
        return CompletionResponse(ok=False, refusal=Refusal(code="policy_denied", message=type(exc).__name__))
    finally:
        service.release(context.db, held)


def refusal_text(refusal: Refusal | None) -> str:
    if refusal is None:
        return "provider_error"
    return f"{refusal.code}: {refusal.message}" if refusal.message else refusal.code


__all__ = [
    "LegacyCall",
    "LegacyContext",
    "complete",
    "context_for_upload",
    "ctx_field",
    "gateway_service",
    "number",
    "refusal_text",
    "scalar",
    "text",
    "wire",
]
