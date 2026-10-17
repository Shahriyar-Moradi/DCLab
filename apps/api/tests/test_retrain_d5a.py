"""D5a (ADR 0012): retrain on newest data — policy, start, worker tick, comparison, switch / keep.

The end to end runs three real auto-trains through the worker path with AI off: an automatic retrain
on an older-period file is not comparable (its final test overlaps the training period of the model in
use) and a person keeps the model in use; the Inbox question leads a person to retrain on a later file,
the comparison is stored once, and accepting the rule's proposal switches with the re-baseline
(dataset, split plan, feature recipe and champion move together).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pandas as pd
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db.models import (
    DatasetAsset,
    LlmInvocation,
    RetrainPolicy,
    UserRole,
    ModelVersion,
    MonitoringWindow,
    ProjectDecisionRecord,
    RetrainComparison,
    RetrainRequest,
    WorkspaceRole,
)
from app.domain.decision_records import RULE_REFS_BOOTSTRAP, RULE_RETRAIN_CHALLENGER, DecisionActor
from app.domain.errors import DecisionActorNotPermittedError, InvalidDecisionRecordError, RunQuotaExceededError
from app.engine.serving.batch_scoring import ScoringError
from app.services import batch_prediction_service, experiment_branch_service
from app.services import retrain_comparison as rc
from app.services import retrain_service as rs
from app.services.auth_service import create_user
from app.services.monitoring_service import week_start
from app.services.project_ref_service import RefMove, propose_ref_move
from app.services.retrain_comparison import _labels, _NotComparable, later_than_training
from app.services.retrain_service import run_retrain_tick
from app.services.service_token_service import create_service_token
from test_v1_batch_predictions import _frame, _upload
from test_v1_contract_conventions import _assert_envelope
from test_v1_resources_experiments import _h, _key, _member, _root, _work, setup  # noqa: F401 (fixture)

POLICY_KEYS = {"project_id", "policy", "version", "etag", "updated_at", "updated_by_user_id", "owner_can_start",
               "auto_paused_until"}
STATUS_KEYS = {"project_id", "policy", "state", "model_in_use_id", "latest_window_start", "latest_window_status",
               "newer_dataset_id", "request", "comparison", "comparison_withheld", "real_outcomes", "proposal_id",
               "proposal_state", "can_start", "notes"}
REQUEST_KEYS = {"id", "origin", "created_at", "incumbent_model_version_id", "dataset_id", "experiment_id",
                "experiment_status", "window_start", "carried", "not_carried"}
COMPARISON_KEYS = {"status", "reason_code", "message", "wording", "time_column", "incumbent_training_end",
                   "final_test_start", "final_test_rows", "challenger", "incumbent", "proposal_refusal", "computed_at"}


def _token(db, setup) -> str:  # noqa: F811
    secret = create_service_token(db, creator=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="d5a",
                                  scopes=("read", "experiments:write"), expires_in_days=1,
                                  current_password="test-password")[1]
    db.commit()
    return secret


def _bearer(secret: str, key: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}", **({"Idempotency-Key": key} if key else {})}


def _put_policy(client, setup, policy: str, if_match: str | None, *, user=None, headers=None):  # noqa: F811
    head = headers or _h(setup, user, key=_key())
    if if_match is not None:
        head = {**head, "If-Match": if_match}
    return client.put(f"/v1/projects/{setup['alpha_project'].id}/retrain-policy",
                      json={"policy": policy, "rationale": f"set {policy}"}, headers=head)


def _dated(seed: int, start: str, n: int = 400) -> pd.DataFrame:
    frame = _frame("binary", n=n, seed=seed)
    days = pd.Series(range(n)) * 700 // n
    return frame.assign(signup_date=(pd.Timestamp(start) + pd.to_timedelta(days, unit="D")).dt.strftime("%Y-%m-%d"))


def _window(db, setup, version_id, start: datetime) -> MonitoringWindow:  # noqa: F811
    row = MonitoringWindow(
        workspace_id=setup["alpha"].id, project_id=setup["alpha_project"].id, model_version_id=version_id,
        window_start=start, window_end=start + timedelta(days=7), revision=1, n_rows=600, n_batch_predictions=1,
        input_drift={"max_psi": 0.4, "features": []}, prediction_drift={"psi": 0.05}, outcomes={},
        status="alert", summary_code="input_drift", summary="weekly check")
    db.add(row)
    db.commit()
    return row


def _status(client, setup, headers=None) -> dict:  # noqa: F811
    response = client.get(f"/v1/projects/{setup['alpha_project'].id}/retrain-status", headers=headers or _h(setup))
    assert response.status_code == 200, response.text
    assert set(response.json()) == STATUS_KEYS
    return response.json()


def _inbox(client, setup, tab: str, headers=None) -> list[dict]:  # noqa: F811
    response = client.get("/v1/inbox", params={"tab": tab, "project_id": str(setup["alpha_project"].id), "limit": 100},
                          headers=headers or _h(setup))
    assert response.status_code == 200, response.text
    return response.json()["items"]


# --- the leak-free condition (pure) -------------------------------------------------------------


def test_later_than_training_is_strict_at_the_boundary():
    train = pd.Series(["2024-01-01", "2024-03-31"], name="d")
    assert later_than_training(train, pd.Series(["2024-04-01", "2024-05-02"], name="d")) == (None, {
        "incumbent_training_end": "2024-03-31T00:00:00+00:00", "final_test_start": "2024-04-01T00:00:00+00:00",
        "final_test_rows": 2})
    # An equal time is not later: one test row on the training end makes it not comparable.
    assert later_than_training(train, pd.Series(["2024-03-31", "2024-05-02"], name="d"))[0] == (
        "final_test_overlaps_training")
    # One format for both sides, the training rows': another spelling is unreadable, never re-guessed.
    assert later_than_training(train, pd.Series(["2024-04-01T00:00:00+00:00"], name="d"))[0] == "time_missing"
    day_first = pd.Series(["13/01/2024", "15/01/2024", "20/01/2024"], name="d")
    assert later_than_training(day_first, pd.Series(["02/01/2024", "03/01/2024", "05/01/2024"], name="d"))[0] == (
        "final_test_overlaps_training")  # 2-5 January, read day-first like training: before its end
    assert later_than_training(train, pd.Series(["2024-04-01", "not a date"], name="d"))[0] == "time_missing"
    assert later_than_training(pd.Series(["2024-01-01", None], name="d"), pd.Series(["2025-01-01"], name="d"))[0] == (
        "time_missing")
    assert later_than_training(pd.Series([1, 5], name="t"), pd.Series([5, 6], name="t"))[0] == (
        "final_test_overlaps_training")
    assert later_than_training(pd.Series([1, 5], name="t"), pd.Series([6, 9], name="t"))[0] is None
    assert later_than_training(pd.Series([1, 5], name="t"), pd.Series(["2024-01-01"], name="t"))[0] == (
        "time_kinds_differ")
    assert later_than_training(pd.Series([], name="t", dtype=float), pd.Series([6.0], name="t"))[0] == "time_missing"


def test_labels_must_be_coded_the_same_way_by_both_models():
    target = {"positive": ["yes"], "negative": ["no"]}
    coded = _labels("binary", pd.Series(["yes", "no", "yes"]), target, None)
    assert coded.tolist() == [1.0, 0.0, 1.0]
    for raw, spellings in ((pd.Series(["yes", "no", "maybe"]), target),  # a label the model in use never saw
                           (pd.Series(["yes", "no"]), {"positive": ["no"], "negative": ["yes"]})):  # flipped
        with pytest.raises(_NotComparable) as refused:
            _labels("binary", raw, spellings, None)
        assert refused.value.code == "target_coding_differs"
    with pytest.raises(_NotComparable):
        _labels("multiclass", pd.Series(["gold", "tin"]), None, ["bronze", "silver", "gold"])


def test_only_the_retrain_rule_may_propose_a_ref_move_and_only_with_its_own_key(db_session, setup):  # noqa: F811
    project = setup["alpha_project"]
    moves = [RefMove("dataset", setup["alpha_dataset"].id, None)]
    with pytest.raises(DecisionActorNotPermittedError) as refused:
        propose_ref_move(db_session, workspace_id=setup["alpha"].id, project_id=project.id,
                         actor=DecisionActor.rule_actor(RULE_REFS_BOOTSTRAP), moves=moves, rationale="x",
                         evidence_refs=[], idempotency_key="k")
    assert refused.value.reason == "rule_not_permitted"
    with pytest.raises(InvalidDecisionRecordError) as keyless:
        propose_ref_move(db_session, workspace_id=setup["alpha"].id, project_id=project.id,
                         actor=DecisionActor.rule_actor(RULE_RETRAIN_CHALLENGER), moves=moves, rationale="x",
                         evidence_refs=[])
    assert keyless.value.reason == "rule_key_required"


# --- policy, refusals, tenancy (no training) ------------------------------------------------------


def test_policy_and_status_reads_writes_and_refusals(client, db_session, setup):  # noqa: F811
    db, pid = db_session, setup["alpha_project"].id
    secret = _token(db, setup)
    read = client.get(f"/v1/projects/{pid}/retrain-policy", headers=_h(setup))
    assert read.status_code == 200 and set(read.json()) == POLICY_KEYS and read.headers["ETag"] == '"0"'
    assert (read.json()["policy"], read.json()["version"], read.json()["owner_can_start"]) == ("off", 0, None)
    _assert_envelope(_put_policy(client, setup, "auto", None), 428, "precondition_required")
    _assert_envelope(_put_policy(client, setup, "auto", '"3"'), 412, "precondition_failed")
    engineer = _member(db, setup, WorkspaceRole.ML_ENGINEER)
    assert _put_policy(client, setup, "auto", '"0"', user=engineer).status_code == 403  # admins only
    assert _put_policy(client, setup, "auto", '"0"', user=_member(db, setup, WorkspaceRole.VIEWER)).status_code == 403
    token_put = _put_policy(client, setup, "auto", '"0"', headers=_bearer(secret, _key()))
    assert token_put.status_code == 403 and token_put.json()["error"]["code"] in {
        "service_token_not_permitted", "human_session_required"}
    no_reason = client.put(f"/v1/projects/{pid}/retrain-policy", json={"policy": "ask"},
                           headers={**_h(setup, key=_key()), "If-Match": '"0"'})
    assert no_reason.status_code == 422
    saved = _put_policy(client, setup, "auto", '"0"')
    assert saved.status_code == 200, saved.text
    assert (saved.json()["policy"], saved.json()["version"], saved.headers["ETag"]) == ("auto", 1, '"1"')
    assert saved.json()["updated_by_user_id"] == str(setup["alpha_admin"].id) and saved.json()["owner_can_start"]
    record = db.scalar(select(ProjectDecisionRecord).where(ProjectDecisionRecord.project_id == pid,
                                                           ProjectDecisionRecord.decision_type == "retrain_policy_changed"))
    assert (record.state, record.actor_kind, record.rationale) == ("accepted", "human", "set auto")
    assert record.facts == {"policy": "auto", "previous": "off", "version": 1}
    token_read = client.get(f"/v1/projects/{pid}/retrain-policy", headers=_bearer(secret))
    assert token_read.status_code == 200 and token_read.json()["updated_by_user_id"] is None
    activity = client.get("/v1/activity", params={"project_id": str(pid)}, headers=_h(setup)).json()["items"]
    assert any(item["summary"] == "Retrain policy changed" for item in activity)

    status = _status(client, setup)
    assert (status["policy"], status["state"], status["request"], status["comparison"]) == ("auto", "idle", None, None)
    assert status["can_start"] is False  # D8a: no model in use
    assert _status(client, setup, _bearer(secret))["comparison_withheld"] is False
    dataset = setup["alpha_dataset"].id
    _assert_envelope(client.post(f"/v1/projects/{pid}/retrain", json={"dataset_id": str(dataset)},
                                 headers=_h(setup, key=_key())), 409, "no_model_in_use")
    token_post = client.post(f"/v1/projects/{pid}/retrain", json={"dataset_id": str(dataset)},
                             headers=_bearer(secret, _key()))
    assert token_post.status_code == 403
    assert client.post(f"/v1/projects/{pid}/retrain", json={"dataset_id": str(dataset)},
                       headers=_h(setup, _member(db, setup, WorkspaceRole.VIEWER), key=_key())).status_code == 403
    beta = _h(setup, setup["beta_admin"], key=_key(), workspace=setup["beta"])
    for response in (client.get(f"/v1/projects/{pid}/retrain-status", headers=beta),
                     client.get(f"/v1/projects/{pid}/retrain-policy", headers=beta),
                     client.post(f"/v1/projects/{pid}/retrain", json={"dataset_id": str(dataset)}, headers=beta),
                     _put_policy(client, setup, "ask", '"1"', headers=beta)):
        _assert_envelope(response, 404, "not_found")  # another tenant's project
    assert run_retrain_tick(db) == {"compared": 0, "asked": 0, "started": 0}  # no model in use: nothing


# --- paths without training: comparison refusals, proposal refusal, uploader rule, staff, backoff --


def _comparison_world(monkeypatch, *, strategy: str = "temporal_future", tasks=("binary", "binary")):
    """A request, a challenger and an incumbent as plain objects; ``_scoped`` / ``_frame_and_map`` /
    ``winner_evidence`` stubbed (no data, no model): every check before scoring runs for real."""

    ids = {name: uuid4() for name in ("ws", "version", "incumbent", "challenger", "new_plan")}
    version = SimpleNamespace(id=ids["version"], pipeline_run_id=ids["incumbent"])
    incumbent = SimpleNamespace(id=ids["incumbent"], result={"task": {"task_type": tasks[0]}})
    challenger = SimpleNamespace(id=ids["challenger"], workspace_id=ids["ws"], split_plan_id=ids["new_plan"],
                                 result={"task": {"task_type": tasks[1]}})
    plan = SimpleNamespace(holdout_strategy=strategy, time_column="d")
    rows = {("ModelVersion", version.id): version, ("Experiment", incumbent.id): incumbent,
            ("SplitPlan", plan_id := ids["new_plan"]): plan}
    monkeypatch.setattr(rc, "_scoped", lambda _db, model, _ws, row_id: rows.get((model.__name__, row_id)))
    old = pd.DataFrame({"d": ["2024-01-01", "2024-02-01", "2024-03-01"], "label": ["yes", "no", "yes"]})
    new = pd.DataFrame({"d": ["2024-04-01", "2024-05-01", "2024-06-01"], "label": ["no", "yes", "no"]})
    frames = {incumbent.id: (old, SimpleNamespace(train_folds={0: 1, 1: 1, 2: 1}, holdout_rows=frozenset())),
              challenger.id: (new, SimpleNamespace(train_folds={0: 1}, holdout_rows=frozenset({1, 2})))}
    monkeypatch.setattr(rc, "_frame_and_map", lambda _db, experiment: frames[experiment.id])
    monkeypatch.setattr(experiment_branch_service, "winner_evidence", lambda _db, _exp: {
        "selection_metric": "roc_auc", "holdout": {"roc_auc": 0.7}, "decision_threshold": 0.5})
    request = SimpleNamespace(id=uuid4(), workspace_id=ids["ws"], incumbent_model_version_id=version.id)
    assert plan_id == challenger.split_plan_id
    return request, challenger


def test_comparison_refuses_a_mixed_in_time_final_test_another_task_and_an_unscorable_model(monkeypatch):
    request, challenger = _comparison_world(monkeypatch, strategy="stratified")
    result = rc.compare(None, request, challenger)
    assert (result["status"], result["reason_code"], result["challenger_holdout_strategy"]) == (
        "not_comparable", "challenger_test_not_latest", "stratified")
    request, challenger = _comparison_world(monkeypatch, tasks=("regression", "binary"))
    assert rc.compare(None, request, challenger)["reason_code"] == "task_differs"
    request, challenger = _comparison_world(monkeypatch)

    def unscorable(_db, _version):
        raise ScoringError("unsupported_model", "this model version's stored pipeline cannot be used")

    monkeypatch.setattr(batch_prediction_service, "load_version_for_scoring", unscorable)
    result = rc.compare(None, request, challenger)
    # Leak-free (April-June after a March training end), but the model in use cannot score the rows.
    assert (result["status"], result["reason_code"], result["time_column"], result["final_test_rows"]) == (
        "not_comparable", "incumbent_cannot_score", "d", 2)
    assert result["challenger_holdout"] == {"roc_auc": 0.7} and "incumbent" not in result


def test_no_proposal_when_the_model_in_use_changed_meanwhile(monkeypatch):
    request = SimpleNamespace(id=uuid4(), workspace_id=uuid4(), project_id=uuid4(), incumbent_model_version_id=uuid4())
    monkeypatch.setattr(rs, "model_in_use", lambda _db, _ws, _project: uuid4())  # someone switched by hand
    assert rs._propose(None, request, SimpleNamespace(id=uuid4())) == (None, "model_in_use_changed")


def test_the_rule_ignores_files_from_people_who_cannot_start_runs(db_session, setup):  # noqa: F811
    db, ws = db_session, setup["alpha"].id
    viewer = _member(db, setup, WorkspaceRole.VIEWER)
    assets = {}
    for name, creator in (("viewer", viewer.id), ("admin", setup["alpha_admin"].id), ("nobody", None)):
        assets[name] = DatasetAsset(workspace_id=ws, project_id=setup["alpha_project"].id, name=name,
                                    slug=f"d5a-{name}-{uuid4().hex[:8]}", created_by=creator)
        db.add(assets[name])
    db.commit()
    may = {name: rs._uploader_may_train(db, SimpleNamespace(dataset_asset_id=row.id, workspace_id=ws))
           for name, row in assets.items()}
    assert may == {"viewer": False, "admin": True, "nobody": False}


def test_platform_staff_never_set_the_retrain_policy(client, db_session, setup):  # noqa: F811
    staff = create_user(db_session, email=f"d5a-staff-{uuid4().hex}@test.invalid", password="test-password",
                        role=UserRole.DCLAB_ADMIN)
    db_session.commit()
    refused = _put_policy(client, setup, "auto", '"0"', user=staff)
    assert refused.status_code == 403, refused.text
    assert "owner or administrator" in refused.json()["error"]["message"]  # refused by the policy rule itself
    assert db_session.scalar(select(func.count()).select_from(RetrainPolicy)) == 0
    assert db_session.scalar(select(func.count()).select_from(ProjectDecisionRecord).where(
        ProjectDecisionRecord.decision_type == "retrain_policy_changed")) == 0


def test_a_plan_limit_pauses_automatic_starts_for_a_day(client, db_session, setup, monkeypatch):  # noqa: F811
    db, pid = db_session, setup["alpha_project"].id
    assert _put_policy(client, setup, "auto", '"0"').status_code == 200
    version, incumbent = SimpleNamespace(id=uuid4()), SimpleNamespace(source_dataset_id=setup["alpha_dataset"].id)
    monkeypatch.setattr(rs, "_incumbent", lambda _db, _ws, _project: (version, incumbent))
    monkeypatch.setattr(rs, "_alert_window", lambda *_a: SimpleNamespace(id=uuid4(), window_start=week_start(
        datetime.now(UTC))))
    monkeypatch.setattr(rs, "newest_labelled_file", lambda _db, _source: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(rs, "_uploader_may_train", lambda _db, _dataset: True)
    attempts = []

    def over_limit(*_a, **_k):
        attempts.append(1)
        raise RunQuotaExceededError(1)

    monkeypatch.setattr(rs, "start_retrain", over_limit)
    assert run_retrain_tick(db) == {"compared": 0, "asked": 0, "started": 0} and len(attempts) == 1
    paused = client.get(f"/v1/projects/{pid}/retrain-policy", headers=_h(setup)).json()["auto_paused_until"]
    until = datetime.fromisoformat(paused)
    assert timedelta(hours=23) < until - datetime.now(UTC) <= timedelta(hours=24)
    run_retrain_tick(db)
    run_retrain_tick(db, now=datetime.now(UTC) + timedelta(hours=23))
    assert len(attempts) == 1  # paused: no new attempt within the day
    run_retrain_tick(db, now=datetime.now(UTC) + timedelta(hours=25))
    assert len(attempts) == 2  # tried again after the day (and paused again)
    saved = _put_policy(client, setup, "auto", '"1"')  # a person saving the policy clears the pause
    assert saved.status_code == 200 and saved.json()["auto_paused_until"] is None
    assert db.scalar(select(func.count()).select_from(RetrainRequest).where(RetrainRequest.project_id == pid)) == 0


# --- end to end: auto + keep, ask + person + switch ----------------------------------------------


def test_retrain_end_to_end_keep_then_switch(client, db_session, setup, monkeypatch):  # noqa: F811
    db, pid = db_session, setup["alpha_project"].id
    secret = _token(db, setup)
    spec = client.post(f"/v1/projects/{pid}/problem-specs", headers=_h(setup, key=_key()), json={
        "task_type": "binary_classification", "business_objective": "keep customers", "target_column": "label",
        "prediction_time_column": "signup_date", "status": "locked"})
    assert spec.status_code == 201, spec.text
    base = _upload(client, setup, _dated(11, "2023-01-01", n=500), purpose="training")
    root = _root(client, setup, base["id"], problem_spec_id=spec.json()["id"])
    assert root.status_code == 202, root.text
    assert _work(db, UUID(root.json()["id"])).status == "completed"
    incumbent = db.scalar(select(ModelVersion).where(ModelVersion.pipeline_run_id == UUID(root.json()["id"])))
    assert _status(client, setup)["model_in_use_id"] == str(incumbent.id)  # bootstrapped champion

    # 1. Automatic: an alert week + a newer upload of OLDER months -> started, not comparable, kept.
    old_months = _upload(client, setup, _dated(12, "2019-01-01"), purpose="training")
    assert _put_policy(client, setup, "auto", '"0"').status_code == 200
    assert run_retrain_tick(db) == {"compared": 0, "asked": 0, "started": 0}  # no alert yet
    week = week_start(datetime.now(UTC))
    _window(db, setup, incumbent.id, week)
    assert _status(client, setup)["state"] == "drift_found"
    assert run_retrain_tick(db)["started"] == 1
    assert run_retrain_tick(db) == {"compared": 0, "asked": 0, "started": 0}  # single flight, once per week
    auto = db.scalar(select(RetrainRequest).where(RetrainRequest.project_id == pid))
    assert (auto.origin, auto.dataset_id, auto.window_start, auto.requested_by_user_id) == (
        "auto", UUID(old_months["id"]), week, setup["alpha_admin"].id)
    first = _status(client, setup)
    assert first["state"] == "retraining" and set(first["request"]) == REQUEST_KEYS
    assert first["can_start"] is False  # D8a: a retrain is running (and its file is already used)
    assert first["request"]["carried"] == ["target_column", "problem_spec"] and first["request"]["not_carried"] == []
    assert _work(db, auto.experiment_id).status == "completed"
    assert run_retrain_tick(db)["compared"] == 1
    assert run_retrain_tick(db)["compared"] == 0  # stored once
    kept = _status(client, setup)
    assert kept["state"] == "waiting_for_you" and set(kept["comparison"]) == COMPARISON_KEYS
    assert (kept["comparison"]["status"], kept["comparison"]["reason_code"], kept["comparison"]["incumbent"]) == (
        "not_comparable", "final_test_overlaps_training", None)
    assert kept["comparison"]["message"] == ("not comparable: the new final test overlaps the training period of "
                                             "the model in use")
    assert kept["comparison"]["wording"] == "same kind of test, latest months"
    assert kept["comparison"]["challenger"]["metrics"]  # the retrained model's own single final test
    challenger_item = next(i for i in _inbox(client, setup, "needs_decision") if i["kind"] == "challenger_ready")
    assert challenger_item["proposed_by"] == "rule" and [a["name"] for a in challenger_item["actions"]] == [
        "accept", "reject"]
    reject = client.post(f"/v1/decisions/{kept['proposal_id']}/reject", json={"rationale": "keep the current model"},
                         headers=_h(setup, key=_key()))
    assert reject.status_code in (200, 201), reject.text
    assert _status(client, setup)["state"] == "kept"
    assert db.scalar(select(func.count()).select_from(ModelVersion).where(ModelVersion.project_id == pid)) == 2

    # 2. Ask: a later file and a new alert week -> an Inbox question, no run; at most one per 7 days.
    assert _put_policy(client, setup, "ask", '"1"').status_code == 200
    later = _upload(client, setup, _dated(13, "2026-01-01"), purpose="training")
    _window(db, setup, incumbent.id, week + timedelta(days=7))
    assert run_retrain_tick(db) == {"compared": 0, "asked": 0, "started": 0}  # rate limit: auto ran this week
    assert run_retrain_tick(db, now=datetime.now(UTC) + timedelta(days=8))["asked"] == 1
    ask = next(i for i in _inbox(client, setup, "needs_decision") if i["kind"] == "retrain_ask")
    assert ask["summary"] == "Retrain on newest data?" and ask["actions"] == [{
        "name": "retrain", "operation": "POST /v1/projects/{project_id}/retrain", "path_params": {"project_id": str(pid)},
        "body": {"dataset_id": later["id"]}, "allowed": True}]
    token_ask = next(i for i in _inbox(client, setup, "needs_decision", _bearer(secret)) if i["kind"] == "retrain_ask")
    assert token_ask["actions"][0]["allowed"] is False
    newest = _upload(client, setup, _dated(14, "2026-06-01", n=200), purpose="training")
    _window(db, setup, incumbent.id, week + timedelta(days=14))
    assert run_retrain_tick(db) == {"compared": 0, "asked": 0, "started": 0}  # one question per 7 days
    assert _status(client, setup)["newer_dataset_id"] == newest["id"]
    # D8a: the server's rule, so the web never guesses: people with ML execution only, never tokens.
    assert _status(client, setup)["can_start"] is True
    assert _status(client, setup, _bearer(secret))["can_start"] is False
    assert _status(client, setup, _h(setup, _member(db, setup, WorkspaceRole.VIEWER)))["can_start"] is False
    assert _status(client, setup, _h(setup, _member(db, setup, WorkspaceRole.ML_ENGINEER)))["can_start"] is True

    # 3. A person answers: quota applies, idempotent, single flight; compared on later rows; switch.
    _assert_envelope(client.post(f"/v1/projects/{pid}/retrain", json={"dataset_id": old_months["id"]},
                                 headers=_h(setup, key=_key())), 409, "dataset_already_retrained")  # one final test
    monkeypatch.setattr(get_settings(), "ml_max_active_runs_per_workspace", 0)
    _assert_envelope(client.post(f"/v1/projects/{pid}/retrain", json={"dataset_id": later["id"]},
                                 headers=_h(setup, key=_key())), 429, "run_quota_exceeded")
    monkeypatch.undo()
    assert db.scalar(select(func.count()).select_from(RetrainRequest).where(RetrainRequest.project_id == pid)) == 2
    _assert_envelope(client.post(f"/v1/projects/{pid}/retrain", json={"dataset_id": base["id"]},
                                 headers=_h(setup, key=_key())), 409, "dataset_not_newer")
    key = _key()
    started = client.post(f"/v1/projects/{pid}/retrain", json={"dataset_id": later["id"]}, headers=_h(setup, key=key))
    assert started.status_code == 202, started.text
    replay = client.post(f"/v1/projects/{pid}/retrain", json={"dataset_id": later["id"]}, headers=_h(setup, key=key))
    assert replay.headers["Location"] == started.headers["Location"] and replay.headers.get(
        "Idempotent-Replayed") == "true"
    _assert_envelope(client.post(f"/v1/projects/{pid}/retrain", json={"dataset_id": later["id"]},
                                 headers=_h(setup, key=_key())), 409, "retrain_in_progress")
    assert started.json()["state"] == "retraining" and started.json()["request"]["origin"] == "person"
    assert started.json()["can_start"] is False
    assert any(i["kind"] == "retrain_ask" and i["status"] == "closed" for i in _inbox(client, setup, "done"))
    run_id = UUID(started.headers["Location"].rsplit("/", 1)[1])
    assert _work(db, run_id).status == "completed"
    assert run_retrain_tick(db)["compared"] == 1
    ready = _status(client, setup)
    comparison = ready["comparison"]
    assert ready["state"] == "waiting_for_you" and comparison["status"] == "compared", comparison
    assert comparison["time_column"] == "signup_date" and comparison["final_test_start"] > comparison[
        "incumbent_training_end"]
    assert set(comparison["incumbent"]["metrics"]) == {"precision", "recall", "f1", "accuracy", "roc_auc", "pr_auc"}
    assert set(comparison["challenger"]["metrics"]) == set(comparison["incumbent"]["metrics"])
    for side in ("challenger", "incumbent"):  # real figures on the same rows, each at its own threshold
        assert comparison[side]["metric_value"] is not None and comparison[side]["decision_threshold"] is not None
        assert all(isinstance(value, float) for value in comparison[side]["metrics"].values()), comparison[side]
    assert comparison["incumbent"]["n_rows"] == comparison["final_test_rows"] == comparison["challenger"]["n_rows"]
    withheld = _status(client, setup, _bearer(secret))
    assert (withheld["comparison"], withheld["comparison_withheld"], withheld["state"]) == (None, True, "waiting_for_you")
    with pytest.raises(DBAPIError):  # evidence is written once
        with db.begin_nested():
            db.execute(text("UPDATE retrain_comparisons SET status = 'not_comparable' WHERE experiment_id = :r"),
                       {"r": run_id})
    db.rollback()

    proposal = client.get(f"/v1/decisions/{ready['proposal_id']}", headers=_h(setup)).json()
    moves = {m["ref_kind"]: m["to"]["id"] for m in proposal["details"]["ref_moves"]}
    challenger = db.scalar(select(ModelVersion).where(ModelVersion.pipeline_run_id == run_id))
    assert proposal["actor"] == {"kind": "rule", "user_id": str(setup["alpha_admin"].id), "rule": RULE_RETRAIN_CHALLENGER,
                                 "agent_run_id": None, "service_token_id": None}
    assert {k: moves[k] for k in ("dataset", "champion_model")} == {"dataset": later["id"],
                                                                     "champion_model": str(challenger.id)}
    assert set(moves) >= {"dataset", "split_plan", "champion_model"}
    refs = {r["ref_kind"]: r for r in client.get(f"/v1/projects/{pid}/refs", headers=_h(setup)).json()["items"]}
    accept = client.post(
        f"/v1/projects/{pid}/refs/champion_model", headers={**_h(setup, key=_key()),
                                                            "If-Match": refs["champion_model"]["etag"]},
        json={"target_id": str(challenger.id), "rationale": "switch to the retrained model", "proposal_id": proposal["id"],
              "evidence_refs": [{"kind": "candidate", "id": str(challenger.selected_candidate_id),
                                 "scope": "final_holdout"}],
              "companion_moves": [{"ref_kind": kind, "target_id": target, "expected_version": refs[kind]["version"]
                                   if kind in refs else None} for kind, target in moves.items()
                                  if kind != "champion_model"]})
    assert accept.status_code in (200, 201), accept.text
    after = {r["ref_kind"]: r["target"]["id"] for r in client.get(f"/v1/projects/{pid}/refs",
                                                                 headers=_h(setup)).json()["items"]}
    assert {kind: after[kind] for kind in moves} == moves  # re-baselined: data, test design, recipe, model
    assert _status(client, setup)["state"] == "switched"
    kinds = [i["kind"] for i in client.get("/v1/activity", params={"project_id": str(pid), "limit": 100},
                                           headers=_h(setup)).json()["items"]]
    assert kinds.count("retrain_requested") == 3 and kinds.count("retrain_compared") == 2
    assert db.scalar(select(func.count()).select_from(RetrainComparison)) == 2
    assert db.scalar(select(func.count()).select_from(LlmInvocation).where(LlmInvocation.llm_used.is_(True))) == 0
