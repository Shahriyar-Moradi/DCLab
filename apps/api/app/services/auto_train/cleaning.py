"""Stage: structural (pre-split, fit-free) cleaning and target coercion."""

from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict

from app.domain.lab_run_stages import CLEANING
from app.engine.features.encode import coerce_binary_target
from app.engine.lab.auto_prepare import structural_clean_frame
from app.engine.lab.schema_inference import MIN_TRAIN_ROWS, TargetChoice
from app.engine.validation.splits import SOURCE_ROW_COLUMN
from app.services.auto_train.context import RunContext, StageHalt


class StructuralCleaningInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    coerced: pd.DataFrame
    columns: list[str]
    target: TargetChoice
    target_evidence: dict[str, Any]
    profile: dict[str, Any]


class StructuralCleaningOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    frame: pd.DataFrame
    feature_columns: list[str]
    cleaning_log: dict[str, Any]


def run_structural_cleaning(
    ctx: RunContext, inp: StructuralCleaningInput
) -> StructuralCleaningOutput:
    target = inp.target
    ctx.stage(CLEANING)
    evidence_timer = ctx.evidence_start("structural_cleaning")
    feature_columns = [c for c in inp.columns if c != target.column]
    frame, cleaning_log = structural_clean_frame(
        inp.coerced,
        target=target.column,
        feature_columns=feature_columns,
        source_row_column=SOURCE_ROW_COLUMN,
    )
    if target.task_type == "binary":
        frame[target.column] = coerce_binary_target(frame[target.column])
    elif target.task_type == "multiclass":
        # Labels stay as-is; the engine codes them over the full label set.
        # Surrounding whitespace never defines a class; blank labels are unusable.
        frame[target.column] = frame[target.column].map(
            lambda value: (value.strip() or None) if isinstance(value, str) else value
        )
    else:
        frame[target.column] = pd.to_numeric(frame[target.column], errors="coerce")
    invalid_target_rows = int(frame[target.column].isna().sum())
    if invalid_target_rows:
        frame = frame.dropna(subset=[target.column]).reset_index(drop=True)
        cleaning_log["transformations"].append(
            {"step": "drop_unusable_target_rows", "rows_removed": invalid_target_rows}
        )
        cleaning_log["missing_target_rows_removed"] += invalid_target_rows
        cleaning_log["rows_out"] = int(len(frame))
    if target.task_type == "binary":
        frame[target.column] = frame[target.column].astype(int)
    ctx.current_rows = int(len(frame))
    ctx.evidence_finish(evidence_timer)
    if len(frame) < MIN_TRAIN_ROWS:
        ctx.fail(
            f"only {len(frame)} rows left after cleaning",
            extra={
                "target": inp.target_evidence,
                "analysis": inp.profile,
                "cleaning": cleaning_log,
            },
        )
        raise StageHalt
    return StructuralCleaningOutput(
        frame=frame, feature_columns=feature_columns, cleaning_log=cleaning_log
    )
