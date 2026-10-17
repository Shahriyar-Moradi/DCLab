"""The typed v1 action space (ADR 0007 §1). Pure.

Every ``ExperimentChange`` kind appears exactly once: ``family_include`` (F+) is the one retraining
action; the other seven are refused with the review reason of §1c. Setting the operating point (OP)
is no ``ExperimentChange`` (iteration 0, no retrain). F+ tries the installed families the lineage's
portfolio lacks, in a code-owned order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.domain.experiment_changes import CHANGE_KINDS
from app.engine.search.generator import DUMMY_FAMILIES

ACTION_CHANGE_KINDS = frozenset({"family_include"})
# §1c: why each other kind is not a v1 action (each needs its own ADR amendment with tests).
REFUSED_CHANGE_KINDS: Mapping[str, str] = {
    "class_weighting": "never_fires_weighted_variants_already_exist",
    "family_exclude": "needs_a_non_inferiority_rule",
    "hyperparameter_override": "applies_to_untuned_candidates_only",
    "threshold_objective": "the_operating_point_is_iteration_zero",
    "metric_override": "the_goal_never_changes_the_primary_metric",
    "feature_transform_add": "feature_actions_are_later",
    "feature_transform_remove": "feature_actions_are_later",
}
# The order F+ tries missing families in: the default portfolio first (a run whose ``families``
# subset left one out), then the families no default run includes.
_CLASSIFICATION = ("logistic_regression", "random_forest", "xgboost", "lightgbm", "catboost", "extra_trees",
                   "gradient_boosting")
FAMILY_PREFERENCE: Mapping[str, tuple[str, ...]] = {
    "binary": _CLASSIFICATION,
    "multiclass": _CLASSIFICATION,
    "regression": ("linear_regression", "random_forest_regressor", "xgboost_regressor", "lightgbm_regressor",
                   "catboost_regressor", "extra_trees_regressor", "gradient_boosting_regressor", "ridge", "lasso",
                   "elasticnet"),
}


class ActionRefused(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def check_action(change: Mapping[str, Any]) -> None:
    """A loop try is exactly one ``family_include``; every other kind is refused (§1c)."""

    kind = str(change.get("kind") or "")
    if kind in REFUSED_CHANGE_KINDS:
        raise ActionRefused(f"refused_in_v1:{REFUSED_CHANGE_KINDS[kind]}")
    if kind not in ACTION_CHANGE_KINDS:
        raise ActionRefused("unknown_change_kind")
    if not isinstance(change.get("family"), str) or set(change) != {"kind", "family"}:
        raise ActionRefused("family_include_needs_one_family")


def candidate_families(task_type: str, portfolio: Iterable[str], installed: Iterable[str]) -> list[str]:
    """Installed families the portfolio lacks, in ``FAMILY_PREFERENCE`` order (never a dummy baseline)."""

    have, available = set(portfolio), set(installed)
    return [family for family in FAMILY_PREFERENCE.get(task_type, ())
            if family in available and family not in have and family not in DUMMY_FAMILIES]


def closure_ok() -> bool:
    """Every change kind is exactly one action row or one refused row."""

    return (set(ACTION_CHANGE_KINDS) | set(REFUSED_CHANGE_KINDS)) == set(CHANGE_KINDS) and not (
        set(ACTION_CHANGE_KINDS) & set(REFUSED_CHANGE_KINDS))
