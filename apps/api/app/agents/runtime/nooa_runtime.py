"""The NOOA Predict runtime (ADR 0008 §6; ADR 0009 §1 rule b, §5.1, §5.4, §9; P6.3-A).

The only DCLab module that imports ``nooa`` (``nooa==0.0.10``, the optional ``agents``
dependency), and only lazily: without it every run of this runtime ends ``failed`` with
``nooa_unavailable`` and nothing else changes. Specialist classes are registry metadata
(``base.AgentClass``); this module materializes each as a NOOA ``Agent`` subclass with
exactly one generation method under ``PredictStrategy`` (one structured call, no code
execution; ``max_retries=1`` because the gateway already retries an invalid specialist
output once). CodeAct, the sandbox, NOOA's tools, MCP client and CLI are never imported
here (CI rule b).

Fail closed, at the start of every run: the agent's LLM object must be this run's
``GatewayLLM`` and every generation method of its class must resolve to exactly
``PredictStrategy`` with no per-method LLM override; a provider-key variable outside
DCLab's ``DCLAB_`` namespace refuses the run (``providers.ambient_provider_keys``:
LiteLLM / NOOA's model registry would read it).

``GatewayLLM`` is NOOA's LLM object and the only way out: each ``acall`` is one
``session.complete`` (pre-call hooks, gateway, ledger, ``llm_call_*`` events). NOOA's
messages are dropped — the provider prompt is the agent's prompt release and the
redacted envelope, built by the gateway — so no provider SDK and no LiteLLM call runs.
Network side effects of the library are switched off before use: LiteLLM's remote
cost-map fetch at import (``LITELLM_LOCAL_MODEL_COST_MAP``) and NOOA's auto tracing,
which probes the ``:5001`` viewer and would export OTLP spans. No OpenTelemetry
exporter is installed (a process-global tracer would be shared across runs and
tenants); NOOA's per-agent events map to the recorder instead (§5.4): a validated
``PREDICT`` turn -> ``step_validated``; model calls are the harness's ``llm_call_*``;
a NOOA error ends the run through the harness (``run_failed``). Specialists get no
tools. The harness runs ``arun`` under the run's wall time (asyncio timeout).
"""

from __future__ import annotations

import inspect
import logging
import os
import re
import types
from typing import Any

from pydantic import BaseModel

from app.agents.contracts import AgentRunSpec
from app.agents.gateway.providers import ambient_provider_keys
from app.agents.runtime.base import AgentClass, RuntimeOutput, RuntimeRefused, agent_class, finish, run_coroutine

logger = logging.getLogger("dclab.agents.nooa")

NOOA_PIN = "0.0.10"
VERSION = f"nooa=={NOOA_PIN}"
PREDICT = "PREDICT"  # NOOA's PredictStrategy.name, as carried by its turn events
_LOADED: types.SimpleNamespace | None = None
_TYPES: dict[AgentClass, type] = {}


def load() -> types.SimpleNamespace:
    """Import NOOA once (pinned), with its network side effects switched off."""

    global _LOADED
    if _LOADED is not None:
        return _LOADED
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"  # before LiteLLM (a NOOA import) loads
    try:
        import nooa
        import nooa.agent as nooa_agent
        from nooa.config import PredictConfig
    except Exception:  # noqa: BLE001 - missing or broken optional dependency: fail closed
        raise RuntimeRefused("nooa_unavailable") from None
    if getattr(nooa, "__version__", None) != NOOA_PIN or not hasattr(nooa_agent, "_auto_tracing_attempted"):
        raise RuntimeRefused("nooa_version_mismatch", str(getattr(nooa, "__version__", None)))
    nooa_agent._auto_tracing_attempted = True  # no :5001 probe, no automatic OTLP exporter
    logging.getLogger("nooa").setLevel(logging.WARNING)  # its DEBUG lines carry raw model output
    _LOADED = types.SimpleNamespace(Agent=nooa.Agent, PredictStrategy=nooa.PredictStrategy,
                                    PredictConfig=PredictConfig, strategy=nooa.strategy,
                                    LLMResponse=nooa.LLMResponse)
    return _LOADED


