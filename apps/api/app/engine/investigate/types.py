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
