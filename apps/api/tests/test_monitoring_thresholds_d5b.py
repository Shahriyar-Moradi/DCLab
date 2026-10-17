"""D5b (ADR 0012 D4): editable monitoring thresholds — bounds, authority, History, future-only use.

Pure checks cover the floors' significance gate (an exact one-sided binomial test, Bonferroni over
the floors set) and the PSI lines; API checks cover bounds, who may change them (owners and
administrators only, never ML engineers, viewers, platform staff or tokens), If-Match, the History
entry and tenancy; the end to end trains a real model, scores a drifted file and shows that a closed
week keeps the values stored on it (also when linked outcomes recompute it) while the open week uses
the new values and stores them, with floors judged on real outcomes.
"""

from __future__ import annotations

import math
from datetime import timedelta
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import get_settings
from app.db.models import (
    MonitoringThresholdSet,
    MonitoringWindow,
    ProjectDecisionRecord,
    UserRole,
    WorkspaceRole,
)
from app.domain import monitoring as dm
from app.engine.serving import monitoring as mon
from app.services import monitoring_service as ms
from app.services.auth_service import create_user
from app.services.service_token_service import create_service_token
from test_monitoring import _link, _recompute, _run_monitoring, _windows
from test_v1_batch_predictions import _frame, _predict, _trained, _upload, _work
from test_v1_contract_conventions import _assert_envelope
from test_v1_resources_experiments import _h, _key, _member, setup  # noqa: F401 (fixture)

READ_KEYS = {"project_id", "version", "etag", "psi_watch", "psi_alert", "precision_floor", "recall_floor",
             "defaults", "bounds", "changed_at", "changed_by_user_id", "can_edit", "notes"}
VALUE_KEYS = {"psi_watch", "psi_alert", "precision_floor", "recall_floor"}
BOUND_KEYS = {"psi_watch_min", "psi_watch_max", "psi_alert_max", "floor_min", "floor_max"}
IN_FORCE_KEYS = {"psi_watch", "psi_alert", "significance_level", "flagged_share_relative_watch",
                 "flagged_share_absolute_watch", "min_rows", "min_labels", "min_per_class", "precision_floor",
                 "recall_floor", "version", "changed_at", "changed_by_user_id"}
USED_KEYS = {"version", "psi_watch", "psi_alert", "precision_floor", "recall_floor"}
CHECK_KEYS = {"floor", "status", "reason_code", "value", "successes", "trials", "p_value"}
DEFAULTS = {"version": 0, "psi_watch": 0.1, "psi_alert": 0.2, "precision_floor": None, "recall_floor": None}
V1 = {"psi_watch": 0.3, "psi_alert": 0.6, "precision_floor": 0.95, "recall_floor": 0.95}


def _body(values: dict, rationale: str = "fewer false alarms") -> dict:
    return {**values, "rationale": rationale}


def _put(client, setup, values: dict, if_match: str | None, *, user=None, headers=None, project_id=None):  # noqa: F811
    head = headers or _h(setup, user, key=_key())
    if if_match is not None:
        head = {**head, "If-Match": if_match}
    pid = project_id or setup["alpha_project"].id
    return client.put(f"/v1/projects/{pid}/monitoring-thresholds", json=_body(values), headers=head)


def _token(db, setup) -> str:  # noqa: F811
    secret = create_service_token(db, creator=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="d5b",
                                  scopes=("read", "experiments:write"), expires_in_days=1,
                                  current_password="test-password")[1]
    db.commit()
    return secret


def _hand_binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


# --- pure: the floors' gate and the PSI lines ------------------------------------------------------


