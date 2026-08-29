"""Integration: the decision agent overrides auto_prepare only on ambiguous columns.

Telco-shaped TotalCharges that are missing exactly where tenure == 0 should
become domain_fill 0 instead of impute_median. Every other column must keep the
rule-engine action.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pandas as pd

from app.db.models import ClientLabUpload, DEFAULT_WORKSPACE_ID, LabDecisionRecord
from app.engine.lab.auto_prepare import coerce_numeric_like, plan_missing_values
from app.engine.lab.column_map import MIN_TRAIN_ROWS
from app.services.auto_train_service import run_auto_train_job


def _make_upload(db_session, *, stored_path: str, record_count: int) -> ClientLabUpload:
    row = ClientLabUpload(
        workspace_id=DEFAULT_WORKSPACE_ID,
        category="Revenue",
        original_filename="telco.csv",
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


def test_telco_total_charges_domain_fill_overrides_impute_mean_only(db_session, tmp_path, monkeypatch):
    from app.engine.lab import llm_client
    from app.services import lab_decision_ledger

    llm_client._CACHE.clear()
    settings = SimpleNamespace(
        decision_agent_enabled=True,
        decision_agent_api_key="sk-test",
        decision_agent_model="gpt-4o-mini",
    )
    monkeypatch.setattr(lab_decision_ledger, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)

    consulted: list[str] = []

    def fake_post(url, **kwargs):
        evidence = json.loads(kwargs["json"]["messages"][1]["content"])
        consulted.append(evidence["column"])
        payload = {
            "action": "domain_fill",
            "evidence_field": "missingness_cooccurrence",
            "fill_value": 0,
            "rationale": "missingness_cooccurrence exact_match with tenure 0",
            "confidence": 0.95,
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload)}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(llm_client.httpx, "post", fake_post)

    raw = _telco_tenure_zero_frame()
    path = tmp_path / "telco_tenure_zero.csv"
    raw.to_csv(path, index=False)
    upload = _make_upload(db_session, stored_path=str(path), record_count=len(raw))

    run_auto_train_job(db_session, upload.id)
    db_session.refresh(upload)
    assert upload.pipeline_status == "completed"

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
    assert consulted == ["TotalCharges"]

    total = by_column["TotalCharges"]
    assert total.rule_decision == "impute_median"
    assert total.final_decision == "domain_fill"
    assert total.fill_value == 0
    assert total.source == "llm"
    assert total.validator_verdict == "accept"
    assert total.raw_llm_output is not None
    assert total.raw_llm_output["fill_value"] == 0

    for column, rule_action in expected_rule.items():
        row = by_column[column]
        assert row.rule_decision == rule_action
        if column == "TotalCharges":
            continue
        assert row.final_decision == rule_action
        assert row.source == "rule"
        assert row.raw_llm_output is None
        assert row.fill_value is None

    logged = {
        item["column"]: item["action"]
        for item in upload.pipeline_log["missing_value_decisions"]["column_decisions"]
    }
    assert logged["TotalCharges"] == "domain_fill"
    for column, rule_action in expected_rule.items():
        if column != "TotalCharges":
            assert logged[column] == rule_action
