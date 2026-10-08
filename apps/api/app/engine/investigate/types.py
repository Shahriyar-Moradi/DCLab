"""Typed inputs and outputs of the trust checks. No database, no I/O."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from app.domain.findings import (
    FindingCheck,
    FindingSeverity,
    FindingStatus,
    RecommendationKind,
    render_message,
)


@dataclass(frozen=True)
class Finding:
    """One trust check. ``status`` is pass | warning | fail, or ``not_evaluated`` (with
    ``evidence["not_evaluated_reason"]``) when its evidence is missing or the check
    errored. ``evidence`` holds numbers and column names only (training rows and CV
    folds); ``message`` renders the plain-language template."""

    check: FindingCheck
    status: FindingStatus
    severity: FindingSeverity
    evidence: dict[str, Any]
    recommendation_kind: RecommendationKind | None
    message_keys: tuple[str, ...]

    @property
    def message(self) -> str:
        return render_message(self.message_keys, self.evidence)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["message_keys"] = list(self.message_keys)
        return payload


@dataclass(frozen=True)
class RunEvidence:
    """What the checks may read of a finished run: CV fold metrics, the winner refit
    scored on its own training rows, the dummy baseline's CV, the training-partition
    class profile and the leakage audit. Built by ``run_evidence_from_result``, which
    never copies a final-holdout key, so no check can read the holdout."""

    task_type: str | None
    primary_metric: str | None
    winner_family: str | None = None
    winner_features: tuple[str, ...] = ()
    winner_cv: Mapping[str, float] = field(default_factory=dict)
    winner_cv_std: Mapping[str, float] = field(default_factory=dict)
    winner_class_weighted: bool = False
    # Tuned winners are re-tuned on the full training partition before the final fit.
    final_fit_retuned: bool = False
    n_folds: int | None = None
    # Winner refit on the full training partition, scored on those same rows (raw scale).
    train_metrics: Mapping[str, float] = field(default_factory=dict)
    baseline_family: str | None = None
    baseline_cv: Mapping[str, float] = field(default_factory=dict)
    # Best trained linear/logistic candidate (the less flexible reference model).
    simple_family: str | None = None
    simple_cv: Mapping[str, float] = field(default_factory=dict)
    class_weighted_candidates: int = 0
    class_distribution: Mapping[str, int] | None = None
    minority_class_fraction: float | None = None
    imbalance_ratio: float | None = None
    leakage_audited: bool = False
    leakage_risks: Sequence[Mapping[str, Any]] = ()
    leakage_exclusions: Sequence[Mapping[str, Any]] = ()
    allowed_features: tuple[str, ...] = ()
    # P5.1-A: the winner's per-fold metrics and its out-of-fold summary (``result["oof_evidence"]``:
    # calibration bins and subgroup scores of CV validation rows; ``engine.investigate.oof``).
    winner_fold_metrics: Sequence[Mapping[str, float]] = ()
    oof: Mapping[str, Any] = field(default_factory=dict)
    # How the run split its rows (HoldoutPlan strategy) and the plan's time / group columns.
    split_strategy: str | None = None
    time_column: str | None = None
    group_column: str | None = None
    # Modeled columns produced by as_of_aggregate transforms (P5.0-A registry); none today.
    as_of_features: tuple[str, ...] = ()


@dataclass(frozen=True)
class SplitFeatures:
    """The model columns of the training partition and of the test partition, built in
    the worker for the train -> test checks. FEATURES ONLY on the test side: no test
    label, prediction or metric ever enters, and the checks store aggregates and column
    names only. ``train_labels`` are training-partition labels (conflicting duplicates);
    times and groups are the plan's time / group column values of each partition."""

    train: Any  # pandas DataFrame
    test: Any  # pandas DataFrame
    train_labels: Any = None
    time_column: str | None = None
    train_times: Any = None
    test_times: Any = None
    group_column: str | None = None
    train_groups: Any = None
    test_groups: Any = None
    # The whole time column in frame order and the (train, test) row masks into it: the check
    # reads it ONCE (as the splitter does) and then splits, so both sides share one date format.
    all_times: Any = None
    time_masks: Any = None


@dataclass(frozen=True)
class ParentEvidence:
    """The parent experiment of a branch, from its stored result: CV only (never holdout)."""

    experiment_id: str
    same_split_plan: bool
    primary_metric: str | None
    features: tuple[str, ...]
    cv: Mapping[str, float] = field(default_factory=dict)
    cv_std: Mapping[str, float] = field(default_factory=dict)
    winner_family: str | None = None
    # Per-fold CV metrics in fold order (same SplitPlan -> the same outer folds as the child).
    fold_metrics: Sequence[Mapping[str, float]] = ()


@dataclass(frozen=True)
class TimeTravelProbe:
    """What the truncation replay (``future_influence_probe``) needs: a producer that
    recomputes the as-of features of a frame, and training-partition rows only."""

    produce: Any  # Callable[[DataFrame], DataFrame]
    frame: Any  # pandas DataFrame (training partition)
