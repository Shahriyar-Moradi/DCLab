"""Branch runs (ADR 0006 §4): apply the materialized change set inside the stages.

A branch shell Experiment carries ``change_set`` (insert-only) and the
materialized ``config.branch_overrides`` / ``config.objective`` written by
``experiment_branch_service``; the worker never walks ancestors. Every helper
here is deterministic and train-only: column treatments are decided before the
fold-local Pipelines, never on holdout rows, and never re-include a column the
run's own leakage plan excluded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pandas as pd

from sqlalchemy.orm import Session

from app.db.models import ClientLabUpload, Experiment, SplitPlan
from app.domain.errors import InvalidChangeSetError, SplitPlanLineageError
from app.engine.features.encode import infer_datetime_format
from app.engine.lab.auto_prepare import (
    MAX_CATEGORICAL_CARDINALITY,
    MissingValuePlan,
    apply_feature_engineering_actions,
)
from app.services.experiment_branch_service import INCLUDING_TREATMENTS

# ``encode_datetime_columns`` converts a date-named column at >= 80% parsed values.
DATETIME_MIN_PARSED_FRACTION = 0.8


@dataclass(frozen=True)
class BranchRun:
    experiment_id: UUID
    parent_id: UUID
    split_plan: SplitPlan
    target_column: str
    task_type: str
    overrides: dict[str, Any]
    objective: dict[str, Any] | None

    @property
    def columns(self) -> dict[str, dict[str, Any]]:
        return dict(self.overrides.get("columns") or {})


def load_branch_run(db: Session, upload: ClientLabUpload) -> BranchRun | None:
    """The branch this upload's shell runs, or None for a root run."""

    shell = db.get(Experiment, upload.experiment_id) if upload.experiment_id is not None else None
    if shell is None or shell.change_set is None:
        return None
    config = dict(shell.config or {})
    overrides = dict(config.get("branch_overrides") or {})
    plan = db.get(SplitPlan, shell.split_plan_id) if shell.split_plan_id is not None else None
    if plan is None or plan.workspace_id != shell.workspace_id or not overrides:
        raise SplitPlanLineageError("branch has no split plan or materialized overrides", code="split_plan_missing")
    return BranchRun(
        experiment_id=shell.id,
        parent_id=shell.parent_pipeline_run_id,
        split_plan=plan,
        target_column=str(overrides.get("target_column")),
        task_type=str(overrides.get("task_type")),
        overrides=overrides,
        objective=config.get("objective"),
    )


def require_same_target(branch: BranchRun, target_column: str | None, task_type: str | None) -> None:
    if target_column != branch.target_column or task_type != branch.task_type:
        raise InvalidChangeSetError(
            "new_root_required",
            f"branch resolved {target_column!r}/{task_type!r}, parent is "
            f"{branch.target_column!r}/{branch.task_type!r}",
            path="target_column",
        )


def _columns_path(column: str) -> str:
    return f"branch_overrides.columns.{column}"


def apply_missing_value_overrides(
    branch: BranchRun, missing_plan: MissingValuePlan, *, leakage_excluded: set[str]
) -> None:
    """Drop or keep columns against the missing-value rule (mutates ``missing_plan``)."""

    decisions = {item.column: item for item in missing_plan.column_decisions}
    for column, spec in sorted(branch.columns.items()):
        treatment = spec.get("treatment")
        if (treatment in INCLUDING_TREATMENTS or spec.get("datetime_extract") is True) and (
            column in leakage_excluded
        ):
            raise InvalidChangeSetError(
                "leakage_excluded_column",
                f"{column!r} is excluded by this run's leakage plan",
                path=_columns_path(column),
            )
        decision = decisions.get(column)
        if treatment == "drop":
            if column not in missing_plan.dropped_columns:
                missing_plan.dropped_columns.append(column)
            if decision is not None:
                decision.action = "drop_column"
        elif treatment in INCLUDING_TREATMENTS and column in missing_plan.dropped_columns:
            missing_plan.dropped_columns.remove(column)
            if decision is not None:
                decision.action = (
                    "keep"
                    if decision.missing_count == 0
                    else "impute_most_frequent"
                    if treatment == "categorical"
                    else "impute_median"
                )


