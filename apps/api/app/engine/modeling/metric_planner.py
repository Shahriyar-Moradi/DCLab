"""Choose the primary model-selection metric from a train-only ProblemProfile."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.engine.modeling.problem_profile import ProblemProfile

METRIC_PLAN_VERSION = "dclab.metric_plan.v1"
MEANINGFUL_IMBALANCE_RATIO = 2.0
MEANINGFUL_MINORITY_FRACTION = 0.35

BINARY_SECONDARY = (
    "pr_auc",
    "roc_auc",
    "f1",
    "precision",
    "recall",
    "balanced_accuracy",
    "accuracy",
    "log_loss",
    "brier_score",
)
MULTICLASS_SECONDARY = (
    "balanced_accuracy",
    "accuracy",
    "weighted_f1",
    "macro_precision",
    "macro_recall",
    "log_loss",
    "roc_auc_ovr",
)
REGRESSION_SECONDARY = ("rmse", "r2", "mse")


@dataclass
class MetricPlan:
    primary_metric: str
    secondary_metrics: list[str] = field(default_factory=list)
    reason: str = ""
    version: str = METRIC_PLAN_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Any) -> MetricPlan:
        from app.engine.modeling.coerce import from_mapping

        plan = from_mapping(cls, payload)
        if plan is None:
            raise ValueError("MetricPlan evidence is missing.")
        return plan


def _meaningful_imbalance(profile: ProblemProfile) -> bool:
    if profile.imbalance_ratio is not None and profile.imbalance_ratio >= MEANINGFUL_IMBALANCE_RATIO:
        return True
    if (
        profile.minority_class_fraction is not None
        and profile.minority_class_fraction < MEANINGFUL_MINORITY_FRACTION
    ):
        return True
    return False


def plan_metrics(profile: ProblemProfile) -> MetricPlan:
    if profile.task_type == "regression":
        return MetricPlan(
            primary_metric="mae",
            secondary_metrics=list(REGRESSION_SECONDARY),
            reason="Regression model selection uses MAE as the primary score; RMSE, R2, and MSE are reported.",
        )
    if profile.task_type == "binary":
        if _meaningful_imbalance(profile):
            return MetricPlan(
                primary_metric="pr_auc",
                secondary_metrics=list(BINARY_SECONDARY),
                reason=(
                    "Binary labels are meaningfully imbalanced "
                    f"(imbalance_ratio={profile.imbalance_ratio}, "
                    f"minority_class_fraction={profile.minority_class_fraction}); "
                    "PR-AUC is the primary ranking metric."
                ),
            )
        return MetricPlan(
            primary_metric="pr_auc",
            secondary_metrics=list(BINARY_SECONDARY),
            reason=(
                "DCLab's safe binary convention is PR-AUC even when classes are balanced, "
                "because it remains a ranking metric and matches existing Labs winner selection."
            ),
        )
    if profile.task_type == "multiclass":
        return MetricPlan(
            primary_metric="macro_f1",
            secondary_metrics=list(MULTICLASS_SECONDARY),
            reason=(
                "Multiclass model selection uses macro-F1 so every class counts equally "
                "regardless of its frequency; balanced accuracy, log-loss and one-vs-rest "
                "ROC-AUC are reported."
            ),
        )
    raise ValueError(f"Metric planning does not support task_type={profile.task_type!r}.")
