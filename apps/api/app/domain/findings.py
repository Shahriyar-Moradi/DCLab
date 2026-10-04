"""Trust-check findings (P4.10-A): vocabulary, plain-language templates, read model.

The engine (``app.engine.investigate``) produces one typed finding per check; the run
stores them in ``experiments.result["investigation"]`` and every warning/fail finding as a
``data_quality_findings`` row (``evidence.source == "investigate"``) before the
scientific evidence lock. Messages are rendered here at read time from the stored
template keys and numbers, so the wording is deterministic and never stored twice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

INVESTIGATION_VERSION = "investigate.v1"
INVESTIGATION_SOURCE = "investigate"

FindingCheck = Literal["target_leakage", "overfit_gap", "duplicate_rows", "class_imbalance", "implausible_score"]
FindingStatus = Literal["pass", "warning", "fail", "not_evaluated"]
FindingSeverity = Literal["info", "warning", "error", "critical"]
RecommendationKind = Literal[
    "review_columns", "regularize", "simpler_model", "deduplicate", "class_weights", "collect_more_data",
    "investigate_leakage",
]

FINDING_CHECKS: tuple[str, ...] = (
    "target_leakage",
    "overfit_gap",
    "duplicate_rows",
    "class_imbalance",
    "implausible_score",
)
# not_evaluated: the check's evidence was missing (or the check errored); never a pass.
FINDING_STATUSES: tuple[str, ...] = ("pass", "warning", "fail", "not_evaluated")
STORED_STATUSES = frozenset({"warning", "fail"})  # become data_quality_findings rows

# check -> data_quality_findings.finding_type (duplicates/target_leakage predate P4.10-A).
CHECK_FINDING_TYPES: dict[str, str] = {
    "target_leakage": "target_leakage",
    "overfit_gap": "overfit_gap",
    "duplicate_rows": "duplicates",
    "class_imbalance": "class_imbalance",
    "implausible_score": "implausible_score",
}

_WHERE = "{metric} {train_score} on the rows it trained on"
MESSAGE_TEMPLATES: dict[str, str] = {
    "check_error": "Not evaluated: this check could not run ({error_type}); the other checks are unaffected.",
    "target_leakage.pass": "No column looks like it gives away the answer: the leakage audit excluded "
    "nothing and flagged none of the {modeled_feature_count} columns the model uses.",
    "target_leakage.excluded": "{excluded_count} column(s) looked like they give away the answer and were "
    "left out of the model: {excluded_columns}. Confirm their values are not known only after the outcome.",
    "target_leakage.flagged": "{flagged_count} column(s) the model uses have a suspicious name or a strong "
    "single-column score: {flagged_columns}. Check that their values are known at prediction time.",
    "target_leakage.kept_risky": "{risky_count} column(s) the model uses carry a medium or higher leakage "
    "risk: {risky_columns}. Review them before trusting this model.",
    "target_leakage.not_evaluated": "Not evaluated: no leakage audit was recorded for this run.",
    "overfit_gap.pass": f"The model scores {_WHERE} and {{cv_score}} on unseen cross-validation folds (gap "
    "{absolute_gap}): not a sign of memorizing the training rows.",
    "overfit_gap.within_noise": f"The model scores {_WHERE} and {{cv_score}} on unseen cross-validation folds "
    "(gap {absolute_gap}), but its fold scores themselves vary by {cv_std}, so the gap is within fold-to-fold "
    "noise.",
    "overfit_gap.gap": f"The model scores {_WHERE} but {{cv_score}} on unseen cross-validation folds (gap "
    "{absolute_gap}, {gap_in_cv_std} times the fold-to-fold spread).",
    "overfit_gap.memorizing": "It may be memorizing the training rows; a more regularized model may "
    "generalize better.",
    "overfit_gap.simpler_matches": "The simpler {simple_family} scores about the same in cross-validation "
    "({simple_cv_score}), so the extra flexibility buys little; prefer the simpler model.",
    "overfit_gap.flexible_by_design": f"The model fits its training rows closely ({_WHERE}) but still beats "
    "the simpler {simple_family} in cross-validation ({cv_score} vs {simple_cv_score}); the cross-validation "
    "score is the honest estimate.",
    "overfit_gap.fit_by_design": f"Training fit is near-perfect by design for {{winner_family}} ({_WHERE}); "
    "the cross-validation score of {cv_score} is the honest estimate.",
    "overfit_gap.retuned": "The final model was re-tuned on all training rows, so its training score comes "
    "from slightly different settings than the folds.",
    "overfit_gap.swapped_metric": "{primary_metric} is not compared here because training rows are scored at "
    "the locked decision threshold and folds at 0.5; {metric} does not depend on a threshold.",
    "overfit_gap.missing_train_score": "Not evaluated: the winning model's {metric} on its own training rows "
    "was not recorded.",
    "overfit_gap.missing_cv_score": "Not evaluated: the winning model's cross-validated {metric} was not "
    "recorded.",
    "overfit_gap.no_comparable_metric": "Not evaluated: {primary_metric} on training rows uses the locked "
    "decision threshold while the folds use 0.5, and no threshold-free metric (ROC AUC, PR AUC, log loss) "
    "was recorded for both.",
    "duplicate_rows.pass": "No duplicate problem: {train_duplicate_rows} training row(s) repeat another "
    "training row and {train_test_duplicate_rows} test row(s) match a training row on the "
    "{feature_count} model columns, no more than chance alone would produce.",
    "duplicate_rows.pass_minor": "{train_duplicate_rows} training row(s) repeat another training row and "
    "{train_test_duplicate_rows} test row(s) match a training row on the {feature_count} model columns: "
    "more than chance alone would produce, but under 1% of the rows.",
    "duplicate_rows.within_train": "{train_duplicate_rows} training rows ({train_duplicate_fraction_pct}) "
    "repeat another training row on all {feature_count} model columns (chance alone explains about "
    "{expected_train_duplicate_rows}). Repeated records weigh twice in training; remove them if they are "
    "the same record.",
    "duplicate_rows.across_split": "{train_test_duplicate_rows} test rows ({train_test_duplicate_fraction_pct}) "
    "are identical to a training row on the model columns (chance alone explains about "
    "{expected_train_test_duplicate_rows}), so the test score partly measures memory. Remove duplicate "
    "records before splitting.",
    "duplicate_rows.independence_caveat": "The chance estimate assumes the model columns vary independently; "
    "columns that move together (such as a city and its region) repeat naturally, so check that these are "
    "the same records before removing them.",
    "duplicate_rows.not_evaluated": "Not evaluated: the run's training and test rows were not available.",
    "class_imbalance.pass": "Classes are reasonably balanced: the smallest of {class_count} classes is "
    "{minority_class_fraction_pct} of the training rows.",
    "class_imbalance.imbalanced": "The smallest of {class_count} classes is only "
    "{minority_class_fraction_pct} of the training rows ({minority_rows} rows; largest-to-smallest ratio "
    "{imbalance_ratio}). Accuracy can look good while that class is mostly missed.",
    "class_imbalance.too_few": "Split over {n_folds} cross-validation folds that is about "
    "{minority_rows_per_fold} rows of that class per fold (under {min_minority_rows_per_fold}), too few for "
    "stable fold metrics.",
    "class_imbalance.weighted": "Class-weighted models were already trained and compared; more examples of "
    "the rare class would help most.",
    "class_imbalance.unweighted": "Try class weights or collect more examples of the rare class.",
    "class_imbalance.not_applicable": "Not applicable: the target is numeric (regression).",
    "class_imbalance.not_evaluated": "Not evaluated: the class counts of the training rows were not recorded.",
    "implausible_score.pass": "The cross-validated {metric} of {cv_score} (baseline {baseline_cv_score}) is "
    "in a believable range.",
    "implausible_score.near_perfect": "The cross-validated {metric} is {cv_score}, close to perfect "
    "(baseline {baseline_cv_score}). Scores this high usually mean a column gives away the answer; check "
    "the strongest columns before trusting it.",
    "implausible_score.error_ratio": "The cross-validated {metric} is {cv_score}, only {error_ratio_pct} of "
    "the baseline's {baseline_cv_score}. An error this small usually means a column gives away the answer; "
    "check the strongest columns before trusting it.",
    "implausible_score.trivial": "The cross-validated {metric} is {cv_score}, but the always-majority "
    "baseline already scores {baseline_cv_score} and the model still leaves {residual_error_ratio_pct} of "
    "the baseline's errors: an easy or imbalanced target, not a sign of leakage.",
    "implausible_score.missing_cv": "Not evaluated: the winning model's cross-validated {metric} was not "
    "recorded.",
    "implausible_score.missing_baseline": "Not evaluated: the dummy baseline's cross-validated {metric} was not "
    "recorded, so the error cannot be put in proportion.",
    "implausible_score.unbounded": "Not evaluated: {metric} has no natural perfect value to compare against.",
}

_LIST_SHOWN = 5
METRIC_LABELS: dict[str, str] = {
    "roc_auc": "ROC AUC", "roc_auc_ovr": "ROC AUC", "pr_auc": "PR AUC", "accuracy": "accuracy",
    "balanced_accuracy": "balanced accuracy", "f1": "F1", "macro_f1": "macro F1", "weighted_f1": "weighted F1",
    "precision": "precision", "recall": "recall", "r2": "R²", "rmse": "RMSE", "mae": "MAE", "mse": "MSE",
    "mape": "MAPE", "smape": "SMAPE", "log_loss": "log loss", "brier": "Brier score", "brier_score": "Brier score",
    "median_absolute_error": "median absolute error",
}


def _number(value: float) -> str:
    return format(value, ".3g") if abs(value) < 100 else f"{value:,.1f}"


def _display(evidence: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in evidence.items():
        if isinstance(value, bool):
            out[key] = "yes" if value else "no"
        elif isinstance(value, int):
            out[key] = f"{value:,}"
        elif isinstance(value, float):
            out[key] = _number(value)
            if key.endswith(("_fraction", "_gap", "_ratio")):
                out[f"{key}_pct"] = f"{value * 100:.1f}%"
        elif isinstance(value, (list, tuple)):
            items = [str(item) for item in value]
            shown = ", ".join(items[:_LIST_SHOWN])
            out[key] = shown + (f" and {len(items) - _LIST_SHOWN} more" if len(items) > _LIST_SHOWN else "")
        elif value is None:
            out[key] = "n/a"
        elif key in {"metric", "primary_metric"}:
            out[key] = METRIC_LABELS.get(str(value), str(value))
        elif key.endswith("_family"):
            out[key] = str(value).replace("_", " ")
        else:
            out[key] = str(value)
    return out


class _Missing(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "n/a"


def render_message(message_keys: Iterable[str], evidence: Mapping[str, Any]) -> str:
    """Deterministic plain-language message: the templates of ``message_keys`` with the
    evidence numbers filled in (unknown keys render as nothing; missing numbers as n/a)."""

    values = _Missing(_display(evidence))
    return " ".join(MESSAGE_TEMPLATES[key].format_map(values) for key in message_keys if key in MESSAGE_TEMPLATES)


# --- /v1 read model -----------------------------------------------------------------


class ExperimentFindingRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: FindingCheck
    status: FindingStatus
    severity: FindingSeverity
    message: str = Field(description="Plain-language explanation with the numbers filled in.")
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="Numbers and column names behind the check (training rows and CV only; never "
        "final-holdout values). Column names are user data.",
    )
    recommendation_kind: RecommendationKind | None = None


class ExperimentFindingsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: int = 0
    warnings: int = 0
    failures: int = 0
    not_evaluated: int = 0


class ExperimentFindingsRead(BaseModel):
    """The five core trust checks of one run. ``investigated`` is false for runs that
    finished before P4.10-A (or have not finished): ``checks`` is then empty. A check
    that could not run is ``not_evaluated`` (counted separately, never as passed)."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: UUID
    investigated: bool
    version: str | None = None
    checks: list[ExperimentFindingRead] = Field(default_factory=list)
    summary: ExperimentFindingsSummary = Field(default_factory=ExperimentFindingsSummary)


