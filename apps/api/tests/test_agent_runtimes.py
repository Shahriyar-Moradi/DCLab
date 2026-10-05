"""P6.3-A: the fake and NOOA Predict runtimes on the harness (ADR 0008 §6; ADR 0009 §1 rules
b-c, §5.1, §5.4, §9).

Runs without the optional ``agents`` dependency: the registry / class-inspection test checks
declared strategies (and that ``nooa_predict`` fails closed) when ``nooa`` is missing, and
NOOA's own strategy resolution when it is installed. NOOA-only tests ``importorskip``. No
network: model calls go through the gateway's fake provider and a socket guard records any
connection attempt.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import socket
import subprocess
import sys
import textwrap
import time
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.agents.contracts import RunLimits
from app.agents.gateway.providers import ProviderError, ambient_provider_keys
from app.agents.harness import service as svc
from app.agents.harness.replay import replay
from app.agents.harness.service import AgentService
from app.agents.runtime import fake_runtime, nooa_runtime
from app.agents.runtime.base import (
    AGENT_CLASSES,
    AgentClass,
    ProposalDraft,
    RuntimeOutput,
    RuntimeRefused,
    RuntimeWallTimeout,
    register_agent_class,
    run_coroutine,
    unregister_agent_class,
)
from app.db.models import AgentProposal, AgentRun
from test_agent_harness import OK, HarnessStep, _events, _types, hz  # noqa: F401  (fixture)
from test_agent_persistence import agent_run, agents  # noqa: F401  (fixtures)
from test_decision_record_service import _force_lock, g, setup  # noqa: F401  (fixtures)

CASES = Path(__file__).resolve().parent / "fixtures" / "fake_runtime"
TASK = "Decide whether the experiment should be kept or revised."


def _to_output(out: HarnessStep) -> RuntimeOutput:
    return RuntimeOutput(output=out, proposal=ProposalDraft(
        proposal_type="ExperimentReviewProposal", decision_point_key="experiment.review",
        payload={"verdict": out.verdict}, rationale="fixture specialist"))


def _validate(output: RuntimeOutput) -> list[str]:
    return ["low_confidence"] if output.output is not None and output.output.confidence < 0.1 else []


@pytest.fixture
def specialist():
    """The test-only specialist (real classes are P6.4-A): the harness fixture prompt release."""

    item = register_agent_class(AgentClass(agent_key="harness_fixture", output_schema=HarnessStep, task=TASK,
                                           method="review", max_output_tokens=200, to_output=_to_output,
                                           validate_output=_validate))
    yield item
    unregister_agent_class("harness_fixture")


@pytest.fixture
def no_network(monkeypatch):
    attempts: list = []

    def guard(self, address):
        attempts.append(address)
        raise OSError("network disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", guard)
    return attempts


def _service(hz, runtimes=None):  # noqa: F811
    return AgentService(gateway=hz.gateway, settings=lambda: hz.settings, runtimes=runtimes)


def _spec(hz, **overrides):  # noqa: F811
    values = dict(kind="specialist", tool_surface=None, runtime="fake", runtime_version=fake_runtime.VERSION,
                  decision_point_key="experiment.review")
    values.update(overrides)
    return hz.spec(**values)


# --- registry metadata and the Predict-only rule (with and without nooa) --------------------------


def test_registry_refuses_anything_but_a_predict_declaration():
    base = dict(agent_key="planted_agent", output_schema=HarnessStep, task=TASK)
    for bad in (dict(strategy="codeact"), dict(strategy="react"), dict(method="_private"), dict(method="__call__"),
                dict(method="Review"), dict(task="  "), dict(agent_key="Bad Key"), dict(output_schema=dict),
                dict(max_output_tokens=0)):
        with pytest.raises(ValueError, match="refused"):
            register_agent_class(AgentClass(**{**base, **bad}))
    assert "planted_agent" not in AGENT_CLASSES
    item = register_agent_class(AgentClass(**base))
    try:
        assert register_agent_class(item) is item  # idempotent for the same object
        with pytest.raises(ValueError, match="already registered"):
            register_agent_class(AgentClass(**base))
    finally:
        unregister_agent_class("planted_agent")


def test_every_registered_agent_class_resolves_to_predict_only(specialist):
    """CI test (ADR 0008 §6): fails if any registered class's generation method resolves to
    a non-Predict strategy. Never a silent pass: without nooa the declarations are checked
    and ``nooa_predict`` must refuse; with nooa each class is materialized and NOOA's own
    per-method strategy resolution is inspected."""

    import app.agents.harness.service  # noqa: F401  (registers the built-in runtimes and classes)

    classes = list(AGENT_CLASSES.values())
    assert specialist in classes
    for item in classes:
        assert item.strategy == "predict", item.agent_key
    if nooa_runtime.available():
        for item in classes:
            cls = nooa_runtime.agent_type(item)
            assert nooa_runtime.predict_violations(cls, item) == [], item.agent_key
            assert set(nooa_runtime.generation_methods(cls)) == {item.method}
    else:
        spec = SimpleNamespace(agent_key=specialist.agent_key)
        with pytest.raises(RuntimeRefused) as refused:
            nooa_runtime.factory(spec)
        assert refused.value.code == "nooa_unavailable"


def test_nooa_unavailable_fails_closed(monkeypatch, specialist):
    monkeypatch.setattr(nooa_runtime, "_LOADED", None)
    monkeypatch.setitem(sys.modules, "nooa", None)  # import nooa -> ImportError, installed or not
    assert nooa_runtime.available() is False
    with pytest.raises(RuntimeRefused) as refused:
        nooa_runtime.factory(SimpleNamespace(agent_key=specialist.agent_key))
    assert refused.value.code == "nooa_unavailable"
    with pytest.raises(RuntimeRefused) as unknown:
        nooa_runtime.factory(SimpleNamespace(agent_key="no_such_agent"))
    assert unknown.value.code == "agent_class_unknown"


def test_runtimes_are_registered_inside_the_harness():
    assert svc.RUNTIMES["fake"] is fake_runtime.factory
    assert svc.RUNTIMES["nooa_predict"] is nooa_runtime.factory


def test_ambient_provider_keys_are_names_only():
    environ = {"OPENAI_API_KEY": "sk-secret", "nvidia_api_key": "x", "DCLAB_OPENAI_API_KEY": "kept",
               "HOME": "/home", "API_KEY_ROTATION": "1", "ANTHROPIC_AUTH_TOKEN": "t", "AZURE_OPENAI_AD_TOKEN": "t",
               "HF_TOKEN": "t", "GOOGLE_APPLICATION_CREDENTIALS": "/k.json", "AWS_ACCESS_KEY_ID": "storage"}
    assert ambient_provider_keys(environ) == ("ANTHROPIC_AUTH_TOKEN", "AZURE_OPENAI_AD_TOKEN",
                                              "GOOGLE_APPLICATION_CREDENTIALS", "HF_TOKEN", "OPENAI_API_KEY",
                                              "nvidia_api_key")
    assert "sk-secret" not in repr(ambient_provider_keys(environ))


# --- the asyncio wall-time runner -------------------------------------------------------------------


def test_run_coroutine_times_out_and_works_under_a_running_loop():
    async def slow():
        await asyncio.sleep(5)

    started = time.monotonic()
    with pytest.raises(RuntimeWallTimeout):
        run_coroutine(slow, timeout_s=0.05)
    assert time.monotonic() - started < 2

    async def inner_timeout():
        raise TimeoutError("a provider timeout, not the wall")

    with pytest.raises(TimeoutError, match="provider timeout"):
        run_coroutine(inner_timeout, timeout_s=5)

    async def answer():
        await asyncio.sleep(0)
        return 42

    async def caller():  # an async caller: the runtime runs on a helper thread's loop
        return run_coroutine(answer, timeout_s=5)

    assert run_coroutine(answer) == 42 and asyncio.run(caller()) == 42

    async def deaf():  # swallows the cancellation and answers late
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            return "late"

    with pytest.raises(RuntimeWallTimeout):
        run_coroutine(deaf, timeout_s=0.05)
    marker = contextvars.ContextVar("marker", default="unset")

    async def read_marker():
        return marker.get()

    async def caller_with_context():
        marker.set("request")
        return run_coroutine(read_marker, timeout_s=5)

    assert asyncio.run(caller_with_context()) == "request"  # context follows onto the helper thread


# --- the fake runtime --------------------------------------------------------------------------------


def test_fake_case_loader_refuses_bad_names_paths_and_mismatches(tmp_path):
    for agent_key, case in (("harness_fixture", "missing"), ("harness_fixture", "../default"),
                            ("../harness_fixture", "default"), ("harness_fixture", "Default")):
        with pytest.raises(RuntimeRefused) as missing:
            fake_runtime.load_case(agent_key, case, CASES)
        assert missing.value.code == "fake_case_missing"
    (tmp_path / "harness_fixture").mkdir()
    (tmp_path / "harness_fixture" / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "harness_fixture" / "extra.json").write_text(
        '{"schema_version": 1, "agent_key": "harness_fixture", "case": "extra", "steps": [], "shell": "rm"}',
        encoding="utf-8")
    for case, root in (("wrong_key", CASES), ("broken", tmp_path), ("extra", tmp_path)):
        with pytest.raises(RuntimeRefused) as invalid:
            fake_runtime.load_case("harness_fixture", case, root)
        assert invalid.value.code == "fake_case_invalid", case
    assert fake_runtime.load_case("harness_fixture", "default", CASES).steps[0].kind == "llm"


def test_fake_runtime_is_refused_outside_development(monkeypatch, specialist):
    spec = SimpleNamespace(agent_key=specialist.agent_key)
    runtime = fake_runtime.factory(spec, cases_root=CASES)
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(dclab_env="production"))
    for call in (lambda: fake_runtime.factory(spec, cases_root=CASES), lambda: runtime.run(SimpleNamespace())):
        with pytest.raises(RuntimeRefused) as refused:
            call()
        assert refused.value.code == "fake_runtime_forbidden"


def test_fake_runtime_default_case_end_to_end_with_replay(hz, specialist, monkeypatch):  # noqa: F811
    g, db = hz.g, hz.db
    monkeypatch.setattr(fake_runtime, "DEFAULT_CASES_ROOT", CASES)  # the registered factory, case "default"
    service = _service(hz)
    result = service.run(db, _spec(hz))
    assert (result.status, result.error_code, len(result.proposal_ids)) == ("completed", None, 1)
    assert _types(db, result.run_id) == [
        "run_started", "context_built", "budget_reserved", "llm_call_started", "llm_call_finished",
        "proposal_created", "budget_settled", "run_finished"]
    run = db.get(AgentRun, result.run_id)
    assert (run.runtime, run.usage["calls"], run.limits["tool_calls"]) == ("fake", 1, 0)
    [proposal] = db.scalars(select(AgentProposal).where(AgentProposal.run_id == run.id))
    assert (proposal.proposal_type, proposal.payload, proposal.status) == (
        "ExperimentReviewProposal", {"verdict": "keep"}, "shadow")
    assert len(hz.fake.calls) == 1
    same = replay(db, workspace_id=g.ws, run_id=result.run_id, actor=g.actor, service=service)
    assert same.equal and same.mismatches == ()


def test_fake_runtime_scripted_cases_and_specialist_tool_denial(hz, specialist):  # noqa: F811
    g, db = hz.g, hz.db
    service = _service(hz, {"fake": partial(fake_runtime.factory, case="scripted", cases_root=CASES)})
    scripted = service.run(db, _spec(hz))
    assert scripted.status == "completed"
    [proposal] = db.scalars(select(AgentProposal).where(AgentProposal.run_id == scripted.run_id))
    assert proposal.payload == {"verdict": "revise"}  # the scripted proposal, not the model's "keep"
    assert hz.fake.calls[-1].max_output_tokens == 300
    probe = _service(hz, {"fake": partial(fake_runtime.factory, case="probe_tool", cases_root=CASES)}).run(
        db, _spec(hz, subject_id=g.exp[1]))
    assert probe.status == "completed"
    denied = [e for e in _events(db, probe.run_id) if e.type == "tool_call_denied"]
    assert len(denied) == 1 and denied[0].payload["code"] == "tool_not_available"  # specialists get no tools


def test_runtime_refusals_end_failed_with_their_code(hz, specialist, monkeypatch):  # noqa: F811
    g, db = hz.g, hz.db
    cases = (
        ({"fake": partial(fake_runtime.factory, case="missing", cases_root=CASES)}, {}, "fake_case_missing"),
        (None, {"agent_key": "gateway_fixture"}, "agent_class_unknown"),
    )
    for index, (runtimes, overrides, code) in enumerate(cases):
        result = _service(hz, runtimes).run(db, _spec(hz, subject_id=g.exp[index], **overrides))
        assert (result.status, result.error_code) == ("failed", code)
        run = db.get(AgentRun, result.run_id)
        assert run.budget_released_at is not None and _types(db, result.run_id)[-1] == "run_failed"
    assert hz.fake.calls == []


# --- the harness's asyncio wall-time timeout and step events -----------------------------------------


class _AsyncRuntime:
    name, version, output_schema = "fake", "async==1", HarnessStep

    def __init__(self, sleep_s=0.0, steps=()):
        self.sleep_s, self.steps = sleep_s, steps

    def run(self, session):  # never used by the harness when arun exists
        raise AssertionError("the harness runs arun")

    async def arun(self, session):
        response = session.complete(output_schema=HarnessStep, max_output_tokens=200)
        for method, strategy in self.steps:
            session.step(method=method, strategy=strategy)
        await asyncio.sleep(self.sleep_s)
        return RuntimeOutput(output=response.output)


def test_async_runtime_runs_under_the_wall_time_timeout(hz):  # noqa: F811
    g, db = hz.g, hz.db
    limits = RunLimits(steps=2, tokens=24000, wall_s=1, cost_micros=100_000, tool_calls=0)
    started = time.monotonic()
    slow = _service(hz, {"fake": lambda _spec: _AsyncRuntime(sleep_s=5)}).run(db, _spec(hz, limits=limits))
    assert time.monotonic() - started < 4
    assert (slow.status, slow.error_code) == ("timed_out", "wall_limit")
    run = db.get(AgentRun, slow.run_id)
    assert run.budget_released_at is not None and run.usage["calls"] == 1
    assert _types(db, slow.run_id)[-2:] == ["budget_settled", "run_failed"]
    fast = _service(hz, {"fake": lambda _spec: _AsyncRuntime(steps=(("review", "predict"), ("Bad Name", "x y")))}).run(
        db, _spec(hz, subject_id=g.exp[1], limits=limits))
    assert fast.status == "completed"
    steps = [e.payload for e in _events(db, fast.run_id) if e.type == "step_validated"]
    assert steps == [{"call": 1, "method": "review", "strategy": "predict"},
                     {"call": 1, "method": "invalid_method", "strategy": "invalid_strategy"}]


def test_a_hook_violation_inside_an_async_runtime_fails_the_run(hz):  # noqa: F811
    from app.agents.harness import hooks as h

    planted = h.default_chain((h.Hook("pre_call", "planted", lambda ctx, _call: h.NarrowLimits(ctx.limits)),))
    service = AgentService(gateway=hz.gateway, settings=lambda: hz.settings, hooks=planted,
                           runtimes={"fake": lambda _spec: _AsyncRuntime()})
    result = service.run(hz.db, _spec(hz))
    assert (result.status, result.error_code) == ("failed", "hook_violation") and hz.fake.calls == []


# --- NOOA (importorskip unless DCLAB_REQUIRE_NOOA=1: needs the optional agents dependency) -------------


@pytest.fixture
def nooa(monkeypatch):
    if os.environ.get("DCLAB_REQUIRE_NOOA") != "1":  # a job with the agents dependency sets it: no skip
        pytest.importorskip("nooa")
    for name in ambient_provider_keys():
        monkeypatch.delenv(name)
    return nooa_runtime.load()


def test_nooa_load_switches_off_network_side_effects(nooa):
    import nooa.agent as nooa_agent

    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
    assert nooa_agent._auto_tracing_attempted is True
    assert "nooa.cli" not in sys.modules and "nooa.viewer" not in sys.modules


def test_nooa_import_and_agent_start_make_no_network_call(nooa):
    """In a fresh interpreter (nothing imported yet): LiteLLM's import-time cost-map fetch and
    NOOA's :5001 viewer probe on the first agent are both switched off by ``load``."""

    code = textwrap.dedent("""
        import socket
        from types import SimpleNamespace
        attempts = []
        def guard(self, address):
            attempts.append(address)
            raise OSError("blocked")
        socket.socket.connect = guard
        from pydantic import BaseModel
        from app.agents.runtime import nooa_runtime
        from app.agents.runtime.base import AgentClass
        class Out(BaseModel):
            verdict: str
        item = AgentClass(agent_key="probe_agent", output_schema=Out, task="Probe.")
        session = SimpleNamespace(limits=SimpleNamespace(tokens=100))
        llm = nooa_runtime.GatewayLLM(session, item)
        nooa_runtime.assert_ready(nooa_runtime.agent_type(item)(llm), llm, item)
        print("attempts", len(attempts))
    """)
    env = {key: value for key, value in os.environ.items() if key != "LITELLM_LOCAL_MODEL_COST_MAP"}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().splitlines()[-1] == "attempts 0"


