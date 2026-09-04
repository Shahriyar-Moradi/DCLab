"""Choose one authoritative HoldoutPlan from pre-split structural evidence.

Planning happens after structural cleaning and before the final holdout is
locked. It may use column names, dtypes, row/entity structure, timestamp
structure, the locked target/task, and non-learned structural statistics.
It must not use predictive performance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from app.engine.modeling.problem_profile import ProblemProfile, build_problem_profile
from app.engine.modeling.validation_planner import (
    DEFAULT_FOLDS,
    _strong_temporal_candidate,
    _top_repeated_entity,
)
from app.engine.validation.splits import SOURCE_ROW_COLUMN

HOLDOUT_PLAN_VERSION = "dclab.holdout_plan.v1"
DEFAULT_TEST_SIZE = 0.2

STRATIFIED_RANDOM = "stratified_random"
RANDOM = "random"
GROUP_DISJOINT = "group_disjoint"
TEMPORAL_FUTURE = "temporal_future"
UNSUPPORTED = "unsupported"

GROUP_HOLDOUT_STRATEGIES = {GROUP_DISJOINT}
TEMPORAL_HOLDOUT_STRATEGIES = {TEMPORAL_FUTURE}


class HoldoutUnsupportedError(ValueError):
    """Raised when grouping and temporal constraints both apply, or a required isolation split cannot be formed."""


@dataclass
class HoldoutPlan:
    strategy: str
    test_size: float
    random_state: int
    stratified: bool
    group_column: str | None
    time_column: str | None
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    plan_version: str = HOLDOUT_PLAN_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Any) -> HoldoutPlan:
        from app.engine.modeling.coerce import from_mapping

        plan = from_mapping(cls, payload)
        if plan is None:
            raise ValueError("HoldoutPlan evidence is missing.")
        return plan


def require_supported_holdout(plan: HoldoutPlan) -> HoldoutPlan:
    if plan.strategy == UNSUPPORTED:
        raise HoldoutUnsupportedError(plan.reason)
    return plan


def holdout_plan_event_payload(plan: HoldoutPlan) -> dict[str, Any]:
    return {
        "strategy": plan.strategy,
        "test_size": plan.test_size,
        "random_state": plan.random_state,
        "stratified": plan.stratified,
        "group_column": plan.group_column,
        "time_column": plan.time_column,
        "reason": plan.reason,
        "plan_version": plan.plan_version,
    }


def holdout_locked_event_payload(split: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy": split.get("strategy"),
        "n_train": split.get("n_train"),
        "n_test": split.get("n_test"),
        "requested_test_size": split.get("requested_test_size"),
        "actual_test_size": split.get("actual_test_size"),
        "group_column": split.get("group_column"),
        "group_overlap_count": split.get("group_overlap_count"),
        "time_column": split.get("time_column"),
        "train_time_min": split.get("train_time_min"),
        "train_time_max": split.get("train_time_max"),
        "test_time_min": split.get("test_time_min"),
        "test_time_max": split.get("test_time_max"),
        "strict_temporal_order": split.get("strict_temporal_order"),
        "provenance_disjoint": split.get("provenance_disjoint"),
    }


def plan_holdout(
    frame: pd.DataFrame,
    *,
    target: str,
    task_type: str,
    test_size: float = DEFAULT_TEST_SIZE,
    random_state: int = 42,
    profile: ProblemProfile | None = None,
) -> HoldoutPlan:
    """Return exactly one HoldoutPlan. Never silently fall back to random."""
    feature_columns = [name for name in frame.columns if name not in {target, SOURCE_ROW_COLUMN}]
    problem = profile or build_problem_profile(
        frame,
        target=target,
        task_type=task_type,
        feature_columns=feature_columns,
    )
    entity = _top_repeated_entity(problem)
    temporal = _strong_temporal_candidate(problem, DEFAULT_FOLDS)
    grouping_needed = entity is not None
    temporal_needed = temporal is not None
    evidence: dict[str, Any] = {
        "row_count": int(len(frame)),
        "task_type": task_type,
        "repeated_entity": entity,
        "strong_temporal": temporal,
        "grouping_needed": grouping_needed,
        "temporal_needed": temporal_needed,
        "time_candidates": [item["column"] for item in problem.time_candidates],
    }
    size = float(test_size)

    if grouping_needed and temporal_needed:
        return HoldoutPlan(
            strategy=UNSUPPORTED,
            test_size=size,
            random_state=random_state,
            stratified=task_type == "binary",
            group_column=str(entity["column"]),
            time_column=str(temporal["column"]),
            reason=(
                "Repeated-entity grouping and strong temporal prediction structure "
                "are both present. No verified combined final-holdout strategy is available."
            ),
            evidence=evidence,
        )

    if temporal_needed:
        return HoldoutPlan(
            strategy=TEMPORAL_FUTURE,
            test_size=size,
            random_state=random_state,
            stratified=False,
            group_column=None,
            time_column=str(temporal["column"]),
            reason=(
                "The cleaned table has strong temporal prediction structure; "
                "the final holdout is the latest chronological slice."
            ),
            evidence=evidence,
        )

    if grouping_needed:
        group_column = str(entity["column"])
        unique_groups = int(entity.get("unique_count") or 0)
        if group_column in frame.columns:
            unique_groups = int(frame[group_column].nunique(dropna=True))
        if unique_groups < 2:
            return HoldoutPlan(
                strategy=UNSUPPORTED,
                test_size=size,
                random_state=random_state,
                stratified=False,
                group_column=group_column,
                time_column=None,
                reason="Repeated entities were detected but fewer than two groups exist for a disjoint holdout.",
                evidence={**evidence, "unique_groups": unique_groups},
            )
        return HoldoutPlan(
            strategy=GROUP_DISJOINT,
            test_size=size,
            random_state=random_state,
            stratified=False,
            group_column=group_column,
            time_column=None,
            reason=(
                "Repeated entities require a group-disjoint final holdout so no entity "
                "appears in both train and test."
            ),
            evidence={**evidence, "unique_groups": unique_groups},
        )

    if task_type == "binary":
        return HoldoutPlan(
            strategy=STRATIFIED_RANDOM,
            test_size=size,
            random_state=random_state,
            stratified=True,
            group_column=None,
            time_column=None,
            reason="Ordinary binary classification uses a stratified random 80/20 final holdout.",
            evidence=evidence,
        )

    return HoldoutPlan(
        strategy=RANDOM,
        test_size=size,
        random_state=random_state,
        stratified=False,
        group_column=None,
        time_column=None,
        reason="Ordinary regression uses a random 80/20 final holdout.",
        evidence=evidence,
    )
