"""Weekly scheduled scoring: one schedule per project, saved by a person; the worker tick scores
the week's newest scoring file with the model in use approved for scoring, once per slot.

Real auto-train, approval, worker tick and worker job (``dclab worker run --once``) with a fake
clock for the weekly slots; DB constraints, append-only run log, tenancy and authority.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    BatchPrediction,
    Dataset,
    ExecutionRequest,
    MlJob,
    ModelRelease,
    ModelVersion,
    ScoringSchedule,
    ScoringScheduleRun,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.domain.scoring_schedules import ScoringScheduleWrite
from app.services import batch_prediction_service as bps
from app.services import scoring_schedule_service as sss
from app.services.ml_job_service import process_next_job
from app.services.scoring_schedule_service import (
    next_slot_after,
    run_scoring_schedule_tick,
    save_schedule,
    slot_at_or_before,
)
from app.services.service_token_service import create_service_token
from test_v1_batch_predictions import _download, _frame, _predict, _trained, _upload
from test_v1_contract_conventions import _assert_envelope, _headers
from test_v1_resources_experiments import _h, _key, _member, setup  # noqa: F401 (fixture)

MONDAY = datetime(2030, 1, 7, 8, 0, tzinfo=UTC)  # a Monday, 08:00 UTC
SLOT = MONDAY.replace(hour=9)  # the schedule below: Mondays 09:00 UTC
WEEK = timedelta(days=7)


def _url(setup, suffix: str = "") -> str:  # noqa: F811
    return f"/v1/projects/{setup['alpha_project'].id}/scoring-schedule{suffix}"


def _save(db, setup, actor=None, *, weekday: int = 0, hour: int = 9, enabled: bool = True,  # noqa: F811
          now: datetime = MONDAY) -> None:
    save_schedule(db, actor=actor or setup["alpha_admin"], workspace_id=setup["alpha"].id,
                  project_id=setup["alpha_project"].id,
                  request=ScoringScheduleWrite(weekday=weekday, hour_utc=hour, enabled=enabled), now=now)
    db.commit()


def _tick(db, at: datetime) -> list[tuple[datetime, str, str | None]]:
    rows = run_scoring_schedule_tick(db, now=at)
    db.expire_all()
    return [(row.planned_for, row.outcome, row.reason_code) for row in rows]


def _runs(db, setup) -> list[ScoringScheduleRun]:  # noqa: F811
    db.expire_all()
    return list(db.scalars(select(ScoringScheduleRun).where(ScoringScheduleRun.project_id == setup["alpha_project"].id)
                           .order_by(ScoringScheduleRun.planned_for)))


def _schedule(db, setup) -> ScoringSchedule:  # noqa: F811
    db.expire_all()
    return db.scalar(select(ScoringSchedule).where(ScoringSchedule.project_id == setup["alpha_project"].id))


def test_weekly_slots_are_utc_and_never_behind():
    assert next_slot_after(MONDAY, 0, 9) == SLOT
    assert next_slot_after(SLOT, 0, 9) == SLOT + WEEK  # exactly at a slot: the next week
    assert slot_at_or_before(SLOT + timedelta(days=6, hours=14), 0, 9) == SLOT
    assert slot_at_or_before(MONDAY, 0, 9) == SLOT - WEEK
    assert slot_at_or_before(MONDAY - timedelta(hours=22), 6, 9) == SLOT - timedelta(days=1)  # Sunday 09:00
    # Any offset is read as UTC: 12:30 at +04:00 is 08:30 UTC, before Monday's 09:00 UTC slot.
    assert next_slot_after(datetime(2030, 1, 7, 12, 30, tzinfo=timezone(timedelta(hours=4))), 0, 9) == SLOT
    # The due scan filters on the bare column, so Postgres can use the partial index (WHERE enabled).
    sql = str(sss.due_query(SLOT, {uuid4()}).compile(dialect=postgresql.dialect()))
    assert "WHERE scoring_schedules.enabled AND" in sql and "IS true" not in sql and "SKIP LOCKED" in sql


def test_save_read_history_and_authority(client, db_session, setup):  # noqa: F811
    db = db_session
    empty = client.get(_url(setup), headers=_h(setup))
    assert empty.status_code == 200 and empty.json()["schedule"] is None and empty.json()["status"] == "off"
    assert "does not retrain" in empty.json()["about"]

    key = _key()
    body = {"weekday": 0, "hour_utc": 9, "enabled": True, "rationale": "Monday list for the sales team"}
    saved = client.put(_url(setup), json=body, headers=_h(setup, key=key))
    assert saved.status_code == 200, saved.text
    state = saved.json()
    schedule = state["schedule"]
    assert state["status"] == "on" and schedule["weekday_name"] == "Monday" and schedule["timezone"] == "UTC"
    next_slot = datetime.fromisoformat(schedule["next_planned_for"])
    assert next_slot > datetime.now(UTC) and (next_slot.weekday(), next_slot.hour, next_slot.minute) == (0, 9, 0)
    assert schedule["updated_by_user_id"] == str(setup["alpha_admin"].id) and schedule["owner_can_score"]
    assert schedule["warning"] == "Runs will score nothing until the project has a model in use."
    replay = client.put(_url(setup), json=body, headers=_h(setup, key=key))
    assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
    _assert_envelope(client.put(_url(setup), json={**body, "hour_utc": 10}, headers=_h(setup, key=key)), 409,
                     "idempotency_key_conflict")
    for bad in ({**body, "weekday": 7}, {**body, "hour_utc": 24}, {**body, "extra": 1}):
        _assert_envelope(client.put(_url(setup), json=bad, headers=_h(setup, key=_key())), 422, "validation_failed")

    # Each save is an accepted History entry with the person's reason; it is never corrected generically.
    records = client.get(f"/v1/projects/{setup['alpha_project'].id}/decisions",
                         params={"decision_type": "scoring_schedule_changed"}, headers=_h(setup)).json()["items"]
    assert len(records) == 1
    record = records[0]
    assert (record["state"], record["actor"]["kind"], record["rationale"]) == (
        "accepted", "human", "Monday list for the sales team")
    assert record["facts"]["weekday"] == 0 and record["facts"]["previous"] is None
    assert record["facts"]["rationale_default"] is False  # the person's own words
    _assert_envelope(client.post(f"/v1/decisions/{record['id']}/supersede", json={"rationale": "x"},
                                 headers=_h(setup, key=_key())), 403, "decision_actor_not_permitted")
    _assert_envelope(client.post(f"/v1/projects/{setup['alpha_project'].id}/decisions",
                                 json={"action": "record", "decision_type": "scoring_schedule_changed",
                                       "subject": {"kind": "project"}, "rationale": "x"},
                                 headers=_h(setup, key=_key())), 422, "validation_failed")

    # Viewers read, never save; service tokens read (no user ids), never save; other tenants see nothing.
    viewer = _member(db, setup, WorkspaceRole.VIEWER)
    assert client.get(_url(setup), headers=_h(setup, viewer)).status_code == 200
    _assert_envelope(client.put(_url(setup), json=body, headers=_h(setup, viewer, key=_key())), 403, "forbidden")
    token = create_service_token(db, creator=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="w",
                                 scopes=("read", "experiments:write", "decisions:propose"), expires_in_days=1,
                                 current_password="test-password")[1]
    db.commit()
    bearer = {"Authorization": f"Bearer {token}"}
    as_token = client.get(_url(setup), headers=bearer)
    assert as_token.status_code == 200 and as_token.json()["schedule"]["updated_by_user_id"] is None
    assert as_token.json()["schedule"]["created_by_user_id"] is None
    assert client.get(_url(setup, "/runs"), headers=bearer).status_code == 200
    _assert_envelope(client.put(_url(setup), json=body, headers={**bearer, "Idempotency-Key": _key()}), 403,
                     "service_token_not_permitted")
    beta = _headers(setup["beta_admin"], setup["beta"].id)
    _assert_envelope(client.get(_url(setup), headers=beta), 404, "not_found")
    _assert_envelope(client.get(_url(setup, "/runs"), headers=beta), 404, "not_found")
    _assert_envelope(client.put(_url(setup), json=body, headers={**beta, "Idempotency-Key": _key()}), 404, "not_found")
    _assert_envelope(client.get(_url(setup, "/runs"), params={"cursor": "c1.x.y"}, headers=_h(setup)), 400,
                     "invalid_cursor")

    # Turning it off keeps it (and says so); the platform kill switch pauses a schedule that is on.
    off = client.put(_url(setup), json={"weekday": 3, "hour_utc": 5, "enabled": False}, headers=_h(setup, key=_key()))
    assert off.status_code == 200 and off.json()["status"] == "off"
    assert off.json()["schedule"]["next_planned_for"] is None and off.json()["schedule"]["warning"] is None
    records = client.get(f"/v1/projects/{setup['alpha_project'].id}/decisions",
                         params={"decision_type": "scoring_schedule_changed"}, headers=_h(setup)).json()["items"]
    # A server-written reason describes what was turned off and is marked as not the person's words.
    assert records[0]["rationale"] == "Weekly scoring turned off (was Mondays at 09:00 UTC)."
    assert records[0]["facts"]["previous"] == {"weekday": 0, "hour_utc": 9, "enabled": True}
    assert records[0]["facts"]["rationale_default"] is True
    client.put(_url(setup), json={"weekday": 2, "hour_utc": 17}, headers=_h(setup, key=_key()))
    settings = get_settings()
    original = (settings.scoring_schedule_enabled, settings.ml_job_dispatcher)
    try:
        settings.scoring_schedule_enabled = False
        paused = client.get(_url(setup), headers=_h(setup)).json()
        assert paused["status"] == "paused_by_platform" and "SCORING_SCHEDULE_ENABLED" in paused["status_message"]
        settings.scoring_schedule_enabled, settings.ml_job_dispatcher = True, "thread"  # no worker runs ticks
        paused = client.get(_url(setup), headers=_h(setup)).json()
        assert paused["status"] == "paused_by_platform" and "ML_JOB_DISPATCHER" in paused["status_message"]
    finally:
        settings.scoring_schedule_enabled, settings.ml_job_dispatcher = original
    assert client.get(_url(setup), headers=_h(setup)).json()["status"] == "on"
    assert db.scalar(select(func.count()).select_from(ScoringSchedule)
                     .where(ScoringSchedule.project_id == setup["alpha_project"].id)) == 1


def test_tick_outcomes_constraints_and_append_only(db_session, setup, monkeypatch):  # noqa: F811
    db = db_session
    engineer = _member(db, setup, WorkspaceRole.ML_ENGINEER)
    _save(db, setup, engineer)  # runs act as the engineer
    assert _tick(db, SLOT - timedelta(minutes=30)) == []  # not due yet
    assert _tick(db, SLOT + timedelta(minutes=5)) == [(SLOT, "skipped_no_model_in_use", None)]
    assert _tick(db, SLOT + timedelta(minutes=5)) == []  # the same tick again: nothing new
    schedule = _schedule(db, setup)
    assert (schedule.last_planned_for, schedule.next_planned_for) == (SLOT, SLOT + WEEK)

    # Kill switch: nothing happens while it is off; the schedule stays due.
    monkeypatch.setattr(get_settings(), "scoring_schedule_enabled", False)
    assert _tick(db, SLOT + WEEK + timedelta(minutes=5)) == []
    monkeypatch.setattr(get_settings(), "scoring_schedule_enabled", True)

    # The person it acts as lost the role: skipped, the schedule stays on with a plain warning.
    membership = db.scalar(select(WorkspaceMembership).where(WorkspaceMembership.user_id == engineer.id,
                                                             WorkspaceMembership.workspace_id == setup["alpha"].id))
    membership.suspended_at = datetime.now(UTC)
    db.commit()
    assert _tick(db, SLOT + WEEK + timedelta(minutes=5)) == [(SLOT + WEEK, "skipped_owner_not_allowed", None)]
    state = sss.schedule_state(db, actor=setup["alpha_admin"], workspace_id=setup["alpha"].id,
                               project_id=setup["alpha_project"].id, agent=False)
    assert state.status == "on" and not state.schedule.owner_can_score
    assert state.schedule.warning.startswith("Nothing scored: the person who saved the schedule")

    # The worker was down for more than a day past the slot: one skipped_missed, never a backlog.
    assert _tick(db, SLOT + 4 * WEEK + timedelta(hours=30)) == [
        (SLOT + 4 * WEEK, "skipped_missed", "worker_was_down")]
    assert _schedule(db, setup).next_planned_for == SLOT + 5 * WEEK
    # A slot already handled is never handled twice (a re-saved or reset schedule only advances).
    db.execute(text("UPDATE scoring_schedules SET next_planned_for = :slot WHERE project_id = :p"),
               {"slot": SLOT + 4 * WEEK, "p": setup["alpha_project"].id})
    db.commit()
    assert _tick(db, SLOT + 4 * WEEK + timedelta(hours=30)) == []
    assert _schedule(db, setup).next_planned_for == SLOT + 5 * WEEK and len(_runs(db, setup)) == 3

    # The run log is append-only and constrained.
    first = _runs(db, setup)[0]
    for statement in ("UPDATE scoring_schedule_runs SET reason_code = 'run_limit' WHERE id = :id",
                      "DELETE FROM scoring_schedule_runs WHERE id = :id"):
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(text(statement), {"id": first.id})
        db.rollback()
    base = {"workspace_id": setup["alpha"].id, "project_id": setup["alpha_project"].id,
            "schedule_id": first.schedule_id}
    for row in ({**base, "planned_for": first.planned_for, "outcome": "skipped_no_new_file"},  # same slot
                {**base, "planned_for": SLOT + 9 * WEEK, "outcome": "queued"},  # queued needs its prediction
                {**base, "planned_for": SLOT + 9 * WEEK, "outcome": "done"},
                {**base, "planned_for": SLOT + 9 * WEEK, "outcome": "failed_to_queue", "reason_code": "oops"},
                {**base, "planned_for": SLOT + 9 * WEEK, "outcome": "skipped_no_approval",
                 "model_release_id": uuid4()},  # a release without its version
                {**base, "workspace_id": setup["beta"].id, "planned_for": SLOT + 9 * WEEK,
                 "outcome": "skipped_no_new_file"}):  # another tenant's schedule
        with pytest.raises(IntegrityError):
            db.add(ScoringScheduleRun(**row))
            db.flush()
        db.rollback()
    for change in ("weekday = 7", "hour_utc = 24"):
        with pytest.raises(IntegrityError):
            db.execute(text(f"UPDATE scoring_schedules SET {change} WHERE id = :id"), {"id": first.schedule_id})
        db.rollback()
    with pytest.raises(IntegrityError):  # one schedule per project
        db.add(ScoringSchedule(workspace_id=setup["alpha"].id, project_id=setup["alpha_project"].id, weekday=1,
                               hour_utc=1, enabled=True, next_planned_for=SLOT))
        db.flush()
    db.rollback()


def test_due_scan_claims_each_schedule_once_under_concurrency(db_session, setup):  # noqa: F811
    db = db_session
    _save(db, setup)
    at = SLOT + timedelta(minutes=1)
    first, second = Session(bind=db.get_bind()), Session(bind=db.get_bind())
    try:
        claimed = sss._claim_due(first, at, set())  # worker A holds the row lock, mid-run
        assert claimed is not None
        assert run_scoring_schedule_tick(second, now=at) == []  # worker B skips it (SKIP LOCKED)
        first.rollback()  # A crashed: nothing was written
        assert [row.outcome for row in run_scoring_schedule_tick(second, now=at)] == ["skipped_no_model_in_use"]
        assert run_scoring_schedule_tick(first, now=at) == []  # A again: no longer due
    finally:
        first.close()
        second.close()
    assert len(_runs(db, setup)) == 1


def _approve(client, db, setup, experiment, mv_id) -> ModelRelease:  # noqa: F811
    project_id = experiment.project_id
    refs = {r["ref_kind"]: r for r in client.get(f"/v1/projects/{project_id}/refs", headers=_h(setup)).json()["items"]}
    model = db.get(ModelVersion, mv_id)
    companions = [{"ref_kind": "feature_recipe", "target_id": str(model.feature_set_version_id),
                   "expected_version": refs["feature_recipe"]["version"]}] if "feature_recipe" in refs else []
    approve = client.post(
        f"/v1/projects/{project_id}/refs/champion_model",
        json={"target_id": str(mv_id), "rationale": "approved for scoring", "companion_moves": companions,
              "evidence_refs": [{"kind": "candidate", "id": str(model.selected_candidate_id), "scope": "final_holdout"}]},
        headers=_h(setup, key=_key(), **{"If-Match": refs["champion_model"]["etag"]}),
    )
    assert approve.status_code == 200, approve.text
    return db.scalar(select(ModelRelease).where(ModelRelease.model_version_id == mv_id)
                     .order_by(ModelRelease.created_at.desc()).limit(1))


def _predictions(db) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(BatchPrediction))


def test_scheduled_scoring_end_to_end_through_the_worker(client, db_session, setup, monkeypatch):  # noqa: F811
    from app.cli.main import cmd_worker_run

    db = db_session
    frame = _frame("binary", n=200)
    experiment, mv_id = _trained(client, db, setup, frame, "label")
    _save(db, setup)  # Mondays 09:00 UTC, created after the training upload

    # The first run's rule put a model in use, but no person approved it: never scored on a schedule.
    assert _tick(db, SLOT + timedelta(minutes=5)) == [(SLOT, "skipped_no_approval", None)]
    release = _approve(client, db, setup, experiment, mv_id)
    week = SLOT + WEEK
    assert _tick(db, week + timedelta(minutes=5)) == [(week, "skipped_no_new_file", None)]
    assert _runs(db, setup)[-1].model_release_id == release.id

    # A copy of the training file uploaded for scoring is refused like "Score new data" refuses it.
    copy = _upload(client, setup, frame)
    source = db.get(Dataset, experiment.source_dataset_id)
    assert db.get(Dataset, UUID(copy["id"])).content_digest == source.content_digest
    week += WEEK
    assert _tick(db, week + timedelta(minutes=5)) == [(week, "skipped_no_new_file", None)]

    # A new scoring file while the workspace is at its run limit: failed_to_queue, nothing queued.
    new_file = _upload(client, setup, frame.drop(columns=["label"]).head(40))
    before = _predictions(db)
    monkeypatch.setattr(get_settings(), "ml_max_active_runs_per_workspace", 0)
    week += WEEK
    assert _tick(db, week + timedelta(minutes=5)) == [(week, "failed_to_queue", "run_limit")]
    assert _predictions(db) == before
    monkeypatch.setattr(get_settings(), "ml_max_active_runs_per_workspace", 20)
    # The person lost the role between the tick's check and the queueing: owner-not-allowed, not a failure.
    allowed = bps.can_perform_ml_write
    monkeypatch.setattr(bps, "can_perform_ml_write", lambda *_args, **_kwargs: False)
    week += WEEK
    assert _tick(db, week + timedelta(minutes=5)) == [(week, "skipped_owner_not_allowed", None)]
    monkeypatch.setattr(bps, "can_perform_ml_write", allowed)

    # The real worker loop (`dclab worker run --once`) with a fake clock: its tick queues the week's
    # scoring, then the same iteration claims and runs the job.
    week += WEEK
    monkeypatch.setattr(sss, "_now", lambda: week + timedelta(minutes=5))
    monkeypatch.setattr("app.cli.main._session", lambda: Session(bind=db.get_bind()))
    monkeypatch.setattr("app.config._PROCESS_ROLE", {"worker": False})  # restored after the test
    monkeypatch.setenv("PGAPPNAME", "pytest")  # the worker names its connections (P8.5-A); restored after
    assert cmd_worker_run(argparse.Namespace(once=True, poll_seconds=None)) == 0
    assert _predictions(db) == before + 1
    run = _runs(db, setup)[-1]
    assert (run.planned_for, run.outcome, run.input_dataset_id, run.model_release_id) == (
        week, "queued", UUID(new_file["id"]), release.id)
    prediction = db.get(BatchPrediction, run.batch_prediction_id)
    assert (prediction.status, prediction.model_release_id, prediction.input_dataset_id) == (
        "completed", release.id, UUID(new_file["id"]))
    assert prediction.requested_by_user_id == setup["alpha_admin"].id
    request = db.get(ExecutionRequest, prediction.execution_request_id)
    assert request.source_surface == "system" and request.request_spec["scoring_schedule_run_id"] == str(run.id)
    read = client.get(f"/v1/predictions/{prediction.id}", headers=_h(setup)).json()
    assert read["scheduled"] is True and datetime.fromisoformat(read["scheduled_for"]) == week
    assert read["release"]["id"] == str(release.id) and len(_download(client, setup, str(prediction.id))) == 40
    state = client.get(_url(setup), headers=_h(setup)).json()
    assert state["recent_runs"][0]["outcome"] == "queued" and state["recent_runs"][0]["batch_prediction_id"] == str(
        prediction.id)
    assert state["schedule"]["warning"] is None and len(state["recent_runs"]) == 6

    # The same slot again (a second tick, or a reset schedule): no second prediction.
    assert _tick(db, week + timedelta(minutes=5)) == []
    db.execute(text("UPDATE scoring_schedules SET next_planned_for = :slot WHERE project_id = :p"),
               {"slot": week, "p": setup["alpha_project"].id})
    db.commit()
    assert _tick(db, week + timedelta(minutes=5)) == [] and _predictions(db) == before + 1

    # The scored file is not scored again; a file scored by hand with this model is not either.
    week += WEEK
    assert _tick(db, week + timedelta(minutes=5)) == [(week, "skipped_no_new_file", None)]
    by_hand = _upload(client, setup, frame.drop(columns=["label"]).tail(30))
    manual = _predict(client, setup, mv_id, by_hand["id"])
    assert manual.status_code == 202 and manual.json()["scheduled"] is False
    week += WEEK
    assert _tick(db, week + timedelta(minutes=5)) == [(week, "skipped_no_new_file", None)]

    # The run list pages newest first.
    page = client.get(_url(setup, "/runs"), params={"limit": 4}, headers=_h(setup)).json()
    rest = client.get(_url(setup, "/runs"), params={"limit": 4, "cursor": page["next_cursor"]}, headers=_h(setup)).json()
    slots = [item["planned_for"] for item in page["items"] + rest["items"]]
    assert len(slots) == 8 and slots == sorted(slots, reverse=True) and rest["next_cursor"] is None
    assert page["items"][0]["message"].startswith("Nothing scored: no new file")

    # The job re-checks a scheduled scoring before it loads anything. A rollback after queueing
    # (approve the previous version again) fails it; scoring by hand keeps today's checks.
    branch = client.post(f"/v1/experiments/{experiment.id}/branches", headers=_h(setup, key=_key()), json={
        "intent": "without xgboost", "changes": [{"kind": "family_exclude", "family": "xgboost"}]})
    assert branch.status_code == 202, branch.text
    branch_id = UUID(branch.json()["id"])
    job = db.scalar(select(MlJob).where(MlJob.pipeline_run_id == branch_id))
    assert process_next_job(db, job_id=job.id, claimed_by="sched-test").status == "completed"
    db.expire_all()
    v2 = db.scalar(select(ModelVersion.id).where(ModelVersion.pipeline_run_id == branch_id))
    release_v2 = _approve(client, db, setup, experiment, v2)
    _upload(client, setup, frame.drop(columns=["label"]).iloc[50:90])
    week += WEEK
    assert _tick(db, week + timedelta(minutes=5)) == [(week, "queued", None)]
    queued = _runs(db, setup)[-1]
    assert queued.model_release_id == release_v2.id
    rollback = _approve(client, db, setup, experiment, mv_id)
    assert rollback.id not in (release.id, release_v2.id)
    failed = _run_job(db, queued.batch_prediction_id)
    assert (failed.status, failed.error_code, failed.output_artifact_id, failed.rows_out) == (
        "failed", "release_mismatch", None, None)
    assert failed.error_message == "the model in use changed before this scheduled run started"
    assert _run_job(db, UUID(manual.json()["id"])).status == "completed"  # by hand, under the earlier approval

    # The person it acts as can no longer score when the job runs: failed, nothing scored.
    _upload(client, setup, frame.drop(columns=["label"]).iloc[100:140])
    week += WEEK
    assert _tick(db, week + timedelta(minutes=5)) == [(week, "queued", None)]
    queued = _runs(db, setup)[-1]
    assert queued.model_release_id == rollback.id
    db.execute(text("UPDATE users SET is_active = false WHERE id = :id"), {"id": setup["alpha_admin"].id})
    db.commit()
    failed = _run_job(db, queued.batch_prediction_id)
    assert (failed.status, failed.error_code, failed.output_artifact_id, failed.rows_out) == (
        "failed", "schedule_owner_not_allowed", None, None)


def _run_job(db, prediction_id: UUID) -> BatchPrediction:
    db.expire_all()
    job = db.scalar(select(MlJob).where(MlJob.target_id == prediction_id))
    process_next_job(db, job_id=job.id, claimed_by="sched-test")
    db.expire_all()
    return db.get(BatchPrediction, prediction_id)
