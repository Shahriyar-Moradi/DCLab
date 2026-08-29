"""Unit tests for the Lab decision-agent LLM client.

Mocked tests never call a provider. The live smoke test is skipped unless
DECISION_AGENT_LIVE=1 and a real API key are set in the environment.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import httpx
import pytest

from app.engine.lab.evidence import ColumnEvidence, MissingnessCooccurrence
from app.engine.lab.llm_client import (
    DecisionAgentUnavailable,
    MissingValueDecision,
    request_decision,
)
from app.engine.lab.prompts.missing_value_v1 import PROMPT_VERSION


def _evidence() -> ColumnEvidence:
    return ColumnEvidence(
        column="TotalCharges",
        dtype="float64",
        missing_count=3,
        missing_fraction=0.25,
        correlation_with_target=None,
        missingness_cooccurrence=[
            MissingnessCooccurrence(
                other_column="tenure",
                other_value=0,
                missing_and_value_count=3,
                rows_with_value=3,
                fraction_of_missing=1.0,
                fraction_of_value=1.0,
                exact_match=True,
            )
        ],
        sample_rows=[{"TotalCharges": None, "tenure": 0, "Churn": "No"}],
    )


def _settings(*, enabled: bool, api_key: str, model: str = "gpt-4o-mini") -> SimpleNamespace:
    return SimpleNamespace(
        decision_agent_enabled=enabled,
        decision_agent_api_key=api_key,
        decision_agent_model=model,
    )


@pytest.fixture(autouse=True)
def _clear_decision_cache():
    from app.engine.lab import llm_client

    llm_client._CACHE.clear()
    yield
    llm_client._CACHE.clear()


def _valid_payload() -> dict:
    return {
        "action": "domain_fill",
        "evidence_field": "missingness_cooccurrence",
        "fill_value": 0,
        "rationale": "missingness_cooccurrence exact_match with tenure 0",
        "confidence": 0.94,
    }


def test_happy_path_returns_validated_decision_and_caches(monkeypatch):
    from app.engine.lab import llm_client

    monkeypatch.setattr(llm_client, "get_settings", lambda: _settings(enabled=True, api_key="sk-test"))

    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(_valid_payload())}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(llm_client.httpx, "post", fake_post)

    first = request_decision(_evidence(), PROMPT_VERSION)
    second = request_decision(_evidence(), PROMPT_VERSION)

    assert first == second
    assert isinstance(first, MissingValueDecision)
    assert first.action == "domain_fill"
    assert first.evidence_field == "missingness_cooccurrence"
    assert first.fill_value == 0
    assert len(calls) == 1
    body = calls[0]["json"]
    assert body["temperature"] == 0
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "drop_rows" in body["response_format"]["json_schema"]["schema"]["properties"]["action"]["enum"]


def test_unavailable_when_flag_off(monkeypatch):
    from app.engine.lab import llm_client

    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: _settings(enabled=False, api_key="sk-test"),
    )
    monkeypatch.setattr(
        llm_client.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not call the provider")),
    )

    with pytest.raises(DecisionAgentUnavailable, match="disabled"):
        request_decision(_evidence(), PROMPT_VERSION)


def test_unavailable_when_api_key_missing(monkeypatch):
    from app.engine.lab import llm_client

    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: _settings(enabled=True, api_key=""),
    )
    monkeypatch.setattr(
        llm_client.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not call the provider")),
    )

    with pytest.raises(DecisionAgentUnavailable, match="API key"):
        request_decision(_evidence(), PROMPT_VERSION)


@pytest.mark.skipif(
    os.environ.get("DECISION_AGENT_LIVE") != "1"
    or not (os.environ.get("DECISION_AGENT_API_KEY") or os.environ.get("OPENAI_API_KEY")),
    reason="live smoke requires DECISION_AGENT_LIVE=1 and a real API key",
)
def test_live_smoke_request_decision(monkeypatch):
    from app.engine.lab import llm_client

    key = (os.environ.get("DECISION_AGENT_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    monkeypatch.setattr(llm_client, "get_settings", lambda: _settings(enabled=True, api_key=key))

    decision = request_decision(_evidence(), PROMPT_VERSION)
    assert isinstance(decision, MissingValueDecision)
    assert decision.action in {
        "drop_rows",
        "impute_mean",
        "impute_median",
        "impute_most_frequent",
        "domain_fill",
    }
