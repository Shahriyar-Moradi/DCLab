"""Shared validation schema for case-study configs.

Every case study — real (Olist) or synthetic — validates against the same
``CaseStudyConfig``. Fields specific to a later step (``decision_actions`` for
Step 3, ``segment_columns`` for Step 5) are wired here now, per the build
doc's instruction, even though nothing reads them yet.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TaskType = Literal["binary", "regression", "time_to_event", "multiclass", "forecasting", "survival"]


class DataSourceConfig(BaseModel):
    """Where the case study's frame comes from.

    ``kind="olist"`` loads the real Olist adapter (``app.engine.datasets.olist``).
    ``kind="synthetic"`` uses a documented generator with a known true latent
    probability function and a ground-truth sidecar that is never fed to
    training or ingestion — the same pattern used for the Lab environment's
    synthetic company data. The generator itself is built in Step 1; this
    field only names which one a case study expects.
    """

    kind: Literal["olist", "synthetic"]

    # olist-only
    as_of_dates: list[str] | None = None
    entity_filter: str | None = Field(
        default=None,
        description=(
            "Human-readable description of any population filter applied to the "
            "raw Olist snapshot before scoring (e.g. 'customers with no order in "
            "the 90 days before as_of_date'). Applied in the data-loading step; "
            "documented here so every report states exactly which population "
            "this case study covers."
        ),
    )

    # synthetic-only
    generator: str | None = Field(
        default=None,
        description="Name of the Step-1 generator function that produces this case study's frame + ground truth.",
    )
    n_entities: int | None = None
    seed: int | None = None

    @model_validator(mode="after")
    def _validate_kind_specific_fields(self) -> "DataSourceConfig":
        if self.kind == "synthetic":
            missing = [
                name
                for name, value in (("generator", self.generator), ("n_entities", self.n_entities), ("seed", self.seed))
                if value is None
            ]
            if missing:
                raise ValueError(f"synthetic data_source is missing required field(s): {', '.join(missing)}")
        if self.kind == "olist" and self.generator is not None:
            raise ValueError("olist data_source must not declare a synthetic 'generator'")
        return self


class TargetDefinition(BaseModel):
    """Entity, prediction time, horizon, and label the case study predicts."""

    entity_id: str = "entity_id"
    prediction_time_column: str = "as_of_date"
    horizon_days: int = Field(gt=0)
    target_column: str
    task_type: TaskType
    evaluation_metric: str


class CaseStudyConfig(BaseModel):
    id: str
    name: str
    business_problem: str
    data_source: DataSourceConfig
    target: TargetDefinition
    feature_groups: dict[str, list[str]]
    validation_strategy: str = "time"

    # Optional override merged onto configs/experiments/default.yaml's search
    # block when Step 2 wires the DCLab runner. Empty means "use the default."
    search_overrides: dict = Field(default_factory=dict)

    # Rendered verbatim into every report this case study produces (Step 7).
    # Required to be non-empty for case studies built on a proxy target
    # rather than the real business outcome (CS2).
    honesty_note: str | None = None

    # Wired now for Step 3 (decision policy actions) and Step 5 (segment
    # breakdown columns) so those steps don't need a schema migration later.
    decision_actions: list[str] = Field(default_factory=list)
    segment_columns: list[str] = Field(default_factory=list)

    @field_validator("feature_groups")
    @classmethod
    def _non_empty_groups(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if not value:
            raise ValueError("feature_groups must declare at least one group")
        for group, columns in value.items():
            if not columns:
                raise ValueError(f"feature group '{group}' has no columns")
        return value