def test_floors_are_judged_only_on_measured_binary_outcomes_and_clearly_below():
    measured = {"status": "measured", "confusion": {"tn": 50, "fp": 40, "fn": 5, "tp": 20}}
    assert mon.floor_checks(measured, "binary", {"precision": None, "recall": None}) is None  # no floor: nothing
    one = mon.floor_checks(measured, "binary", {"precision": 0.6, "recall": None})
    assert set(one) == {"test", "alpha", "per_floor_alpha", "precision"} and set(one["precision"]) == CHECK_KEYS
    check = one["precision"]
    assert (check["status"], check["successes"], check["trials"], check["value"]) == ("below", 20, 60, 0.3333)
    assert check["p_value"] == pytest.approx(_hand_binom_cdf(20, 60, 0.6), abs=1e-6) and one["per_floor_alpha"] == 0.01
    both = mon.floor_checks(measured, "binary", {"precision": 0.3, "recall": 0.6})
    assert both["per_floor_alpha"] == 0.005  # Bonferroni over the floors set
    assert both["precision"]["status"] == "ok" and both["precision"]["p_value"] is None  # at or above: ok
    assert both["recall"]["status"] == "ok"  # 20 / 25 = 0.8
    # Under the floor on few rows: within what chance allows, never an alarm.
    small = mon.floor_checks({"status": "measured", "confusion": {"tn": 50, "fp": 4, "fn": 5, "tp": 2}}, "binary",
                             {"precision": 0.6, "recall": 0.6})
    assert [small[name]["status"] for name in ("precision", "recall")] == ["not_judged", "not_judged"]
    assert {small[name]["reason_code"] for name in ("precision", "recall")} == {"within_chance"}
    assert small["precision"]["p_value"] == pytest.approx(_hand_binom_cdf(2, 6, 0.6), abs=1e-6)
    # Outcomes not linked / not measured, another task, no flagged rows or no positives: not judged.
    for outcomes, task, reason in (({"status": "not_linked"}, "binary", "outcomes_not_measured"),
                                   ({"status": "not_enough_labels", "n_labelled": 40}, "binary",
                                    "outcomes_not_measured"),
                                   ({"status": "measured", "metrics": {"accuracy": 0.7}}, "multiclass", "not_binary")):
        checks = mon.floor_checks(outcomes, task, {"precision": 0.5, "recall": 0.5})
        assert {(c["status"], c["reason_code"]) for c in (checks["precision"], checks["recall"])} == {
            ("not_judged", reason)}
        assert mon.floors_below(checks) == [] and dm.floor_status({"floors": checks}) == "not_judged"
    none = mon.floor_checks({"status": "measured", "confusion": {"tn": 90, "fp": 0, "fn": 0, "tp": 0}}, "binary",
                            {"precision": 0.5, "recall": 0.5})
    assert (none["precision"]["reason_code"], none["recall"]["reason_code"]) == ("no_flagged_rows",
                                                                                  "no_positive_outcomes")
    # Too few of the floor's own rows (min per class): not judged, whatever the rate.
    for tp, fp in ((0, 1), (1, 1), (0, 3)):
        few = mon.floor_checks({"status": "measured", "confusion": {"tn": 90, "fp": fp, "fn": 30, "tp": tp}},
                               "binary", {"precision": 0.9, "recall": None}, min_trials=10)
        assert (few["precision"]["status"], few["precision"]["reason_code"]) == ("not_judged", "too_few_flagged_rows")
    few_recall = mon.floor_checks({"status": "measured", "confusion": {"tn": 90, "fp": 30, "fn": 2, "tp": 1}},
                                  "binary", {"precision": None, "recall": 0.9}, min_trials=10)
    assert few_recall["recall"]["reason_code"] == "too_few_positive_outcomes"
    # Exactly at the floor is ok; the test is strict at its boundary (p == alpha is not below).
    at = mon.floor_checks({"status": "measured", "confusion": {"tn": 9, "fp": 4, "fn": 1, "tp": 6}}, "binary",
                          {"precision": 0.6, "recall": None})
    assert (at["precision"]["status"], at["precision"]["value"]) == ("ok", 0.6)
    from scipy.stats import binom

    exact = float(binom.cdf(20, 60, 0.6))  # the very p the gate computes
    assert exact == pytest.approx(_hand_binom_cdf(20, 60, 0.6), rel=1e-9)
    edge = mon.floor_checks(measured, "binary", {"precision": 0.6, "recall": None}, alpha=exact)
    assert (edge["precision"]["status"], edge["precision"]["reason_code"]) == ("not_judged", "within_chance")
    unread = mon.floor_checks({"status": "measured", "metrics": {"precision": 0.5}}, "binary",
                              {"precision": 0.6, "recall": None})
    assert unread["precision"]["reason_code"] == "confusion_unavailable"
    assert "not available" in dm.floor_sentences(unread)[0]
    from app.services.notification_service import _headline

    assert _headline("drift_alert", "below_floor").startswith("Real outcomes fell below a floor")
    assert _headline("drift_alert", "inputs_moved").startswith("Weekly monitoring found")
    assert mon.floors_below(one) == ["precision"] and dm.floor_status({"floors": one}) == "below"
    assert dm.floor_status({"floors": both}) == "ok" and dm.floor_status({"status": "measured"}) is None
    words = dm.floor_sentences(one)
    assert words == ["Precision on real outcomes is 33% (20 of 60), below the floor of 60% set for this project, "
                     "and clearly so for this many rows."]
    assert "within what chance allows" in dm.floor_sentences(small)[0]
    assert dm.floor_summary(one) == "Real outcomes are below the floor set for this project: precision 33% (floor 60%)."
    finding = dm.finding_for("alert", "s", {"features": []}, {"status": "ok"}, {"floors": one})
    assert finding is not None and finding.code == "performance_below_floor"
    assert dm.finding_for("alert", "s", {"features": []}, {"status": "alert"}, {"floors": one}).code == (
        "scores_look_different")  # a score alert keeps its own finding


