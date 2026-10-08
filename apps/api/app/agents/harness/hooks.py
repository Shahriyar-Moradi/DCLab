"""Harness hooks with closed effect sets (ADR 0009 §5.2).

Five stages, each with the only effects it may return:

| stage | effects |
| --- | --- |
| ``pre_run`` | ``Observe``, ``Deny``, ``NarrowLimits`` |
| ``pre_call`` | ``Observe``, ``Deny``, ``AddSystemNote`` (bounded) |
| ``pre_tool`` | ``Observe``, ``Deny``, ``ModifyArguments`` (only the ``validator`` hook), ``DowngradeToProposal`` |
| ``post_tool`` | ``Observe``, ``ModifyResult`` (shaping / bounding / holdout stripping only), ``AttachCitation`` |
| ``post_run`` | ``Observe``, ``DenyOutput`` (-> ``rejected_by_validator``), ``AttachCitation`` |

Hooks are registered in code, ordered, listed in the run's first event, and are
read-only: they get a ``HookContext`` whose session they may only read (switches,
levels, node existence), never write or commit; no hook may call a provider, write
product state or widen limits (``NarrowLimits`` is clamped to the current limits; ``ModifyResult`` may not add
a holdout key, widen the class or scope, or drop a source). A hook that raises denies
(``hook_failed``, fail closed); an effect outside its stage's set is a
``HookViolation`` (the run fails). The first ``Deny`` / ``DenyOutput`` stops the chain.
The recorder and the budget settle/release are not hooks: they run at fixed lifecycle
points of ``service.py`` and cannot be removed or reordered by a registered hook.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal
from uuid import UUID

from app.agents.contracts import Citation, ContextField, RunLimits
from app.agents.tools.catalog import FORBIDDEN_OPERATIONS, ToolDefinition, ToolError
from app.agents.tools.shaping import HOLDOUT_KEY, Shaped, Text, strip_holdout

logger = logging.getLogger("dclab.agents.harness")

Stage = Literal["pre_run", "pre_call", "pre_tool", "post_tool", "post_run"]
STAGES: tuple[Stage, ...] = ("pre_run", "pre_call", "pre_tool", "post_tool", "post_run")
VALIDATOR_HOOK = "validator"
SYSTEM_NOTE_MAX_CHARS = 500
_RANK = {"metadata": 0, "aggregates": 1, "sample_values": 2, "none": 0, "cv": 1}


@dataclass(frozen=True)
class Observe:
    note: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Deny:
    reason: str
    hook: str = ""  # set by the chain: which hook denied


@dataclass(frozen=True)
class NarrowLimits:
    limits: RunLimits


@dataclass(frozen=True)
class AddSystemNote:
    text: str


@dataclass(frozen=True)
class ModifyArguments:
    arguments: dict[str, Any]


@dataclass(frozen=True)
class DowngradeToProposal:
    pass


@dataclass(frozen=True)
class ModifyResult:
    shaped: Shaped


@dataclass(frozen=True)
class AttachCitation:
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class DenyOutput:
    reason: str


Effect = Observe | Deny | NarrowLimits | AddSystemNote | ModifyArguments | DowngradeToProposal | ModifyResult \
    | AttachCitation | DenyOutput
ALLOWED_EFFECTS: dict[Stage, tuple[type, ...]] = {
    "pre_run": (Observe, Deny, NarrowLimits),
    "pre_call": (Observe, Deny, AddSystemNote),
    "pre_tool": (Observe, Deny, ModifyArguments, DowngradeToProposal),
    "post_tool": (Observe, ModifyResult, AttachCitation),
    "post_run": (Observe, DenyOutput, AttachCitation),
}


class HookViolation(Exception):
    """A hook returned an effect its stage does not allow (a programming error)."""


@dataclass
class HookContext:
    """What hooks may read: the run's identity, principal, limits and counters. Read-only:
    the ``db`` session serves checks (switches, levels, node existence) and a hook never
    adds, flushes or commits through it; ``record`` is the harness's own recorder."""

    db: Any
    run_id: UUID
    workspace_id: UUID
    project_id: UUID | None
    agent_key: str
    purpose: str
    principal: Any
    may_propose: bool
    data_class: str
    outcome_scope: str
    limits: RunLimits
    tools: tuple[str, ...]
    ai_enabled: bool
    usage: dict[str, int] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)
    proposal_ids: list[UUID] = field(default_factory=list)
    record: Callable[..., Any] | None = None  # the recorder (observe-only hooks)
    tool_ctx: Any = None  # the run's consumer-mode tool context (output validators read through it)


