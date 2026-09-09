"""Durable ML job boundary: persist in the API transaction, claim in a worker."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.models import ClientLabUpload, DEFAULT_WORKSPACE_ID, MlJob
from app.domain.ml_jobs import (
    HANDLER_LABS_AUTO_TRAIN,
    HANDLER_VERSION_LABS_AUTO_TRAIN,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_TYPE_AUTO_TRAIN,
)
from app.services.job_dispatcher import (
    PostgresJobDispatcher,
    get_job_dispatcher,
    start_local_ml_worker,
    uses_in_process_worker,
)
from app.services.ml_job_service import (
    claim_next_queued_job,
    commit_job_heartbeat,
    create_auto_train_job,
    execute_job,
    process_next_job,
    recover_abandoned_jobs,
)


def _make_upload(db_session, *, kind: str = "plain_text", record_count: int = 3) -> ClientLabUpload:
    row = ClientLabUpload(
        workspace_id=DEFAULT_WORKSPACE_ID,
        category="Revenue",
        original_filename="upload.csv",
        stored_path="/tmp/durable-ml-job.csv",
        kind=kind,
        record_count=record_count,
        fields_noticed=[],
        has_named_fields=False,
        pipeline_status="queued",
        client_status="queued",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _queued_job(db_session, *, max_attempts: int = 3) -> MlJob:
    upload = _make_upload(db_session)
    return create_auto_train_job(
        db_session,
        workspace_id=upload.workspace_id,
        upload_id=upload.id,
        max_attempts=max_attempts,
    )


def test_thread_dispatcher_starts_in_process_worker(monkeypatch):
    from app.services import job_dispatcher as jd

    monkeypatch.setattr(get_settings(), "ml_job_dispatcher", "thread")
    with jd._local_worker_lock:
        previous = jd._local_worker_stop
        jd._local_worker_stop = None
    try:
        assert uses_in_process_worker() is True
        stop = start_local_ml_worker()
        assert stop is not None
        assert start_local_ml_worker() is stop
    finally:
        if jd._local_worker_stop is not None:
            jd._local_worker_stop.set()
        with jd._local_worker_lock:
            jd._local_worker_stop = previous


def test_production_dispatcher_is_postgres_not_a_thread():
    dispatcher = get_job_dispatcher()
    assert dispatcher.name == "postgres"
    assert isinstance(dispatcher, PostgresJobDispatcher)
    dispatcher.dispatch(upload_id=DEFAULT_WORKSPACE_ID)
    assert dispatcher.dispatch(upload_id=DEFAULT_WORKSPACE_ID) is None
    assert uses_in_process_worker() is False
    assert start_local_ml_worker() is None


def test_upload_persists_queued_job_and_request_return_does_not_run_it(
    auth_client, db_session
):
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": ("customers.csv", b"tenure,churn\n1,Yes\n2,No\n", "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "queued"
    upload_id = body["id"]
    db_session.expire_all()
    upload = db_session.get(ClientLabUpload, upload_id)
    assert upload is not None
    assert upload.pipeline_status == "queued"
    job = db_session.scalar(select(MlJob).where(MlJob.upload_id == upload.id))
    assert job is not None
    assert job.job_type == JOB_TYPE_AUTO_TRAIN
    assert job.handler_key == HANDLER_LABS_AUTO_TRAIN
    assert job.handler_version == HANDLER_VERSION_LABS_AUTO_TRAIN
    assert job.target_id == upload.id
    assert job.upload_id == upload.id
    assert job.workspace_id == upload.workspace_id
    assert job.project_id is not None
    assert job.execution_request_id is not None
    assert job.workflow_run_id is not None
    assert job.pipeline_run_id is not None
    assert job.status == JOB_QUEUED
    assert job.attempts == 0
    assert job.priority == 0
    assert job.available_at is not None
    assert job.claimed_by is None
    assert job.lease_expires_at is None
    assert job.payload.get("upload_id") == upload_id
    assert job.started_at is None
    assert job.completed_at is None


def test_api_restart_does_not_lose_queued_job(db_session, test_engine):
    job = _queued_job(db_session)
    job_id = job.id
    upload_id = job.upload_id
    db_session.commit()

    SessionLocal = sessionmaker(bind=test_engine)
    restarted = SessionLocal()
    try:
        loaded = restarted.get(MlJob, job_id)
        assert loaded is not None
        assert loaded.status == JOB_QUEUED
        processed = process_next_job(restarted)
        assert processed is not None
        assert processed.id == job_id
        assert processed.status == JOB_COMPLETED
        upload = restarted.get(ClientLabUpload, upload_id)
        assert upload is not None
        assert upload.pipeline_status == "skipped"
    finally:
        restarted.close()


def test_atomic_claim_and_two_workers_cannot_claim_same_job(db_session, test_engine):
    job = _queued_job(db_session)
    job_id = job.id
    db_session.commit()

    SessionLocal = sessionmaker(bind=test_engine)
    worker_a = SessionLocal()
    worker_b = SessionLocal()
    try:
        claimed_a = claim_next_queued_job(worker_a)
        claimed_b = claim_next_queued_job(worker_b)
        assert claimed_a is not None
        assert claimed_a.id == job_id
        assert claimed_a.status == JOB_RUNNING
        assert claimed_a.attempts == 1
        assert claimed_b is None
        worker_a.commit()
        worker_b.rollback()
    finally:
        worker_a.close()
        worker_b.close()

    db_session.expire_all()
    leftover = claim_next_queued_job(db_session)
    assert leftover is None
    stored = db_session.get(MlJob, job_id)
    assert stored is not None
    assert stored.status == JOB_RUNNING


def test_retry_after_failure_then_exhausts_max_attempts(db_session):
    job = _queued_job(db_session, max_attempts=2)
    db_session.commit()

    def boom(_db, _upload_id):
        raise RuntimeError("synthetic worker crash")

    first = process_next_job(db_session, runner=boom)
    assert first is not None
    assert first.status == JOB_QUEUED
    assert first.attempts == 1
    assert "synthetic worker crash" in (first.failure_reason or "")

    second = process_next_job(db_session, runner=boom)
    assert second is not None
    assert second.status == JOB_FAILED
    assert second.attempts == 2
    assert second.completed_at is not None

    third = process_next_job(db_session, runner=boom)
    assert third is None


def test_abandoned_running_job_is_requeued_then_failed_at_max_attempts(db_session):
    job = _queued_job(db_session, max_attempts=2)
    now = datetime.now(UTC)
    job.status = JOB_RUNNING
    job.attempts = 1
    job.started_at = now - timedelta(minutes=20)
    job.heartbeat_at = now - timedelta(minutes=20)
    db_session.commit()

    recovered = recover_abandoned_jobs(
        db_session, now=now, heartbeat_timeout_seconds=60
    )
    db_session.commit()
    assert [row.id for row in recovered] == [job.id]
    db_session.refresh(job)
    assert job.status == JOB_QUEUED
    assert job.attempts == 1

    job.status = JOB_RUNNING
    job.attempts = 2
    job.started_at = now - timedelta(minutes=20)
    job.heartbeat_at = now - timedelta(minutes=20)
    db_session.commit()
    recover_abandoned_jobs(db_session, now=now, heartbeat_timeout_seconds=60)
    db_session.commit()
    db_session.refresh(job)
    assert job.status == JOB_FAILED
    assert "abandoned" in (job.failure_reason or "")
    assert job.completed_at is not None


def _complete_upload(db, upload_id):
    upload = db.get(ClientLabUpload, upload_id)
    assert upload is not None
    upload.pipeline_status = "completed"


def test_long_runner_gets_real_terminal_timestamp(db_session):
    job = _queued_job(db_session)
    db_session.commit()

    def runner(db, upload_id):
        time.sleep(0.05)
        _complete_upload(db, upload_id)

    result = process_next_job(db_session, runner=runner)
    assert result is not None
    assert result.status == JOB_COMPLETED
    assert result.queued_at is not None
    assert result.started_at is not None
    assert result.completed_at is not None
    assert result.queued_at <= result.started_at <= result.completed_at
    assert result.completed_at - result.started_at >= timedelta(milliseconds=40)


def test_heartbeat_is_visible_on_second_connection_before_training_commits(
    db_session, test_engine
):
    job = _queued_job(db_session)
    db_session.commit()
    claimed = claim_next_queued_job(db_session)
    assert claimed is not None
    db_session.commit()
    job_id = claimed.id
    before = claimed.heartbeat_at
    seen: dict[str, object] = {}

    def runner(db, upload_id):
        upload = db.get(ClientLabUpload, upload_id)
        assert upload is not None
        upload.pipeline_status = "training"
        db.flush()
        commit_job_heartbeat(job_id, bind=db.get_bind())
        other = sessionmaker(bind=test_engine)()
        try:
            visible = other.get(MlJob, job_id)
            leaked = other.get(ClientLabUpload, upload_id)
            assert visible is not None
            assert leaked is not None
            seen["heartbeat"] = visible.heartbeat_at
            seen["upload_status"] = leaked.pipeline_status
        finally:
            other.close()
        _complete_upload(db, upload_id)

    result = execute_job(db_session, claimed, runner=runner)
    assert result.status == JOB_COMPLETED
    assert seen["heartbeat"] is not None
    assert before is not None
    assert seen["heartbeat"] >= before
    assert seen["upload_status"] == "queued"


def test_live_job_with_independent_heartbeat_is_not_recovered(db_session, test_engine):
    job = _queued_job(db_session)
    db_session.commit()
    claimed = claim_next_queued_job(db_session)
    assert claimed is not None
    db_session.commit()
    job_id = claimed.id
    seen: dict[str, list] = {}

    def runner(db, upload_id):
        time.sleep(0.05)
        commit_job_heartbeat(job_id, bind=db.get_bind())
        other = sessionmaker(bind=test_engine)()
        try:
            recovered = recover_abandoned_jobs(
                other, now=datetime.now(UTC), heartbeat_timeout_seconds=0.02
            )
            seen["recovered"] = [row.id for row in recovered]
        finally:
            other.rollback()
            other.close()
        _complete_upload(db, upload_id)

    result = execute_job(db_session, claimed, runner=runner)
    assert result.status == JOB_COMPLETED
    assert seen["recovered"] == []


def test_claim_skips_jobs_not_yet_available(db_session):
    job = _queued_job(db_session)
    job.available_at = datetime.now(UTC) + timedelta(hours=1)
    db_session.commit()
    assert claim_next_queued_job(db_session) is None
    db_session.expire_all()
    stored = db_session.get(MlJob, job.id)
    assert stored is not None
    assert stored.status == JOB_QUEUED


def test_claim_prefers_higher_priority(db_session):
    from app.services.ml_job_service import create_ml_job

    low = _queued_job(db_session)
    high_upload = _make_upload(db_session)
    high = create_ml_job(
        db_session,
        workspace_id=high_upload.workspace_id,
        job_type="inspect",
        handler_key="future.inspect",
        target_id=high_upload.id,
        priority=10,
        payload={"kind": "inspect"},
    )
    db_session.commit()
    claimed = claim_next_queued_job(db_session)
    assert claimed is not None
    assert claimed.id == high.id
    assert claimed.id != low.id


def test_one_execution_request_may_own_multiple_jobs(db_session):
    from app.db.models import ExecutionRequest
    from app.domain.execution_requests import (
        OPERATION_MODEL_BUILD,
        REQUEST_ACCEPTED,
        SOURCE_SYSTEM,
    )
    from app.services.ml_job_service import create_ml_job

    auto = _queued_job(db_session)
    request = ExecutionRequest(
        workspace_id=auto.workspace_id,
        operation=OPERATION_MODEL_BUILD,
        source_surface=SOURCE_SYSTEM,
        status=REQUEST_ACCEPTED,
        request_spec={"upload_id": str(auto.upload_id)},
    )
    db_session.add(request)
    db_session.flush()
    auto.execution_request_id = request.id
    sibling = create_ml_job(
        db_session,
        workspace_id=auto.workspace_id,
        execution_request_id=request.id,
        job_type="inspect",
        handler_key="future.inspect",
        target_id=auto.target_id,
        payload={"kind": "inspect"},
    )
    db_session.commit()
    assert sibling.id != auto.id
    assert sibling.execution_request_id == auto.execution_request_id == request.id
    assert sibling.upload_id is None


def test_postgres_accepts_extensible_job_type_slug(db_session):
    from app.services.ml_job_service import create_ml_job

    upload = _make_upload(db_session)
    job = create_ml_job(
        db_session,
        workspace_id=upload.workspace_id,
        job_type="column_profile",
        handler_key="future.column_profile",
        target_id=upload.id,
        payload={"dataset_id": str(upload.id)},
    )
    db_session.commit()
    stored = db_session.get(MlJob, job.id)
    assert stored is not None
    assert stored.job_type == "column_profile"
    assert stored.handler_key == "future.column_profile"
    assert stored.upload_id is None


def test_postgres_rejects_job_payload_rows_and_closed_auto_train_without_upload(
    db_session,
):
    upload = _make_upload(db_session)
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO ml_jobs (
                    id, workspace_id, job_type, handler_key, target_id, status,
                    payload, attempts, max_attempts
                ) VALUES (
                    gen_random_uuid(), :workspace, 'inspect', 'future.inspect',
                    :target, 'queued', '{"rows":[1]}'::jsonb, 0, 3
                )
                """
            ),
            {"workspace": upload.workspace_id, "target": upload.id},
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO ml_jobs (
                    id, workspace_id, job_type, handler_key, target_id, status,
                    attempts, max_attempts
                ) VALUES (
                    gen_random_uuid(), :workspace, 'auto_train', 'labs.auto_train',
                    :target, 'queued', 0, 3
                )
                """
            ),
            {"workspace": upload.workspace_id, "target": upload.id},
        )
        db_session.commit()
    db_session.rollback()


def test_auto_train_is_the_registered_handler(db_session):
    from app.services.job_handlers import registered_handler_keys

    assert HANDLER_LABS_AUTO_TRAIN in registered_handler_keys()
    job = _queued_job(db_session)
    db_session.commit()
    result = process_next_job(db_session)
    assert result is not None
    assert result.id == job.id
    assert result.handler_key == HANDLER_LABS_AUTO_TRAIN
    assert result.status == JOB_COMPLETED


def test_unknown_handler_is_retried_then_failed(db_session):
    from app.services.ml_job_service import create_ml_job

    upload = _make_upload(db_session)
    create_ml_job(
        db_session,
        workspace_id=upload.workspace_id,
        job_type="inspect",
        handler_key="future.inspect",
        target_id=upload.id,
        max_attempts=2,
    )
    db_session.commit()
    first = process_next_job(db_session)
    assert first is not None
    assert first.status == JOB_QUEUED
    assert "unsupported ml job handler" in (first.failure_reason or "")
    second = process_next_job(db_session)
    assert second is not None
    assert second.status == JOB_FAILED


def test_expired_lease_recovers_even_with_recent_heartbeat(db_session):
    job = _queued_job(db_session, max_attempts=2)
    now = datetime.now(UTC)
    job.status = JOB_RUNNING
    job.attempts = 1
    job.started_at = now
    job.heartbeat_at = now
    job.claimed_by = "worker-a"
    job.lease_expires_at = now - timedelta(seconds=1)
    db_session.commit()
    recovered = recover_abandoned_jobs(db_session, now=now, heartbeat_timeout_seconds=60)
    db_session.commit()
    assert [row.id for row in recovered] == [job.id]
    db_session.refresh(job)
    assert job.status == JOB_QUEUED
    assert job.claimed_by is None
    assert job.lease_expires_at is None


def test_claim_sets_claimed_by_and_lease(db_session):
    job = _queued_job(db_session)
    db_session.commit()
    claimed = claim_next_queued_job(db_session, claimed_by="worker-1")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.claimed_by == "worker-1"
    assert claimed.lease_expires_at is not None
    assert claimed.heartbeat_at is not None
    assert claimed.lease_expires_at >= claimed.heartbeat_at
