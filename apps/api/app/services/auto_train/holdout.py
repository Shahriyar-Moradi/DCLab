"""Stage: plan and lock the final holdout before any modeling decision."""

from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict

from app.db.models import ClientLabUpload, Dataset, WorkflowRun
from app.domain.lab_run_stages import SPLITTING
from app.domain.state_graph import SPLIT_PLAN_HOLDOUT_STRATEGIES
from app.engine.lab.schema_inference import TargetChoice
from app.engine.modeling.holdout_planner import (
    HoldoutPlan,
    HoldoutUnsupportedError,
    holdout_locked_event_payload,
    holdout_plan_event_payload,
    plan_holdout,
    require_supported_holdout,
)
from app.engine.validation.splits import split_holdout_by_assignment, split_train_test_holdout
from app.services.auto_train.context import RunContext, StageHalt
from app.services.auto_train.split_plan import partition_source
from app.services.split_plan_service import StoredHoldout, find_stored_holdout


class HoldoutLockInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    frame: pd.DataFrame
    target: TargetChoice
    target_evidence: dict[str, Any]
    profile: dict[str, Any]
    cleaning_log: dict[str, Any]
    # For the SplitPlan lookup (ADR 0006 §3); None keeps the per-run split.
    upload: ClientLabUpload | None = None
    workflow_run: WorkflowRun | None = None


class HoldoutLockOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    holdout_plan: HoldoutPlan
    locked_train: pd.DataFrame
    locked_split: dict[str, Any]
    # The published source dataset a SplitPlan may partition (None: no plan).
    source_dataset: Dataset | None = None
    # A stored plan whose map partitioned this run (reuse; nothing re-split).
    stored_holdout: StoredHoldout | None = None


def run_holdout_lock(ctx: RunContext, inp: HoldoutLockInput) -> HoldoutLockOutput:
    frame = inp.frame
    target = inp.target
    # The final holdout is locked after pre-split structural analysis and
    # before any modeling decision is derived.
    holdout_plan = plan_holdout(
        frame,
        target=target.column,
        task_type=target.task_type,
        test_size=0.2,
        random_state=42,
    )
    failure_extra = {
        "target": inp.target_evidence,
        "analysis": inp.profile,
        "cleaning": inp.cleaning_log,
    }
    source = (
        partition_source(ctx, inp.upload, inp.workflow_run, frame)
        if inp.upload is not None
        else None
    )
    stored: StoredHoldout | None = None
    if source is not None and holdout_plan.strategy in SPLIT_PLAN_HOLDOUT_STRATEGIES:
        try:
            stored = find_stored_holdout(
                ctx.db,
                source_dataset=source,
                target_column=target.column,
                task_type=target.task_type,
                holdout_plan=holdout_plan,
                cleaning_log=inp.cleaning_log,
            )
        except ValueError as exc:
            ctx.fail(str(exc), extra={**failure_extra, "holdout_plan": holdout_plan.to_dict()})
            raise StageHalt from exc
        if stored is not None:
            # Reuse copy rule: the stored HoldoutPlan (same identity) is this run's.
            holdout_plan = stored.holdout_plan
    ctx.emit_event(
        "holdout_plan",
        "holdout_plan_selected",
        "completed",
        holdout_plan_event_payload(holdout_plan),
    )
    if holdout_plan.strategy == "unsupported":
        ctx.fail(
            holdout_plan.reason,
            extra={
                "target": inp.target_evidence,
                "analysis": inp.profile,
                "cleaning": inp.cleaning_log,
                "holdout_plan": holdout_plan.to_dict(),
            },
        )
        raise StageHalt
    ctx.stage(SPLITTING)
    evidence_timer = ctx.evidence_start("splitting")
    try:
        require_supported_holdout(holdout_plan)
        if stored is not None:
            # Partition by the stored map; a frame with other rows fails closed.
            locked_train, _locked_val, _locked_test, locked_split = split_holdout_by_assignment(
                frame,
                plan=holdout_plan,
                holdout_rows=stored.assignment.holdout_rows,
                train_rows=stored.assignment.train_folds.keys(),
            )
        else:
            locked_train, _locked_val, _locked_test, locked_split = split_train_test_holdout(
                frame,
                target=target.column,
                test_size=holdout_plan.test_size,
                seed=holdout_plan.random_state,
                plan=holdout_plan,
            )
    except (HoldoutUnsupportedError, ValueError) as exc:
        ctx.fail(
            str(exc),
            extra={
                "target": inp.target_evidence,
                "analysis": inp.profile,
                "cleaning": inp.cleaning_log,
                "holdout_plan": holdout_plan.to_dict(),
            },
        )
        raise StageHalt from exc
    ctx.emit_event(
        "holdout_lock",
        "holdout_locked",
        "completed",
        holdout_locked_event_payload(locked_split),
    )
    ctx.trace(
        "splitting",
        "app.engine.validation.splits.split_train_test_holdout",
        strategy=locked_split.get("strategy"),
        n_train=locked_split.get("n_train"),
        n_test=locked_split.get("n_test"),
        provenance_disjoint=locked_split.get("provenance_disjoint"),
        group_column=locked_split.get("group_column"),
        time_column=locked_split.get("time_column"),
    )
    ctx.evidence_finish(evidence_timer)
    return HoldoutLockOutput(
        holdout_plan=holdout_plan,
        locked_train=locked_train,
        locked_split=locked_split,
        source_dataset=source,
        stored_holdout=stored,
    )
