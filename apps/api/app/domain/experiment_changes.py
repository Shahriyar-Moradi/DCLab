"""Typed change sets for branched experiments (ADR 0006 §4).

``ExperimentChangeSet`` is the structural gate for ``experiments.change_set``:
schema version, discriminated change kinds, whole-set uniqueness rules, size and
a recursive forbidden-key check. Registry-, task- and dataset-dependent checks
(family allowlists, observed classes, metric sets, leakage exclusions) need the
parent experiment and belong to the branch service (P2.4-A).

The SQL CHECK strings below are the database backstop shared by ``db/models.py``;
Alembic 0063 inlines them literally.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.execution_requests import FORBIDDEN_REQUEST_PAYLOAD_KEYS
from app.domain.state_graph import STATE_GRAPH_JSON_MAX_BYTES, sql_no_forbidden_keys

CHANGE_SET_SCHEMA_VERSION = 1
CHANGE_SET_MAX_CHANGES = 32
CHANGE_SET_MAX_BYTES = STATE_GRAPH_JSON_MAX_BYTES
INTENT_MAX_CHARS = 2000

# Exactly what the engine executes and codegen reproduces today (§4).
FEATURE_TRANSFORM_ALLOWLIST = (
    "drop_column",
    "keep",
    "impute_median",
    "impute_most_frequent",
    "datetime_extract",
)
# Dummy baselines are always evaluated (P1.4-A1) and can never be excluded.
# Mirrors ``app.engine.search.generator.DUMMY_FAMILIES``.
NON_EXCLUDABLE_FAMILIES = ("majority", "mean", "median")

Scalar = Union[bool, int, float, str]


class _Change(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class HyperparameterOverride(_Change):
    kind: Literal["hyperparameter_override"]
    family: str = Field(min_length=1, max_length=64)
    parameters: dict[str, Scalar] = Field(min_length=1, max_length=32)


class FamilyInclude(_Change):
    kind: Literal["family_include"]
    family: str = Field(min_length=1, max_length=64)


class FamilyExclude(_Change):
    kind: Literal["family_exclude"]
    family: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _not_a_baseline(self) -> "FamilyExclude":
        if self.family in NON_EXCLUDABLE_FAMILIES:
            raise ValueError(f"dummy baseline '{self.family}' cannot be excluded")
        return self


class ClassWeighting(_Change):
    kind: Literal["class_weighting"]
    mode: Literal["none", "balanced", "custom"]
    weights: dict[str, float] | None = None

    @model_validator(mode="after")
    def _weights_match_mode(self) -> "ClassWeighting":
        if self.mode == "custom":
            if not self.weights:
                raise ValueError("custom class weighting requires weights")
            if any(value <= 0 for value in self.weights.values()):
                raise ValueError("class weights must be > 0")
        elif self.weights is not None:
            raise ValueError("weights are only allowed with mode 'custom'")
        return self


class ThresholdConstraint(_Change):
    metric: str = Field(min_length=1, max_length=64)
    op: Literal[">=", "<="]
    value: float


class ThresholdObjective(_Change):
    kind: Literal["threshold_objective"]
    constraints: list[ThresholdConstraint] = Field(default_factory=list, max_length=16)
    cost_false_positive: float | None = Field(default=None, ge=0)
    cost_false_negative: float | None = Field(default=None, ge=0)


class MetricOverride(_Change):
    kind: Literal["metric_override"]
    primary_metric: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=512)


class FeatureTransformAdd(_Change):
    kind: Literal["feature_transform_add"]
    column: str = Field(min_length=1, max_length=256)
    transform: Literal[FEATURE_TRANSFORM_ALLOWLIST]  # type: ignore[valid-type]
    parameters: dict[str, Scalar] | None = None


class FeatureTransformRemove(_Change):
    kind: Literal["feature_transform_remove"]
    column: str = Field(min_length=1, max_length=256)
    transform: Literal[FEATURE_TRANSFORM_ALLOWLIST]  # type: ignore[valid-type]
    parameters: dict[str, Scalar] | None = None


ExperimentChange = Annotated[
    Union[
        HyperparameterOverride,
        FamilyInclude,
        FamilyExclude,
        ClassWeighting,
        ThresholdObjective,
        MetricOverride,
        FeatureTransformAdd,
        FeatureTransformRemove,
    ],
    Field(discriminator="kind"),
]

CHANGE_KINDS = (
    "hyperparameter_override",
    "family_include",
    "family_exclude",
    "class_weighting",
    "threshold_objective",
    "metric_override",
    "feature_transform_add",
    "feature_transform_remove",
)


def _identity(change: BaseModel) -> tuple[str, ...]:
    """Duplicate key; kinds without family/column are therefore singletons."""

    kind = getattr(change, "kind")
    if hasattr(change, "family"):
        return (kind, getattr(change, "family"))
    if hasattr(change, "column"):
        return (kind, getattr(change, "column"), getattr(change, "transform"))
    return (kind,)


def _forbidden_paths(value: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_REQUEST_PAYLOAD_KEYS:
                hits.append(f"{path}.{key}")
            hits.extend(_forbidden_paths(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(_forbidden_paths(item, f"{path}[{index}]"))
    return hits


class ExperimentChangeSet(BaseModel):
    """What a branch changes relative to its parent. Never dataset/split/target/task."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: Literal[1] = CHANGE_SET_SCHEMA_VERSION
    changes: list[ExperimentChange] = Field(min_length=1, max_length=CHANGE_SET_MAX_CHANGES)

    @model_validator(mode="after")
    def _whole_set_rules(self) -> "ExperimentChangeSet":
        seen: set[tuple[str, ...]] = set()
        for change in self.changes:
            identity = _identity(change)
            if identity in seen:
                raise ValueError(f"duplicate change {identity}")
            seen.add(identity)
        stored = self.to_storage()
        hits = _forbidden_paths(stored)
        if hits:
            raise ValueError(f"forbidden keys in change set: {', '.join(hits)}")
        if len(json.dumps(stored, sort_keys=True).encode("utf-8")) > CHANGE_SET_MAX_BYTES:
            raise ValueError(f"change set exceeds {CHANGE_SET_MAX_BYTES} bytes")
        return self

    def to_storage(self) -> dict[str, Any]:
        """JSON stored in ``experiments.change_set``."""

        return self.model_dump(mode="json", exclude_none=True)