@dataclass(frozen=True)
class CallInput:
    fields: tuple[ContextField, ...]
    user_text: tuple[Any, ...]


@dataclass(frozen=True)
class ToolCallInput:
    call: int
    name: str
    definition: ToolDefinition | None
    arguments: Any


@dataclass(frozen=True)
class ToolResult:
    call: int
    name: str
    arguments: dict[str, Any]
    argument_digest: str
    shaped: Shaped


@dataclass(frozen=True)
class RunOutput:
    output: Any  # RuntimeOutput | None
    runtime: Any


@dataclass(frozen=True)
class Hook:
    stage: Stage
    name: str
    fn: Callable[[HookContext, Any], Effect | None]


def _shapes(original: Any, modified: Any) -> bool:
    """``modified`` only removes or bounds parts of ``original``: no new key or item, a
    value never moves to another path, and user / dataset text keeps its partition
    (origin, aggregate, sample flags) so numbers cannot move into the system part."""

    if modified is None:
        return True
    if isinstance(original, Text):  # a prefix, a display limit never raised, the same partition
        return isinstance(modified, Text) and original.text.startswith(modified.text) and (
            modified.limit <= original.limit) and (
            modified.origin, modified.aggregate, modified.sample) == (original.origin, original.aggregate, original.sample)
    if isinstance(original, dict):
        return isinstance(modified, dict) and set(modified) <= set(original) and all(
            _shapes(original[key], item) for key, item in modified.items())
    if isinstance(original, (list, tuple)):  # an in-order subsequence (items dropped, never added)
        if not isinstance(modified, (list, tuple)):
            return False
        remaining = iter(original)
        return all(any(_shapes(old, new) for old in remaining) for new in modified)
    if isinstance(original, str) and type(modified) is type(original):
        return original.startswith(modified)  # bounded text (codes stay whole: equal or a prefix)
    return type(modified) is type(original) and modified == original


def _check_result(original: Shaped, modified: Shaped) -> None:
    if (not isinstance(modified, Shaped) or _RANK[modified.data_class] > _RANK[original.data_class]
            or _RANK[modified.outcome_scope] > _RANK[original.outcome_scope]
            or not set(original.source_datasets) <= set(modified.source_datasets)
            or not set(original.aggregates) <= set(modified.aggregates)
            or not _shapes(original.payload, modified.payload)
            or strip_holdout(modified.payload) != modified.payload):
        raise HookViolation("modify_result may only shape, bound or strip holdout")


class HookChain:
    def __init__(self, hooks: tuple[Hook, ...]) -> None:
        for hook in hooks:
            if hook.stage not in STAGES:
                raise HookViolation(f"unknown hook stage {hook.stage}")
        self.hooks = hooks

    def listing(self) -> list[dict[str, str]]:
        return [{"stage": hook.stage, "name": hook.name} for hook in self.hooks]

    def run(self, stage: Stage, ctx: HookContext, subject: Any) -> list[Effect]:
        effects: list[Effect] = []
        for hook in (item for item in self.hooks if item.stage == stage):
            try:
                effect = hook.fn(ctx, subject)
            except HookViolation:
                raise
            except Exception:  # noqa: BLE001 - fail closed
                logger.exception("harness hook failed", extra={"hook": hook.name, "stage": stage})
                effect = DenyOutput("hook_failed") if stage == "post_run" else (
                    Deny("hook_failed") if Deny in ALLOWED_EFFECTS[stage] else None)
                if effect is None:
                    raise HookViolation(f"{hook.name} failed in {stage}") from None
            if effect is None:
                continue
            if not isinstance(effect, ALLOWED_EFFECTS[stage]):
                raise HookViolation(f"{hook.name} may not return {type(effect).__name__} in {stage}")
            if isinstance(effect, ModifyArguments) and hook.name != VALIDATOR_HOOK:
                raise HookViolation("only the tool's validator may modify arguments")
            if isinstance(effect, ModifyResult):
                _check_result(subject.shaped, effect.shaped)
                subject = replace(subject, shaped=effect.shaped)
            if isinstance(effect, ModifyArguments):
                subject = replace(subject, arguments=effect.arguments)
            if isinstance(effect, NarrowLimits):
                effect = NarrowLimits(ctx.limits.narrow(effect.limits))
            if isinstance(effect, AddSystemNote):
                effect = AddSystemNote(effect.text[:SYSTEM_NOTE_MAX_CHARS])
            if isinstance(effect, Deny):
                effect = Deny(effect.reason, hook.name)
            effects.append(effect)
            if isinstance(effect, (Deny, DenyOutput)):
                break
        return effects


