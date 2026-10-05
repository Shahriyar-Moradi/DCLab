"""The runtime protocol (ADR 0009 §5.1 step 6; P6.10-A).

A runtime gets a ``RuntimeSession`` — the redacted envelope, the run's limits, the
tool names it may call and two calls back into the harness — and returns a
``RuntimeOutput``. It never receives a database session, a storage client or a
provider key: every model call is ``session.complete`` (pre-call hooks, then the
gateway, then the ledger and an event) and every tool call is ``session.call_tool``
(registry, capability, validator, consumer-mode service, shaping, events; write
tools become L1 proposals and never act). One runtime instance per run; the harness
builds it from ``AgentRunSpec`` with the factory registered for ``spec.runtime``.

P6.3-A: a runtime with ``arun`` (the NOOA runtime) runs under an asyncio wall-time
timeout (``run_coroutine``); a runtime that cannot start or continue raises
``RuntimeRefused`` (the run ends ``failed`` with its code). Specialist agent classes
are registry metadata (``AgentClass``): a declared strategy, which must be ``predict``
(ADR 0008 §6: CodeAct is banned), an output schema and one generation method;
``nooa_runtime`` materializes them as NOOA ``PredictStrategy`` classes.
"""

from __future__ import annotations

import asyncio
import contextvars
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel

from app.agents.contracts import AgentRunSpec, Citation, ContextEnvelope, ContextField, RunLimits, TranscriptItem
from app.agents.gateway.contract import CompletionResponse
from app.domain.agent_records import CODE_PATTERN, KEY_PATTERN

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

    def step(self, *, method: str, strategy: str) -> None:
        """A validated strategy step (NOOA ``AfterTurn``) -> ``step_validated``. Optional:
        replay's session makes it a no-op; runtimes call it through ``getattr``."""


class Runtime(Protocol):
    name: str  # agent_runs.runtime
    version: str
    output_schema: type[BaseModel] | None

    def run(self, session: RuntimeSession) -> RuntimeOutput:
        ...


RuntimeFactory = Callable[[AgentRunSpec], Runtime]
_CODE = re.compile(CODE_PATTERN)
_KEY = re.compile(KEY_PATTERN)
_T = TypeVar("_T")


class RuntimeRefused(Exception):
    """A runtime refuses to start or continue: a missing optional dependency, a forbidden
    environment, an unknown agent class, a strategy or adapter violation. The harness
    ends the run ``failed`` with ``code`` (fail closed), never as an internal error."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code if _CODE.fullmatch(code) else "runtime_refused"


class RuntimeWallTimeout(Exception):
    """The run's wall time ran out while an async runtime was running."""


def run_coroutine(make: Callable[[], Awaitable[_T]], *, timeout_s: float | None = None) -> _T:
    """Run an async runtime to completion on a fresh event loop, bounded by ``timeout_s``.

    The runtime's model and tool calls are synchronous (they hold the caller's db
    session), so the timeout fires at the runtime's next ``await``; a model call itself
    is bounded by the gateway's own timeout (the remaining wall time). Raises
    ``RuntimeWallTimeout`` only when this timeout expired (a ``TimeoutError`` raised
    inside the runtime stays what it is). When a loop already runs in this thread (an
    async caller), the coroutine runs on a helper thread while the caller waits, so the
    db session is never used by two threads at once."""

    async def bounded() -> _T:
        scope = asyncio.timeout(timeout_s)
        try:
            async with scope:
                result = await make()
        except TimeoutError:
            if scope.expired():
                raise RuntimeWallTimeout from None
            raise
        if scope.expired():  # the runtime swallowed the cancellation: still out of time
            raise RuntimeWallTimeout
        return result

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(bounded())
    context = contextvars.copy_context()  # request-scoped context (token checks) follows the run
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="dclab-agent-runtime") as pool:
        return pool.submit(context.run, lambda: asyncio.run(bounded())).result()


# --- specialist agent classes (registry metadata; ADR 0008 §6, ADR 0009 §1) -----------------------


@dataclass(frozen=True)
class AgentClass:
    """A specialist agent class as data. P6.4-A registers the real ones (critic,
    investigator, planner, hypothesis). ``task`` is the generation method's docstring
    (NOOA's task text, which never leaves the process: the provider prompt is the
    agent's prompt release, built by the gateway from the redacted envelope);
    ``to_output`` maps the validated model output to the run's output (citations, a
    typed ``ProposalDraft``); ``validate_output`` is the deterministic validator the
    harness's output hook runs (``-> [codes]``)."""

    agent_key: str
    output_schema: type[BaseModel]
    task: str
    method: str = "run"
    strategy: str = "predict"  # declared; anything else is refused at registration
    max_output_tokens: int = 2000
    to_output: Callable[[BaseModel], RuntimeOutput] | None = None
    validate_output: Callable[[RuntimeOutput], Sequence[str]] | None = None


AGENT_CLASSES: dict[str, AgentClass] = {}


def declared_strategy_violations(item: AgentClass) -> list[str]:
    """Why ``item`` cannot be a specialist (empty = Predict-only by declaration)."""

    reasons = []
    if item.strategy != "predict":
        reasons.append("strategy_not_predict")
    if not _KEY.fullmatch(item.agent_key):
        reasons.append("agent_key_invalid")
    if not (item.method.isidentifier() and _CODE.fullmatch(item.method)):
        reasons.append("method_invalid")  # public snake_case, never a dunder or private name
    if not (isinstance(item.output_schema, type) and issubclass(item.output_schema, BaseModel)):
        reasons.append("output_schema_invalid")
    if not item.task.strip():
        reasons.append("task_missing")
    if not 1 <= item.max_output_tokens <= 32_000:
        reasons.append("max_output_tokens_invalid")
    return reasons


def register_agent_class(item: AgentClass) -> AgentClass:
    reasons = declared_strategy_violations(item)
    if reasons:
        raise ValueError(f"agent class {item.agent_key!r} refused: {', '.join(reasons)}")
    current = AGENT_CLASSES.get(item.agent_key)
    if current is not None and current is not item:
        raise ValueError(f"agent class {item.agent_key!r} is already registered")
    AGENT_CLASSES[item.agent_key] = item
    return item


def unregister_agent_class(agent_key: str) -> None:
    AGENT_CLASSES.pop(agent_key, None)


def agent_class(agent_key: str) -> AgentClass:
    item = AGENT_CLASSES.get(agent_key)
    if item is None or declared_strategy_violations(item):
        raise RuntimeRefused("agent_class_unknown", agent_key)
    return item


def finish(item: AgentClass, output: BaseModel) -> RuntimeOutput:
    return item.to_output(output) if item.to_output is not None else RuntimeOutput(output=output)
