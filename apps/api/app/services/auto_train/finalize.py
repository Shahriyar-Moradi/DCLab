"""Stage: deterministic verification, technical report and terminal status."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.db.models import ClientLabUpload, Experiment, WorkflowRun
from app.domain.lab_run_stages import COMPLETED
from app.services.auto_train.context import RunContext, service_module
from app.services.pipeline_verifier import verify_pipeline
from app.services.technical_run_report import build_technical_run_report


class FinalizeInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    upload: ClientLabUpload
    experiment: Experiment
    workflow_run: WorkflowRun | None
    result: dict[str, Any]
    log: dict[str, Any]


class FinalizeOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: str
    experiment: Experiment
    result: dict[str, Any]


def run_finalize(ctx: RunContext, inp: FinalizeInput) -> FinalizeOutput:
    svc = service_module()
    db = ctx.db
    upload = inp.upload
    experiment = inp.experiment
    result = inp.result
    log = inp.log
    from app.services.reproducibility_service import store_report_artifacts

    preliminary_report = build_technical_run_report(
        db,
        upload=upload,
        experiment=experiment,
        result=result,
        pipeline_log=log,
    )
    verification_timer = ctx.evidence_start("deterministic_verification")
    verification = verify_pipeline(preliminary_report, db=db)
    ctx.evidence_finish(verification_timer)
    result["deterministic_verification"] = verification
    log["deterministic_verification"] = verification

    report_timer = ctx.evidence_start("report_generation")
    result["technical_report"] = build_technical_run_report(
        db,
        upload=upload,
        experiment=experiment,
        result=result,
        pipeline_log={**log, "stage_timings": [*result["stage_timings"], ctx.evidence_stage_timings[-1]]},
    )
    ctx.evidence_finish(report_timer)
    workflow_elapsed = {
        "stage": "workflow_elapsed",
        "started_at": ctx.total_started_at.isoformat(),
        "ended_at": datetime.now(UTC).isoformat(),
        "duration_ms": max(0.001, (time.perf_counter() - ctx.total_timer) * 1000.0),
        "status": "completed",
        "rows_in": int(upload.record_count),
        "rows_out": ctx.current_rows,
    }
    result["stage_timings"] = [
        *result["stage_timings"],
        *ctx.evidence_stage_timings[-2:],
        workflow_elapsed,
    ]
    log["stage_timings"] = list(result["stage_timings"])
    result["technical_report"]["stage_timings"] = list(result["stage_timings"])
    result["technical_report"]["deterministic_verification"] = verification
    (Path(experiment.artifact_dir) / "result.json").write_text(
        json.dumps(result, default=str, indent=2) + "\n",
        encoding="utf-8",
    )
    experiment.result = result
    ctx.finish_stage()
    store_report_artifacts(db, experiment)
    svc._mark(db, upload, status=COMPLETED, log=log, experiment_id=experiment.id)
    ctx.emit_event(
        "terminal",
        "pipeline_terminal",
        "completed",
        {
            "model_version_created": inp.workflow_run is not None,
            "candidate_count": len(experiment.candidates),
        },
    )
    ctx.request_routine_advisory_verification()
    return FinalizeOutput(status=COMPLETED, experiment=experiment, result=result)