def denial(effects: list[Effect]) -> str | None:
    found = next((item for item in effects if isinstance(item, (Deny, DenyOutput))), None)
    return found.reason if found is not None else None


# --- built-in hooks --------------------------------------------------------------------------


def _capability_pre_run(ctx: HookContext, _subject: Any) -> Effect | None:
    if not ctx.principal.can_read:
        return Deny("forbidden")
    if ctx.may_propose and not ctx.principal.can_propose:
        return Deny("ml_write_required")
    return None


def _switch_check(ctx: HookContext, _subject: Any) -> Effect | None:
    from app.agents.governance.switches import effective_switches

    blocking = effective_switches(ctx.db, ctx.workspace_id).blocking(
        ai_enabled=ctx.ai_enabled, agent_key=ctx.agent_key, purpose=ctx.purpose)
    return Deny("kill_switch") if blocking else None


def _budget_pre_run(ctx: HookContext, _subject: Any) -> Effect | None:
    if ctx.limits.steps and not ctx.limits.cost_micros:
        return Deny("budget_exhausted")
    return Observe({"hold_micros": ctx.limits.cost_micros})


def _redaction_verifier(ctx: HookContext, call: CallInput) -> Effect | None:
    for item in call.fields:
        if (not item.sources or HOLDOUT_KEY.search(item.key) or _RANK.get(item.data_class, 9) > _RANK[ctx.data_class]
                or _RANK.get(item.outcome_scope, 9) > _RANK[ctx.outcome_scope]):
            return Deny("redaction_violation")
    if any(getattr(text, "untrusted_text", None) is None for text in call.user_text):
        return Deny("redaction_violation")
    return None


def _step_counter(ctx: HookContext, _call: CallInput) -> Effect | None:
    usage = ctx.usage
    if usage.get("steps", 0) >= ctx.limits.steps:
        return Deny("step_limit")
    if usage.get("tokens_in", 0) + usage.get("tokens_out", 0) >= ctx.limits.tokens:
        return Deny("token_limit")
    if time.monotonic() - ctx.started >= ctx.limits.wall_s:
        return Deny("wall_limit")
    return None


def _forbidden_op_guard(ctx: HookContext, call: ToolCallInput) -> Effect | None:
    if any(operation in str(call.name) for operation in FORBIDDEN_OPERATIONS):
        return Deny("forbidden_operation")
    if call.definition is None:
        return Deny("unknown_tool")
    if call.name not in ctx.tools:
        return Deny("tool_not_available")
    if ctx.usage.get("tool_calls", 0) >= ctx.limits.tool_calls:
        return Deny("tool_call_limit")
    return None


def _capability_pre_tool(ctx: HookContext, call: ToolCallInput) -> Effect | None:
    principal, definition = ctx.principal, call.definition
    if principal.token_scopes is not None:  # a service token acts within its scopes only
        if not set(definition.capability) <= principal.token_scopes:
            return Deny("insufficient_scope")
        if set(definition.capability) - {"read"} and not principal.ml_write_role:
            return Deny("ml_write_required")
    if definition.effect == "proposal" and not (principal.can_propose and ctx.may_propose):
        return Deny("ml_write_required")
    if definition.effect == "proposal":  # re-authorized per write call (a role can change mid-run)
        from app.services.authorization_service import can_execute_workspace_ml

        if not can_execute_workspace_ml(ctx.db, principal.user, ctx.workspace_id):
            return Deny("ml_write_required")
    return None