def available() -> bool:
    try:
        load()
    except RuntimeRefused:
        return False
    return True


def _require_clean_environment() -> None:
    found = ambient_provider_keys()
    if found:
        raise RuntimeRefused("ambient_provider_key", f"{len(found)} variable(s)")


class GatewayRefusal(Exception):  # noqa: N818 - not a ValueError/TypeError: Predict never retries it
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GatewayLLM:
    """NOOA's LLM object for one run: ``acall`` -> ``session.complete``. Never a provider."""

    model = "dclab-gateway"  # NOOA reads it for metrics and cache keys only
    context_window = 1_000_000

    def __init__(self, session: Any, item: AgentClass) -> None:
        self.config = {"context_window": self.context_window}
        self._session, self._item = session, item
        self.max_output_tokens = max(1, min(item.max_output_tokens, session.limits.tokens))
        self.calls = 0
        self.refusal: str | None = None
        self.violation: str | None = None
        self.escaped: BaseException | None = None  # a harness error inside a call: re-raised as is

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)  # NOOA's own prompt never leaves the process: no tokenizer

    async def acall(self, messages: list[dict[str, Any]], tools: Any = None, output_model: Any = None,
                    **_ignored: Any) -> Any:
        if self.violation is None and tools:
            self.violation = "tools_not_allowed"  # specialist runs get no tool calls
        if self.violation is None and output_model is not self._item.output_schema:
            self.violation = "schema_mismatch"
        if self.violation is not None:
            raise GatewayRefusal(self.violation)
        self.calls += 1
        try:
            response = self._session.complete(output_schema=self._item.output_schema,
                                              max_output_tokens=self.max_output_tokens)
        except BaseException as exc:  # never let NOOA turn a harness error into a model error
            self.escaped = exc
            raise
        if not response.ok or response.output is None:
            self.refusal = response.refusal.code if response.refusal else "provider_error"
            raise GatewayRefusal(self.refusal)
        return load().LLMResponse(
            raw_response=None, content=response.output, tool_calls=[], finish_reason="stop",
            assistant_message={"role": "assistant", "content": response.output.model_dump_json()},
            usage={"prompt_tokens": response.usage.input_tokens, "completion_tokens": response.usage.output_tokens})

    def call(self, *_args: Any, **_kwargs: Any) -> Any:
        self.violation = self.violation or "sync_call"
        raise GatewayRefusal(self.violation)

    def close(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


# --- materialized classes and the Predict-only check ---------------------------------------------


def generation_methods(cls: type) -> dict[str, Any]:
    """Every NOOA generation method reachable on ``cls`` (an LLM writes the result)."""

    found: dict[str, Any] = {}
    for klass in cls.__mro__:
        for name, value in vars(klass).items():
            func = getattr(value, "__func__", value)
            if name not in found and getattr(func, "_needs_generation", False):
                found[name] = func
    return found


def predict_violations(cls: type, item: AgentClass | None = None) -> list[tuple[str, str]]:
    """``(method, code)`` for each generation method that does not resolve to exactly
    ``PredictStrategy`` (``None`` = NOOA's default, CodeAct) or carries its own LLM."""

    nooa = load()
    methods = generation_methods(cls)
    found = []
    for name, func in sorted(methods.items()):
        # The class attribute is NOOA's wrapper; its actor reads the markers from the wrapped
        # function: both copies must say Predict, with no LLM of their own.
        copies = (func, inspect.unwrap(func))
        if any(type(getattr(item, "_plan_strategy", None)) is not nooa.PredictStrategy for item in copies):
            found.append((name, "strategy_not_predict"))
        if any(getattr(item, "_plan_llm", None) is not None for item in copies):
            found.append((name, "method_llm_override"))
    if item is not None and set(methods) != {item.method}:
        found.append((item.method, "generation_methods_mismatch"))
    return found


def agent_type(item: AgentClass) -> type:
    """The NOOA class of a registered specialist (built once per class, checked when built)."""

    cached = _TYPES.get(item)
    if cached is not None:
        return cached
    nooa = load()

    async def generate(self):  # noqa: ANN001, ANN202 - the ellipsis body marks a generation method
        ...

    name = "".join(part.title() for part in re.split(r"[^a-z0-9]+", item.agent_key) if part) + "Agent"
    generate.__name__ = item.method
    generate.__qualname__ = f"{name}.{item.method}"
    generate.__doc__ = item.task
    generate.__annotations__ = {"return": item.output_schema}
    method = nooa.strategy(nooa.PredictStrategy(config=nooa.PredictConfig(max_retries=1)))(generate)
    cls = types.new_class(name, (nooa.Agent,), {}, lambda ns: ns.update({
        "__module__": __name__, "__doc__": f"DCLab specialist {item.agent_key} (Predict only).",
        item.method: method}))
    problems = predict_violations(cls, item)
    if problems:
        raise RuntimeRefused(problems[0][1], f"{item.agent_key}.{problems[0][0]}")
    _TYPES[item] = cls
    return cls


def assert_ready(agent: Any, llm: GatewayLLM, item: AgentClass) -> None:
    """ADR 0009 §5.1: the LLM object is this run's gateway adapter and the class is
    Predict-only; otherwise the run is refused before any model call."""

    if type(llm) is not GatewayLLM or agent.llm is not llm or getattr(agent, "_llm", None) is not llm:
        raise RuntimeRefused("llm_not_gateway")
    problems = predict_violations(type(agent), item)
    if problems:
        raise RuntimeRefused(problems[0][1], problems[0][0])


class NooaPredictRuntime:
    name = "nooa_predict"
    version = VERSION

    def __init__(self, item: AgentClass, agent_cls: type) -> None:
        self.agent_class, self._type = item, agent_cls
        self.output_schema: type[BaseModel] = item.output_schema
        self.validate_output = item.validate_output
        self._used = False

    def run(self, session: Any) -> RuntimeOutput:
        return run_coroutine(lambda: self.arun(session))

    async def arun(self, session: Any) -> RuntimeOutput:
        if self._used:
            raise RuntimeRefused("runtime_reused")  # one instance (and one NOOA agent) per run
        self._used = True
        _require_clean_environment()
        llm = GatewayLLM(session, self.agent_class)
        agent = self._type(llm)
        assert_ready(agent, llm, self.agent_class)
        step = getattr(session, "step", None)

        def on_turn_start(event: Any) -> None:
            if getattr(event, "strategy", None) != PREDICT:
                llm.violation = llm.violation or "strategy_not_predict"  # the next model call refuses

        def on_turn_end(event: Any) -> None:
            if event.is_final and event.success and event.strategy == PREDICT and callable(step):
                try:
                    step(method=event.method_name, strategy="predict")
                except BaseException as exc:  # the recorder failed: surfaced after NOOA returns
                    llm.escaped = llm.escaped or exc

        events = agent.event_manager
        unsubscribe = (events.on("BeforeTurn", on_turn_start), events.on("AfterTurn", on_turn_end))
        try:
            result = await getattr(agent, self.agent_class.method)()
        except Exception as exc:  # noqa: BLE001 - NOOA errors end the run through the harness
            if llm.escaped is not None:
                raise llm.escaped from None
            if llm.violation:
                raise RuntimeRefused(llm.violation) from None
            if llm.refusal:
                return RuntimeOutput(meta={"refusal": llm.refusal})
            logger.warning("nooa generation failed", extra={"agent_key": self.agent_class.agent_key,
                                                            "error_type": type(exc).__name__})
            return RuntimeOutput(meta={"error": "generation_failed"})
        finally:
            for off in unsubscribe:
                off()
        if llm.escaped is not None:
            raise llm.escaped
        if llm.violation:
            raise RuntimeRefused(llm.violation)
        if not isinstance(result, self.output_schema):
            raise RuntimeRefused("schema_mismatch")
        return finish(self.agent_class, result)


def factory(spec: AgentRunSpec) -> NooaPredictRuntime:
    item = agent_class(spec.agent_key)
    load()
    _require_clean_environment()
    return NooaPredictRuntime(item, agent_type(item))
