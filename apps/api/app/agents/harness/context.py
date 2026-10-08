"""The run's ``ContextEnvelope`` (ADR 0009 §5.1 step 3; ADR 0008 §2b).

Built in agent consumer mode from the catalog's read tools (the same in-process
``/v1`` services, authorization and holdout-free shapers), so the envelope is
metadata and aggregates only, tagged per field, with no holdout key or scope. Fields
above the run's data class or outcome scope are dropped here (the gateway would drop
them too); the envelope is capped at 500 fields. Its digest (canonical JSON of the
tagged fields) is written once to ``agent_runs.context_digest``. An agent class may add
its own holdout-free sections (``AgentClass.context``, P6.4-A: the Critic's model card and
non-holdout verifier checks, the Investigator's and Planner's column metadata), rendered
and filtered exactly like tool results. No field may carry a holdout-scoped report key
(``HOLDOUT_SCOPED_REPORT_KEYS``: the pipeline auditor's stored advisory report is floored
with the full, holdout-including deterministic status): such a field is dropped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.contracts import ContextEnvelope, ContextField
from app.agents.harness.recorder import digest
from app.agents.runtime.base import ContextRequest
from app.agents.tools.catalog import ToolContext, ToolError, catalog
from app.agents.tools.render import to_context_fields
from app.agents.tools.shaping import names_holdout_report

MAX_FIELDS = 500
_RANK = {"metadata": 0, "aggregates": 1, "sample_values": 2, "none": 0, "cv": 1}
# Subject kind -> (read tool, argument) pairs added after the project summary.
SUBJECT_TOOLS: dict[str, tuple[tuple[str, str], ...]] = {
    "experiment": (("get_experiment", "experiment_id"), ("get_findings", "experiment_id")),
    "model_version": (("get_model", "model_version_id"), ("get_model_card", "model_version_id")),
    "dataset_version": (("inspect_dataset", "dataset_id"),),
}


@dataclass(frozen=True)
class BuiltContext:
    envelope: ContextEnvelope
    digest: str
    reads: tuple[str, ...]
    dropped: dict[str, int] = field(default_factory=dict)


def within(fields: tuple[ContextField, ...], *, data_class: str, outcome_scope: str) -> tuple[ContextField, ...]:
    return tuple(item for item in fields if _RANK[item.data_class] <= _RANK[data_class]
                 and _RANK[item.outcome_scope] <= _RANK[outcome_scope]
                 and not names_holdout_report(item.key) and not names_holdout_report(item.value))


def build_context(ctx: ToolContext, *, project_id: Any, subject_kind: str | None, subject_id: Any,
                  data_class: str, outcome_scope: str, extra: Callable[[ContextRequest], Any] | None = None
                  ) -> BuiltContext:
    calls: list[tuple[str, dict[str, Any]]] = []
    if project_id is not None:
        calls.append(("inspect_project", {"project_id": str(project_id)}))
    for tool, argument in SUBJECT_TOOLS.get(subject_kind or "", ()):
        if subject_id is not None:
            calls.append((tool, {argument: str(subject_id)}))
    fields: list[ContextField] = []
    reads: list[str] = []
    dropped = {"unavailable": 0, "class_or_scope": 0, "capped": 0}
    sections: list[tuple[str, Callable[[], Any]]] = [
        (tool, lambda tool=tool, arguments=arguments: catalog()[tool].read(ctx, arguments)) for tool, arguments in calls]
    if extra is not None:
        request = ContextRequest(tool_ctx=ctx, project_id=project_id, subject_kind=subject_kind, subject_id=subject_id)
        try:
            # A section is a Shaped result or a callable producing one (failing on its own).
            sections += [(name, item if callable(item) else (lambda item=item: item)) for name, item in extra(request)]
        except ToolError:
            dropped["unavailable"] += 1
    for tool, read in sections:
        try:
            shaped = read()
        except ToolError:
            dropped["unavailable"] += 1  # e.g. no findings yet; authorization was checked first
            continue
        rendered = to_context_fields(tool, shaped)
        kept = within(rendered, data_class=data_class, outcome_scope=outcome_scope)
        dropped["class_or_scope"] += len(rendered) - len(kept)
        fields.extend(kept)
        reads.append(tool)
    dropped["capped"] = max(0, len(fields) - MAX_FIELDS)
    envelope = ContextEnvelope(fields=tuple(fields[:MAX_FIELDS]))
    return BuiltContext(envelope=envelope, digest=digest([item.model_dump(mode="json") for item in envelope.fields]),
                        reads=tuple(reads), dropped=dropped)
