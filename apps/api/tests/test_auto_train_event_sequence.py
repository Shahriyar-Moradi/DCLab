"""P1.5-A: the auto-train job's observable sequence is a contract.

Splitting ``run_auto_train_job`` into typed stages must not change which
pipeline events are emitted (stage, type, status, payload keys) or which
upload statuses are marked, in what order. The golden file is regenerated only
for an intentional behaviour change:

    UPDATE_AUTO_TRAIN_EVENT_SNAPSHOT=1 pytest apps/api/tests/test_auto_train_event_sequence.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.db.models import ClientLabUpload
from app.services import auto_train_service
from app.services.auto_train_service import run_auto_train_job
from app.services.observability_service import PipelineRunObserver

SNAPSHOT = Path(__file__).parent / "fixtures" / "auto_train_event_sequence.json"


def _classification_frame(n: int = 220, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 72, n).astype(float)
    tenure[::17] = np.nan
    contract = rng.choice(["Month-to-month", "One year", "Two year"], n)
    churn = np.where(rng.binomial(1, np.where(contract == "Month-to-month", 0.55, 0.18)) == 1, "Yes", "No")
    return pd.DataFrame(
        {
            "tenure": tenure,
            "MonthlyCharges": rng.uniform(20, 120, n),
            "contract": contract,
            "churn": churn,
        }
    )


def _regression_frame(n: int = 200, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 72, n)
    monthly = rng.uniform(20, 120, n)
    return pd.DataFrame(
        {
            "tenure": tenure,
            "MonthlyCharges": monthly,
            "segment": rng.choice(["smb", "midmarket", "enterprise"], n),
            "energy_output": 30 + 1.8 * tenure + 0.35 * monthly + rng.normal(0, 6, n),
        }
    )


def _no_label_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_id": [f"C{i}" for i in range(80)],
            "plan_name": ["Gold", "Silver"] * 40,
            "amount": list(range(80)),
        }
    )


SCENARIOS = {
    "binary_completed": _classification_frame,
    # Profiling raises: the generic failure path (_fail, verification, terminal event).
    "profiling_error_failed": _classification_frame,
    "regression_completed": _regression_frame,
    "no_label_needs_input": _no_label_frame,
}


def _capture(monkeypatch, auth_client, db_session, frame: pd.DataFrame) -> dict:
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None
    )
    events: list[list] = []
    marks: list[str] = []
    real_emit = PipelineRunObserver.emit
    real_mark = auto_train_service._mark

    def emit(self, stage, event_type, status, payload=None, duration_ms=None):
        events.append([stage, event_type, status, sorted((payload or {}).keys())])
        return real_emit(self, stage, event_type, status, payload, duration_ms)

    def mark(db, row, *, status, **kwargs):
        marks.append(status)
        return real_mark(db, row, status=status, **kwargs)

    monkeypatch.setattr(PipelineRunObserver, "emit", emit)
    monkeypatch.setattr(auto_train_service, "_mark", mark)
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": ("data.csv", frame.to_csv(index=False).encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    upload_id = response.json()["run_id"]
    run_auto_train_job(db_session, upload_id)
    db_session.expire_all()
    upload = db_session.get(ClientLabUpload, upload_id)
    return {"final_status": upload.pipeline_status, "marks": marks, "events": events}


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_auto_train_event_sequence_is_unchanged(scenario, monkeypatch, auth_client, db_session):
    if scenario == "profiling_error_failed":
        def boom(*_args, **_kwargs):
            raise RuntimeError("synthetic profiling failure")

        monkeypatch.setattr(auto_train_service, "profile_frame", boom)
    observed = _capture(monkeypatch, auth_client, db_session, SCENARIOS[scenario]())
    golden = json.loads(SNAPSHOT.read_text()) if SNAPSHOT.exists() else {}
    if os.environ.get("UPDATE_AUTO_TRAIN_EVENT_SNAPSHOT"):
        golden[scenario] = observed
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
    assert scenario in golden, "run with UPDATE_AUTO_TRAIN_EVENT_SNAPSHOT=1 to create the snapshot"
    assert observed == golden[scenario]