def datetime_overrides(branch: BranchRun) -> tuple[set[str], list[str]]:
    """(columns never converted, columns always converted) to epoch seconds."""

    off = {column for column, spec in branch.columns.items() if spec.get("datetime_extract") is False}
    on = sorted(column for column, spec in branch.columns.items() if spec.get("datetime_extract") is True)
    return off, on


def forced_datetime_action(
    engineered_train: pd.DataFrame, columns: list[str], already: set[str]
) -> tuple[pd.DataFrame, dict[str, Any] | None]:
    """The same stateless epoch transform ``engineer_features`` applies, for named columns."""

    todo = [column for column in columns if column not in already]
    for column in todo:
        if column not in engineered_train.columns:
            raise InvalidChangeSetError(
                "transform_not_applicable", f"{column!r} is not modeled in this run", path=_columns_path(column)
            )
        series = engineered_train[column]
        parsed = pd.to_datetime(series, errors="coerce") if not _is_numeric(engineered_train, column) else None
        # Same bar as the automatic detector (encode_datetime_columns), on train rows only.
        if parsed is None or float(parsed.notna().mean()) < DATETIME_MIN_PARSED_FRACTION:
            raise InvalidChangeSetError(
                "transform_not_applicable",
                f"{column!r} does not parse as dates on the training partition",
                path=_columns_path(column),
            )
    if not todo:
        return engineered_train, None
    action = {
        "step": "datetime_to_unix_seconds",
        "transformation": "datetime_to_epoch",
        "columns": todo,
        "input_columns": todo,
        "output_columns": todo,
        "reason": "Branch change: convert these columns to unix seconds.",
        "parameters": {
            "unit": "seconds",
            "epoch": "unix",
            "formats": {column: infer_datetime_format(engineered_train[column]) for column in todo},
        },
        "learned_from_data": False,
        "decision_partition": "train",
        "source": "branch_change_set",
    }
    return apply_feature_engineering_actions(engineered_train, [action]), action


def _is_numeric(frame: pd.DataFrame, column: str) -> bool:
    series = frame[column]
    return bool(pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series))


def apply_role_overrides(
    branch: BranchRun,
    engineered_train: pd.DataFrame,
    num_cols: list[str],
    cat_cols: list[str],
    *,
    allowed_predictors: set[str],
    identifiers: set[str],
) -> tuple[list[str], list[str]]:
    """Force numeric (median-imputed) / categorical (most-frequent) / kept roles."""

    num, cat = list(num_cols), list(cat_cols)
    for column, spec in sorted(branch.columns.items()):
        treatment = spec.get("treatment")
        if treatment == "drop":
            num = [name for name in num if name != column]
            cat = [name for name in cat if name != column]
            continue
        if treatment not in INCLUDING_TREATMENTS:
            continue
        if column not in allowed_predictors:
            raise InvalidChangeSetError(
                "leakage_excluded_column",
                f"{column!r} is excluded by this run's leakage plan",
                path=_columns_path(column),
            )
        if column in identifiers:
            raise InvalidChangeSetError(
                "identifier_column", f"{column!r} is an identifier in this run", path=_columns_path(column)
            )
        if column not in engineered_train.columns:
            raise InvalidChangeSetError(
                "transform_not_applicable", f"{column!r} is not modeled in this run", path=_columns_path(column)
            )
        numeric = _is_numeric(engineered_train, column)
        if treatment == "numeric" and not numeric:
            raise InvalidChangeSetError(
                "transform_not_applicable", f"{column!r} is not numeric in this run", path=_columns_path(column)
            )
        if treatment == "keep" and (column in num or column in cat):
            continue
        role = "categorical" if treatment == "categorical" or (treatment == "keep" and not numeric) else "numeric"
        if role == "categorical" and column not in cat and int(
                engineered_train[column].nunique(dropna=True)) > MAX_CATEGORICAL_CARDINALITY:
            raise InvalidChangeSetError(  # the engine's one-hot cap (ADR 0008 §1b), as the L2 RoleValidator
                "categorical_cardinality_above_cap",
                f"{column!r} has more than {MAX_CATEGORICAL_CARDINALITY} distinct values in the training rows",
                path=_columns_path(column))
        num = [name for name in num if name != column]
        cat = [name for name in cat if name != column]
        (num if role == "numeric" else cat).append(column)
    return num, cat