def test_psi_lines_follow_the_values_passed_and_default_to_the_code_constants():
    assert (mon.PSI_WATCH, mon.PSI_ALERT) == (DEFAULTS["psi_watch"], DEFAULTS["psi_alert"]) == (
        ms.default_limits()["psi_watch"], ms.default_limits()["psi_alert"])
    assert [mon.psi_status(v, 0.3, 0.6) for v in (0.25, 0.3, 0.59, 0.6)] == ["ok", "watch", "watch", "alert"]
    assert mon.gated_status(0.7, 0.2, 0.01, 0.3, 0.6) == "ok" and mon.gated_status(0.7, 0.001, 0.01, 0.3, 0.6) == "alert"
    assert dm.feature_sentence({"name": "x", "kind": "numeric", "psi": 0.2, "status": "ok"}, 0.3) == "x moved little (PSI 0.20)."
    assert "within what chance" in dm.feature_sentence({"name": "x", "kind": "numeric", "psi": 0.2, "status": "ok"})


# --- API: bounds, authority, If-Match, History, tenancy ------------------------------------------


def test_bounds_are_fixed_and_checked_by_the_api_and_the_database(client, db_session, setup):  # noqa: F811
    valid = {"psi_watch": 0.1, "psi_alert": 0.2, "precision_floor": None, "recall_floor": None}
    for change in ({"psi_watch": 0.04}, {"psi_watch": 0.51}, {"psi_alert": 0.1}, {"psi_alert": 0.05},
                   {"psi_alert": 1.01}, {"precision_floor": 0.0}, {"recall_floor": 1.0}, {"precision_floor": 0.005}):
        response = _put(client, setup, {**valid, "psi_watch": 0.2, "psi_alert": 0.4, **change}, '"0"')
        assert response.status_code == 422, (change, response.text)
    pid = setup["alpha_project"].id
    head = {**_h(setup, key=_key()), "If-Match": '"0"'}
    missing = client.put(f"/v1/projects/{pid}/monitoring-thresholds",
                         json={"psi_watch": 0.2, "psi_alert": 0.4, "precision_floor": 0.5, "rationale": "x"},
                         headers=head)
    assert missing.status_code == 422  # every value is explicit (null switches a floor off)
    extra = client.put(f"/v1/projects/{pid}/monitoring-thresholds",
                       json={**_body({**valid, "psi_alert": 0.3}), "significance_level": 0.5}, headers=head)
    assert extra.status_code == 422  # the code-owned values are not editable
    no_reason = client.put(f"/v1/projects/{pid}/monitoring-thresholds", json={**valid, "psi_alert": 0.3},
                           headers=head)
    assert no_reason.status_code == 422
    # Edges are inside the bounds.
    edge = _put(client, setup, {"psi_watch": 0.5, "psi_alert": 1.0, "precision_floor": 0.01, "recall_floor": 0.99},
                '"0"')
    assert edge.status_code == 200, edge.text
    # The database refuses what the API refuses (backstop), and rows never change.
    db = db_session
    row = db.scalar(select(MonitoringThresholdSet).where(MonitoringThresholdSet.project_id == pid))
    assert edge.json()["version"] == row.version == 1
    for column, value in (("psi_watch", 0.6), ("psi_alert", 0.4), ("precision_floor", 1.0)):
        values = {"psi_watch": 0.5, "psi_alert": 1.0, column: value}
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(MonitoringThresholdSet(
                    workspace_id=row.workspace_id, project_id=pid, version=2,
                    decision_record_id=row.decision_record_id, created_by_user_id=row.created_by_user_id, **values))
                db.flush()
    with pytest.raises(DBAPIError, match="immutable"):
        db.execute(text("UPDATE monitoring_thresholds SET psi_watch = 0.2 WHERE id = :id"), {"id": row.id})
    db.rollback()


