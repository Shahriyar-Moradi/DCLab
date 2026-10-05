"""Shared state for one auto-train run, plus the event/evidence helpers.

These were closures inside ``run_auto_train_job`` sharing state through
``nonlocal``; they are methods here so stage functions can be separate.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from types import ModuleType
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ClientLabUpload, Experiment
from app.domain.errors import RunCancelledError
from app.domain.lab_run_stages import FAILED, QUEUED
from app.services.observability_service import PipelineRunObserver
from app.services.pipeline_audit_service import request_pipeline_verification
from app.services.pipeline_verifier import verify_pipeline
from app.services.technical_run_report import build_technical_run_report

# Same logger as before the split, so log capture/filters keep matching.
logger = logging.getLogger("app.services.auto_train_service")

EVENT_STAGE_NAMES = {
    "file_ingestion": "ingestion",
    "profiling": "profiling_eda",
    "target_task_resolution": "target_task",
    "structural_cleaning": "structural_cleaning",
    "holdout_plan": "holdout_plan",
    "splitting": "holdout_lock",
    "train_only_decisions": "train_only_decisions",
    "column_roles": "column_roles",
    "feature_engineering": "feature_engineering",
    "preprocessing_setup": "preprocessing_configuration",
    "deterministic_verification": "deterministic_verification",
    "report_generation": "report",
}


def service_module() -> ModuleType:
    """``app.services.auto_train_service``, resolved at call time.

    Stages look up ``_mark``, ``profile_frame``, ``_load_upload_frame``,
    ``materialize_client_upload`` and the module's helpers through it so
    monkeypatches on that module keep applying, without an import cycle.
    """
    from app.services import auto_train_service

    return auto_train_service


class StageHalt(Exception):
    """A stage already recorded the run's outcome (``fail`` or needs-input); stop."""


