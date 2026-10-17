"""Synthetic-only live check of the gateway's provider adapters: Anthropic (default since 2026-10-16) and the
legacy OpenAI one (no network in these tests)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agents.gateway.providers.fake import FakeProvider
from app.agents.gateway.providers.openai import OpenAIProvider
from app.db.models import MlRunVerification
from app.domain.ml_verification import PipelineAuditReport
from app.services.openai_smoke import (
    LEGACY_OPENAI_SMOKE_MODEL,
    SMOKE_MODEL,
    OpenAISmokeError,
    run_anthropic_verification_smoke,
    run_openai_verification_smoke,
    synthetic_smoke_report,
)
from app.services.verification_evidence import build_verification_evidence


def _report(refs=("deterministic.synthetic_evidence",)) -> PipelineAuditReport:
    return PipelineAuditReport(
        overall_status="VERIFIED",
        summary="Synthetic smoke evidence is internally consistent.",
        stages=[
            {
                "stage": "pipeline",
                "status": "VERIFIED",
                "summary": "The supplied synthetic deterministic check passed.",
                "evidence_refs": list(refs),
                "issues": [],
                "recommendations": [],
            }
        ],
        critical_issues=[],
        warnings=[],
        recommendations=[],
        confidence=1.0,
    )


class _FakeClient:
    """Stands in for the SDK client the adapter builds; records the key it was given."""

    def __init__(self, seen: dict, **kwargs) -> None:
        seen.update(kwargs)
        self.responses = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        return SimpleNamespace(output_parsed=_report(), usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                               model="gpt-6-luna", id="resp-1")


def test_smoke_refuses_to_run_without_the_provider_key(monkeypatch):
    monkeypatch.delenv("DCLAB_OPENAI_API_KEY", raising=False)
    with pytest.raises(OpenAISmokeError) as caught:
        run_openai_verification_smoke(provider=OpenAIProvider(
            client_factory=lambda **_: pytest.fail("network boundary created")))
    assert caught.value.code == "provider_not_configured"


def test_smoke_reuses_production_evidence_and_never_leaks_or_persists_key(db_session, monkeypatch):
    secret = "sk-smoke-test-secret-value"
    monkeypatch.setenv("DCLAB_OPENAI_API_KEY", secret)
    seen: dict = {}
    before = db_session.query(MlRunVerification).count()
    summary = run_openai_verification_smoke(
        provider=OpenAIProvider(client_factory=lambda **kwargs: _FakeClient(seen, **kwargs)))
    after = db_session.query(MlRunVerification).count()

    assert seen["api_key"] == secret  # read by the provider adapter only
    fake = FakeProvider([_report().model_dump(mode="json")], environment="test")
    run_openai_verification_smoke(provider=fake)
    expected = build_verification_evidence(synthetic_smoke_report())
    assert fake.calls[0].model == LEGACY_OPENAI_SMOKE_MODEL == "gpt-6-luna"
    assert json.loads(fake.calls[0].input_json) == expected.payload
    assert "final_test_evaluation" not in expected.payload  # holdout never enters the evidence
    assert summary["evidence_digest"] == expected.digest
    assert set(summary) == {"provider", "model", "status", "request_duration_ms", "evidence_digest"}
    assert secret not in json.dumps(summary)
    assert after == before


def test_synthetic_smoke_fixture_contains_no_customer_rows_or_sensitive_values():
    payload = json.dumps(synthetic_smoke_report(), sort_keys=True)
    assert "customer" not in payload.lower()
    assert "@" not in payload
    assert "sk-" not in payload
    assert "+1" not in payload


def test_smoke_rejects_invalid_evidence_references_without_exposing_key(monkeypatch):
    secret = "sk-do-not-print-this"
    monkeypatch.setenv("DCLAB_OPENAI_API_KEY", secret)
    invented = FakeProvider([_report(refs=("invented.reference",)).model_dump(mode="json")], environment="test")
    with pytest.raises(OpenAISmokeError) as exc_info:
        run_openai_verification_smoke(provider=invented)
    assert exc_info.value.code == "provider_request_failed"
    assert secret not in str(exc_info.value)


def _summary():
    return {"provider": "openai", "model": LEGACY_OPENAI_SMOKE_MODEL, "status": "VERIFIED", "request_duration_ms": 1.0,
            "evidence_digest": "a" * 64}


def test_cli_prints_only_safe_smoke_summary(db_session, monkeypatch, capsys):
    from app.agents.governance.seed import seed_platform_governance
    from app.cli.main import cmd_verify_openai_smoke
    from app.config import get_settings
    import app.services.openai_smoke as smoke

    secret = "sk-never-in-cli-output"
    seed_platform_governance(db_session, environment="test")  # global_ai on outside production
    db_session.commit()
    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(smoke, "run_openai_verification_smoke", _summary)
    try:
        assert cmd_verify_openai_smoke(SimpleNamespace(live=True)) == 1  # LEGACY: inactive by default
        assert capsys.readouterr().err.strip() == "live smoke not executed: legacy_provider_disabled"
        monkeypatch.setenv("AI_LEGACY_PROVIDERS_ENABLED", "true")
        get_settings.cache_clear()
        assert cmd_verify_openai_smoke(SimpleNamespace(live=True)) == 0
    finally:
        monkeypatch.delenv("AI_ENABLED")
        monkeypatch.delenv("AI_LEGACY_PROVIDERS_ENABLED", raising=False)
        get_settings.cache_clear()
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert json.loads(captured.out)["model"] == LEGACY_OPENAI_SMOKE_MODEL


@pytest.mark.parametrize("live, ai_enabled, seeded, code", [
    (False, True, True, "live_flag_missing"),
    (True, False, True, "kill_switch:setting:AI_ENABLED"),
    (True, True, False, "kill_switch:platform:global_ai"),
])
def test_cli_smoke_refuses_without_live_flag_and_ai_switched_on(db_session, monkeypatch, capsys, live,
                                                                 ai_enabled, seeded, code):
    from app.agents.governance.seed import seed_platform_governance
    from app.cli.main import cmd_verify_openai_smoke
    from app.config import get_settings
    import app.services.openai_smoke as smoke

    if seeded:
        seed_platform_governance(db_session, environment="test")
        db_session.commit()
    monkeypatch.setenv("AI_ENABLED", "true" if ai_enabled else "false")
    get_settings.cache_clear()
    monkeypatch.setattr(smoke, "run_openai_verification_smoke", lambda: pytest.fail("the live call must not run"))
    try:
        assert cmd_verify_openai_smoke(SimpleNamespace(live=live)) == 1
    finally:
        monkeypatch.delenv("AI_ENABLED")
        get_settings.cache_clear()
    assert capsys.readouterr().err.strip() == f"live smoke not executed: {code}"


def test_installed_openai_sdk_exposes_responses_parse_without_a_network_call():
    from openai import OpenAI

    client = OpenAI(api_key="not-a-real-key")
    assert callable(client.responses.parse)


# --- the Anthropic smoke (default) ------------------------------------------------------------


def _anthropic(secret_seen: list, *, status: int = 200):
    import httpx

    from app.agents.gateway.providers.anthropic import AnthropicProvider

    def handler(request):
        secret_seen.append(request.headers["x-api-key"])
        if status != 200:
            return httpx.Response(status, json={"type": "error"})
        return httpx.Response(200, headers={"request-id": "req_smoke"}, json={
            "id": "msg_1", "model": SMOKE_MODEL, "stop_reason": "end_turn",
            "content": [{"type": "text", "text": _report().model_dump_json()}],
            "usage": {"input_tokens": 10, "output_tokens": 5}})

    return AnthropicProvider(client_factory=lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw))


def test_anthropic_smoke_is_the_default_and_never_leaks_the_key(db_session, monkeypatch):
    secret = "test-anthropic-smoke-key-not-real"
    monkeypatch.delenv("DCLAB_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)  # the founder's plain name is accepted
    seen: list = []
    before = db_session.query(MlRunVerification).count()
    summary = run_anthropic_verification_smoke(provider=_anthropic(seen))
    assert seen == [secret] and SMOKE_MODEL == "claude-sonnet-5-5"  # exactly one request, key in the header only
    assert (summary["provider"], summary["model"], summary["status"]) == ("anthropic", SMOKE_MODEL, "VERIFIED")
    assert summary["evidence_digest"] == build_verification_evidence(synthetic_smoke_report()).digest
    assert secret not in json.dumps(summary) and db_session.query(MlRunVerification).count() == before
    with pytest.raises(OpenAISmokeError) as failed:
        run_anthropic_verification_smoke(provider=_anthropic(seen, status=529))
    assert failed.value.code == "provider_server_error" and secret not in str(failed.value) and len(seen) == 2
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(OpenAISmokeError) as missing:
        run_anthropic_verification_smoke(provider=_anthropic(seen))
    assert missing.value.code == "provider_not_configured" and len(seen) == 2


def test_cli_ai_smoke_defaults_to_anthropic_and_obeys_its_provider_switch(db_session, monkeypatch, capsys):
    from app.agents.governance.seed import seed_platform_governance
    from app.agents.governance.switches import flip_off
    from app.cli.main import cmd_verify_ai_smoke, main
    from app.config import get_settings
    import app.services.openai_smoke as smoke

    seed_platform_governance(db_session, environment="test")
    db_session.commit()
    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()
    summary = {"provider": "anthropic", "model": SMOKE_MODEL, "status": "VERIFIED", "request_duration_ms": 1.0,
               "evidence_digest": "a" * 64}
    monkeypatch.setattr(smoke, "run_anthropic_verification_smoke", lambda: summary)
    monkeypatch.setattr(smoke, "run_openai_verification_smoke", lambda: pytest.fail("the legacy smoke must not run"))
    try:
        assert main(["verify-ai-smoke", "--live"]) == 0
        assert json.loads(capsys.readouterr().out) == summary
        assert cmd_verify_ai_smoke(SimpleNamespace(live=True, provider="mistral")) == 1
        assert capsys.readouterr().err.strip() == "live smoke not executed: unknown_provider"
        flip_off(db_session, workspace_id=None, switch_key="provider:anthropic", reason="test",
                 actor_rule="test.switch.v1")
        db_session.commit()
        assert main(["verify-ai-smoke", "--live"]) == 1
        assert capsys.readouterr().err.strip() == "live smoke not executed: kill_switch:platform:provider:anthropic"
    finally:
        monkeypatch.delenv("AI_ENABLED")
        get_settings.cache_clear()