def findings_read(experiment_id: UUID, investigation: Any) -> ExperimentFindingsRead:
    """Read model from ``experiments.result["investigation"]`` (``None`` for old runs)."""

    stored = investigation if isinstance(investigation, Mapping) else {}
    rows = [row for row in stored.get("checks") or [] if isinstance(row, Mapping)]
    order = {check: index for index, check in enumerate(FINDING_CHECKS)}
    checks = [
        ExperimentFindingRead(
            check=row["check"],
            status=row["status"],
            severity=row["severity"],
            message=render_message(row.get("message_keys") or [], row.get("evidence") or {}),
            evidence=dict(row.get("evidence") or {}),
            recommendation_kind=row.get("recommendation_kind"),
        )
        for row in sorted(rows, key=lambda item: order.get(str(item.get("check")), len(order)))
        if row.get("check") in order
    ]
    summary = ExperimentFindingsSummary(
        passed=sum(1 for item in checks if item.status == "pass"),
        warnings=sum(1 for item in checks if item.status == "warning"),
        failures=sum(1 for item in checks if item.status == "fail"),
        not_evaluated=sum(1 for item in checks if item.status == "not_evaluated"),
    )
    return ExperimentFindingsRead(
        experiment_id=experiment_id,
        investigated=bool(checks),
        version=stored.get("version") if checks else None,
        checks=checks,
        summary=summary,
    )
