"""Stage: persist or reuse the project's SplitPlan (ADR 0006 §3).

Runs after the holdout lock and the train-only validation plan, before any
column-role, preprocessing or candidate decision. The holdout and outer folds
of this run become one immutable, digest-addressed ``split_plans`` row, or the
existing one for the same plan identity (whose map already partitioned the
frame at the holdout lock, see ``holdout.py``). Runs whose lineage cannot carry
a plan (no project-scoped published source dataset, or an already-locked run
being re-finalized) keep the per-run split, as before.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any
from uuid import UUID

import pandas as pd
from pydantic import BaseModel, ConfigDict

from app.db.models import ClientLabUpload, Dataset, Experiment, WorkflowRun
from app.domain.errors import SplitPlanLineageError
from app.engine.lab.schema_inference import TargetChoice
from app.engine.modeling.holdout_planner import HoldoutPlan
from app.engine.modeling.leakage_auditor import ModelDevelopmentPlan
from app.engine.modeling.validation_planner import ValidationPlan, iter_validation_folds
from app.engine.validation.split_assignment import (
    SplitAssignment,
    SplitAssignmentMismatchError,
    build_split_assignment,
)
from app.engine.validation.splits import SOURCE_ROW_COLUMN
from app.services.auto_train.context import RunContext, StageHalt, service_module
from app.services.split_plan_service import (
    SplitPlanUnavailable,
    StoredHoldout,
    require_partition_source,
    resolve_split_plan,
)

logger = logging.getLogger("app.services.auto_train_service")


def partition_source(
    ctx: RunContext,
    upload: ClientLabUpload,
    workflow_run: WorkflowRun | None,
    frame: pd.DataFrame,
) -> Dataset | None:
    """The run's published source dataset if its lineage can carry a SplitPlan."""

    db = ctx.db
    shell = db.get(Experiment, upload.experiment_id) if upload.experiment_id is not None else None
    if shell is not None and shell.scientific_evidence_locked_at is not None:
        return None
    project_id = (
        shell.project_id
        if shell is not None and shell.project_id is not None
        else service_module()._resolve_auto_train_project_id(db, upload, workflow_run)
    )
    try:
        if SOURCE_ROW_COLUMN not in frame.columns:
            raise SplitPlanUnavailable("modeling frame has no source-row provenance")
        return require_partition_source(
            db,
            workspace_id=upload.workspace_id,
            project_id=project_id,
            source_dataset_id=upload.dataset_id,
        )
    except SplitPlanUnavailable as exc:
        logger.info("auto-train %s: no split plan (%s)", upload.id, exc)
        ctx.trace("split_plan", "app.services.split_plan_service.resolve_split_plan", skipped=str(exc))
        return None


class SplitPlanInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    upload: ClientLabUpload
    source_dataset: Dataset | None
    stored_holdout: StoredHoldout | None
    locked_train: pd.DataFrame
    locked_split: dict[str, Any]
    target: TargetChoice
    target_evidence: dict[str, Any]
    profile: dict[str, Any]
    cleaning_log: dict[str, Any]
    holdout_plan: HoldoutPlan
    validation_plan: ValidationPlan
    development_plan: ModelDevelopmentPlan


class SplitPlanOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    split_plan_id: UUID | None
    assignment: SplitAssignment | None
    # (holdout rows, train rows) when the holdout came from a stored map, so the
    # runner partitions by it as well; None means the runner re-splits.
    holdout_partition: tuple[frozenset[int], frozenset[int]] | None
    # The stored plans on reuse (reuse copy rule); this run's plans otherwise.
    holdout_plan: HoldoutPlan
    validation_plan: ValidationPlan
    development_plan: ModelDevelopmentPlan


def _unchanged(inp: SplitPlanInput) -> SplitPlanOutput:
    return SplitPlanOutput(
        split_plan_id=None,
        assignment=None,
        holdout_partition=None,
        holdout_plan=inp.holdout_plan,
        validation_plan=inp.validation_plan,
        development_plan=inp.development_plan,
    )


def run_split_plan(ctx: RunContext, inp: SplitPlanInput) -> SplitPlanOutput:
    db = ctx.db
    upload = inp.upload
    source = inp.source_dataset
    if source is None:
        return _unchanged(inp)
    target = inp.target.column
    holdout_rows = [int(row) for row in inp.locked_split.get("test_source_rows") or []]
    train_rows = [int(row) for row in inp.locked_split.get("train_source_rows") or []]

    def derive_assignment() -> SplitAssignment:
        # Only for a new plan: the outer folds of the locked train partition.
        folds = list(
            iter_validation_folds(
                inp.validation_plan, inp.locked_train, inp.locked_train[target].to_numpy()
            )
        )
        return build_split_assignment(
            train_source_rows=train_rows,
            holdout_source_rows=holdout_rows,
            train_frame=inp.locked_train,
            folds=folds,
        )

    try:
        resolved = resolve_split_plan(
            db,
            source_dataset=source,
            target_column=target,
            task_type=inp.target.task_type,
            holdout_plan=inp.holdout_plan,
            validation_plan=inp.validation_plan,
            cleaning_log=inp.cleaning_log,
            holdout_rows=holdout_rows,
            train_rows=train_rows,
            derive_assignment=derive_assignment,
            stored=inp.stored_holdout,
            created_by=upload.requested_by,
        )
        db.commit()
    except SplitPlanUnavailable as exc:
        db.rollback()
        logger.info("auto-train %s: no split plan (%s)", upload.id, exc)
        ctx.trace("split_plan", "app.services.split_plan_service.resolve_split_plan", skipped=str(exc))
        return _unchanged(inp)
    except (SplitAssignmentMismatchError, SplitPlanLineageError, ValueError) as exc:
        db.rollback()
        ctx.fail(
            str(exc),
            extra={
                "target": inp.target_evidence,
                "analysis": inp.profile,
                "cleaning": inp.cleaning_log,
                "holdout_plan": inp.holdout_plan.to_dict(),
            },
        )
        raise StageHalt from exc

    plan = resolved.split_plan
    ctx.emit_event(
        "split_plan",
        "split_plan_resolved",
        "completed",
        {
            "split_plan_id": str(plan.id),
            "version": plan.version,
            "reused": resolved.reused,
            "plan_digest": plan.plan_digest,
            "assignment_digest": plan.assignment_digest,
            "train_row_count": plan.train_row_count,
            "holdout_row_count": plan.holdout_row_count,
        },
    )
    ctx.trace(
        "split_plan",
        "app.services.split_plan_service.resolve_split_plan",
        split_plan_id=str(plan.id),
        version=plan.version,
        reused=resolved.reused,
        holdout_from_stored_map=inp.stored_holdout is not None,
        assignment_digest=plan.assignment_digest,
    )
    development_plan = inp.development_plan
    if resolved.reused:
        development_plan = replace(
            development_plan, validation_plan=resolved.validation_plan.to_dict()
        )
    partition = None
    if inp.stored_holdout is not None:
        partition = (frozenset(resolved.assignment.holdout_rows), frozenset(resolved.assignment.train_folds))
    return SplitPlanOutput(
        split_plan_id=plan.id,
        assignment=resolved.assignment,
        holdout_partition=partition,
        holdout_plan=resolved.holdout_plan,
        validation_plan=resolved.validation_plan,
        development_plan=development_plan,
    )
