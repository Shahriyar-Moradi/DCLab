"""Column-type decision agent: evidence, validator, and auto-train wiring.

A 3-value integer plan code is reclassified as categorical and logged (through the
AI gateway, fake provider). A normal numeric like tenure never reaches the LLM. With
the agent off, roles stay on the dtype rule and the gateway is never called.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from app.db.models import ClientLabUpload, DEFAULT_WORKSPACE_ID, LabDecisionRecord
from app.engine.lab.column_map import MIN_TRAIN_ROWS
from app.engine.lab.decision_validator import (
    ALLOWED_COLUMN_TYPE_ACTIONS,
    MIN_CONFIDENCE,
    validate_column_type_decision,
)
from app.engine.lab.evidence import (
    COLUMN_TYPE_AMBIGUOUS_UNIQUE_MAX,
    build_column_type_evidence,
    is_ambiguous_column_type,
)
from app.engine.lab.llm_client import ColumnTypeDecision
from app.engine.lab.prompts.column_type_v1 import SYSTEM_PROMPT  # the validator's action enum
from app.engine.lab.prompts.column_type_v2 import PROMPT_VERSION  # the prompt the agent is called with
from app.services.auto_train_service import run_auto_train_job
from legacy_ai_support import column_of, enable_legacy_ai, forbid_gateway, sent, upload_via_api


def _plan_code_frame(n: int = 200) -> pd.DataFrame:
    """Integer plan_code has three values; tenure is a normal numeric count."""
    assert n >= MIN_TRAIN_ROWS
    tenure = [1 + (i % 71) for i in range(n)]
    plan_code = [1 + (i % 3) for i in range(n)]
    monthly = [20.0 + (i % 80) * 0.37 for i in range(n)]
    gender = (["Male", "Female"] * ((n // 2) + 1))[:n]
    contract = (["Month-to-month", "One year", "Two year"] * ((n // 3) + 1))[:n]
    churn = (["No", "Yes"] * ((n // 2) + 1))[:n]
    return pd.DataFrame(
        {
            "customer_id": [f"C{i:03d}" for i in range(n)],
            "tenure": tenure,
            "plan_code": plan_code,
            "MonthlyCharges": monthly,
            "gender": gender,
            "contract": contract,
            "churn": churn,
        }
    )


def _make_upload(db_session, *, stored_path: str, record_count: int) -> ClientLabUpload:
    row = ClientLabUpload(
        workspace_id=DEFAULT_WORKSPACE_ID,
        category="Revenue",
        original_filename="plans.csv",
        stored_path=stored_path,
        kind="spreadsheet",
        record_count=record_count,
        fields_noticed=[],
        has_named_fields=True,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


_LEAKAGE_UNSURE = {"availability_status": "unknown", "risk_level": "LOW", "evidence_field": "column",
                   "rationale": "unsure", "confidence": 0.1}


def _enable_agent(monkeypatch, db_session, column_type: dict):
    return enable_legacy_ai(monkeypatch, db_session, handler=lambda call: (
        column_type if call.agent_key == "column_type" else _LEAKAGE_UNSURE))


def _disable_agent(monkeypatch) -> None:
    from app.engine.lab import llm_client
    from app.services import lab_decision_ledger

    settings = SimpleNamespace(ai_enabled=False)  # P6.9-A: AI_ENABLED is the agent's gate
    monkeypatch.setattr(lab_decision_ledger, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)


def _type_decision(**overrides) -> ColumnTypeDecision:
    payload = {
        "action": "categorical",
        "evidence_field": "cardinality",
        "rationale": "cardinality is 3 repeating plan codes",
        "confidence": 0.94,
    }
    payload.update(overrides)
    try:
        return ColumnTypeDecision.model_validate(payload)
    except Exception:
        return ColumnTypeDecision.model_construct(**payload)


def test_build_column_type_evidence_fields():
    frame = _plan_code_frame(n=60)
    evidence = build_column_type_evidence(frame, "plan_code")

    assert evidence.column == "plan_code"
    assert "int" in evidence.dtype.lower()
    assert evidence.cardinality == 3
    assert evidence.cardinality_ratio == pytest.approx(3 / 60)
    assert evidence.sample_values == [1, 2, 3]


def test_same_frame_always_produces_identical_type_evidence():
    frame = _plan_code_frame(n=60)
    first = build_column_type_evidence(frame, "plan_code")
    second = build_column_type_evidence(frame, "plan_code")
    assert first == second
    assert frame["plan_code"].tolist()[:3] == [1, 2, 3]


def test_only_low_cardinality_numeric_is_ambiguous():
    frame = _plan_code_frame(n=200)
    plan = build_column_type_evidence(frame, "plan_code")
    tenure = build_column_type_evidence(frame, "tenure")
    monthly = build_column_type_evidence(frame, "MonthlyCharges")

    assert plan.cardinality <= COLUMN_TYPE_AMBIGUOUS_UNIQUE_MAX
    assert is_ambiguous_column_type(frame, "plan_code", plan) is True
    assert tenure.cardinality > COLUMN_TYPE_AMBIGUOUS_UNIQUE_MAX
    assert is_ambiguous_column_type(frame, "tenure", tenure) is False
    assert is_ambiguous_column_type(frame, "MonthlyCharges", monthly) is False
    assert is_ambiguous_column_type(frame, "gender", build_column_type_evidence(frame, "gender")) is False


def test_allowed_actions_come_from_column_type_v1():
    assert ALLOWED_COLUMN_TYPE_ACTIONS == {"numerical", "categorical", "identifier"}
    for action in ALLOWED_COLUMN_TYPE_ACTIONS:
        assert action in SYSTEM_PROMPT


def test_validator_accepts_categorical_backed_by_cardinality():
    evidence = build_column_type_evidence(_plan_code_frame(n=60), "plan_code")
    result = validate_column_type_decision(evidence, _type_decision(), withheld=frozenset())
    assert result.verdict == "accept"
    assert result.reason == ""


def test_validator_rejects_identifier_when_ratio_is_low():
    evidence = build_column_type_evidence(_plan_code_frame(n=60), "plan_code")
    result = validate_column_type_decision(
        evidence,
        _type_decision(action="identifier", evidence_field="cardinality_ratio"),
        withheld=frozenset(),
    )
    assert result.verdict == "reject"
    assert "identifier" in result.reason


def test_validator_rejects_low_confidence():
    evidence = build_column_type_evidence(_plan_code_frame(n=60), "plan_code")
    result = validate_column_type_decision(evidence, _type_decision(confidence=0.2), withheld=frozenset())
    assert result.verdict == "reject"
    assert "confidence" in result.reason
    assert MIN_CONFIDENCE == 0.7


def test_validator_rejects_cited_field_that_does_not_exist():
    evidence = build_column_type_evidence(_plan_code_frame(n=60), "plan_code")
    result = validate_column_type_decision(evidence, _type_decision(evidence_field="skewness"), withheld=frozenset())
    assert result.verdict == "reject"
    assert "skewness" in result.reason


def test_plan_code_answer_is_logged_and_only_the_role_point_applies_roles(auth_client, db_session, monkeypatch):
    raw = _plan_code_frame()
    upload = upload_via_api(auth_client, db_session, monkeypatch, raw, filename="plans.csv")
    ai = _enable_agent(monkeypatch, db_session, {
        "action": "categorical",
        "evidence_field": "cardinality",
        "rationale": "cardinality is 3 repeating plan codes",
        "confidence": 0.95,
    })

    run_auto_train_job(db_session, upload.id)
    db_session.refresh(upload)
    assert upload.pipeline_status == "completed"

    type_calls = [call for call in ai.fake.calls if call.agent_key == "column_type"]
    consulted = [column_of(call) for call in type_calls]
    assert all("cardinality" in sent(call) and "sample_values" not in sent(call) for call in type_calls)
    assert consulted == ["plan_code"]
    assert "tenure" not in consulted
    assert "MonthlyCharges" not in consulted

    # P6.9-A: the legacy writer no longer applies roles; column.semantic_role (Jev) does, by level.
    assert "plan_code" in upload.pipeline_log["numerical_cols"]
    assert "plan_code" not in upload.pipeline_log["categorical_cols"]
    assert "tenure" in upload.pipeline_log["numerical_cols"]

    type_rows = (
        db_session.query(LabDecisionRecord)
        .filter(
            LabDecisionRecord.upload_id == upload.id,
            LabDecisionRecord.prompt_version == PROMPT_VERSION,
        )
        .all()
    )
    by_column = {row.column: row for row in type_rows}
    assert set(by_column) == {"plan_code"}
    row = by_column["plan_code"]
    assert row.rule_decision == "numerical"
    assert row.final_decision == "numerical"
    assert row.source == "rule"
    assert row.validator_verdict == "accept"
    assert row.raw_llm_output["action"] == "categorical"
    assert row.raw_llm_output["rationale"]
    assert row.evidence_snapshot["cardinality"] == 3
    assert row.evidence_snapshot["column"] == "plan_code"


def test_agent_disabled_plan_code_stays_numerical(db_session, tmp_path, monkeypatch):
    _disable_agent(monkeypatch)
    forbid_gateway(monkeypatch)

    raw = _plan_code_frame()
    path = tmp_path / "plans.csv"
    raw.to_csv(path, index=False)
    upload = _make_upload(db_session, stored_path=str(path), record_count=len(raw))

    run_auto_train_job(db_session, upload.id)
    db_session.refresh(upload)
    assert upload.pipeline_status == "completed"
    assert "plan_code" in upload.pipeline_log["numerical_cols"]
    assert "plan_code" not in upload.pipeline_log["categorical_cols"]
    assert "tenure" in upload.pipeline_log["numerical_cols"]

    type_rows = (
        db_session.query(LabDecisionRecord)
        .filter(
            LabDecisionRecord.upload_id == upload.id,
            LabDecisionRecord.prompt_version == PROMPT_VERSION,
        )
        .all()
    )
    assert type_rows == []


def test_validator_rejection_keeps_numerical_role(auth_client, db_session, monkeypatch):
    raw = _plan_code_frame()
    upload = upload_via_api(auth_client, db_session, monkeypatch, raw, filename="plans.csv")
    _enable_agent(monkeypatch, db_session, {
        "action": "identifier",
        "evidence_field": "cardinality_ratio",
        "rationale": "claiming identifier without a high uniqueness ratio",
        "confidence": 0.95,
    })

    run_auto_train_job(db_session, upload.id)
    db_session.refresh(upload)
    assert upload.pipeline_status == "completed"
    assert "plan_code" in upload.pipeline_log["numerical_cols"]
    assert "plan_code" not in upload.pipeline_log["categorical_cols"]

    row = (
        db_session.query(LabDecisionRecord)
        .filter(
            LabDecisionRecord.upload_id == upload.id,
            LabDecisionRecord.prompt_version == PROMPT_VERSION,
            LabDecisionRecord.column == "plan_code",
        )
        .one()
    )
    assert row.rule_decision == "numerical"
    assert row.final_decision == "numerical"
    assert row.source == "fallback"
    assert row.validator_verdict.startswith("reject:")


def test_categorical_citing_withheld_sample_values_is_rejected():
    evidence = build_column_type_evidence(_plan_code_frame(n=60), "plan_code")
    decision = _type_decision(evidence_field="sample_values")
    assert validate_column_type_decision(evidence, decision, withheld=frozenset()).verdict == "accept"
    result = validate_column_type_decision(evidence, decision, withheld=frozenset({"sample_values"}))
    assert result.verdict == "reject" and "withheld" in result.reason
