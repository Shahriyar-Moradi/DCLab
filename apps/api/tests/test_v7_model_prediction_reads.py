"""V7 BE-B: model, prediction and monitoring reads for the v7 screens.

B2 project model versions (CV only, in use / waiting / approved earlier), B3 the feature contract
(names and types, never values), B4 the stored package verification, B5 the flagged count, B6
the capped rows page of a predictions file (same authorization as the download). Real worker
jobs for the trained cases; the seeded graph for the version states.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pandas as pd
import pytest
from sqlalchemy import select

from app.db.models import (
    Artifact,
    EvaluationMetric,
    ModelEvaluation,
    ModelSelectionDecision,
    ModelVersion,
    WorkspaceRole,
)
from app.engine.explain import REASON_COLUMNS
from app.engine.serving import model_package as mp
from app.engine.serving.batch_scoring import flagged_summary
from app.services import project_ref_service as prs
from app.services.artifact_service import read_artifact_bytes
from app.services.batch_prediction_service import _row_text
from app.services.project_ref_service import RefMove
from app.services.service_token_service import create_service_token
from test_decision_record_service import _bootstrap, _force_lock, _holdout, g  # noqa: F401 (fixture)
from test_v1_batch_predictions import _download, _frame, _predict, _trained, _upload, _with_inf, _work
from test_v1_contract_conventions import _assert_envelope, _headers
from test_v1_resources_experiments import _h, _member, setup  # noqa: F401 (fixture)

PAGE_KEYS = {"prediction_id", "task_type", "order", "entity_column", "decision_threshold", "rows_total",
             "rows_reachable", "limit", "next_cursor", "items", "reasons", "notes", "untrusted_fields"}
ROW_KEYS = {"row_number", "entity", "probability", "flagged", "label", "prediction", "reasons"}
VERSION_ITEM_KEYS = {"id", "version", "created_at", "experiment_id", "candidate_key", "family", "algorithm",
                     "split_plan_id", "saved_format", "selection_metric", "cv", "cv_at_locked_threshold",
                     "decision_threshold", "status", "in_use", "approved_for_scoring", "waiting_for_approval",
                     "pending_decision_id", "last_approved_at"}
CONTRACT_KEYS = {"model_version_id", "status", "contract_format", "column_count", "columns", "target_column",
                 "unknown_columns", "unseen_categories", "contract_digest", "manifest_digest", "untrusted_fields"}
VERIFICATION_KEYS = {"status", "checked_at", "checks", "rows_compared", "row_source", "comparison", "tolerance",
                     "max_abs_difference", "note"}


def _token(db, setup) -> dict[str, str]:  # noqa: F811
    secret = create_service_token(db, creator=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="v7b",
                                  scopes=("read",), expires_in_days=1, current_password="test-password")[1]
    db.commit()
    return {"Authorization": f"Bearer {secret}"}


def _ok(response) -> dict:
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "private, no-store"
    return response.json()


def _all_rows(client, headers, prediction_id, order: str) -> list[dict]:
    page = _ok(client.get(f"/v1/predictions/{prediction_id}/rows", headers=headers,
                          params={"order": order, "limit": 25}))
    items, cursor = list(page["items"]), page["next_cursor"]
    while cursor:
        page = _ok(client.get(f"/v1/predictions/{prediction_id}/rows", headers=headers,
                              params={"order": order, "limit": 200, "cursor": cursor}))
        items += page["items"]
        cursor = page["next_cursor"]
    return items


def test_binary_contract_verification_flagged_and_rows(client, db_session, setup, monkeypatch):  # noqa: F811
    db = db_session
    frame = _frame("binary")
    experiment, mv_id = _trained(client, db, setup, frame, "label")
    token, beta = _token(db, setup), _h(setup, setup["beta_admin"], workspace=setup["beta"])
    viewer = _h(setup, _member(db, setup, WorkspaceRole.VIEWER))

    # B4: the worker's build-time checks, stored with the model artifact and in the package manifest.
    version = client.get(f"/v1/model-versions/{mv_id}", headers=_h(setup)).json()
    checked = version["package_verification"]
    assert set(checked) == VERIFICATION_KEYS and checked["status"] == "verified"
    assert [(c["check"], c["passed"]) for c in checked["checks"]] == [
        ("opens", True), ("digests", True), ("same_predictions", True)]
    n_pool = len(frame) - int(experiment.result["best_single"]["n_test_rows"])
    assert checked["rows_compared"] == min(mp.PARITY_ROWS, n_pool) == 100
    assert (checked["row_source"], checked["comparison"], checked["tolerance"], checked["max_abs_difference"]) == (
        "training_partition", "exact", 0.0, 0.0)
    model = db.get(ModelVersion, mv_id)
    artifact = db.get(Artifact, model.model_artifact_id)
    manifest = mp.read_package(read_artifact_bytes(db, workspace_id=setup["alpha"].id, artifact_id=artifact.id)).manifest
    assert manifest["verification"] == artifact.extra_metadata["verification"]
    assert manifest["verification"]["members_digest"] == mp.members_digest(manifest)
    assert client.get(f"/v1/model-versions/{mv_id}", headers=token).json()["package_verification"] == checked

    # B3: names and types from the stored manifest; never an allowed value or a date format.
    response = client.get(f"/v1/model-versions/{mv_id}/feature-contract", headers=_h(setup))
    contract = _ok(response)
    assert set(contract) == CONTRACT_KEYS and contract["status"] == "available"
    assert [c["name"] for c in contract["columns"]] == manifest["features"] == experiment.result["best_single"]["features"]
    assert all(set(c) == {"name", "role", "training_dtype", "required", "is_date"} for c in contract["columns"])
    by_name = {c["name"]: c for c in contract["columns"]}
    assert by_name["plan"]["role"] == "categorical" and by_name["signup_date"]["is_date"] is True
    assert contract["target_column"] == "label" and contract["unknown_columns"] == "ignored_and_listed"
    assert contract["manifest_digest"] == db.get(Artifact, model.feature_manifest_artifact_id).content_digest
    assert "basic" not in response.text and "%Y" not in response.text and "allowed_values" not in response.text
    assert _ok(client.get(f"/v1/model-versions/{mv_id}/feature-contract", headers=token)) == contract
    _assert_envelope(client.get(f"/v1/model-versions/{mv_id}/feature-contract", headers=beta), 404, "not_found")

    # B2: this run's version, CV only (the read's final-test figures never appear).
    listed = _ok(client.get(f"/v1/projects/{experiment.project_id}/model-versions", headers=_h(setup)))
    item = next(row for row in listed["items"] if row["id"] == str(mv_id))
    assert set(item) == VERSION_ITEM_KEYS and item["cv"] == version["metrics"]["cv"] and item["cv"]
    assert item["in_use"] == version["is_champion"] and item["approved_for_scoring"] == (version["release"] is not None)
    assert item["decision_threshold"] == pytest.approx(experiment.result["decision_threshold"]["value"])
    assert "holdout" not in json.dumps(listed) and "final_test" not in json.dumps(listed)
    _assert_envelope(client.get(f"/v1/projects/{experiment.project_id}/model-versions", headers=beta), 404, "not_found")

    # B6 before the scoring completes; then B5 + B6 on the completed file.
    scoring = _upload(client, setup, _with_inf(frame).assign(notes="free text"))
    prediction_id = _predict(client, setup, mv_id, scoring["id"]).json()["id"]
    rows_path = f"/v1/predictions/{prediction_id}/rows"
    _assert_envelope(client.get(rows_path, headers=_h(setup)), 409, "prediction_not_ready")
    assert _work(db, prediction_id).status == "completed"
    done = client.get(f"/v1/predictions/{prediction_id}", headers=_h(setup)).json()
    out = _download(client, setup, prediction_id)
    flagged = done["flagged"]
    assert set(flagged) == {"count", "rows", "share", "decision_threshold"}
    assert (flagged["count"], flagged["rows"]) == (int(out["label"].sum()), len(frame))
    assert flagged["share"] == pytest.approx(flagged["count"] / len(frame), abs=1e-6)
    assert flagged["decision_threshold"] == pytest.approx(done["decision_threshold"])

    page = _ok(client.get(rows_path, headers=_h(setup), params={"limit": 25}))
    assert set(page) == PAGE_KEYS and (page["task_type"], page["order"], page["entity_column"]) == (
        "binary", "score", "customer_id")
    assert (page["rows_total"], page["rows_reachable"], len(page["items"])) == (len(frame), len(frame), 25)
    assert page["reasons"] == done["reasons"]
    items = _all_rows(client, _h(setup), prediction_id, "score")
    file_order = out.sort_values(["probability", "row_number"], ascending=[False, True], kind="mergesort")
    assert [row["row_number"] for row in items] == file_order["row_number"].tolist()
    source = out.set_index("row_number")
    for row in items:  # exactly the file's cells, nothing more
        assert set(row) == ROW_KEYS
        cells = source.loc[row["row_number"]]
        assert row["entity"] == cells["customer_id"] and row["flagged"] == bool(cells["label"] == 1)
        assert row["probability"] == pytest.approx(float(cells["probability"]), abs=1e-12)
        assert row["label"] is None and row["prediction"] is None
        assert row["reasons"] == [cells[name] for name in REASON_COLUMNS if isinstance(cells[name], str)]
    in_file_order = _ok(client.get(rows_path, headers=_h(setup), params={"order": "row", "limit": 3}))
    assert [row["row_number"] for row in in_file_order["items"]] == [1, 2, 3]

    # Caps: page size, cursors bound to the prediction and the order, reachable rows, file size.
    _assert_envelope(client.get(rows_path, headers=_h(setup), params={"limit": 201}), 422, "validation_failed")
    for cursor in (in_file_order["next_cursor"], "c1.e30.forged", "not-a-cursor"):
        _assert_envelope(client.get(rows_path, headers=_h(setup), params={"cursor": cursor}), 400, "invalid_cursor")
    monkeypatch.setattr("app.services.batch_prediction_service.ROWS_REACHABLE_MAX", 30)
    capped = _all_rows(client, _h(setup), prediction_id, "score")
    assert [row["row_number"] for row in capped] == file_order["row_number"].tolist()[:30]
    monkeypatch.setattr("app.services.batch_prediction_service.ROWS_FILE_MAX_BYTES", 10)
    _assert_envelope(client.get(rows_path, headers=_h(setup)), 409, "prediction_rows_too_large")
    monkeypatch.undo()
    monkeypatch.setattr("app.services.batch_prediction_service.ROWS_FILE_MAX_ROWS", len(frame) - 1)
    _assert_envelope(client.get(rows_path, headers=_h(setup)), 409, "prediction_rows_too_large")
    monkeypatch.undo()
    monkeypatch.setattr("app.services.batch_prediction_service._ROWS_READS", threading.BoundedSemaphore(1))
    monkeypatch.setattr("app.services.batch_prediction_service._ROWS_WAIT_SECONDS", 0.01)
    import app.services.batch_prediction_service as bps

    bps._ROWS_READS.acquire()  # every parse slot taken
    _assert_envelope(client.get(rows_path, headers=_h(setup)), 429, "rows_busy")
    bps._ROWS_READS.release()
    monkeypatch.undo()

    # Authorization = the download's: workspace readers and read tokens; another tenant is 404.
    first = _ok(client.get(rows_path, headers=_h(setup), params={"limit": 5}))
    assert _ok(client.get(rows_path, headers=viewer, params={"limit": 5}))["items"] == first["items"]
    assert _ok(client.get(rows_path, headers=token, params={"limit": 5}))["items"] == first["items"]
    _assert_envelope(client.get(rows_path, headers=beta), 404, "not_found")
    _assert_envelope(client.get(f"/v1/predictions/{uuid4()}/rows", headers=_h(setup)), 404, "not_found")
    assert client.get(rows_path).status_code == 401


def test_multiclass_parquet_rows_and_no_flagged_count(client, db_session, setup):  # noqa: F811
    db = db_session
    frame = _frame("multiclass", seed=4)
    experiment, mv_id = _trained(client, db, setup, frame, "tier")
    card = client.get(f"/v1/model-versions/{mv_id}/card", headers=_h(setup)).json()
    assert card["final_evaluation"]["status"] == "reported" and card["final_evaluation"]["confusion"] is None
    scoring = _upload(client, setup, frame.drop(columns=["tier"]).assign(customer_id=[f"m{i:04d}" for i in range(240)]))
    prediction_id = _predict(client, setup, mv_id, scoring["id"], output_format="parquet").json()["id"]
    assert _work(db, prediction_id).status == "completed"
    assert client.get(f"/v1/predictions/{prediction_id}", headers=_h(setup)).json()["flagged"] is None
    out = _download(client, setup, prediction_id)
    items = _all_rows(client, _h(setup), prediction_id, "row")
    assert [row["row_number"] for row in items] == list(range(1, 241))
    labels = sorted(experiment.result["class_labels"])
    for row, (_, cells) in zip(items, out.iterrows()):
        top = max(float(cells[f"probability_{label}"]) for label in labels)
        assert row["label"] == cells["label"] and row["probability"] == pytest.approx(top, abs=1e-12)
        assert row["flagged"] is None and row["prediction"] is None and row["entity"] == cells["customer_id"]


def test_version_states_waiting_approved_and_cv_only(client, db_session, g):  # noqa: F811
    db = db_session
    headers = _headers(g.actor, g.ws)
    path = f"/v1/projects/{g.project.id}/model-versions"
    # A locked winner with CV evidence (its final holdout roc_auc is seeded at 0.81 and must never show).
    cand1 = db.scalar(select(ModelVersion.selected_candidate_id).where(ModelVersion.id == g.mv[1]))
    db.add(ModelSelectionDecision(workspace_id=g.ws, project_id=g.project.id, pipeline_run_id=g.exp[1],
                                  selected_candidate_id=cand1, selection_metric="roc_auc", selected_score=0.7123,
                                  selection_policy="max cv", reason="cv", evidence={},
                                  locked_at=datetime(2026, 9, 2, tzinfo=UTC)))
    evaluation = ModelEvaluation(workspace_id=g.ws, project_id=g.project.id, candidate_id=cand1,
                                 evaluation_type="cross_validation", evaluation_scope="cv_aggregate",
                                 dataset_id=g.prepared.id, status="completed", summary={})
    db.add(evaluation)
    db.flush()
    db.add(EvaluationMetric(model_evaluation_id=evaluation.id, metric_name="roc_auc", metric_value=0.7123))
    db.commit()
    _bootstrap(db, g)  # a person puts v1 in use (one release)
    _force_lock(db, g.exp[1])
    proposal = prs.propose_ref_move(
        db, workspace_id=g.ws, project_id=g.project.id, actor=g.agent,
        moves=[RefMove("champion_model", g.mv[1], 1), RefMove("feature_recipe", g.fsv[1], 1)],
        rationale="v2 beats v1", evidence_refs=[{"kind": "experiment", "id": str(g.exp[1])}])
    db.commit()

    page = _ok(client.get(path, headers=headers))
    states = {UUID(item["id"]): item for item in page["items"]}
    assert UUID(page["model_in_use_id"]) == g.mv[0] and set(states) == set(g.mv.values())
    order = [(item["created_at"], UUID(item["id"])) for item in page["items"]]
    assert order == sorted(order, reverse=True)  # newest first, ties by id
    v1, v2, v4 = states[g.mv[0]], states[g.mv[1]], states[g.mv[3]]
    assert (v1["status"], v1["in_use"], v1["approved_for_scoring"], v1["waiting_for_approval"]) == (
        "in_use", True, True, False)
    assert v1["last_approved_at"] is not None
    assert (v2["status"], v2["waiting_for_approval"], v2["pending_decision_id"]) == (
        "waiting_for_approval", True, str(proposal.id))
    assert v2["cv"] == {"roc_auc": 0.7123} and v2["selection_metric"] == "roc_auc"
    assert "0.81" not in json.dumps(page)
    assert (v4["status"], v4["cv"]) == ("not_in_use", {})
    seeded = client.get(f"/v1/model-versions/{g.mv[0]}", headers=headers).json()["package_verification"]
    assert seeded["status"] == "not_recorded" and seeded["checks"] == []  # a package saved before B4

    # Paging: cursors bound to the project.
    first = _ok(client.get(path, headers=headers, params={"limit": 1}))
    second = _ok(client.get(path, headers=headers, params={"limit": 1, "cursor": first["next_cursor"]}))
    assert [first["items"][0]["id"], second["items"][0]["id"]] == [item["id"] for item in page["items"]][:2]
    assert page["next_cursor"] is None and first["limit"] == 1
    tampered = first["next_cursor"][:-2] + ("AA" if not first["next_cursor"].endswith("AA") else "BB")
    _assert_envelope(client.get(path, headers=headers, params={"cursor": tampered}), 400, "invalid_cursor")

    # The person accepts: v2 in use and approved, v1 approved earlier.
    prs.move_ref(db, workspace_id=g.ws, project_id=g.project.id, actor=g.human, proposal_id=proposal.id,
                 moves=[RefMove("champion_model", g.mv[1], 1), RefMove("feature_recipe", g.fsv[1], 1)],
                 rationale="reviewed", evidence_refs=_holdout(g, 1))
    db.commit()
    states = {UUID(item["id"]): item for item in _ok(client.get(path, headers=headers))["items"]}
    assert (states[g.mv[1]]["status"], states[g.mv[1]]["approved_for_scoring"]) == ("in_use", True)
    assert (states[g.mv[0]]["status"], states[g.mv[0]]["in_use"]) == ("approved_earlier", False)
    assert states[g.mv[1]]["pending_decision_id"] is None


# --- pure helpers ---------------------------------------------------------------------------------


def test_flagged_summary_counts_label_one_for_binary_only():
    table = pd.DataFrame({"row_number": [1, 2, 3], "probability": [0.9, 0.2, 0.6], "label": [1, 0, 1]})
    binary = SimpleNamespace(task_type="binary", decision_threshold=0.5)
    assert flagged_summary(table, binary) == {"count": 2, "rows": 3, "share": 0.666667, "decision_threshold": 0.5}
    assert flagged_summary(table.iloc[0:0], binary) == {"count": 0, "rows": 0, "share": None,
                                                        "decision_threshold": 0.5}
    assert flagged_summary(table, SimpleNamespace(task_type="multiclass", decision_threshold=None)) is None


@pytest.mark.parametrize(("value", "expected"), [
    ("c00012", "c00012"), ("  a\tb\nc ", "a b c"), ("x\x1b[2Jy", "x [2Jy"), ("=HYPERLINK(1)", "=HYPERLINK(1)"),
    ("", None), (None, None), (float("nan"), None), ("y" * 400, "y" * 253 + "..."),
])
def test_row_cells_are_inert_plain_text(value, expected):
    assert _row_text(value, 256) == expected
