"""Stage: train-only decisions (model-development plan + missing values).

Everything here is derived from the locked training partition only.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict

from app.db.models import ClientLabUpload
from app.engine.lab.auto_prepare import MissingValuePlan, plan_missing_values
from app.engine.lab.schema_inference import TargetChoice
from app.engine.modeling.holdout_planner import HoldoutPlan
from app.engine.modeling.leakage_auditor import (
    ModelDevelopmentPlan,
    consult_leakage_llm,
    plan_model_development,
)
from app.engine.modeling.metric_planner import MetricPlan
from app.engine.modeling.objective import Objective
from app.engine.modeling.validation_planner import ValidationPlan
from app.engine.validation.splits import SOURCE_ROW_COLUMN
from app.services.auto_train.branch import apply_missing_value_overrides
from app.services.auto_train.context import RunContext, StageHalt
from app.services.lab_decision_ledger import record_missing_value_decisions


class TrainOnlyDecisionsInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    upload: ClientLabUpload
    frame: pd.DataFrame
    locked_train: pd.DataFrame
    locked_split: dict[str, Any]
    feature_columns: list[str]
    target: TargetChoice
    target_evidence: dict[str, Any]
    profile: dict[str, Any]
    cleaning_log: dict[str, Any]
    holdout_plan: HoldoutPlan
    run_objective: Objective | None


class TrainOnlyDecisionsOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    frame: pd.DataFrame
    locked_train: pd.DataFrame
    cleaning_log: dict[str, Any]
    validation_plan: ValidationPlan
    metric_plan: MetricPlan
    development_plan: ModelDevelopmentPlan
    missing_plan: MissingValuePlan
    allowed_predictors: set[str]
    leakage_excluded: list[str]
    kept_columns: list[str]
    modeled_kept_columns: list[str]


def run_train_only_decisions(
    ctx: RunContext, inp: TrainOnlyDecisionsInput
) -> TrainOnlyDecisionsOutput:
    db = ctx.db
    upload = inp.upload
    target = inp.target
    frame = inp.frame
    locked_train = inp.locked_train
    locked_split = inp.locked_split
    cleaning_log = inp.cleaning_log
    evidence_timer = ctx.evidence_start("train_only_decisions")

    def _planning_on_event(event_type: str, payload: dict[str, Any]) -> None:
        data = dict(payload)
        stage = str(data.pop("stage", event_type))
        status = str(data.pop("status", "completed"))
        duration_ms = data.pop("duration_ms", None)
        ctx.emit_event(stage, event_type, status, data, duration_ms)

    (
        _problem_profile,
        _validation_plan,
        _metric_plan,
        _leakage_audit,
        development_plan,
    ) = plan_model_development(
        locked_train,
        target=target.column,
        task_type=target.task_type,
        requested_folds=5,
        random_state=42,
        reviewer=consult_leakage_llm,
        conservative_auto_train=True,
        on_event=_planning_on_event,
        objective=inp.run_objective,
    )
    if _validation_plan.strategy == "unsupported" or not _validation_plan.actual_folds:
        ctx.evidence_finish(evidence_timer, status="failed")
        ctx.fail(
            _validation_plan.reason
            or _validation_plan.fallback_reason
            or "Validation plan is unsupported.",
            extra={
                "target": inp.target_evidence,
                "analysis": inp.profile,
                "cleaning": cleaning_log,
                "holdout_plan": inp.holdout_plan.to_dict(),
                "model_development_plan": development_plan.to_dict(),
            },
        )
        raise StageHalt
    allowed_predictors = set(development_plan.allowed_features)
    leakage_excluded = [item["column"] for item in development_plan.excluded_features]
    leakage_blocked = {
        item["column"]
        for item in development_plan.excluded_features
        if item["risk"] in {"HIGH", "CRITICAL"}
    }
    decision_columns = [
        c for c in inp.feature_columns if c in locked_train.columns and c != SOURCE_ROW_COLUMN
    ]
    missing_plan = plan_missing_values(locked_train, decision_columns)
    if ctx.branch is not None:
        # Branch column treatments (drop / keep), decided before the ledger records them.
        apply_missing_value_overrides(ctx.branch, missing_plan, leakage_excluded=set(leakage_excluded))
    locked_train = record_missing_value_decisions(
        db,
        upload.id,
        locked_train,
        missing_plan,
        target.column,
    )
    db.commit()
    ctx.emit_event(
        "missing_value_decisions",
        "missing_value_decisions_completed",
        "completed",
        {
            "decision_count": len(missing_plan.column_decisions),
            "dropped_column_count": len(missing_plan.dropped_columns),
        },
    )
    for decision in missing_plan.column_decisions:
        if decision.action == "domain_fill" and decision.fill_value is not None and decision.column in frame:
            frame[decision.column] = frame[decision.column].fillna(decision.fill_value)
    if missing_plan.dropped_columns:
        frame = frame.drop(columns=[c for c in missing_plan.dropped_columns if c in frame.columns])
        locked_train = locked_train.drop(
            columns=[c for c in missing_plan.dropped_columns if c in locked_train.columns]
        )
    kept_columns = [
        c
        for c in decision_columns
        if c not in missing_plan.dropped_columns and c in locked_train.columns
    ]
    modeled_kept_columns = [c for c in kept_columns if c not in leakage_blocked]
    cleaning_log["decision_scope"] = "locked_training_partition_only"
    cleaning_log["dropped_columns"] = list(missing_plan.dropped_columns)
    cleaning_log["leakage_excluded_predictors"] = list(leakage_excluded)
    cleaning_log["missing_value_plan"] = {
        "evidence_rows": int(len(locked_train)),
        "decision_partition": "train",
        "evidence_source_rows": list(locked_split.get("train_source_rows") or []),
        "dropped_columns": list(missing_plan.dropped_columns),
        "rows_with_missing": missing_plan.rows_with_missing,
        "row_missing_fraction": missing_plan.row_missing_fraction,
        "drop_rows_recommended": missing_plan.drop_rows_recommended,
        "column_decisions": [asdict(item) for item in missing_plan.column_decisions],
    }
    ctx.trace(
        "train_only_modeling_decisions",
        "app.engine.lab.auto_prepare.plan_missing_values",
        evidence_rows=int(len(locked_train)),
        evidence_scope="train_only",
        dropped_columns=list(missing_plan.dropped_columns),
        rows_with_missing=missing_plan.rows_with_missing,
        column_decisions=[item.column + ":" + item.action for item in missing_plan.column_decisions],
    )
    ctx.evidence_finish(evidence_timer)
    return TrainOnlyDecisionsOutput(
        frame=frame,
        locked_train=locked_train,
        cleaning_log=cleaning_log,
        validation_plan=_validation_plan,
        metric_plan=_metric_plan,
        development_plan=development_plan,
        missing_plan=missing_plan,
        allowed_predictors=allowed_predictors,
        leakage_excluded=leakage_excluded,
        kept_columns=kept_columns,
        modeled_kept_columns=modeled_kept_columns,
    )
