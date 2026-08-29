"""Adversarial checks for the missing-value decision validator.

Accepted decisions must be backed by evidence values, not just field names.
Rejects fall back to auto_prepare — these tests only cover the reject/accept
gate, not that fallback.
"""

from __future__ import annotations

from app.engine.lab.decision_validator import (
    ALLOWED_ACTIONS,
    MIN_CONFIDENCE,
    validate_decision,
)
from app.engine.lab.evidence import ColumnEvidence, MissingnessCooccurrence
from app.engine.lab.llm_client import MissingValueDecision
from app.engine.lab.prompts.missing_value_v1 import SYSTEM_PROMPT


def _telco_evidence() -> ColumnEvidence:
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


def _decision(**overrides) -> MissingValueDecision:
    payload = {
        "action": "domain_fill",
        "evidence_field": "missingness_cooccurrence",
        "fill_value": 0,
        "rationale": "missingness_cooccurrence exact_match with tenure 0",
        "confidence": 0.94,
    }
    payload.update(overrides)
    try:
        return MissingValueDecision.model_validate(payload)
    except Exception:
        return MissingValueDecision.model_construct(**payload)


def test_allowed_actions_come_from_missing_value_v1():
    assert ALLOWED_ACTIONS == {
        "drop_rows",
        "impute_mean",
        "impute_median",
        "impute_most_frequent",
        "domain_fill",
    }
    for action in ALLOWED_ACTIONS:
        assert action in SYSTEM_PROMPT


def test_accepts_when_cooccurrence_actually_matches_claimed_fill():
    result = validate_decision(_telco_evidence(), _decision())
    assert result.verdict == "accept"
    assert result.reason == ""


def test_rejects_when_evidence_does_not_support_claimed_pattern():
    # Same domain_fill / tenure==0 claim, but the co-occurrence is a weak,
    # different pattern — the field exists, the values do not back the claim.
    evidence = ColumnEvidence(
        column="TotalCharges",
        dtype="float64",
        missing_count=40,
        missing_fraction=0.4,
        correlation_with_target=None,
        missingness_cooccurrence=[
            MissingnessCooccurrence(
                other_column="Contract",
                other_value="Month-to-month",
                missing_and_value_count=12,
                rows_with_value=50,
                fraction_of_missing=0.3,
                fraction_of_value=0.24,
                exact_match=False,
            )
        ],
        sample_rows=[{"TotalCharges": 42.3, "Contract": "Month-to-month"}],
    )
    result = validate_decision(evidence, _decision())
    assert result.verdict == "reject"
    assert result.reason
    assert "does not support" in result.reason or "does not match" in result.reason


def test_rejects_when_fill_value_does_not_match_cooccurrence_value():
    # Strong tenure==0 pattern, but the decision claims fill_value 99.
    result = validate_decision(_telco_evidence(), _decision(fill_value=99))
    assert result.verdict == "reject"
    assert "99" in result.reason
    assert "other_value" in result.reason


def test_rejects_when_cited_field_does_not_exist():
    result = validate_decision(_telco_evidence(), _decision(evidence_field="skewness"))
    assert result.verdict == "reject"
    assert "skewness" in result.reason
    assert "does not exist" in result.reason


def test_rejects_low_confidence():
    assert MIN_CONFIDENCE == 0.7
    result = validate_decision(_telco_evidence(), _decision(confidence=0.2))
    assert result.verdict == "reject"
    assert "confidence" in result.reason
    assert "0.2" in result.reason


def test_rejects_action_outside_prompt_enum():
    result = validate_decision(_telco_evidence(), _decision(action="drop_column"))
    assert result.verdict == "reject"
    assert "drop_column" in result.reason
