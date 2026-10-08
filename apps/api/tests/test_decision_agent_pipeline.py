"""Integration: the decision agent overrides auto_prepare only on ambiguous columns.

The agent runs through the AI gateway (fake provider). Telco-shaped TotalCharges
that are missing exactly where tenure == 0 is the only consulted column; every other
column keeps the rule-engine action. Under the accepted data policy (no sample values,
ADR 0009 §8) the co-occurring value never reaches the model, so a domain fill citing
it is rejected and the rule action stays.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select

from app.db.models import LabDecisionRecord, LlmInvocation, ProjectDecisionRecord
from app.engine.lab.auto_prepare import coerce_numeric_like, plan_missing_values
from app.engine.lab.column_map import MIN_TRAIN_ROWS
from app.services.auto_train_service import run_auto_train_job
from legacy_ai_support import column_of, enable_legacy_ai, upload_via_api

_LOW_CONFIDENCE = {
    "column_type": {"action": "numerical", "evidence_field": "dtype", "rationale": "unsure", "confidence": 0.1},
    "leakage_review": {"availability_status": "unknown", "risk_level": "LOW", "evidence_field": "column",
                       "rationale": "unsure", "confidence": 0.1},
}


def _answer(missing_value: dict):
    def handler(call):
        return missing_value if call.agent_key == "missing_value" else _LOW_CONFIDENCE[call.agent_key]

    return handler


def _telco_tenure_zero_frame(n: int = 80, n_new: int = 8) -> pd.DataFrame:
    """TotalCharges is blank exactly on the new-customer (tenure == 0) rows."""
    assert n >= MIN_TRAIN_ROWS
    assert 0 < n_new < n
    tenure = [0] * n_new + [1 + (i % 71) for i in range(n - n_new)]
    monthly = [29.85 + (i % 40) for i in range(n)]
    total: list[object] = [" "] * n_new
    for i in range(n_new, n):
        total.append(round(tenure[i] * monthly[i], 2))
    contract = (["Month-to-month", "One year", "Two year"] * ((n // 3) + 1))[:n]
    gender = (["Male", "Female"] * ((n // 2) + 1))[:n]
    churn = (["No", "Yes"] * ((n // 2) + 1))[:n]
    return pd.DataFrame(
        {
            "customer_id": [f"C{i:03d}" for i in range(n)],
            "tenure": tenure,
            "MonthlyCharges": monthly,
            "TotalCharges": total,
            "gender": gender,
            "contract": contract,
            "churn": churn,
        }
    )


def _run(auth_client, db_session, monkeypatch, missing_value: dict):
    raw = _telco_tenure_zero_frame()
    upload = upload_via_api(auth_client, db_session, monkeypatch, raw, filename="telco_tenure_zero.csv")
    ai = enable_legacy_ai(monkeypatch, db_session, handler=_answer(missing_value))
    run_auto_train_job(db_session, upload.id)
    db_session.refresh(upload)
    assert upload.pipeline_status == "completed", upload.pipeline_log

    prepared = coerce_numeric_like(raw.copy(), list(raw.columns))
    prepared = prepared.dropna(subset=["churn"]).reset_index(drop=True)
    feature_columns = [name for name in prepared.columns if name != "churn"]
    rule_plan = plan_missing_values(prepared, feature_columns)
    expected_rule = {item.column: item.action for item in rule_plan.column_decisions}
    assert expected_rule["TotalCharges"] == "impute_median"

    rows = (
        db_session.query(LabDecisionRecord)
        .filter(LabDecisionRecord.upload_id == upload.id)
        .all()
    )
    by_column = {row.column: row for row in rows}
    assert set(by_column) == set(expected_rule)
    assert [column_of(call) for call in ai.fake.calls] == ["TotalCharges"]
    for column, rule_action in expected_rule.items():
        row = by_column[column]
        assert row.rule_decision == rule_action
        if column == "TotalCharges":
            continue
        assert row.final_decision == rule_action
        assert row.source == "rule"
        assert row.raw_llm_output is None
        assert row.fill_value is None
    return upload, by_column, expected_rule


def test_telco_total_charges_answer_is_recorded_and_applied_only_by_level(auth_client, db_session, monkeypatch):
    # P6.9-A: the legacy agent is an interim AI source of column.missing_value_action. At the
    # default L0 its answer is logged beside the rule value (shadow) and nothing changes.
    upload, by_column, expected_rule = _run(auth_client, db_session, monkeypatch, {
        "action": "drop_column", "evidence_field": "missing_fraction",  # an exclusion: never above L1
        "rationale": "many values are missing", "confidence": 0.95,
    })
    total = by_column["TotalCharges"]
    assert total.rule_decision == "impute_median"
    assert total.final_decision == "impute_median"
    assert total.source == "rule"
    assert total.validator_verdict == "accept"
    assert total.raw_llm_output["action"] == "drop_column"
    invocation = db_session.get(LlmInvocation, total.llm_invocation_id)
    assert (invocation.status, invocation.llm_used, invocation.purpose) == ("completed", True, "semantic_missing_value")
    assert invocation.reason == "LLM used: YES — missing-value evidence was ambiguous."
    assert invocation.final_decision["final_decision"] == "impute_median"
    assert invocation.final_decision["ai_decision"] == "drop_column"

    logged = {
        item["column"]: item["action"]
        for item in upload.pipeline_log["missing_value_decisions"]["column_decisions"]
    }
    assert logged == expected_rule
    record = db_session.scalars(select(ProjectDecisionRecord).where(
        ProjectDecisionRecord.experiment_id == upload.experiment_id,
        ProjectDecisionRecord.decision_type == "decision_point_resolved")).all()
    missing = next(r for r in record if r.details["decision_point"] == "column.missing_value_action")
    column = next(c for c in missing.details["columns"] if c["column"] == "TotalCharges")
    assert (column["ai"], column["used"], column["source"], column["level"]) == (
        "drop_column", "impute_median", "rule", 0)
    assert missing.details["release_id"] and missing.actor_kind == "rule"


def test_at_l2_a_legacy_exclusion_is_still_capped_at_l1_and_never_applied(auth_client, db_session, monkeypatch):
    from app.agents.governance.decision_points import answer_ceiling

    monkeypatch.setattr("app.agents.governance.snapshot.effective_level",
                        lambda db, ws, key, kind=None, prompt_release_id=None, model_id=None:
                        min(2, answer_ceiling(key, kind)))
    upload, by_column, _expected = _run(auth_client, db_session, monkeypatch, {
        "action": "drop_column", "evidence_field": "missing_fraction",
        "rationale": "many values are missing", "confidence": 0.95,
    })
    total = by_column["TotalCharges"]
    assert (total.final_decision, total.source) == ("impute_median", "rule")  # an exclusion is ≤ L1
    assert upload.pipeline_log["missing_value_decisions"]["column_decisions"]
    record = db_session.scalars(select(ProjectDecisionRecord).where(
        ProjectDecisionRecord.experiment_id == upload.experiment_id,
        ProjectDecisionRecord.decision_type == "decision_point_resolved")).all()
    missing = next(r for r in record if r.details["decision_point"] == "column.missing_value_action")
    column = next(c for c in missing.details["columns"] if c["column"] == "TotalCharges")
    assert (column["ai"], column["used"], column["source"], column["level"]) == (
        "drop_column", "impute_median", "rule", 1)
    assert not any(c["source"] == "ai" for c in missing.details["columns"])


def test_an_unsupported_drop_column_answer_is_rejected(auth_client, db_session, monkeypatch):
    # missing_value v3 offers no domain fill; a drop citing co-occurrence is rejected.
    upload, by_column, _expected = _run(auth_client, db_session, monkeypatch, {
        "action": "drop_column", "evidence_field": "missingness_cooccurrence",
        "rationale": "missingness_cooccurrence exact_match with tenure 0", "confidence": 0.95,
    })
    total = by_column["TotalCharges"]
    assert total.final_decision == "impute_median"
    assert total.source == "fallback"
    assert total.fill_value is None
    assert total.validator_verdict.startswith("reject:") and "drop_column must cite" in total.validator_verdict
    assert total.raw_llm_output["action"] == "drop_column"
    invocation = db_session.get(LlmInvocation, total.llm_invocation_id)
    assert (invocation.status, invocation.reason) == (
        "rejected", "LLM used: YES — validator rejected the missing-value response.")
    assert invocation.final_decision["final_decision"] == "impute_median"
    assert "rationale" not in invocation.safe_output
    deterministic = db_session.scalars(select(LlmInvocation).where(
        LlmInvocation.experiment_id == upload.experiment_id, LlmInvocation.purpose == "semantic_missing_value",
        LlmInvocation.llm_used.is_(False))).all()
    assert deterministic and all(row.status == "not_used" for row in deterministic)