def _decision_point(ctx: HookContext, call: ToolCallInput) -> Effect | None:
    from app.agents.governance.decision_points import REGISTRY, answer_ceiling

    if call.definition.effect != "proposal":
        return None
    point = REGISTRY.get(call.definition.decision_point_key or "")
    if point is None or point.cap < 1:
        return Deny("decision_point_unavailable")
    # ADR 0008 §6: every lead write tool is an L1 confirm card (never shadow, never applied).
    return Observe({"level": 1, "ceiling": answer_ceiling(point.key)})


def _validator(ctx: HookContext, call: ToolCallInput) -> Effect | None:
    from app.agents.harness.validation import parse_tool_arguments

    try:
        return ModifyArguments(parse_tool_arguments(call.definition, call.arguments))
    except ToolError as exc:
        return Deny(exc.code)


def _shaper(ctx: HookContext, result: ToolResult) -> Effect | None:
    stripped = strip_holdout(result.shaped.payload)
    return None if stripped == result.shaped.payload else ModifyResult(replace(result.shaped, payload=stripped))


_CITED_ARGUMENTS = (("experiment_id", "experiment"), ("model_version_id", "model_version"),
                    ("dataset_id", "dataset_version"), ("prediction_id", "prediction"))


def _citation_builder(ctx: HookContext, result: ToolResult) -> Effect | None:
    cited = tuple(Citation(kind=kind, id=UUID(str(result.arguments[key])))
                  for key, kind in _CITED_ARGUMENTS if result.arguments.get(key))
    return AttachCitation(cited) if cited else None


def _output_validator(ctx: HookContext, run: RunOutput) -> Effect | None:
    from app.agents.harness.validation import output_reasons

    reasons = output_reasons(ctx.db, run.output, run.runtime, workspace_id=ctx.workspace_id,
                             project_id=ctx.project_id, run_id=ctx.run_id, tool_ctx=ctx.tool_ctx)
    return DenyOutput(reasons[0]) if reasons else None


def _eval_sampler(ctx: HookContext, run: RunOutput) -> Effect | None:
    # R3 samples every run (always on): proposals and model calls are flagged here.
    return Observe({"eval_sample": {"proposal_ids": [str(item) for item in ctx.proposal_ids],
                                    "llm_calls": ctx.usage.get("calls", 0)}})


BUILTIN_HOOKS: tuple[Hook, ...] = (
    Hook("pre_run", "capability", _capability_pre_run),
    Hook("pre_run", "switch_check", _switch_check),
    Hook("pre_run", "budget", _budget_pre_run),
    Hook("pre_call", "redaction_verifier", _redaction_verifier),
    Hook("pre_call", "step_counter", _step_counter),
    Hook("pre_tool", "forbidden_op_guard", _forbidden_op_guard),
    Hook("pre_tool", "capability", _capability_pre_tool),
    Hook("pre_tool", "decision_point", _decision_point),
    Hook("pre_tool", VALIDATOR_HOOK, _validator),
    Hook("post_tool", "shaper", _shaper),
    Hook("post_tool", "citation_builder", _citation_builder),
    Hook("post_run", "output_validator", _output_validator),
    Hook("post_run", "eval_sampler", _eval_sampler),
)


BUILTIN_NAMES = frozenset(hook.name for hook in BUILTIN_HOOKS)


def default_chain(extra: tuple[Hook, ...] = ()) -> HookChain:
    """Built-ins first (they cannot be reordered away), then registered extras, which may
    not reuse a built-in name (no second ``validator`` with the right to modify arguments)."""

    if any(hook.name in BUILTIN_NAMES for hook in extra):
        raise HookViolation("extra hooks may not reuse a built-in hook name")
    return HookChain(BUILTIN_HOOKS + tuple(extra))
