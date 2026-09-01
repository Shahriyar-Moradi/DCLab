"""Shared dataclasses for tasks, candidates, and experiment search."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ExperimentStatus(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    PROFILING = "PROFILING"
    FEATURE_ENGINEERING = "FEATURE_ENGINEERING"
    GENERATING_CANDIDATES = "GENERATING_CANDIDATES"
    TRAINING = "TRAINING"
    EVALUATING = "EVALUATING"
    FILTERING = "FILTERING"
    SELECTING = "SELECTING"
    ENSEMBLING = "ENSEMBLING"
    REPORTING = "REPORTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskType(str, Enum):
    BINARY = "binary"
    REGRESSION = "regression"
    TIME_TO_EVENT = "time_to_event"
    MULTICLASS = "multiclass"
    FORECASTING = "forecasting"
    SURVIVAL = "survival"


@dataclass
class SearchConfig:
    strategy: str = "progressive"
    max_feature_group_combinations: int = 32
    max_candidates: int = 48
    max_hyperparameter_trials: int = 0
    max_ensemble_size: int = 7
    max_training_seconds: float = 60.0
    seed: int = 42
    exclude_high_leakage: bool = True
    min_metric: float = 0.55
    retain_min: int = 3
    retain_max: int = 7
    max_abs_correlation: float = 0.95
    n_robustness_folds: int = 3

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskSpec:
    id: str
    name: str
    description: str = ""
    task_type: str = TaskType.BINARY.value
    target: str = "target"
    entity_id: str | None = "entity_id"
    prediction_time_column: str | None = "as_of_date"
    prediction_horizon_days: int | None = None
    evaluation_metric: str = "pr_auc"
    feature_groups: dict[str, list[str]] = field(default_factory=dict)
    validation_strategy: str = "time"
    event_time_column: str | None = None
    event_value_column: str | None = None
    config_path: str | None = None
    # Only set for the "open_ingest" search strategy: numeric vs categorical
    # column roles the ColumnTransformer preprocessor needs. Empty for every
    # other task — this does not change how admin /admin/lab tasks behave.
    column_roles: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Candidate:
    candidate_id: str
    task_id: str
    feature_groups: tuple[str, ...]
    features: tuple[str, ...]
    model_family: str
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    preprocessing: dict[str, Any] = field(default_factory=dict)
    validation_strategy: str = "time"
    random_seed: int = 42
    fingerprint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "task_id": self.task_id,
            "feature_groups": list(self.feature_groups),
            "features": list(self.features),
            "model_family": self.model_family,
            "hyperparameters": self.hyperparameters,
            "preprocessing": self.preprocessing,
            "validation_strategy": self.validation_strategy,
            "random_seed": self.random_seed,
            "fingerprint": self.fingerprint,
            "metadata": self.metadata,
        }
