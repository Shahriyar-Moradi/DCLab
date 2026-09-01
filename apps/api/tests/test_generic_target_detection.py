"""Generic target/task inference for unrelated arbitrary-upload schemas."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import numpy as np
import pandas as pd

from app.engine.lab.decision_validator import validate_target_selection_decision
from app.engine.lab.evidence import build_target_selection_evidence
from app.engine.lab.llm_client import TargetSelectionDecision
from app.engine.lab.schema_inference import (
    choose_target_deterministically,
    generate_target_candidates,
    infer_entity_column,
)
from app.services.lab_decision_ledger import resolve_target_selection


def _rng_frame(seed: int = 7, n: int = 200) -> tuple[np.random.Generator, int]:
    return np.random.default_rng(seed), n


def test_unfamiliar_classification_target_is_inferred_without_use_case_aliases():
    rng, n = _rng_frame()
    frame = pd.DataFrame(
        {
            "age": rng.integers(18, 80, n),
            "income": rng.uniform(25_000, 150_000, n),
            "region": rng.choice(["north", "south", "west"], n),
            "defaulted": rng.choice(["yes", "no"], n),
        }
    )
    choice = choose_target_deterministically(frame, list(frame.columns))
    assert choice.column == "defaulted"
    assert choice.task_type == "binary"
    assert choice.source == "rule"


def test_unfamiliar_regression_target_is_inferred_from_generic_output_semantics():
    rng, n = _rng_frame()
    frame = pd.DataFrame(
        {
            "temperature": rng.uniform(10, 40, n),
            "humidity": rng.integers(20, 90, n),
            "pressure": rng.uniform(980, 1035, n),
            "energy_output": rng.uniform(250, 800, n),
        }
    )
    choice = choose_target_deterministically(frame, list(frame.columns))
    assert choice.column == "energy_output"
    assert choice.task_type == "regression"
    assert choice.evaluation_metric == "mae"


def test_unfamiliar_binary_outcome_and_identifier_exclusion():
    rng, n = _rng_frame()
    frame = pd.DataFrame(
        {
            "transaction_id": [f"txn-{index}" for index in range(n)],
            "f1": rng.normal(size=n),
            "f2": rng.normal(size=n),
            "outcome_x": rng.integers(0, 2, n),
        }
    )
    candidates = generate_target_candidates(frame, list(frame.columns))
    assert "transaction_id" not in {item.column for item in candidates}
    choice = choose_target_deterministically(frame, list(frame.columns))
    assert choice.column == "outcome_x"
    assert infer_entity_column(frame, list(frame.columns)) == "transaction_id"


def test_explicit_target_overrides_a_stronger_deterministic_candidate():
    rng, n = _rng_frame()
    frame = pd.DataFrame(
        {
            "feature": rng.normal(size=n),
            "outcome_x": rng.integers(0, 2, n),
            "manual_measure": rng.uniform(0, 100, n),
        }
    )
    choice = choose_target_deterministically(
        frame,
        list(frame.columns),
        explicit_target="manual_measure",
    )
    assert choice.column == "manual_measure"
    assert choice.task_type == "regression"
    assert choice.source == "explicit"
    assert choice.confidence == 1.0


def test_invalid_explicit_target_fails_closed():
    frame = pd.DataFrame({"feature": [1, 2, 3], "label": [0, 1, 0]})
    choice = choose_target_deterministically(
        frame,
        list(frame.columns),
        explicit_target="invented",
    )
    assert choice.column is None
    assert choice.source == "explicit"
    assert "not present" in choice.reason


def test_llm_target_validator_rejects_nonexistent_column():
    frame = pd.DataFrame({"feature_a": range(100), "feature_b": np.arange(100) * 2.5})
    candidates = generate_target_candidates(frame, list(frame.columns))
    evidence = build_target_selection_evidence(len(frame), len(frame.columns), candidates)
    decision = TargetSelectionDecision(
        target="invented",
        task_type="regression",
        evidence_field="columns",
        rationale="invented",
        confidence=0.95,
    )
    result = validate_target_selection_decision(evidence, decision)
    assert result.verdict == "reject"
    assert "not a real eligible column" in result.reason


def test_ambiguous_target_uses_existing_configured_llm_path(monkeypatch):
    from app.engine.lab import llm_client
    from app.services import lab_decision_ledger

    llm_client._TARGET_SELECTION_CACHE.clear()
    settings = SimpleNamespace(
        decision_agent_enabled=True,
        decision_agent_api_key="sk-test",
        decision_agent_model="configured-small-model",
    )
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    monkeypatch.setattr(lab_decision_ledger, "get_settings", lambda: settings)

    seen: dict = {}

    def fake_post(url, **kwargs):
        seen.update(kwargs["json"])
        evidence = json.loads(kwargs["json"]["messages"][1]["content"])
        assert evidence["row_count"] == 100
        assert set(evidence) == {"row_count", "column_count", "columns"}
        assert all("sample_values" in item for item in evidence["columns"])
        payload = {
            "target": "measure_b",
            "task_type": "regression",
            "evidence_field": "columns",
            "rationale": "measure_b is the intended response",
            "confidence": 0.91,
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload)}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(llm_client.httpx, "post", fake_post)
    frame = pd.DataFrame(
        {
            "measure_a": np.linspace(1, 50, 100),
            "measure_b": np.linspace(5, 100, 100) ** 1.1,
            "measure_c": np.linspace(3, 75, 100) ** 1.05,
        }
    )
    choice = resolve_target_selection(frame, list(frame.columns))
    assert choice.column == "measure_b"
    assert choice.task_type == "regression"
    assert choice.source == "llm"
    assert choice.validator_verdict == "accept"
    assert seen["model"] == "configured-small-model"
    assert "measure_b is the intended response" == choice.reason


def test_llm_disabled_preserves_safe_ambiguous_failure(monkeypatch):
    from app.services import lab_decision_ledger

    monkeypatch.setattr(
        lab_decision_ledger,
        "get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )
    frame = pd.DataFrame(
        {
            "measure_a": np.linspace(1, 50, 100),
            "measure_b": np.linspace(5, 100, 100),
        }
    )
    choice = resolve_target_selection(frame, list(frame.columns))
    assert choice.column is None
    assert choice.source == "fallback"
    assert "disabled or unconfigured" in choice.reason
