"""P4.10-A: GET /v1/experiments/{id}/findings over real auto-train runs.

Real worker jobs (``process_next_job``), no engine mocks. The five trust checks are
stored on the run result and, for non-pass checks, as ``data_quality_findings`` rows
(``evidence.source = "investigate"``) before the scientific evidence lock.
"""

from __future__ import annotations

import json
import re
from uuid import UUID

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import DataQualityFinding, Experiment
from app.domain.findings import FINDING_CHECKS
from app.services.service_token_service import create_service_token
from test_v1_contract_conventions import _assert_envelope
from test_v1_resources_experiments import _csv, _h, _key, _root, _work, setup  # noqa: F401 (fixture)

HOLDOUT_KEY = re.compile(r"holdout|final_test|test_metrics", re.IGNORECASE)


def _upload(client, setup, payload: bytes) -> str:  # noqa: F811
    response = client.post(
        "/v1/datasets",
        headers=_h(setup, key=_key()),
        data={"project_id": str(setup["alpha_project"].id)},
        files={"file": ("rows.csv", payload, "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _planted(n: int = 240, copies: int = 60, seed: int = 21) -> bytes:
    """A direct copy of the label (leak) and records repeated under a new id."""

    frame = pd.read_csv(pd.io.common.BytesIO(_csv(n=n, seed=seed)))
    frame["outcome_code"] = np.where(frame["label"] == "yes", 1, 0)
    frame = pd.concat([frame, frame.iloc[:copies]], ignore_index=True)
    frame.insert(0, "record_id", [f"rec-{index:05d}" for index in range(len(frame))])
    return frame.to_csv(index=False).encode()


def _run(client, db, setup, payload: bytes) -> str:  # noqa: F811
    created = _root(client, setup, _upload(client, setup, payload))
    assert created.status_code == 202, created.text
    experiment_id = created.json()["id"]
    assert _work(db, UUID(experiment_id)).status == "completed"
    return experiment_id


def _checks(body: dict) -> dict[str, dict]:
    return {item["check"]: item for item in body["checks"]}


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for item in value.values() for k in _keys(item)}
    if isinstance(value, list):
        return {k for item in value for k in _keys(item)}
    return set()


def test_planted_leak_and_duplicates_produce_findings(client, db_session, setup):  # noqa: F811
    db = db_session
    experiment_id = _run(client, db, setup, _planted())
    response = client.get(f"/v1/experiments/{experiment_id}/findings", headers=_h(setup))
    assert response.status_code == 200, response.text
    assert response.headers["ETag"]
    body = response.json()
    assert body["investigated"] is True and body["version"] == "investigate.v1"
    assert [item["check"] for item in body["checks"]] == list(FINDING_CHECKS)
    summary = body["summary"]
    assert summary["passed"] + summary["warnings"] + summary["failures"] == 5 and summary["not_evaluated"] == 0
    checks = _checks(body)
    assert not [item for item in body["checks"]
                if item["status"] == "not_evaluated" or "not_evaluated_reason" in item["evidence"]]

    leakage = checks["target_leakage"]
    assert leakage["status"] == "warning" and leakage["recommendation_kind"] == "review_columns"
    assert "outcome_code" in leakage["evidence"]["excluded_columns"]
    assert "record_id" not in leakage["evidence"]["excluded_columns"]  # an identifier, not a leak
    assert "outcome_code" in leakage["message"]
    duplicates = checks["duplicate_rows"]
    assert duplicates["status"] in {"warning", "fail"} and duplicates["recommendation_kind"] == "deduplicate"
    assert duplicates["evidence"]["train_duplicate_rows"] + duplicates["evidence"]["train_test_duplicate_rows"] > 0
    assert "record_id" not in str(duplicates["evidence"])  # hashed on the model columns only
    for item in body["checks"]:
        assert item["message"] and item["severity"] in {"info", "warning", "error", "critical"}

    # Stored in the existing findings owner before the lock; locked rows are immutable.
    db.expire_all()
    experiment = db.get(Experiment, UUID(experiment_id))
    assert experiment.scientific_evidence_locked_at is not None
    rows = list(db.scalars(select(DataQualityFinding).where(
        DataQualityFinding.pipeline_run_id == experiment.id,
        DataQualityFinding.evidence["source"].astext == "investigate",
    )))
    by_check = {row.evidence["check"]: row for row in rows}
    assert {"target_leakage", "duplicate_rows"} <= set(by_check)
    assert by_check["duplicate_rows"].finding_type == "duplicates"
    assert {row.evidence["check"] for row in rows} == {
        c for c, item in checks.items() if item["status"] in {"warning", "fail"}}
    assert all(row.created_at <= experiment.scientific_evidence_locked_at for row in rows)
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text("UPDATE data_quality_findings SET severity = 'info' WHERE id = :id"),
                       {"id": by_check["target_leakage"].id})

    # Tenant isolation: another workspace never sees the run.
    beta = _h(setup, setup["beta_admin"], workspace=setup["beta"])
    _assert_envelope(client.get(f"/v1/experiments/{experiment_id}/findings", headers=beta), 404, "not_found")

    # A read-scoped service token reads the same checks and no holdout value.
    token = create_service_token(db, creator=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="r",
                                 scopes=("read",), expires_in_days=1, current_password="test-password")[1]
    db.commit()
    as_reader = client.get(f"/v1/experiments/{experiment_id}/findings",
                           headers={"Authorization": f"Bearer {token}"})
    assert as_reader.status_code == 200, as_reader.text
    agent = as_reader.json()
    assert [item["status"] for item in agent["checks"]] == [item["status"] for item in body["checks"]]
    assert not {key for key in _keys(agent) if HOLDOUT_KEY.search(key)}
    holdout = {round(float(v), 6) for v in (experiment.result.get("test_metrics") or {}).values()
               if isinstance(v, float)}
    numbers = {round(float(v), 6) for item in agent["checks"] for v in item["evidence"].values()
               if isinstance(v, float)}
    assert not (holdout & numbers) or not holdout

    # Runs finished before P4.10-A: investigated false, no checks.
    result = dict(experiment.result)
    result.pop("investigation")
    experiment.result = result
    db.commit()
    old = client.get(f"/v1/experiments/{experiment_id}/findings", headers=_h(setup)).json()
    assert old["investigated"] is False and old["checks"] == [] and old["version"] is None


def test_clean_run_has_no_leak_or_duplicate_findings(client, db_session, setup):  # noqa: F811
    experiment_id = _run(client, db_session, setup, _csv(seed=5))
    body = client.get(f"/v1/experiments/{experiment_id}/findings", headers=_h(setup)).json()
    checks = _checks(body)
    assert body["summary"]["not_evaluated"] == 0
    assert not [item for item in body["checks"] if "not_evaluated_reason" in item["evidence"]]
    assert checks["target_leakage"]["status"] == "pass"
    assert checks["duplicate_rows"]["status"] == "pass"
    assert checks["implausible_score"]["status"] == "pass"
    assert checks["class_imbalance"]["status"] == "pass"
    assert checks["overfit_gap"]["evidence"]["metric"] == checks["implausible_score"]["evidence"]["metric"]
    assert json.dumps(body)  # serializable, bounded evidence


def test_findings_of_unknown_experiment_is_404(client, setup):  # noqa: F811
    missing = "00000000-0000-0000-0000-000000000000"
    _assert_envelope(client.get(f"/v1/experiments/{missing}/findings", headers=_h(setup)), 404, "not_found")
