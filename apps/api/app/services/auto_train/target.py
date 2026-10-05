"""Stage: resolve the target column + task type, or stop for confirmation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.db.models import ClientLabUpload, Dataset, WorkflowRun
from app.domain.errors import TargetIntentConflictError, TargetNotInDatasetError
from app.domain.lab_run_stages import NEEDS_INPUT
from app.engine.lab.auto_prepare import coerce_numeric_like
from app.engine.lab.schema_inference import TargetChoice
from app.engine.modeling.objective import Objective, ObjectiveError, objective_from_dict
from app.services.auto_train.branch import require_same_target
from app.services.auto_train.context import RunContext, StageHalt, service_module
from app.services.auto_train.decision_points import TARGET, plan_answers
from app.services.auto_train.plan_points import (
    resolve_legacy_target,
    resolve_objective_point,
    resolve_target_point,
)
from app.services.target_intent_service import (
    UNRESOLVED_TARGET_STATUS,
    audit_source_for_choice,
    load_workspace_problem_spec,
    public_target_payload,
    requested_target_from_execution,
    resolve_execution_target,
    target_confirmation_required_payload,
)


class TargetResolutionInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    upload: ClientLabUpload
    frame: pd.DataFrame
    columns: list[str]
    profile: dict[str, Any]
    quality: dict[str, Any]


class TargetResolutionOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    coerced: pd.DataFrame
    workflow_run: WorkflowRun | None
    target: TargetChoice
    target_evidence: dict[str, Any]
    run_objective: Objective | None


def run_target_resolution(ctx: RunContext, inp: TargetResolutionInput) -> TargetResolutionOutput:
    svc = service_module()
    db = ctx.db
    upload = inp.upload
    columns = inp.columns
    profile = inp.profile
    quality = inp.quality

    coerced = coerce_numeric_like(inp.frame, columns)
    evidence_timer = ctx.evidence_start("target_task_resolution")
    workflow_run = db.scalar(
        select(WorkflowRun).where(WorkflowRun.source_upload_id == upload.id)
    )
    dataset = db.get(Dataset, upload.dataset_id) if upload.dataset_id is not None else None
    try:
        requested = requested_target_from_execution(
            db,
            upload_explicit_target=upload.explicit_target_column,
            workflow_run=workflow_run,
        )
        if ctx.branch is None and plan_answers(ctx, TARGET) is not None:  # P6.9-A: target.column (L1)
            spec = load_workspace_problem_spec(
                db, workspace_id=upload.workspace_id,
                problem_spec_id=workflow_run.problem_spec_id if workflow_run is not None else None,
                project_id=workflow_run.project_id if workflow_run is not None else None)
            requested = resolve_target_point(
                ctx, frame=coerced, columns=columns, requested=requested,
                spec_target=spec.target_column if spec is not None else None)
        target = resolve_execution_target(
            db,
            workspace_id=upload.workspace_id,
            frame=coerced,
            columns=columns,
            dataset=dataset,
            problem_spec_id=(
                workflow_run.problem_spec_id if workflow_run is not None else None
            ),
            project_id=workflow_run.project_id if workflow_run is not None else None,
            requested_target=requested,
            upload_id=upload.id,
        )
    except (TargetIntentConflictError, TargetNotInDatasetError) as exc:
        ctx.evidence_finish(evidence_timer, status="failed")
        detail = exc.public_detail()
        ctx.fail(
            str(exc),
            extra={"target": detail, "analysis": profile, "quality": quality},
        )
        raise StageHalt from exc
    suggestion = resolve_legacy_target(ctx, target) if target.column is None else None  # target.column (L1)
    target_evidence = {
        **public_target_payload(target),
        "locked_at": datetime.now(UTC).isoformat() if target.column is not None else None,
    }
    if workflow_run is not None:
        workflow_run.resolved_target = target.column
        workflow_run.task_type = target.task_type if target.column is not None else None
        db.commit()
    if target.column is None:
        if target.source == "explicit" or target.intent_source in {
            "problem_spec",
            "request",
        }:
            ctx.evidence_finish(evidence_timer, status="failed")
            ctx.fail(
                target.reason,
                extra={
                    "target": target_evidence,
                    "analysis": profile,
                    "quality": quality,
                },
            )
            raise StageHalt
        ctx.evidence_finish(evidence_timer, status=UNRESOLVED_TARGET_STATUS)
        row = db.get(ClientLabUpload, ctx.upload_id)
        if row is None:
            raise StageHalt
        ctx.finish_stage(status="completed")
        waiting = target_confirmation_required_payload(target)
        if suggestion is not None:  # the legacy agent's answer, for a person to confirm (L1)
            waiting["ai_suggestion"] = suggestion
        payload = {
            "target": {**target_evidence, "status": UNRESOLVED_TARGET_STATUS},
            "target_confirmation": waiting,
            "analysis": profile,
            "eda": {
                "row_count": profile["row_count"],
                "column_count": profile["column_count"],
                "duplicate_rows": profile.get("duplicate_rows", profile.get("duplicate_count")),
            },
            "quality": quality,
            "reason": target.reason,
        }
        svc._mark(db, row, status=NEEDS_INPUT, log=payload)
        from app.services.execution_request_service import mark_execution_needs_input

        mark_execution_needs_input(
            db,
            upload=row,
            waiting=waiting,
            source=audit_source_for_choice(target),
        )
        db.commit()
        raise StageHalt
    ctx.evidence_finish(evidence_timer)
    if target.task_type not in {"binary", "multiclass", "regression"}:
        ctx.fail(
            f"target {target.column!r} implies unsupported task type {target.task_type!r}",
            extra={"target": target_evidence, "analysis": profile, "quality": quality},
        )
        raise StageHalt
    try:
        if ctx.branch is not None:
            # A branch never changes target/task; its objective is the parent's
            # materialized objective ⊕ the change set (no ProblemSpec re-read).
            require_same_target(ctx.branch, target.column, target.task_type)
            run_objective = objective_from_dict(ctx.branch.objective, task_type=target.task_type)
            run_objective = None if run_objective is None or run_objective.is_empty else run_objective
        else:
            run_objective = svc._run_objective(db, upload, workflow_run, target.task_type)
            run_objective = resolve_objective_point(ctx, target.task_type, run_objective)  # spec.objective
    except ObjectiveError as exc:
        ctx.fail(
            f"the problem spec objective is invalid for a {target.task_type} target: {exc}",
            extra={"target": target_evidence, "analysis": profile, "quality": quality},
        )
        raise StageHalt from exc
    return TargetResolutionOutput(
        coerced=coerced,
        workflow_run=workflow_run,
        target=target,
        target_evidence=target_evidence,
        run_objective=run_objective,
    )
