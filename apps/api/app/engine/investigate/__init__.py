"""Plain-language trust checks of a finished run (P4.10-A).

Pure and typed: ``investigate(run_evidence_from_result(result), train_features=...,
test_row_hashes=...)`` returns one ``Finding`` per check (target leakage, train-vs-CV
overfit gap, duplicate rows, class imbalance, too-good-to-be-true score). The final
holdout is read by nothing here except the duplicate check, which receives the test
partition as row hashes of the model columns only.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.domain.findings import INVESTIGATION_VERSION
from app.engine.investigate.checks import (
    check_class_imbalance,
    check_duplicate_rows,
    check_implausible_score,
    check_overfit_gap,
    check_target_leakage,
    investigate,
    investigate_result,
    row_hashes,
    run_evidence_from_result,
)
from app.engine.investigate.types import Finding, RunEvidence


def investigation_payload(findings: Sequence[Finding]) -> dict[str, Any]:
    """``experiments.result["investigation"]``: every check, passes included."""

    return {"version": INVESTIGATION_VERSION, "checks": [item.to_dict() for item in findings]}


__all__ = [
    "Finding",
    "RunEvidence",
    "check_class_imbalance",
    "check_duplicate_rows",
    "check_implausible_score",
    "check_overfit_gap",
    "check_target_leakage",
    "investigate",
    "investigate_result",
    "investigation_payload",
    "row_hashes",
    "run_evidence_from_result",
]