def test_nooa_predict_through_the_gateway_only(hz, specialist, nooa, no_network):  # noqa: F811
    g, db = hz.g, hz.db
    service = _service(hz)  # the registered nooa_predict factory
    result = service.run(db, _spec(hz, runtime="nooa_predict", runtime_version=nooa_runtime.VERSION))
    assert (result.status, result.error_code, len(result.proposal_ids)) == ("completed", None, 1)
    assert _types(db, result.run_id) == [
        "run_started", "context_built", "budget_reserved", "llm_call_started", "llm_call_finished",
        "step_validated", "proposal_created", "budget_settled", "run_finished"]
    step = next(e for e in _events(db, result.run_id) if e.type == "step_validated")
    assert step.payload == {"call": 1, "method": "review", "strategy": "predict"}
    # One model call, made by the gateway with the release prompt and the redacted envelope only:
    # NOOA's own prompt (docstring, agent doc) never reaches the provider.
    assert len(hz.fake.calls) == 1 and TASK not in hz.fake.calls[0].instructions + hz.fake.calls[0].input_json
    assert no_network == []
    [proposal] = db.scalars(select(AgentProposal).where(AgentProposal.run_id == result.run_id))
    assert proposal.payload == {"verdict": "keep"}
    same = replay(db, workspace_id=g.ws, run_id=result.run_id, actor=g.actor, service=service)
    assert same.equal and same.mismatches == () and len(hz.fake.calls) == 1


