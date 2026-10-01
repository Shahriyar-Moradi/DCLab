"""Stage: plan and lock the final holdout before any modeling decision."""

from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict

from app.domain.lab_run_stages import SPLITTING
from app.engine.lab.schema_inference import TargetChoice
from app.engine.modeling.holdout_planner import (
    HoldoutPlan,
    HoldoutUnsupportedError,
    holdout_locked_event_payload,
    holdout_plan_event_payload,
    plan_holdout,
    require_supported_holdout,
)
from app.engine.validation.splits import split_train_test_holdout
from app.services.auto_train.context import RunContext, StageHalt


class HoldoutLockInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    frame: pd.DataFrame
    target: TargetChoice
    target_evidence: dict[str, Any]
    profile: dict[str, Any]
    cleaning_log: dict[str, Any]


class HoldoutLockOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    holdout_plan: HoldoutPlan
    locked_train: pd.DataFrame
    locked_split: dict[str, Any]


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
        holdout_plan=holdout_plan, locked_train=locked_train, locked_split=locked_split
    )