# --- experiments CHECK backstops (§4) -----------------------------------------
CK_EXPERIMENTS_INTENT_NOT_BLANK = "intent IS NULL OR char_length(btrim(intent)) > 0"
CK_EXPERIMENTS_CHANGE_SET_OBJECT = (
    "change_set IS NULL OR jsonb_typeof(change_set) = 'object'"
)
CK_EXPERIMENTS_CHANGE_SET_SCHEMA_VERSION = (
    "change_set IS NULL OR COALESCE((change_set ->> 'schema_version') ~ '^[0-9]+$', false)"
)
CK_EXPERIMENTS_CHANGE_SET_CHANGES = (
    "change_set IS NULL OR CASE WHEN jsonb_typeof(change_set -> 'changes') = 'array' "
    f"THEN jsonb_array_length(change_set -> 'changes') BETWEEN 1 AND {CHANGE_SET_MAX_CHANGES} "
    "ELSE false END"
)
CK_EXPERIMENTS_CHANGE_SET_BOUNDED = (
    f"change_set IS NULL OR octet_length(CAST(change_set AS TEXT)) <= {CHANGE_SET_MAX_BYTES}"
)
CK_EXPERIMENTS_CHANGE_SET_NO_SECRETS = (
    f"change_set IS NULL OR {sql_no_forbidden_keys('change_set')}"
)
CK_EXPERIMENTS_CHANGE_SET_REQUIRES_PARENT = (
    "change_set IS NULL OR parent_pipeline_run_id IS NOT NULL"
)
CK_EXPERIMENTS_SPLIT_PLAN_REQUIRES_PROJECT = (
    "split_plan_id IS NULL OR project_id IS NOT NULL"
)
