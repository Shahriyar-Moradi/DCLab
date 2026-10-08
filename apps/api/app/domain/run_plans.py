"""The ``plan`` input of a root run (ADR 0008 § Consequences, §1b, §2; P6.9-A step A2).

An ``ExperimentPlanProposal`` payload: the agent-before answers of the AI-before decision
points, travelling into the run as typed inputs (the worker never waits on an LLM).
Each field belongs to one registry point. A plan the human ``accepted`` applies as human
input (validated at job run); an ``applied`` plan (L2) may carry only §1b L2 kinds —
a family subset and a time budget ≤ the rule's, impute_median ↔ impute_most_frequent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PLAN_PROPOSAL_TYPE = "ExperimentPlanProposal"
PLAN_STATUSES = ("accepted", "applied")
# The rule's time budget (auto_train_service._search_config); a plan may only lower it.
RULE_TRAINING_SECONDS = 600.0
# Tighten-only split validator (ADR 0008 §2): the code-owned holdout-fraction floor.
HOLDOUT_FRACTION_FLOOR = 0.2
HOLDOUT_FRACTION_CEILING = 0.5
IMPUTE_ACTIONS = ("impute_median", "impute_most_frequent")
PLAN_FIELDS = {
    "target.column": ("target_column",),
    "spec.objective": ("primary_metric",),
    "split.strategy": ("split",),
    "training.families_budget": ("families", "max_training_seconds"),
    "column.missing_value_action": ("missing_values",),
}


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanSplit(_Frozen):
    strategy: Literal["stratified_random", "random", "group_disjoint", "temporal_future"]
    group_column: str | None = Field(default=None, min_length=1, max_length=256)
    time_column: str | None = Field(default=None, min_length=1, max_length=256)
    test_size: float | None = Field(default=None, ge=0.05, le=HOLDOUT_FRACTION_CEILING)


class ExperimentPlan(_Frozen):
    target_column: str | None = Field(default=None, min_length=1, max_length=256)
    primary_metric: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    split: PlanSplit | None = None
    families: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=16)
    max_training_seconds: float | None = Field(default=None, gt=0, le=RULE_TRAINING_SECONDS)
    missing_values: dict[str, Literal["impute_median", "impute_most_frequent", "drop_column"]] | None = Field(
        default=None, max_length=256)

    @field_validator("families")
    @classmethod
    def _family_keys(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and any(not name.replace("_", "").isalnum() or len(name) > 64 for name in value):
            raise ValueError("families must be registry family keys")
        return value

    def l1_fields(self) -> list[str]:
        """Answers whose §1b ceiling is L1 whatever the rule says (an ``applied`` plan may
        not carry them): target, metric, split, and any exclusion (``drop_column``)."""

        fields = [name for name in ("target_column", "primary_metric", "split") if getattr(self, name) is not None]
        if any(action == "drop_column" for action in (self.missing_values or {}).values()):
            fields.append("missing_values")
        return fields


__all__ = ["HOLDOUT_FRACTION_CEILING", "HOLDOUT_FRACTION_FLOOR", "IMPUTE_ACTIONS", "PLAN_FIELDS",
           "PLAN_PROPOSAL_TYPE", "PLAN_STATUSES", "RULE_TRAINING_SECONDS", "ExperimentPlan", "PlanSplit"]