def test_reads_writes_authority_if_match_history_and_tenancy(client, db_session, setup):  # noqa: F811
    db, pid = db_session, setup["alpha_project"].id
    secret = _token(db, setup)
    read = client.get(f"/v1/projects/{pid}/monitoring-thresholds", headers=_h(setup))
    assert read.status_code == 200 and set(read.json()) == READ_KEYS and read.headers["ETag"] == '"0"'
    body = read.json()
    assert {key: body[key] for key in ("version", *VALUE_KEYS)} == DEFAULTS
    assert set(body["defaults"]) == VALUE_KEYS and set(body["bounds"]) == BOUND_KEYS
    assert body["bounds"] == {"psi_watch_min": 0.05, "psi_watch_max": 0.5, "psi_alert_max": 1.0, "floor_min": 0.01,
                              "floor_max": 0.99}
    assert (body["can_edit"], body["changed_at"], body["changed_by_user_id"]) == (True, None, None)
    _assert_envelope(_put(client, setup, V1, None), 428, "precondition_required")
    _assert_envelope(_put(client, setup, V1, '"3"'), 412, "precondition_failed")
    engineer = _member(db, setup, WorkspaceRole.ML_ENGINEER)
    viewer = _member(db, setup, WorkspaceRole.VIEWER)
    for user in (engineer, viewer):
        assert _put(client, setup, V1, '"0"', user=user).status_code == 403
    assert client.get(f"/v1/projects/{pid}/monitoring-thresholds", headers=_h(setup, engineer)).json()["can_edit"] is False
    staff = create_user(db, email=f"d5b-staff-{uuid4().hex}@test.invalid", password="test-password",
                        role=UserRole.DCLAB_ADMIN)
    db.commit()
    refused = _put(client, setup, V1, '"0"', user=staff)
    assert refused.status_code == 403 and "owner or administrator" in refused.json()["error"]["message"]
    token_put = _put(client, setup, V1, '"0"', headers={"Authorization": f"Bearer {secret}", "Idempotency-Key": _key()})
    assert token_put.status_code == 403 and token_put.json()["error"]["code"] in {
        "service_token_not_permitted", "human_session_required"}
    assert db.scalar(select(func.count()).select_from(MonitoringThresholdSet)) == 0

    # An administrator saves (replayable with the same key); a second save by an owner.
    key = _key()
    saved = _put(client, setup, V1, '"0"', headers=_h(setup, key=key))
    assert saved.status_code == 200, saved.text
    assert (saved.json()["version"], saved.headers["ETag"]) == (1, '"1"') and set(saved.json()) == READ_KEYS
    assert {k: saved.json()[k] for k in VALUE_KEYS} == V1
    assert saved.json()["changed_by_user_id"] == str(setup["alpha_admin"].id) and saved.json()["changed_at"]
    replay = _put(client, setup, V1, '"0"', headers=_h(setup, key=key))
    assert replay.status_code == 200 and replay.json()["version"] == 1
    assert db.scalar(select(func.count()).select_from(MonitoringThresholdSet)) == 1
    _assert_envelope(_put(client, setup, V1, '"1"'), 409, "thresholds_unchanged")
    _assert_envelope(_put(client, setup, {**V1, "recall_floor": None}, '"0"'), 412, "precondition_failed")  # stale
    owner = create_user(db, email=f"d5b-owner-{uuid4().hex}@test.invalid", password="test-password",
                        role=UserRole.WORKSPACE_OWNER, workspace_id=setup["alpha"].id)
    db.commit()
    second = _put(client, setup, {**V1, "recall_floor": None}, '"1"', user=owner)
    assert second.status_code == 200 and second.json()["version"] == 2 and second.json()["recall_floor"] is None

    # History: one accepted, server-typed entry per change with the person's reason.
    db.expire_all()
    records = sorted(db.scalars(select(ProjectDecisionRecord).where(
        ProjectDecisionRecord.project_id == pid, ProjectDecisionRecord.decision_type == "monitoring_thresholds_changed")
    ).all(), key=lambda record: record.facts["version"])
    assert [(r.state, r.actor_kind, r.rationale) for r in records] == [("accepted", "human", "fewer false alarms")] * 2
    assert records[0].facts == {"version": 1, "values": V1, "previous": {k: DEFAULTS[k] for k in VALUE_KEYS}}
    assert records[1].facts["previous"] == V1 and records[1].actor_user_id == owner.id
    rows = db.scalars(select(MonitoringThresholdSet).order_by(MonitoringThresholdSet.version)).all()
    assert [r.decision_record_id for r in rows] == [r.id for r in records]
    assert records[0].details == {"monitoring_thresholds_id": str(rows[0].id)}
    activity = client.get("/v1/activity", params={"project_id": str(pid)}, headers=_h(setup)).json()["items"]
    assert any(item["summary"] == "Monitoring thresholds changed" for item in activity)

    # Reads show the values in force and who changed them (never to a token).
    token_read = client.get(f"/v1/projects/{pid}/monitoring-thresholds", headers={"Authorization": f"Bearer {secret}"})
    assert token_read.status_code == 200 and set(token_read.json()) == READ_KEYS
    assert (token_read.json()["changed_by_user_id"], token_read.json()["can_edit"]) == (None, False)
    overview = client.get(f"/v1/projects/{pid}/monitoring", headers=_h(setup)).json()["thresholds"]
    assert set(overview) == IN_FORCE_KEYS and overview["version"] == 2
    assert (overview["psi_watch"], overview["precision_floor"], overview["changed_by_user_id"]) == (
        0.3, 0.95, str(owner.id))
    by_token = client.get(f"/v1/projects/{pid}/monitoring", headers={"Authorization": f"Bearer {secret}"})
    assert by_token.status_code == 200 and by_token.json()["thresholds"]["changed_by_user_id"] is None

    # Another tenant sees nothing and changes nothing.
    beta = _h(setup, setup["beta_admin"], key=_key(), workspace=setup["beta"])
    _assert_envelope(client.get(f"/v1/projects/{pid}/monitoring-thresholds", headers=beta), 404, "not_found")
    _assert_envelope(_put(client, setup, V1, '"2"', headers=beta), 404, "not_found")
    other = _put(client, setup, V1, '"0"', headers=_h(setup, setup["beta_admin"], key=_key(), workspace=setup["beta"]),
                 project_id=setup["beta_project"].id)
    assert other.status_code == 200 and other.json()["version"] == 1  # its own project, its own versions


