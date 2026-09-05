"""PipelineStageRun is live command-center state, not a post-hoc rebuild."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from adaptive_modeling.fixtures import ordinary_binary
from adaptive_modeling.production import disable_background_job, post_labs_csv
from app.db.models import ClientLabUpload, Experiment, MlRunEvent, PipelineStageRun
from app.services.auto_train_service import run_auto_train_job
from app.services.lineage_service import create_pipeline_run, create_workflow_run
from app.services.observability_service import PipelineRunObserver
from app.services.workflow_execution_service import (
    complete_pipeline_stage_run,
    reconcile_pipeline_stage_runs,
    start_pipeline_stage_run,
)
from test_execution_hierarchy import make_hierarchy


@pytest.fixture
def _rule_engine_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )


@pytest.fixture()
def hierarchy(db_session, tmp_path):
    return make_hierarchy(db_session, tmp_path)


def _pipeline(db_session, hierarchy):
    run = create_workflow_run(
        db_session,
        workspace_id=hierarchy["workspace"].id,
        workflow=hierarchy["workflow"],
        requester=hierarchy["actor"],
        trigger_type="manual",
        source_type="dataset",
    )
    pipeline = create_pipeline_run(
        db_session,
        workflow_run=run,
        environment=hierarchy["env"],
        dataset=hierarchy["dataset"],
        task=hierarchy["task"],
    )
    db_session.commit()
    observer = PipelineRunObserver(pipeline.workspace_id, run.id, pipeline.id)
    return pipeline, observer


def _stages(db_session, pipeline_id):
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(PipelineStageRun)
            .where(PipelineStageRun.pipeline_run_id == pipeline_id)
            .order_by(PipelineStageRun.sequence, PipelineStageRun.id)
        )
    )


def _events(db_session, pipeline_id):
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(MlRunEvent)
            .where(MlRunEvent.experiment_id == pipeline_id)
            .order_by(MlRunEvent.sequence)
        )
    )


def test_running_stage_is_queryable_before_completion(db_session, hierarchy):
    pipeline, observer = _pipeline(db_session, hierarchy)
    observer.emit("profiling_eda", "operation_started", "started", {"rows_in": 12})
    rows = _stages(db_session, pipeline.id)
    assert len(rows) == 1
    assert rows[0].stage_key == "profiling_eda"
    assert rows[0].status == "running"
    assert rows[0].started_at is not None
    assert rows[0].completed_at is None
    assert rows[0].sequence == 1


def test_completed_stage_is_updated_not_replaced(db_session, hierarchy):
    pipeline, observer = _pipeline(db_session, hierarchy)
    observer.emit("profiling_eda", "operation_started", "started")
    started = _stages(db_session, pipeline.id)[0]
    observer.emit(
        "profiling_eda",
        "operation_completed",
        "completed",
        {"rows_out": 12},
        duration_ms=8.5,
    )
    rows = _stages(db_session, pipeline.id)
    assert len(rows) == 1
    assert rows[0].id == started.id
    assert rows[0].sequence == started.sequence
    assert rows[0].status == "completed"
    assert rows[0].completed_at is not None
    assert rows[0].duration_ms == pytest.approx(8.5)
    assert rows[0].output_summary.get("rows_out") == 12


def test_failed_stage_retains_evidence(db_session, hierarchy):
    pipeline, observer = _pipeline(db_session, hierarchy)
    observer.emit("holdout_lock", "operation_started", "started")
    started = _stages(db_session, pipeline.id)[0]
    observer.emit(
        "holdout_lock",
        "operation_completed",
        "failed",
        {"failure_code": "split_error", "reason": "groups too small"},
        duration_ms=3,
    )
    rows = _stages(db_session, pipeline.id)
    assert len(rows) == 1
    assert rows[0].id == started.id
    assert rows[0].status == "failed"
    assert rows[0].failure_code == "split_error"
    assert rows[0].failure_reason == "groups too small"
    assert rows[0].started_at is not None
    assert rows[0].completed_at is not None


def test_process_failure_leaves_last_started_stage_visible(
    auth_client, db_session, monkeypatch, _rule_engine_only
):
    disable_background_job(monkeypatch)
    monkeypatch.setattr(
        "app.services.auto_train_service._load_upload_frame",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("disk exploded")),
    )
    created = post_labs_csv(
        auth_client,
        ordinary_binary(),
        filename="stage_crash.csv",
        target="outcome",
    )
    assert created.status_code == 200, created.text
    upload = db_session.get(ClientLabUpload, created.json()["id"])
    assert upload is not None
    run_auto_train_job(db_session, upload.id)
    db_session.expire_all()
    experiment = db_session.get(Experiment, upload.experiment_id)
    assert experiment is not None
    rows = _stages(db_session, experiment.id)
    assert rows
    visible = [row for row in rows if row.stage_key in {"ingesting", "ingestion"}]
    assert visible
    last_started = visible[-1]
    assert last_started.status in {"running", "failed"}
    assert last_started.started_at is not None
    if last_started.status == "failed":
        assert last_started.failure_reason
        assert "disk exploded" in last_started.failure_reason


def test_retry_is_idempotent(db_session, hierarchy):
    pipeline, observer = _pipeline(db_session, hierarchy)
    observer.emit("feature_engineering", "operation_started", "started")
    first = _stages(db_session, pipeline.id)[0]
    observer.emit("feature_engineering", "operation_started", "started")
    again = _stages(db_session, pipeline.id)
    assert len(again) == 1
    assert again[0].id == first.id
    assert again[0].sequence == first.sequence
    assert again[0].status == "running"
    observer.emit("feature_engineering", "operation_completed", "completed")
    observer.emit("feature_engineering", "operation_started", "started")
    retried = _stages(db_session, pipeline.id)
    assert len(retried) == 1
    assert retried[0].id == first.id
    assert retried[0].status == "running"
    assert retried[0].completed_at is None


def test_pipeline_monitor_sees_in_progress_stage(admin_client, db_session, hierarchy):
    pipeline, observer = _pipeline(db_session, hierarchy)
    observer.emit("cross_validation", "cv_fold_started", "started", {"fold_number": 1})
    db_session.expire_all()
    response = admin_client.get(f"/admin/pipeline-runs/{pipeline.id}/monitor")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["current_stage"] == "cross_validation"
    assert body["summary"]["current_stage_status"] == "running"
    assert body["stages"]
    assert body["stages"][0]["stage_key"] == "cross_validation"
    assert body["stages"][0]["status"] == "running"
    assert body["events"][0]["event_type"] == "cv_fold_started"
    assert body["events"][0]["sequence"] == 1
    assert body["stages"][0]["sequence"] == 1


def test_stage_and_event_order_agree(db_session, hierarchy):
    pipeline, observer = _pipeline(db_session, hierarchy)
    observer.emit("ingesting", "operation_started", "started")
    observer.emit("ingestion", "operation_started", "started")
    observer.emit("ingesting", "operation_completed", "completed")
    stages = _stages(db_session, pipeline.id)
    events = _events(db_session, pipeline.id)
    assert [row.stage_key for row in stages] == ["ingesting", "ingestion"]
    assert [row.sequence for row in stages] == [1, 2]
    first_event_stage = []
    seen = set()
    for event in events:
        if event.stage in seen:
            continue
        seen.add(event.stage)
        first_event_stage.append(event.stage)
    assert first_event_stage == ["ingesting", "ingestion"]
    assert [event.sequence for event in events] == [1, 2, 3]


def test_reconcile_does_not_delete_live_rows(db_session, hierarchy):
    pipeline, _observer = _pipeline(db_session, hierarchy)
    live = start_pipeline_stage_run(db_session, pipeline, stage_key="ingesting")
    db_session.commit()
    reconcile_pipeline_stage_runs(
        db_session,
        pipeline,
        [{"stage": "train", "status": "completed", "duration_ms": 4}],
    )
    db_session.commit()
    rows = _stages(db_session, pipeline.id)
    by_key = {row.stage_key: row for row in rows}
    assert live.id == by_key["ingesting"].id
    assert by_key["ingesting"].status == "running"
    assert by_key["train"].status == "completed"
    complete_pipeline_stage_run(db_session, pipeline, stage_key="ingesting")
    db_session.commit()
    assert _stages(db_session, pipeline.id)[0].id == live.id
