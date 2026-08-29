"""lab_decision_records: one audit row per feature column on a real auto-train."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.db.models import ClientLabUpload, DEFAULT_WORKSPACE_ID, LabDecisionRecord
from app.engine.lab.prompts.missing_value_v1 import PROMPT_VERSION
from app.services.auto_train_service import run_auto_train_job


def _make_upload(db_session, *, stored_path: str, kind: str = "spreadsheet", record_count: int = 200) -> ClientLabUpload:
    row = ClientLabUpload(
        workspace_id=DEFAULT_WORKSPACE_ID,
        category="Revenue",
        original_filename="upload.csv",
        stored_path=stored_path,
        kind=kind,
        record_count=record_count,
        fields_noticed=[],
        has_named_fields=True,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _telco_like_frame(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 72, n)
    monthly = rng.uniform(20, 120, n)
    total = tenure * monthly + rng.normal(0, 50, n)
    total_str = [f"{value:.2f}" if rng.random() > 0.05 else " " for value in total]
    contract = rng.choice(["Month-to-month", "One year", "Two year"], n)
    gender = rng.choice(["Male", "Female"], n)
    churn_p = np.where(contract == "Month-to-month", 0.55, 0.15)
    churn = rng.binomial(1, churn_p)
    return pd.DataFrame(
        {
            "customer_id": [f"C{i}" for i in range(n)],
            "tenure": tenure,
            "MonthlyCharges": monthly,
            "TotalCharges": total_str,
            "gender": gender,
            "contract": contract,
            "churn": np.where(churn == 1, "Yes", "No"),
        }
    )


@pytest.fixture
def _rule_engine_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )


def test_completed_upload_writes_one_row_per_feature_column(db_session, tmp_path, _rule_engine_only):
    frame = _telco_like_frame(n=200)
    path = tmp_path / "telco.csv"
    frame.to_csv(path, index=False)
    upload = _make_upload(db_session, stored_path=str(path), record_count=len(frame))

    run_auto_train_job(db_session, upload.id)
    db_session.refresh(upload)
    assert upload.pipeline_status == "completed"

    target = upload.pipeline_log["target"]["column"]
    feature_columns = [name for name in frame.columns if name != target]
    rows = (
        db_session.query(LabDecisionRecord)
        .filter(LabDecisionRecord.upload_id == upload.id)
        .all()
    )

    assert len(rows) == len(feature_columns)
    assert {row.column for row in rows} == set(feature_columns)
    for row in rows:
        assert row.source == "rule"
        assert row.raw_llm_output is None
        assert row.validator_verdict == "not_run"
        assert row.prompt_version == PROMPT_VERSION
        assert row.final_decision
        assert row.rule_decision == row.final_decision
        assert row.fill_value is None
        assert row.evidence_snapshot["column"] == row.column
        assert "missing_count" in row.evidence_snapshot
        assert "missing_fraction" in row.evidence_snapshot


def test_skipped_upload_writes_no_ledger_rows(db_session, _rule_engine_only):
    upload = _make_upload(
        db_session,
        stored_path="/tmp/does-not-matter.log",
        kind="plain_text",
        record_count=200,
    )
    upload.has_named_fields = False
    db_session.commit()

    run_auto_train_job(db_session, upload.id)
    rows = (
        db_session.query(LabDecisionRecord)
        .filter(LabDecisionRecord.upload_id == upload.id)
        .all()
    )
    assert rows == []
