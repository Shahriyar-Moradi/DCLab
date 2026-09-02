from __future__ import annotations

import json

import pytest

from app.db.models import MlRunVerification
from app.domain.ml_verification import PipelineAuditReport
from app.services.openai_smoke import (
    SMOKE_MODEL,
    OpenAISmokeError,
    run_openai_verification_smoke,
    synthetic_smoke_report,
)
from app.services.verification_evidence import build_verification_evidence


class FakeProvider:
    def __init__(self, *, api_key: str) -> None:
        self.api_key = api_key
        self.calls: list[dict] = []

    def audit(self, *, evidence, model):
        self.calls.append({"evidence": evidence, "model": model})
        return PipelineAuditReport(
            overall_status="VERIFIED",
            summary="Synthetic smoke evidence is internally consistent.",
            stages=[
                {
                    "stage": "pipeline",
                    "status": "VERIFIED",
                    "summary": "The supplied synthetic deterministic check passed.",
                    "evidence_refs": ["deterministic.synthetic_evidence"],
                    "issues": [],
                    "recommendations": [],
                }
            ],
            critical_issues=[],
            warnings=[],
            recommendations=[],
            confidence=1.0,
        )


def _factory(instances: list[FakeProvider]):
    def create(*, api_key: str) -> FakeProvider:
        provider = FakeProvider(api_key=api_key)
        instances.append(provider)
        return provider

    return create


def test_smoke_refuses_to_run_without_openai_api_key():
    with pytest.raises(OpenAISmokeError, match="OPENAI_API_KEY unavailable"):
        run_openai_verification_smoke(api_key="", provider_factory=lambda **_: pytest.fail("network boundary created"))


def test_smoke_reuses_production_evidence_and_never_leaks_or_persists_key(db_session):
    secret = "sk-smoke-test-secret-value"
    providers: list[FakeProvider] = []
    before = db_session.query(MlRunVerification).count()
    summary = run_openai_verification_smoke(api_key=secret, provider_factory=_factory(providers))
    after = db_session.query(MlRunVerification).count()

    expected = build_verification_evidence(synthetic_smoke_report())
    assert providers[0].calls[0]["model"] == SMOKE_MODEL
    assert providers[0].calls[0]["evidence"] == expected.payload
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


def test_smoke_rejects_invalid_evidence_references_without_exposing_key():
    class InvalidReferenceProvider(FakeProvider):
        def audit(self, *, evidence, model):
            report = super().audit(evidence=evidence, model=model)
            return report.model_copy(
                update={
                    "stages": [
                        report.stages[0].model_copy(update={"evidence_refs": ["invented.reference"]})
                    ]
                }
            )

    secret = "sk-do-not-print-this"
    with pytest.raises(OpenAISmokeError) as exc_info:
        run_openai_verification_smoke(
            api_key=secret,
            provider_factory=lambda **kwargs: InvalidReferenceProvider(**kwargs),
        )
    assert exc_info.value.code == "provider_request_failed"
    assert secret not in str(exc_info.value)


def test_cli_prints_only_safe_smoke_summary(monkeypatch, capsys):
    from app.cli.main import cmd_verify_openai_smoke
    import app.services.openai_smoke as smoke

    secret = "sk-never-in-cli-output"
    monkeypatch.setattr(
        smoke,
        "run_openai_verification_smoke",
        lambda: {
            "provider": "openai",
            "model": SMOKE_MODEL,
            "status": "VERIFIED",
            "request_duration_ms": 1.0,
            "evidence_digest": "a" * 64,
        },
    )
    assert cmd_verify_openai_smoke(None) == 0
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert json.loads(captured.out)["model"] == SMOKE_MODEL


def test_installed_openai_sdk_exposes_responses_parse_without_a_network_call():
    from openai import OpenAI

    client = OpenAI(api_key="not-a-real-key")
    assert callable(client.responses.parse)
