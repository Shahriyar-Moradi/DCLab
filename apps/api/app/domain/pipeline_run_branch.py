"""Scientific branch pointers on PipelineRun. This module does not fork runs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

BRANCH_REASON_MAX_CHARS = 512

CK_EXPERIMENTS_PARENT_NOT_SELF = (
    "parent_pipeline_run_id IS NULL OR parent_pipeline_run_id <> id"
)
CK_EXPERIMENTS_BRANCH_KEY = (
    "branch_key IS NULL OR branch_key ~ '^[a-z][a-z0-9_]{0,63}$'"
)
CK_EXPERIMENTS_BRANCH_REASON = (
    "branch_reason IS NULL OR char_length(branch_reason) BETWEEN 1 AND "
    f"{BRANCH_REASON_MAX_CHARS}"
)
CK_EXPERIMENTS_BRANCH_REQUIRES_PARENT = (
    "parent_pipeline_run_id IS NOT NULL "
    "OR (branch_key IS NULL AND branch_reason IS NULL)"
)

# ADR 0007 §6.1 (P5.4-A1a, Alembic 0086): a ``sealed`` run is a CV-only improve try that never
# scores the final test set and never becomes a ModelVersion. Set at INSERT, insert-only.
EVIDENCE_MODE_SCORED = "scored"
EVIDENCE_MODE_SEALED = "sealed"
EVIDENCE_MODES = (EVIDENCE_MODE_SCORED, EVIDENCE_MODE_SEALED)
CK_EXPERIMENTS_EVIDENCE_MODE = "evidence_mode IN ('scored', 'sealed')"
# What every reader shows for a sealed run where the final test would be (never a value).
SEALED_FINAL_TEST_NOTE = "Final test set: not used (improve try)."

# P5.4-A1b (ADR 0007 §6.2): service-only overrides of one branch run. Never a request field, never a
# SearchConfig field: ``branch_experiment(service_overrides=...)`` records them under this config key
# of the new run only, ``execute_experiment`` applies them to its own copy of the configuration, and
# every copy of a run's ``config`` (``search_from_mapping``, a branch's parent config) drops these keys,
# so a manual branch of a final run is an ordinary run.
SERVICE_OVERRIDES_CONFIG_KEY = "service_overrides"
SERVICE_ONLY_CONFIG_KEYS = frozenset({SERVICE_OVERRIDES_CONFIG_KEY, "score_holdout", "evidence_mode",
                                      "expected_selection"})
_CONSTRAINT_OPS = (">=", "<=")
SERVICE_MAX_TRAINING_SECONDS = 43_200.0  # ADR 0007 §3: the loop's hard wall-time maximum


def strip_service_only(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """A copy of a run ``config`` without any service-only key."""

    return {key: value for key, value in dict(config or {}).items() if key not in SERVICE_ONLY_CONFIG_KEYS}


@dataclass(frozen=True)
class ServiceOverrides:
    """``evidence_mode`` (set at INSERT); ``expected_selection`` (a
    ``app.engine.evaluation.comparability.RunEvidence`` pinned from the probe, checked at the winner
    lock before the final test); ``max_training_seconds`` (the pinned budget); ``objective_constraints``
    (the goal's ``(metric, op, value)`` constraints: they replace the objective's constraints so the
    lock solves under them; the primary metric and selection never change)."""

    evidence_mode: str = EVIDENCE_MODE_SCORED
    expected_selection: Any = None
    max_training_seconds: float | None = None
    objective_constraints: tuple[tuple[str, str, float], ...] | None = None

    def __post_init__(self) -> None:
        from app.engine.evaluation.comparability import RunEvidence
        from app.engine.modeling.objective import THRESHOLD_METRICS

        if self.evidence_mode not in EVIDENCE_MODES:
            raise ValueError(f"unknown evidence_mode {self.evidence_mode!r}")
        if self.expected_selection is not None:
            if not isinstance(self.expected_selection, RunEvidence) or not self.expected_selection.winner:
                raise ValueError("expected_selection must be the probe's RunEvidence with a winner")
            if self.evidence_mode == EVIDENCE_MODE_SEALED:
                raise ValueError("a pinned selection guards the final test; a sealed run has none")
        budget = self.max_training_seconds
        if budget is not None and (isinstance(budget, bool) or not isinstance(budget, (int, float))
                                   or not math.isfinite(budget) or not 0 < budget <= SERVICE_MAX_TRAINING_SECONDS):
            raise ValueError(f"max_training_seconds must be in (0, {SERVICE_MAX_TRAINING_SECONDS:g}]")
        for item in self.objective_constraints or ():
            metric, op, value = item
            if (metric not in THRESHOLD_METRICS or op not in _CONSTRAINT_OPS or isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0):
                raise ValueError(f"invalid goal constraint {item!r}")

    def objective(self, base: Mapping[str, Any] | None) -> dict[str, Any] | None:
        """The run's objective with the goal's constraints (unchanged without any)."""

        if self.objective_constraints is None:
            return dict(base) if base is not None else None
        return {**dict(base or {}), "constraints": [
            {"metric": metric, "op": op, "value": float(value)} for metric, op, value in self.objective_constraints]}

    def config_record(self) -> dict[str, Any]:
        return {
            "evidence_mode": self.evidence_mode,
            "expected_selection": None if self.expected_selection is None else self.expected_selection.to_dict(),
            "max_training_seconds": None if self.max_training_seconds is None else float(self.max_training_seconds),
            "objective_constraints": None if self.objective_constraints is None else [
                {"metric": metric, "op": op, "value": float(value)} for metric, op, value in self.objective_constraints],
        }

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "ServiceOverrides | None":
        """The record ``branch_experiment`` wrote on this run (None for every ordinary run)."""

        record = (config or {}).get(SERVICE_OVERRIDES_CONFIG_KEY)
        if not isinstance(record, Mapping):
            return None
        from app.engine.evaluation.comparability import RunEvidence

        expected = record.get("expected_selection")
        constraints = record.get("objective_constraints")
        return cls(
            evidence_mode=str(record.get("evidence_mode") or EVIDENCE_MODE_SCORED),
            expected_selection=RunEvidence.from_dict(expected) if isinstance(expected, Mapping) else None,
            max_training_seconds=record.get("max_training_seconds"),
            objective_constraints=None if constraints is None else tuple(
                (str(item["metric"]), str(item["op"]), float(item["value"])) for item in constraints),
        )