# --- end to end: closed weeks keep their values, the open week uses and stores the new ones -------


def _consistent(window: MonitoringWindow, used: dict) -> None:
    """Every counted feature's status is its gated PSI level at the window's own lines."""

    for row in window.input_drift["features"]:
        if row["kind"] == "date":
            continue
        expected = mon.psi_status(row["psi"], used["psi_watch"], used["psi_alert"]) if row["significant"] else "ok"
        assert row["status"] == expected, (row, used)


def _verdict(window: MonitoringWindow) -> tuple[str, str]:
    return mon.window_verdict(int(get_settings().monitoring_min_rows), window.input_drift, window.prediction_drift)


def _score(client, db, setup, mv_id, dataset_id) -> None:  # noqa: F811
    assert _work(db, _predict(client, setup, mv_id, dataset_id).json()["id"]).status == "completed"
    _run_monitoring(db)


def test_only_new_scored_data_picks_up_new_values_and_floors_alert_on_judged_outcomes(
        client, db_session, setup, monkeypatch):  # noqa: F811
    db = db_session
    monkeypatch.setattr(get_settings(), "monitoring_debounce_minutes", 0.0)
    experiment, mv_id = _trained(client, db, setup, _frame("binary", n=240), "label")
    project_id = experiment.project_id
    drifted = _frame("binary", n=300, seed=22).drop(columns=["label"]).assign(
        plan="enterprise", visits=lambda f: f["visits"] + 9, customer_id=[f"d{i:05d}" for i in range(300)])
    scoring = _upload(client, setup, drifted)
    _score(client, db, setup, mv_id, scoring["id"])
    [first] = _windows(db, project_id)
    assert first.thresholds_used == DEFAULTS and "floors" not in first.outcomes
    assert (first.status, first.summary_code) == _verdict(first)
    _consistent(first, DEFAULTS)
    # A revision recorded before the values were stored counts as the defaults: same result, no new revision.
    columns = ("workspace_id", "project_id", "model_version_id", "model_release_id", "window_start", "window_end",
               "n_rows", "n_batch_predictions", "input_drift", "prediction_drift", "outcomes", "status",
               "summary_code", "summary")
    db.add(MonitoringWindow(**{column: getattr(first, column) for column in columns}, revision=2,
                            thresholds_used=None))
    db.commit()
    _recompute(db, setup, project_id, mv_id, first.window_start)
    assert len(_windows(db, project_id)) == 2

    # Strict floors: the open week keeps its values until new scored data arrives.
    strict = {"psi_watch": 0.1, "psi_alert": 0.2, "precision_floor": 0.95, "recall_floor": 0.95}
    assert _put(client, setup, strict, '"0"', project_id=project_id).status_code == 200
    _recompute(db, setup, project_id, mv_id, first.window_start)
    assert len(_windows(db, project_id)) == 2
    _score(client, db, setup, mv_id, scoring["id"])  # the same file scored again: new scored data
    gated = _windows(db, project_id)[-1]
    assert gated.revision == 3 and gated.thresholds_used == {"version": 1, **strict}
    # No outcomes yet: the floors are not judged and change nothing.
    assert {gated.outcomes["floors"][n]["reason_code"] for n in ("precision", "recall")} == {"outcomes_not_measured"}
    assert (gated.status, gated.summary_code) == _verdict(gated) and gated.status != "alert"

    # Real outcomes linked (the same scorings, the stored values): one floor is clearly missed -> an alert.
    labels = pd.DataFrame({"customer_id": [f"d{i:05d}" for i in range(150)],
                           "churned": ["yes" if i % 3 == 0 else "no" for i in range(150)]})
    link = {"dataset_id": _upload(client, setup, labels)["id"], "key_column": "customer_id", "outcome_column": "churned"}
    assert _link(client, setup, project_id, link).status_code == 201
    _run_monitoring(db)
    alert = _windows(db, project_id)[-1]
    assert alert.revision == 4 and alert.outcomes["status"] == "measured" and alert.thresholds_used == gated.thresholds_used
    checks = alert.outcomes["floors"]
    assert set(checks) == {"test", "alpha", "per_floor_alpha", "precision", "recall"} and checks["per_floor_alpha"] == 0.005
    bare = {key: value for key, value in alert.outcomes.items() if key != "floors"}
    assert checks == mon.floor_checks(bare, "binary", {"precision": 0.95, "recall": 0.95},
                                      min_trials=int(get_settings().monitoring_min_per_class))
    assert mon.floors_below(checks), checks  # 1 in 3 labelled rows had the outcome
    drift_status, drift_code = _verdict(alert)
    assert alert.status == "alert" and alert.summary_code == (drift_code if drift_status == "alert" else "below_floor")
    assert "Real outcomes are below the floor set for this project" in alert.summary
    retrain = client.get(f"/v1/projects/{project_id}/retrain-status", headers=_h(setup))
    assert retrain.status_code == 200 and retrain.json()["latest_window_status"] == "alert"
    assert retrain.json()["state"] == ("below_floor" if alert.summary_code == "below_floor" else "drift_found")

    # A looser edit hides nothing already raised without new scored data; a small newer outcome file
    # never replaces the measured one.
    loose = {"psi_watch": 0.3, "psi_alert": 0.6, "precision_floor": None, "recall_floor": None}
    assert _put(client, setup, loose, '"1"', project_id=project_id).status_code == 200
    _recompute(db, setup, project_id, mv_id, first.window_start)
    small = {**link, "dataset_id": _upload(client, setup, labels.head(20))["id"]}
    assert _link(client, setup, project_id, small).status_code == 201
    _run_monitoring(db)
    still = _windows(db, project_id)[-1]
    assert still.revision == 4 and still.status == "alert" and still.outcomes["outcome_set_id"] == bare["outcome_set_id"]
    # A job queued after the week ended keeps the stored values even with a new scoring (a delayed job).
    real_time = ms._job_time
    monkeypatch.setattr(ms, "_job_time", lambda job: first.window_end + timedelta(hours=1))
    _score(client, db, setup, mv_id, scoring["id"])
    closed = _windows(db, project_id)[-1]
    assert closed.revision == 4 and closed.status == alert.status
    monkeypatch.setattr(ms, "_job_time", real_time)

    # New scored data in the open week: the new values are used and stored.
    _score(client, db, setup, mv_id, scoring["id"])
    current = _windows(db, project_id)[-1]
    v2 = {"version": 2, **loose}
    assert current.revision == 5 and current.thresholds_used == v2 and "floors" not in current.outcomes
    assert (current.status, current.summary_code) == _verdict(current)
    _consistent(current, v2)
    assert [w.thresholds_used for w in _windows(db, project_id)][:4] == [DEFAULTS, None, gated.thresholds_used,
                                                                        gated.thresholds_used]
    _recompute(db, setup, project_id, mv_id, first.window_start)
    assert len(_windows(db, project_id)) == 5  # same inputs, same values: no new revision

    # Reads: the values in force, and each week's own values and floor status.
    body = client.get(f"/v1/projects/{project_id}/monitoring", headers=_h(setup)).json()
    assert body["thresholds"]["version"] == 2 and body["latest"]["revision"] == 5
    assert set(body["latest"]["thresholds_used"]) == USED_KEYS and body["latest"]["thresholds_used"] == v2
    assert body["latest"]["floor_status"] is None
    monday = first.window_start.date().isoformat()
    detail = client.get(f"/v1/projects/{project_id}/monitoring/windows/{monday}", headers=_h(setup)).json()
    assert detail["thresholds_used"] == v2 and detail["thresholds"]["version"] == 2
    assert detail["thresholds"]["changed_by_user_id"] == str(setup["alpha_admin"].id)
    secret = _token(db, setup)
    by_token = client.get(f"/v1/projects/{project_id}/monitoring/windows/{monday}",
                          headers={"Authorization": f"Bearer {secret}"})
    assert by_token.status_code == 200 and by_token.json()["thresholds"]["changed_by_user_id"] is None
    # The alert week's own read keeps its floor sentences (revision 4 is no longer the newest, so check words).
    assert any("below the floor of 95%" in w for w in dm.outcome_sentences(alert.outcomes, ms.thresholds()))