class RunContext:
    def __init__(
        self,
        db: Session,
        upload_id: UUID,
        upload: ClientLabUpload,
        *,
        observer: PipelineRunObserver | None,
        on_heartbeat: Callable[[], None] | None,
        total_started_at: datetime,
        total_timer: float,
    ) -> None:
        self.db = db
        self.upload_id = upload_id
        # Re-read by the persistence stage; ``fail`` reports its record_count.
        self.upload: ClientLabUpload | None = upload
        self.observer = observer
        self.on_heartbeat = on_heartbeat
        self.total_started_at = total_started_at
        self.total_timer = total_timer
        self.current_stage = QUEUED
        self.trace_entries: list[dict[str, Any]] = []
        self.stage_timings: list[dict[str, Any]] = []
        self.evidence_stage_timings: list[dict[str, Any]] = []
        self.current_rows = int(upload.record_count)
        self.active_stage: dict[str, Any] | None = None
        # A branch run (ADR 0006 §4): its parent's split plan and materialized
        # change set (``app.services.auto_train.branch.BranchRun``); None = root.
        self.branch: Any = None
        # P6.9-A: the AI policy snapshot taken at job claim and the resolved decision
        # points (``app.services.auto_train.decision_points.RunDecisionPoints``).
        self.decisions: Any = None
        # P3.1-B2: cleared once the final holdout is touched. A cancel that
        # arrives later is not honoured: the holdout was scored, so the run must
        # finish and lock (record) that single evaluation.
        self.cancellable = True

    def heartbeat(self) -> None:
        """Renew the job lease; stop here if cancellation was requested (checkpoint)."""

        if self.on_heartbeat is None:
            return
        try:
            self.on_heartbeat()
        except RunCancelledError:
            if self.cancellable:
                raise

    def emit_event(
        self,
        stage: str,
        event_type: str,
        status: str,
        payload: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> None:
        if self.observer is not None:
            self.observer.emit(stage, event_type, status, payload, duration_ms)

    def evidence_start(self, stage: str) -> dict[str, Any]:
        event_stage = EVENT_STAGE_NAMES.get(stage, stage)
        self.emit_event(event_stage, "operation_started", "started")
        started_at = datetime.now(UTC)
        return {
            "stage": stage,
            "event_stage": event_stage,
            "started_at": started_at,
            "timer": time.perf_counter(),
            "rows_in": self.current_rows,
        }

    def evidence_finish(self, token: dict[str, Any], *, status: str = "completed") -> dict[str, Any]:
        ended_at = datetime.now(UTC)
        record = {
            "stage": token["stage"],
            "started_at": token["started_at"].isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_ms": max(0.001, (time.perf_counter() - token["timer"]) * 1000.0),
            "status": status,
            "rows_in": token["rows_in"],
            "rows_out": self.current_rows,
        }
        self.evidence_stage_timings.append(record)
        self.emit_event(
            str(token["event_stage"]),
            "operation_completed",
            status,
            {"rows_in": token["rows_in"], "rows_out": self.current_rows},
            record["duration_ms"],
        )
        return record

    def finish_stage(self, *, status: str = "completed", reason: str | None = None) -> None:
        active_stage = self.active_stage
        if active_stage is None:
            return
        ended = datetime.now(UTC)
        duration_ms = max(0.001, (time.perf_counter() - active_stage["timer"]) * 1000.0)
        self.stage_timings.append(
            {
                "stage": active_stage["stage"],
                "started_at": active_stage["started_at"],
                "ended_at": ended.isoformat(),
                "duration_ms": duration_ms,
                "rows_in": active_stage["rows_in"],
                "rows_out": self.current_rows,
                "status": status,
            }
        )
        payload = {
            "rows_in": active_stage["rows_in"],
            "rows_out": self.current_rows,
        }
        if status == "failed" and reason:
            payload["failure_reason"] = reason
        self.emit_event(
            str(active_stage["stage"]),
            "operation_completed",
            status,
            payload,
            duration_ms,
        )
        self.active_stage = None

    def stage(self, stage: str) -> None:
        if self.active_stage is not None and self.active_stage["stage"] == stage:
            return
        self.finish_stage()
        self.current_stage = stage
        self.active_stage = {
            "stage": stage,
            "started_at": datetime.now(UTC).isoformat(),
            "timer": time.perf_counter(),
            "rows_in": self.current_rows,
        }
        self.emit_event(stage, "operation_started", "started", {"rows_in": self.current_rows})
        row = self.db.get(ClientLabUpload, self.upload_id)
        if row is not None:
            service_module()._mark(self.db, row, status=stage)
        self.heartbeat()

    def trace(self, step: str, fn: str, **payload: Any) -> None:
        entry = {"step": step, "fn": fn, **payload}
        self.trace_entries.append(entry)
        logger.info("auto-train %s %s %s", self.upload_id, step, fn)

    def request_routine_advisory_verification(self) -> None:
        """Run only after ML state commits; provider failure cannot fail the job."""
        if not get_settings().pipeline_llm_verifier_enabled:
            return
        try:
            request_pipeline_verification(self.db, self.upload_id)
        except Exception:  # noqa: BLE001 - advisory isolation is intentional
            logger.exception("advisory pipeline verification failed for upload %s", self.upload_id)

    def fail(
        self,
        reason: str,
        extra: dict[str, Any] | None = None,
        experiment_id: UUID | None = None,
    ) -> None:
        db = self.db
        upload = self.upload
        row = db.get(ClientLabUpload, self.upload_id)
        if row is None:
            return
        self.finish_stage(status="failed", reason=reason)
        payload = {**dict(row.pipeline_log or {}), **dict(extra or {})}
        payload["reason"] = reason
        payload["failed_at"] = self.current_stage
        payload["pipeline_trace"] = list(self.trace_entries)
        verification_timer = self.evidence_start("deterministic_verification")
        partial_result = dict(extra or {})
        partial_result.setdefault("status", "FAILED")
        partial_result.setdefault("analysis", partial_result.get("analysis") or {})
        partial_result.setdefault("artifact_dir", None)
        ml_ended_at = datetime.now(UTC)
        ml_execution_total = {
            "stage": "ml_execution_total",
            "started_at": self.total_started_at.isoformat(),
            "ended_at": ml_ended_at.isoformat(),
            "duration_ms": max(0.001, (time.perf_counter() - self.total_timer) * 1000.0),
            "status": "failed",
            "rows_in": int(upload.record_count),
            "rows_out": self.current_rows,
        }
        partial_timings = [*self.evidence_stage_timings, ml_execution_total]
        partial_log = {**payload, "stage_timings": partial_timings}
        effective_experiment_id = experiment_id or row.experiment_id
        experiment = (
            db.get(Experiment, effective_experiment_id)
            if effective_experiment_id is not None
            else None
        )
        preliminary_report = build_technical_run_report(
            db,
            upload=row,
            experiment=experiment,
            result=partial_result,
            pipeline_log=partial_log,
        )
        verification = verify_pipeline(preliminary_report)
        self.evidence_finish(verification_timer)
        partial_result["deterministic_verification"] = verification
        partial_log["deterministic_verification"] = verification
        report_timer = self.evidence_start("report_generation")
        report = build_technical_run_report(
            db,
            upload=row,
            experiment=experiment,
            result=partial_result,
            pipeline_log={
                **partial_log,
                "stage_timings": [*partial_timings, self.evidence_stage_timings[-1]],
            },
        )
        self.evidence_finish(report_timer)
        workflow_ended_at = datetime.now(UTC)
        workflow_elapsed = {
            "stage": "workflow_elapsed",
            "started_at": self.total_started_at.isoformat(),
            "ended_at": workflow_ended_at.isoformat(),
            "duration_ms": max(0.001, (time.perf_counter() - self.total_timer) * 1000.0),
            "status": "failed",
            "rows_in": int(upload.record_count),
            "rows_out": self.current_rows,
        }
        final_timings = [*partial_timings, *self.evidence_stage_timings[-2:], workflow_elapsed]
        report["stage_timings"] = final_timings
        payload["stage_timings"] = final_timings
        payload["deterministic_verification"] = verification
        payload["technical_report"] = report
        if experiment is not None:
            partial_result["deterministic_verification"] = verification
            partial_result["technical_report"] = report
            partial_result["stage_timings"] = final_timings
            partial_result["error"] = reason
            experiment.result = partial_result
            experiment.failure_reason = reason[:2048]
            experiment.result = partial_result
        service_module()._mark(
            db,
            row,
            status=FAILED,
            log=payload,
            experiment_id=effective_experiment_id,
        )
        self.emit_event(
            "terminal",
            "pipeline_terminal",
            "failed",
            {"reason": reason, "failed_at": self.current_stage},
        )
        self.request_routine_advisory_verification()
