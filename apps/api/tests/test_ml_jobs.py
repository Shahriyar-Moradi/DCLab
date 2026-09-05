"""Durable ML job boundary: persist in the API transaction, claim in a worker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.models import ClientLabUpload, DEFAULT_WORKSPACE_ID, MlJob
from app.domain.ml_jobs import (
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_TYPE_AUTO_TRAIN,
)
from app.services.job_dispatcher import PostgresJobDispatcher, get_job_dispatcher
from app.services.ml_job_service import (
    claim_next_queued_job,
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


def test_production_dispatcher_is_postgres_not_a_thread():
    dispatcher = get_job_dispatcher()
    assert dispatcher.name == "postgres"
    assert isinstance(dispatcher, PostgresJobDispatcher)
    dispatcher.dispatch(upload_id=DEFAULT_WORKSPACE_ID)
    assert dispatcher.dispatch(upload_id=DEFAULT_WORKSPACE_ID) is None


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
    assert job.target_id == upload.id
    assert job.upload_id == upload.id
    assert job.workspace_id == upload.workspace_id
    assert job.project_id is not None
    assert job.status == JOB_QUEUED
    assert job.attempts == 0
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
