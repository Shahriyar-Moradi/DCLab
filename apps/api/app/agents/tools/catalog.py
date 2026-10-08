"""The shared agent tool catalog (ADR 0009 §6; ADR 0008 §6).

One ``ToolDefinition`` per tool, consumed by MCP (``packages/dclab_mcp`` registers the
same names, effects and input schemas — the contract test compares it with
``contracts/agent_tools.json``), the lead agent (P6.3-B) and Studio forms. Read tools
execute in agent consumer mode (``service`` fetches through the services the ``/v1``
routes call, ``shaper`` builds the holdout-free neutral read model, ``render`` turns
it into MCP JSON or ``ContextField``s). Write tools never act from an agent: the
harness turns a call into a ``ToolCallProposal`` at L1 (decision point
``decision_point_key``) and the command runs only when a human accepts it (P6.6-A
binds the accept-time call to ``services``). ``accept_proposal`` is an MCP hand-off
only; the lead agent never gets it. There is no tool for any ``FORBIDDEN_OPERATIONS``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.agents.governance.decision_points import REGISTRY as DECISION_POINTS
from app.agents.tools.shaping import Shaped

Effect = Literal["read", "proposal"]
Surface = Literal["mcp", "assistant", "studio_forms"]
SURFACES: tuple[Surface, ...] = ("mcp", "assistant", "studio_forms")
EXPORT_SCHEMA_VERSION = 1

# ADR 0009 §6 / ADR 0008 §10: asserted absent by name and by effect.
FORBIDDEN_OPERATIONS = (
    "read_rows",
    "read_holdout",
    "select_winner",
    "compute_metric",
    "build_split",
    "infer_entity_column",
    "load_model",
    "execute_code",
    "raw_sql",
    "move_ref",  # forbidden without a human actor; there is no agent tool for it
    "promote_champion",
)
# P6.3-B2: ref moves the assistant surface never proposes (``record_decision.ref_moves``): a
# champion move is a human's promote decision (Studio forms / MCP hand-off), so a lead-loop
# call naming one is a ``rejected_by_validator`` proposal (``champion_move_human_only``).
ASSISTANT_REFUSED_REF_MOVES = frozenset({"champion_model"})


class ToolError(Exception):
    """A typed tool failure (never an internal message): ``code`` is a stable key."""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class ToolContext:
    """Who runs a tool in-process: always the acting human (a token's creator for token
    principals) in one workspace; reads use the same authorization as ``/v1``."""

    db: Session
    actor: Any  # app.db.models.User
    workspace_id: UUID
    service_token_id: UUID | None = None
    agent_run_id: UUID | None = None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    effect: Effect
    capability: tuple[str, ...]  # every token scope its /v1 operations need
    description: str
    input_schema: type[BaseModel]
    operations: tuple[str, ...]  # the /v1 operations of the SDK/MCP path
    services: tuple[str, ...]  # the service functions those routes call (in-process path)
    surfaces: frozenset[Surface]
    data_class: Literal["metadata", "aggregates"] = "metadata"
    outcome_scope: Literal["none", "cv"] = "none"
    decision_point_key: str | None = None
    validator: Callable[[BaseModel], BaseModel] | None = None
    # Reads: ``fetch(reads, args)`` composes /v1 reads over a read source (``ServiceReads``
    # in-process; recorded /v1 responses in the copy test); ``shaper`` = stage 1.
    fetch: Callable[[Any, BaseModel], Any] | None = None
    shaper: Callable[[Any], Shaped] | None = None
    notes: tuple[str, ...] = field(default=())

    @property
    def read_only(self) -> bool:
        return self.effect == "read"

    def parse(self, arguments: Mapping[str, Any]) -> BaseModel:
        """Schema + deterministic validator; a failure is a ``ToolError``, never a crash."""

        try:
            parsed = self.input_schema.model_validate(dict(arguments))
        except ValidationError as exc:
            errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors(include_url=False)][:10]
            raise ToolError("invalid_arguments", json.dumps(errors, default=str)[:1000]) from None
        return self.validator(parsed) if self.validator else parsed

    def service(self, ctx: ToolContext, args: BaseModel) -> Any:
        """The in-process path of a read tool: ``fetch`` over the ``/v1`` services."""

        if self.effect != "read" or self.fetch is None:
            raise ToolError("not_a_read_tool", f"{self.name} proposes; it never executes from an agent")
        from app.agents.tools.definitions.reads import ServiceReads

        return self.fetch(ServiceReads(ctx), args)

    def read(self, ctx: ToolContext, arguments: Mapping[str, Any]) -> Shaped:
        """Run a read tool in agent consumer mode (services authorize as on ``/v1``)."""

        from app.agents.tools.definitions.reads import map_service_errors

        if self.shaper is None:
            raise ToolError("not_a_read_tool", f"{self.name} proposes; it never executes from an agent")
        args = self.parse(arguments)
        with map_service_errors():
            return self.shaper(self.service(ctx, args))


@lru_cache(maxsize=1)
def catalog() -> Mapping[str, ToolDefinition]:
    from app.agents.tools.definitions import DEFINITIONS

    return MappingProxyType({definition.name: definition for definition in DEFINITIONS})


def get(name: str) -> ToolDefinition | None:
    return catalog().get(name)


def visible(surface: Surface) -> tuple[ToolDefinition, ...]:
    return tuple(item for item in catalog().values() if surface in item.surfaces)


def export_payload() -> dict[str, Any]:
    """``contracts/agent_tools.json`` (written by ``scripts.generate_truth_artifacts``)."""

    tools = []
    for item in sorted(catalog().values(), key=lambda d: d.name):
        point = DECISION_POINTS.get(item.decision_point_key) if item.decision_point_key else None
        tools.append({
            "name": item.name,
            "effect": item.effect,
            "read_only": item.read_only,
            "capability": list(item.capability),
            "decision_point_key": item.decision_point_key,
            "decision_point_cap": point.cap if point else None,
            "description": item.description,
            "input_schema": item.input_schema.model_json_schema(),
            "operations": list(item.operations),
            "services": list(item.services),
            "surfaces": sorted(item.surfaces),
            "result": {"data_class": item.data_class, "outcome_scope": item.outcome_scope},
            "notes": list(item.notes),
        })
    return {"schema_version": EXPORT_SCHEMA_VERSION, "forbidden_operations": list(FORBIDDEN_OPERATIONS),
            "tools": tools}


def export_text() -> str:
    """Exactly the rendering of ``scripts.truth_drift._json_dump`` (the digest hashes it)."""

    return json.dumps(export_payload(), indent=2, sort_keys=True) + "\n"


def catalog_digest() -> str:
    """``agent_runs.tool_catalog_digest``: sha256 of the export, computed from code (the
    API image does not ship ``contracts/``)."""

    return hashlib.sha256(export_text().encode("utf-8")).hexdigest()