def test_nooa_gateway_refusal_is_one_call_and_never_retried(hz, specialist, nooa):  # noqa: F811
    db = hz.db
    hz.fake._handler = lambda _call: ProviderError("client_error", "bad request")
    result = _service(hz).run(db, _spec(hz, runtime="nooa_predict", runtime_version=nooa_runtime.VERSION))
    assert (result.status, result.error_code) == ("rejected_by_validator", "no_output")
    assert len(hz.fake.calls) == 1 and db.get(AgentRun, result.run_id).usage["calls"] == 1
    final = _events(db, result.run_id)[-1]
    assert final.type == "run_failed" and final.payload["runtime_refusal"]


def test_nooa_runtime_refuses_ambient_provider_keys(specialist, nooa, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-litellm")
    with pytest.raises(RuntimeRefused) as refused:
        nooa_runtime.factory(SimpleNamespace(agent_key=specialist.agent_key))
    assert refused.value.code == "ambient_provider_key" and "sk-not" not in str(refused.value)


def test_nooa_class_inspection_catches_planted_violations(specialist, nooa):
    import nooa as lib
    from nooa.strategies import codeact

    class DefaultStrategy(lib.Agent):  # no @strategy: NOOA's default (CodeAct)
        async def review(self) -> HarnessStep:
            """Review."""
            ...

    class ExplicitCodeAct(lib.Agent):
        @lib.strategy(codeact.CodeActStrategy())
        async def review(self) -> HarnessStep:
            """Review."""
            ...

    class OwnLlm(lib.Agent):
        @lib.strategy(lib.PredictStrategy(), llm=lambda agent: agent.llm)
        async def review(self) -> HarnessStep:
            """Review."""
            ...

    class SubPredict(lib.PredictStrategy):  # a subclass may override execute: not Predict
        pass

    class Disguised(lib.Agent):
        @lib.strategy(SubPredict())
        async def review(self) -> HarnessStep:
            """Review."""
            ...

    class ExtraMethod(nooa_runtime.agent_type(specialist)):
        @lib.strategy(lib.PredictStrategy())
        async def other(self) -> HarnessStep:
            """Other."""
            ...

    expected = {DefaultStrategy: ("review", "strategy_not_predict"), ExplicitCodeAct: ("review", "strategy_not_predict"),
                OwnLlm: ("review", "method_llm_override"), Disguised: ("review", "strategy_not_predict"),
                ExtraMethod: ("review", "generation_methods_mismatch")}
    for cls, violation in expected.items():
        assert violation in nooa_runtime.predict_violations(cls, specialist), cls.__name__
    session = SimpleNamespace(limits=RunLimits(steps=2, tokens=24000, wall_s=60, cost_micros=1, tool_calls=0))
    llm = nooa_runtime.GatewayLLM(session, specialist)
    with pytest.raises(RuntimeRefused) as wrong_llm:
        nooa_runtime.assert_ready(nooa_runtime.agent_type(specialist)(llm=object()), llm, specialist)
    assert wrong_llm.value.code == "llm_not_gateway"
    with pytest.raises(RuntimeRefused) as codeact_class:
        nooa_runtime.assert_ready(DefaultStrategy(llm=llm), llm, specialist)
    assert codeact_class.value.code == "strategy_not_predict"


def test_nooa_adapter_rejects_tools_other_schemas_and_sync_calls(specialist, nooa):
    calls = []
    session = SimpleNamespace(limits=RunLimits(steps=2, tokens=24000, wall_s=60, cost_micros=1, tool_calls=0),
                              complete=lambda **kwargs: calls.append(kwargs))
    for kwargs, code in (({"tools": [object()], "output_model": HarnessStep}, "tools_not_allowed"),
                         ({"output_model": dict}, "schema_mismatch")):
        llm = nooa_runtime.GatewayLLM(session, specialist)
        with pytest.raises(nooa_runtime.GatewayRefusal):
            asyncio.run(llm.acall([], **kwargs))
        assert llm.violation == code
    with pytest.raises(nooa_runtime.GatewayRefusal):
        nooa_runtime.GatewayLLM(session, specialist).call([])
    assert calls == []  # nothing reached the gateway
    runtime = nooa_runtime.factory(SimpleNamespace(agent_key=specialist.agent_key))
    runtime._used = True
    with pytest.raises(RuntimeRefused, match="runtime_reused"):
        runtime.run(session)


def test_nooa_runtime_never_turns_a_harness_error_into_a_model_error(specialist, nooa):
    from app.domain.errors import RunCancelledError

    limits = RunLimits(steps=2, tokens=24000, wall_s=60, cost_micros=1, tool_calls=0)
    for error in (RuntimeError("database went away"), RunCancelledError("cancel requested")):
        def complete(error=error, **_kwargs):
            raise error

        runtime = nooa_runtime.factory(SimpleNamespace(agent_key=specialist.agent_key))
        with pytest.raises(type(error), match=str(error)):
            runtime.run(SimpleNamespace(limits=limits, complete=complete, step=lambda **_k: None))


def test_nooa_run_with_a_hook_violation_fails_the_run(hz, specialist, nooa):  # noqa: F811
    from app.agents.harness import hooks as h

    planted = h.default_chain((h.Hook("pre_call", "planted", lambda ctx, _call: h.NarrowLimits(ctx.limits)),))
    service = AgentService(gateway=hz.gateway, settings=lambda: hz.settings, hooks=planted)
    result = service.run(hz.db, _spec(hz, runtime="nooa_predict", runtime_version=nooa_runtime.VERSION))
    assert (result.status, result.error_code) == ("failed", "hook_violation") and hz.fake.calls == []


def test_gateway_llm_refuses_every_call_after_a_harness_error_escaped(specialist):
    # no nooa needed: the refusal comes before any NOOA object is built (P6.3-A security follow-up)
    calls = []
    session = SimpleNamespace(limits=RunLimits(steps=2, tokens=24000, wall_s=60, cost_micros=1, tool_calls=0),
                              complete=lambda **kwargs: calls.append(kwargs))
    llm = nooa_runtime.GatewayLLM(session, specialist)
    llm.escaped = RuntimeError("database went away")
    with pytest.raises(nooa_runtime.GatewayRefusal, match="harness_error"):
        asyncio.run(llm.acall([], output_model=HarnessStep))
    assert calls == [] and llm.calls == 0
