"""Plain-language trust checks of a finished run (P4.10-A, P5.1-A).

Pure and typed: ``investigate(run_evidence_from_result(result), ...)`` returns one
``Finding`` per check in ``CHECK_ORDER``: the five core checks (target leakage,
train-vs-CV overfit gap, duplicate rows, class imbalance, too-good-to-be-true score),
the CV-evidence checks (fold instability, calibration, subgroup gap, multicollinearity),
the train -> test feature checks (feature drift, temporal shift, missingness shift,
contamination) and the probe/branch checks (time travel, new feature). The final
holdout's labels, predictions and metrics are read by nothing here; the duplicate check
gets test row hashes and the train -> test checks test FEATURE columns only, and every
finding stores aggregates and column names only.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.domain.findings import INVESTIGATION_VERSION
from app.engine.investigate.checks import (
    CHECK_ORDER,
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
from app.engine.investigate.cv_checks import (
    check_calibration,
    check_fold_instability,
    check_multicollinearity,
    check_subgroup_gap,
)
from app.engine.investigate.probe_checks import check_new_feature, check_time_travel
from app.engine.investigate.split_checks import (
    check_contamination,
    check_feature_drift,
    check_missingness_shift,
    check_temporal_shift,
)
from app.engine.investigate.types import Finding, ParentEvidence, RunEvidence, SplitFeatures, TimeTravelProbe


def investigation_payload(findings: Sequence[Finding]) -> dict[str, Any]:
    """``experiments.result["investigation"]``: every check, passes included."""

    return {"version": INVESTIGATION_VERSION, "checks": [item.to_dict() for item in findings]}


__all__ = [
    "CHECK_ORDER",
    "Finding",
    "ParentEvidence",
    "RunEvidence",
    "SplitFeatures",
    "TimeTravelProbe",
    "check_calibration",
    "check_class_imbalance",
    "check_contamination",
    "check_duplicate_rows",
    "check_feature_drift",
    "check_fold_instability",
    "check_implausible_score",
    "check_missingness_shift",
    "check_multicollinearity",
    "check_new_feature",
    "check_overfit_gap",
    "check_subgroup_gap",
    "check_target_leakage",
    "check_temporal_shift",
    "check_time_travel",
    "investigate",
    "investigate_result",
    "investigation_payload",
    "row_hashes",
    "run_evidence_from_result",
]
