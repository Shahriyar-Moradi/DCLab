"""Anthropic gateway adapter, routing to Claude and the legacy-provider switch (2026-10-16).

No network: every request goes to an ``httpx.MockTransport`` through the adapter's client
factory; keys are fake values set with ``monkeypatch``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.agents.gateway import budget
from app.agents.gateway.contract import GatewayRefusal, SemanticAnswers
from app.agents.gateway.providers import ProviderCall, ProviderError, ambient_provider_keys, default_providers
from app.agents.gateway.providers import anthropic as adapter
from app.agents.gateway.providers.anthropic import AnthropicProvider, strict_schema
from app.agents.gateway.router import provider_for, route
from app.agents.governance.platform_default import PLATFORM_DEFAULT, PLATFORM_MODEL_ALLOWLIST, ROLE_NAMES

MODEL = "claude-sonnet-5-5"
DCLAB_KEY = "test-dclab-anthropic-key-not-real"
PLAIN_KEY = "test-plain-anthropic-key-not-real"
PROMPT = "SENTINEL-instructions-5521"
PAYLOAD = '{"note":"SENTINEL-payload-5521"}'
OK = {"verdict": "keep", "confidence": 0.9}


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["keep", "revise"]
    confidence: float = Field(ge=0, le=1)


def _call(**overrides) -> ProviderCall:
    values = dict(model=MODEL, instructions=PROMPT, input_json=PAYLOAD, output_schema=Verdict, max_output_tokens=300,
                  temperature=0.0, timeout_s=7.0, purpose="p", agent_key="a")
    return ProviderCall(**{**values, **overrides})


def _message(text: str | None = None, *, stop: str = "end_turn", content: list | None = None) -> dict:
    blocks = content if content is not None else [{"type": "thinking", "thinking": ""},
                                                  {"type": "text", "text": text or json.dumps(OK)}]
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": MODEL, "content": blocks,
            "stop_reason": stop, "usage": {"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 5,
                                           "cache_creation_input_tokens": 0}}


class Wire:
    """Counts requests and answers each with ``outcome`` (a response dict, an int status or an exception)."""

    def __init__(self, outcome) -> None:
        self.outcome, self.requests, self.factory_calls = outcome, [], []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if isinstance(self.outcome, int):
            return httpx.Response(self.outcome, json={"type": "error", "error": {"type": "x", "message": PROMPT}})
        if isinstance(self.outcome, str):
            return httpx.Response(200, text=self.outcome)
        return httpx.Response(200, json=self.outcome, headers={"request-id": "req_01"})

    def factory(self, **kwargs):
        self.factory_calls.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(self.handler), **kwargs)

    def provider(self) -> AnthropicProvider:
        return AnthropicProvider(client_factory=self.factory)

    def body(self, index: int = -1) -> dict:
        return json.loads(self.requests[index].content)


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("DCLAB_ANTHROPIC_API_KEY", DCLAB_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return monkeypatch


def test_structured_success_maps_usage_request_id_and_model(keys):
    wire = Wire(_message())
    result = wire.provider().complete(_call())
    assert (result.output, result.input_tokens, result.output_tokens, result.request_id, result.resolved_model) == (
        OK, 125, 30, "req_01", MODEL)  # cache tokens would be billed as input: counted in full
    [request] = wire.requests
    assert str(request.url) == "https://api.anthropic.com/v1/messages" and request.method == "POST"
    assert request.headers["x-api-key"] == DCLAB_KEY and request.headers["anthropic-version"] == "2023-06-01"
    body = wire.body()
    assert set(body) == {"model", "max_tokens", "system", "messages", "output_config"}  # no temperature, no retention flags
    assert (body["model"], body["max_tokens"], body["system"]) == (MODEL, 300, PROMPT)
    assert body["messages"] == [{"role": "user", "content": PAYLOAD}]
    assert body["output_config"]["effort"] == "low"
    schema = body["output_config"]["format"]
    assert schema["type"] == "json_schema" and schema["schema"]["additionalProperties"] is False
    assert "minimum" not in json.dumps(schema) and wire.factory_calls == [{"timeout": 7.0}]


BILLED = (125, 30)  # the usage _message reports (input counts cache reads)


@pytest.mark.parametrize("outcome, kind, tokens, unknown", [
    (httpx.ReadTimeout("slow"), "timeout", (0, 0), True),  # sent: may be billed, no usage
    (httpx.ConnectTimeout("slow"), "timeout", (0, 0), False),  # nothing was sent
    (httpx.ConnectError("refused"), "server_error", (0, 0), False),
    (httpx.RemoteProtocolError("dropped"), "server_error", (0, 0), True),
    (500, "server_error", (0, 0), True),
    (529, "server_error", (0, 0), True),
    (429, "rate_limited", (0, 0), False),
    (400, "client_error", (0, 0), False),
    (401, "client_error", (0, 0), False),  # a bad key is a client error like every 4xx but 429
    (404, "client_error", (0, 0), False),
    ("not json at all", "invalid_output", (0, 0), True),
    (_message(content=[{"type": "thinking", "thinking": ""}]), "invalid_output", BILLED, False),  # no text block
    (_message(stop="refusal"), "invalid_output", BILLED, False),
    (_message(stop="max_tokens"), "invalid_output", BILLED, False),
    (_message('{"verdict": "SENTINEL-output-5521", "confidence": 2}'), "invalid_output", BILLED, False),  # schema
    (_message("SENTINEL-output-5521 is not JSON"), "invalid_output", BILLED, False),
])
def test_errors_map_like_the_openai_adapter_with_one_request_and_no_content(keys, outcome, kind, tokens, unknown):
    wire = Wire(outcome)
    with pytest.raises(ProviderError) as failed:
        wire.provider().complete(_call())
    assert failed.value.kind == kind
    # a billed but unusable reply carries its usage; a sent request without usage is charged its worst case
    assert ((failed.value.input_tokens, failed.value.output_tokens), failed.value.billed_unknown) == (tokens, unknown)
    assert len(wire.requests) == 1  # one request per call: no retry, whatever the outcome
    text = f"{failed.value!r} {failed.value} {failed.value.__cause__!r}"
    for secret in (DCLAB_KEY, "SENTINEL"):
        assert secret not in text


def test_reply_is_capped_and_read_under_one_total_deadline(keys, monkeypatch):
    monkeypatch.setattr(adapter, "MAX_RESPONSE_BYTES", 64)
    with pytest.raises(ProviderError) as big:
        Wire(_message()).provider().complete(_call())
    assert (big.value.kind, big.value.detail, big.value.billed_unknown) == ("invalid_output", "response too large", True)
    monkeypatch.setattr(adapter, "MAX_RESPONSE_BYTES", 1_000_000)
    clock = iter([0.0, 3.0, 8.0, 8.0])  # start, first chunk, then past the 7 s deadline
    monkeypatch.setattr(adapter, "_now", lambda: next(clock))
    with pytest.raises(ProviderError) as slow:
        Wire(_message()).provider().complete(_call())
    assert (slow.value.kind, slow.value.billed_unknown) == ("timeout", True)


def test_failed_requests_log_only_the_error_type_enum(keys, caplog):
    class Typed(Wire):
        def handler(self, request):
            self.requests.append(request)
            return httpx.Response(self.outcome[0], json={"type": "error", "error": {
                "type": self.outcome[1], "message": "SENTINEL-provider-message"}})

    caplog.set_level("WARNING", logger=adapter.__name__)
    for status, error_type, logged in ((401, "authentication_error", "authentication_error"),
                                       (529, "overloaded_error", "overloaded_error"),
                                       (400, "SENTINEL-made-up", "unknown")):
        with pytest.raises(ProviderError):
            Typed((status, error_type)).provider().complete(_call())
        record = caplog.records[-1]
        assert (record.levelname, record.status, record.error_type) == ("WARNING", status, logged)
    assert "SENTINEL" not in caplog.text and all("SENTINEL" not in str(r.__dict__) for r in caplog.records)


def test_input_overhead_is_part_of_the_worst_case():
    from app.agents.gateway.limits import GatewayLimits
    from app.agents.gateway.providers.fake import FakeProvider
    from app.agents.gateway.service import GatewayService

    assert AnthropicProvider.input_overhead_tokens(Verdict) == adapter.STRUCTURED_OUTPUT_OVERHEAD_TOKENS
    semantic = AnthropicProvider.input_overhead_tokens(SemanticAnswers)  # frame + prompted schema
    assert semantic > adapter.STRUCTURED_OUTPUT_OVERHEAD_TOKENS + len(adapter.SEMANTIC_FRAME) // 3
    service = GatewayService(providers={"anthropic": AnthropicProvider(), "fake": FakeProvider(environment="test")},
                             limits=GatewayLimits())
    assert service.input_overhead_tokens("anthropic", SemanticAnswers) == semantic
    assert service.input_overhead_tokens("fake", SemanticAnswers) == 0 == service.input_overhead_tokens("x", Verdict)


def test_missing_key_is_not_configured_and_nothing_is_sent(monkeypatch):
    monkeypatch.delenv("DCLAB_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("DCLAB_ANTHROPIC_API_KEY", "   ")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-other-vendor-not-read")
    wire = Wire(_message())
    with pytest.raises(ProviderError) as missing:
        wire.provider().complete(_call())
    assert missing.value.kind == "not_configured" and wire.factory_calls == [] and wire.requests == []
    assert not wire.provider().ready()


@pytest.mark.parametrize("dclab, plain, sent", [
    (DCLAB_KEY, None, DCLAB_KEY),
    (None, PLAIN_KEY, PLAIN_KEY),  # the founder's plain ANTHROPIC_API_KEY
    (DCLAB_KEY, PLAIN_KEY, DCLAB_KEY),  # DCLAB_ first
    ("  ", PLAIN_KEY, PLAIN_KEY),
])
def test_key_order_dclab_first_then_the_plain_name(monkeypatch, dclab, plain, sent):
    for name, value in (("DCLAB_ANTHROPIC_API_KEY", dclab), ("ANTHROPIC_API_KEY", plain)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    wire = Wire(_message())
    assert wire.provider().ready()
    wire.provider().complete(_call())
    assert wire.requests[0].headers["x-api-key"] == sent


def test_free_form_schemas_travel_in_the_prompt_and_jev_calls_are_framed(keys):
    answer = {"answers": [{"question_key": "feature", "answer": {"value": "numeric"}, "confidence": 0.9,
                           "probabilities": {"numeric": 0.9, "other": 0.1}}]}
    wire = Wire(_message("```json\n" + json.dumps(answer) + "\n```"))
    instructions = json.dumps({"question": "Which role?", "criteria": None})
    result = wire.provider().complete(_call(output_schema=SemanticAnswers, instructions=instructions))
    assert result.output["answers"][0]["answer"] == {"value": "numeric"}
    body = wire.body()
    assert "format" not in body["output_config"]  # dict fields cannot be strict
    assert body["system"].startswith(adapter.SEMANTIC_FRAME + instructions)
    assert adapter.JSON_INSTRUCTION in body["system"] and '"answers"' in body["system"]


def test_strict_schema_covers_the_gateway_output_schemas():
    from app.agents.classes.dataset_investigator import DatasetInvestigation
    from app.agents.classes.experiment_critic import ExperimentReview
    from app.agents.classes.experiment_planner import ExperimentPlanOutput
    from app.agents.lead.runtime import AssistantStep
    from app.domain.ml_verification import PipelineAuditReport
    from app.engine.lab.llm_client import MissingValueDecisionV3, TargetSelectionDecision

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                yield node
            for value in node.values():
                yield from objects(value)
        elif isinstance(node, list):
            for value in node:
                yield from objects(value)

    for model in (DatasetInvestigation, ExperimentReview, ExperimentPlanOutput, PipelineAuditReport,
                  MissingValueDecisionV3, TargetSelectionDecision, Verdict):
        schema = strict_schema(model)
        assert schema is not None, model.__name__
        assert all(node["additionalProperties"] is False and node["properties"] for node in objects(schema))
        text = json.dumps(schema)
        assert not any(f'"{word}"' in text for word in ("minimum", "maxLength", "pattern", "maxItems", "oneOf"))
    assert strict_schema(AssistantStep) is None and strict_schema(SemanticAnswers) is None  # dict fields


def test_every_role_routes_to_claude_on_anthropic_and_legacy_ids_are_refused():
    assert PLATFORM_MODEL_ALLOWLIST == {MODEL} and provider_for(MODEL) == "anthropic"
    assert provider_for("gpt-6.1-sol") is None and provider_for("jev-1.13.0") is None
    for role in ROLE_NAMES:  # Jev included: no longer forced through TypeSafe
        assert route(PLATFORM_DEFAULT, role=role, agent_key=None, requested=None) == (
            route(PLATFORM_DEFAULT, role=role, agent_key="dataset_investigator", requested=MODEL))
        assert route(PLATFORM_DEFAULT, role=role, agent_key=None, requested=None).provider == "anthropic"
        for refused in ("gpt-6.1-sol", "gpt-6-luna", "jev-1.13.0", f"{MODEL}-latest", f"{MODEL}-preview"):
            with pytest.raises(GatewayRefusal) as caught:
                route(PLATFORM_DEFAULT, role=role, agent_key=None, requested=refused)
            assert caught.value.refusal.code == "model_not_allowed"
    assert budget.PRICES_MICROS_PER_MTOK[MODEL] == (3_000_000, 15_000_000)  # conservative until verified


def test_legacy_providers_register_only_behind_their_switch():
    from app.config import Settings

    assert Settings.model_fields["ai_legacy_providers_enabled"].default is False
    assert set(default_providers(SimpleNamespace(ai_legacy_providers_enabled=False))) == {"anthropic"}
    assert set(default_providers(SimpleNamespace())) == {"anthropic"}
    assert set(default_providers(SimpleNamespace(ai_legacy_providers_enabled="yes"))) == {"anthropic"}  # True only
    assert set(default_providers(SimpleNamespace(ai_legacy_providers_enabled=True))) == {
        "anthropic", "openai", "typesafe"}


def test_ambient_key_guard_is_unchanged_for_the_plain_anthropic_name():
    # NOOA stays off while the founder's plain ANTHROPIC_API_KEY is set (P6.3-A guard, unchanged).
    env = {"ANTHROPIC_API_KEY": "x", "DCLAB_ANTHROPIC_API_KEY": "y", "PATH": "/bin"}
    assert ambient_provider_keys(env) == ("ANTHROPIC_API_KEY",)


def test_legacy_providers_route_only_behind_the_flag_and_only_for_their_roles(monkeypatch):
    import app.agents.gateway.router as router
    from app.agents.governance.platform_default import AiPolicyV1

    doc = PLATFORM_DEFAULT.model_dump(mode="json")
    for role in ROLE_NAMES:
        doc["models"]["roles"][role]["allowed"] = [MODEL, "gpt-6-luna", "jev-1.13.0"]
    policy = AiPolicyV1.model_validate(doc)
    monkeypatch.setattr(router, "PLATFORM_MODEL_ALLOWLIST", frozenset({MODEL, "gpt-6-luna", "jev-1.13.0"}))
    monkeypatch.setitem(budget.PRICES_MICROS_PER_MTOK, "gpt-6-luna", (100_000, 500_000))
    monkeypatch.setitem(budget.PRICES_MICROS_PER_MTOK, "jev-1.13.0", (42_000, 0))

    def refused(role, model, legacy):
        with pytest.raises(GatewayRefusal):
            route(policy, role=role, agent_key=None, requested=model, legacy=legacy)
        return True

    assert refused("verifier", "gpt-6-luna", False) and refused("jev", "jev-1.13.0", False)  # flag off
    assert route(policy, role="verifier", agent_key=None, requested="gpt-6-luna", legacy=True).provider == "openai"
    assert route(policy, role="jev", agent_key=None, requested="jev-1.13.0", legacy=True).provider == "typesafe"
    assert refused("specialist", "jev-1.13.0", True) and refused("jev", "gpt-6-luna", True)  # never across roles
    assert route(policy, role="jev", agent_key=None, requested=MODEL, legacy=True).provider == "anthropic"


def test_operator_price_overrides_only_raise(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("AI_PRICE_OVERRIDES", '{"claude-sonnet-5-5": [4000000, 1]}')  # platform env only
    assert Settings(_env_file=None).ai_price_overrides == {MODEL: (4_000_000, 1)}
    monkeypatch.delenv("AI_PRICE_OVERRIDES")
    assert Settings.model_fields["ai_price_overrides"].default_factory() == {}
    overrides = {MODEL: (4_000_000, 1), "unpriced-model": (9, 9)}
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(ai_price_overrides=overrides))
    assert budget.price(MODEL) == (4_000_000, 15_000_000)  # input raised, the lower output ignored
    assert budget.cost_micros(MODEL, 1_000_000, 0) == 4_000_000
    with pytest.raises(KeyError):
        budget.price("unpriced-model")  # an override never adds a model
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(ai_price_overrides={MODEL: ("x", 1)}))
    assert budget.price(MODEL) == (3_000_000, 15_000_000)
