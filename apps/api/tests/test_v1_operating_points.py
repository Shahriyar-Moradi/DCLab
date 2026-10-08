"""P5.2-A: GET /v1/experiments/{id}/operating-points and POST .../operating-point over a real
binary run (worker job, no engine mocks), and batch scoring after a choice.

People choose (session/user bearer with ML-write); service tokens read only. A choice is an
accepted ``operating_point_chosen`` decision record superseding the previous one; scoring
keeps the locked threshold; no final-evaluation figure appears in the operating-points read.
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from app.db.models import Experiment, WorkspaceRole
from app.services.service_token_service import create_service_token
from test_v1_batch_predictions import _frame, _predict, _trained, _upload
from test_v1_batch_predictions import _work as _work_prediction
from test_v1_contract_conventions import _assert_envelope
from test_v1_resources_experiments import _h, _key, _member, setup  # noqa: F401 (fixture)

HOLDOUT = re.compile(r"holdout|final_test|test_metrics", re.IGNORECASE)


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for item in value.values() for k in _keys(item)}
    if isinstance(value, list):
        return {k for item in value for k in _keys(item)}
    return set()


def _numbers(value) -> list[float]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [round(float(value), 9)]
    if isinstance(value, dict):
        return [n for item in value.values() for n in _numbers(item)]
    if isinstance(value, list):
        return [n for item in value for n in _numbers(item)]
    return []


def _choose(client, setup, eid, body, *, key=None, headers=None):  # noqa: F811
    return client.post(f"/v1/experiments/{eid}/operating-point", json=body,
                       headers=headers or _h(setup, key=key or _key()))


def test_operating_points_read_choose_supersede_and_scoring(client, db_session, setup):  # noqa: F811
    db = db_session
    frame = _frame("binary").drop(columns=["signup_date"])  # K-fold CV: every training row is out-of-fold once
    experiment, mv_id = _trained(client, db, setup, frame, "label")
    eid, locked = str(experiment.id), experiment.result["decision_threshold"]["value"]
    url = f"/v1/experiments/{eid}/operating-points"

    read = client.get(url, headers=_h(setup))
    assert read.status_code == 200, read.text
    assert read.headers["ETag"] and read.headers["Cache-Control"] == "private, no-store"
    assert {"Authorization", "Cookie"} <= {v.strip() for v in read.headers["Vary"].split(",")}
    body = read.json()
    assert (body["status"], body["outcome_scope"], body["basis"]) == ("available", "cv", "out_of_fold_cv"), {k: body[k] for k in ("reason", "oof_folds", "rows", "positives", "negatives")}
    assert body["rows"] == len(experiment.result["split"]["train_source_rows"])
    assert body["locked"]["threshold"] == locked and body["locked"]["reproduced_from_curve"] is True
    assert body["scoring"] == {"threshold": locked, "uses": "locked_threshold", "note": body["scoring"]["note"]}
    assert body["points"] and body["pareto"] and body["chosen"] is None
    assert not {k for k in _keys(body) if HOLDOUT.search(k)}
    # Value oracle: the run's distinctive final-evaluation numbers appear nowhere in the read.
    distinctive = {round(float(v), 9) for k, v in experiment.result["test_metrics"].items()
                   if k in ("roc_auc", "pr_auc", "log_loss", "brier_score") and isinstance(v, float)}
    assert distinctive and not distinctive & set(_numbers(body))

    # Service tokens read the same body; they never choose.
    token = create_service_token(db, creator=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="r",
                                 scopes=("read", "decisions:propose"), expires_in_days=1,
                                 current_password="test-password")[1]
    db.commit()
    bearer = {"Authorization": f"Bearer {token}"}
    as_token = client.get(url, headers=bearer)
    assert as_token.status_code == 200 and as_token.json() == body
    target = next(p["threshold"] for p in body["pareto"] if p["threshold"] != locked)
    # The generic decision path never writes this service-owned type (person or token).
    generic = {"decision_type": "operating_point_chosen", "subject": {"kind": "experiment", "id": eid},
               "rationale": "x"}
    decisions = f"/v1/projects/{setup['alpha_project'].id}/decisions"
    _assert_envelope(client.post(decisions, json={"action": "record", **generic}, headers=_h(setup, key=_key())),
                     422, "validation_failed")
    _assert_envelope(client.post(decisions, json={"action": "propose", **generic},
                                 headers={**bearer, "Idempotency-Key": _key()}), 422, "validation_failed")
    refused = _choose(client, setup, eid, {"threshold": target, "reason": "x"},
                      headers={**bearer, "Idempotency-Key": _key()})
    assert refused.status_code == 403
    viewer = _member(db, setup, WorkspaceRole.VIEWER)
    assert client.get(url, headers=_h(setup, viewer)).status_code == 200
    _assert_envelope(_choose(client, setup, eid, {"threshold": target, "reason": "x"},
                             headers=_h(setup, viewer, key=_key())), 403, "forbidden")

    # Choose a point on the curve; replay; key reuse with another body.
    key = _key()
    first = _choose(client, setup, eid, {"threshold": target, "reason": "catch more churners"}, key=key)
    assert first.status_code == 201, first.text
    chosen = first.json()
    assert first.headers["Location"] == f"/v1/decisions/{chosen['decision']['id']}"
    assert chosen["chosen"]["threshold"] == target and chosen["chosen"]["method"] == "threshold"
    assert chosen["chosen"]["applies_to_scoring"] is False and chosen["scoring"]["threshold"] == locked
    decision = chosen["decision"]
    assert (decision["decision_type"], decision["state"], decision["actor"]["kind"]) == (
        "operating_point_chosen", "accepted", "human")
    assert decision["subject"]["id"] == eid and decision["facts"]["threshold"] == target
    assert decision["facts"]["basis"] == "out_of_fold_cv" and decision["rationale"] == "catch more churners"
    assert re.fullmatch(r"[0-9a-f]{64}", decision["facts"]["curve_digest"])
    replay = _choose(client, setup, eid, {"threshold": target, "reason": "catch more churners"}, key=key)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["decision"]["id"] == decision["id"]
    _assert_envelope(_choose(client, setup, eid, {"threshold": locked, "reason": "other"}, key=key), 409,
                     "idempotency_key_conflict")
    after = client.get(url, headers=_h(setup)).json()
    assert after["chosen"]["decision_id"] == decision["id"] and after["chosen"]["point"]["threshold"] == target
    assert after["chosen"]["point"]["what_this_means"].startswith(f"At threshold {target:.4g}")
    assert after["chosen"]["curve_changed"] is False

    # A second choice (objective) supersedes the first; the generic supersede path is refused.
    second = _choose(client, setup, eid, {"objective": {"goal": "f1"}, "reason": "balance"})
    assert second.status_code == 201, second.text
    assert second.json()["decision"]["supersedes_id"] == decision["id"]
    assert second.json()["chosen"]["method"] == "objective"
    assert client.get(url, headers=_h(setup)).json()["chosen"]["decision_id"] == second.json()["decision"]["id"]
    records = client.get(f"/v1/projects/{setup['alpha_project'].id}/decisions",
                         params={"decision_type": "operating_point_chosen"}, headers=_h(setup)).json()["items"]
    assert [r["effective_state"] for r in records] == ["accepted", "superseded"]
    head = second.json()["decision"]["id"]
    _assert_envelope(client.post(f"/v1/decisions/{head}/supersede", json={"rationale": "x"},
                                 headers=_h(setup, key=_key())), 403, "decision_actor_not_permitted")

    # Refusals: off-curve threshold, infeasible objective (closest point), invalid bodies.
    _assert_envelope(_choose(client, setup, eid, {"threshold": 0.123456789123, "reason": "x"}), 422,
                     "threshold_not_on_curve")
    infeasible = _choose(client, setup, eid, {"reason": "x", "objective": {
        "goal": "precision", "constraints": [{"metric": "recall", "op": ">=", "value": 0.999},
                                             {"metric": "precision", "op": ">=", "value": 0.999}]}})
    error = _assert_envelope(infeasible, 422, "objective_infeasible")
    assert error["details"]["closest"]["threshold"] in {p["threshold"] for p in body["points"]}
    _assert_envelope(_choose(client, setup, eid, {"reason": "x", "objective": {"goal": "expected_cost"}}), 422,
                     "cost_matrix_required")
    # An infeasible cost objective prices its closest point with the REQUESTED costs.
    priced = _assert_envelope(_choose(client, setup, eid, {"reason": "x", "objective": {
        "goal": "expected_cost", "cost_false_positive": 2, "cost_false_negative": 7,
        "constraints": [{"metric": "recall", "op": ">=", "value": 0.999},
                        {"metric": "precision", "op": ">=", "value": 0.999}]}}), 422, "objective_infeasible")
    closest = priced["details"]["closest"]
    assert closest["expected_cost"] == (2 * closest["fp"] + 7 * closest["fn"]) / body["rows"]
    for bad in ({"threshold": target, "objective": {"goal": "f1"}, "reason": "x"}, {"reason": "x"},
                {"objective": {"goal": "precision"}, "reason": "x"}, {"threshold": target, "reason": ""},
                {"threshold": target, "reason": "x", "actor_kind": "agent"}, {"threshold": str(target), "reason": "x"},
                {"threshold": True, "reason": "x"},
                {"reason": "x", "objective": {"goal": "precision",
                                              "constraints": [{"metric": "recall", "op": ">=", "value": 0}]}},
                {"reason": "x", "objective": {"goal": "expected_cost", "cost_false_positive": 1e308,
                                              "cost_false_negative": 1e308}}):
        _assert_envelope(_choose(client, setup, eid, bad), 422, "validation_failed")

    # Flag nothing (the last candidate, the next float above the top score) is recorded exactly.
    nothing = body["points"][-1]["threshold"]
    assert (body["points"][-1]["tp"], body["points"][-1]["fp"]) == (0, 0)
    edge = _choose(client, setup, eid, {"threshold": nothing, "reason": "pause outreach"})
    assert edge.status_code == 201, edge.text
    assert edge.json()["decision"]["facts"]["threshold"] == nothing == edge.json()["chosen"]["point"]["threshold"]
    assert (edge.json()["chosen"]["point"]["tp"], edge.json()["chosen"]["point"]["fp"]) == (0, 0)

    # Scoring keeps the locked threshold after a choice (no silent change).
    scoring = _upload(client, setup, frame.drop(columns=["label"]))
    created = _predict(client, setup, mv_id, scoring["id"])
    assert created.status_code == 202, created.text
    assert _work_prediction(db, created.json()["id"]).status == "completed"
    done = client.get(f"/v1/predictions/{created.json()['id']}", headers=_h(setup)).json()
    assert done["decision_threshold"] == locked != target

    # Tenant isolation: another workspace sees nothing and cannot choose.
    beta = _h(setup, setup["beta_admin"], workspace=setup["beta"])
    _assert_envelope(client.get(url, headers=beta), 404, "not_found")
    _assert_envelope(_choose(client, setup, eid, {"threshold": target, "reason": "x"},
                             headers={**beta, "Idempotency-Key": _key()}), 404, "not_found")
    _assert_envelope(client.get(f"/v1/experiments/{uuid4()}/operating-points", headers=_h(setup)), 404, "not_found")

    # The choice is anchored to its curve: a changed stored curve is flagged on read.
    row = db.get(Experiment, UUID(eid))
    original = dict(row.result)
    changed = {**original["operating_curve"], "fp": [original["operating_curve"]["fp"][0] - 1,
                                                    *original["operating_curve"]["fp"][1:]]}
    row.result = {**original, "operating_curve": changed}
    db.commit()
    assert client.get(url, headers=_h(setup)).json()["chosen"]["curve_changed"] is True

    # Runs without a stored curve (before P5.2-A) and non-binary runs: typed, never a 500.
    row.result = {k: v for k, v in original.items() if k != "operating_curve"}
    db.commit()
    old = client.get(url, headers=_h(setup)).json()
    assert (old["status"], old["reason"], old["points"]) == ("not_available", "no_operating_curve", [])
    assert old["locked"]["threshold"] == locked and old["chosen"]["point"] is None
    _assert_envelope(_choose(client, setup, eid, {"threshold": target, "reason": "x"}), 409,
                     "operating_points_not_available")
    row.result = {**original, "task": {**original["task"], "task_type": "regression"}}
    db.commit()
    other = client.get(url, headers=_h(setup)).json()
    assert (other["status"], other["locked"], other["points"]) == ("not_applicable", None, [])
    _assert_envelope(_choose(client, setup, eid, {"threshold": target, "reason": "x"}), 409,
                     "operating_points_not_applicable")
