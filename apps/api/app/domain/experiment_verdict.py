"""/v1 read model of the comparison verdict and the split plan's test-set look count
(P5.5-B-read; ADR 0007 option B, §5 and §6.4).

The verdict is computed from the LOCKED per-fold cross-validation evaluations of the two runs'
winners (``outcome_scope: "cv"``); never from ``experiments.result`` and never from a final
evaluation. The look count is metadata: how many experiments on the split plan have (or may have)
scored its final test set, counted conservatively; no final-evaluation value is read for it.
Service tokens get the same body (counts carry no outcome; the field names avoid the holdout words
on purpose, so ``strip_holdout`` keeps them).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.findings import METRIC_LABELS

VerdictValue = Literal["beats_the_noise", "within_the_noise", "worse", "not_comparable"]

SIGN_CONVENTION = (
    "mean_difference and each fold's difference are this experiment minus the other one, in the metric's "
    "own units. For lower_is_better metrics (errors) a negative difference is an improvement; margin and "
    "minimum_effect are magnitudes in the same units."
)
MULTIPLICITY_NOTE = (
    "The margin is corrected for the new candidates this try added (comparisons = T = new_candidates, at least "
    "1): the try is judged at 5% on its own. Each further try on the same folds is judged at 5% again, so "
    "several tries can reach up to N x 5% together (about 1 - 0.95^N, 23% at N = 5): comparisons_on_plan "
    "counts the experiments on this split plan that picked a winner or are still running, the root and these "
    "two included."
)
# Option B: every try scored the final test set once (each spends one look), so no number is fresh.
PLAN_CAVEAT = (
    "Best of {n} tries on the same folds: the cross-validation number is a little optimistic, and each try "
    "also scored the final test set once, so no single number here is a fresh estimate."
)
LOOK_COUNTING = (
    "conservative: every experiment on this split plan that locked a winner, has a final-evaluation row, or "
    "recorded a final_test_started event (the evidence for a run that failed or was cancelled after its final "
    "test began) counts as one look; ordered by the time the winner was locked. Limits: split plans that share "
    "the same holdout rows are counted separately, and a run that died after a lost (best-effort) "
    "final_test_started write is not counted."
)

# Plain text for people and agents: "try" and "model", the metric's plain label, never a raw metric key.
REASON_MESSAGES = {
    "same_experiment": "A try cannot be compared with itself.",
    "no_parent": "This try has no earlier try to compare with; name one with ?against=.",
    "different_split_plan": "The two tries use different splits of the data, so their folds differ: not comparable.",
    "not_finished": "Both tries must have finished before they can be compared.",
    "evidence_not_locked": "Both tries must have locked their results before they can be compared.",
    "no_winner": "One of the tries has no locked best model to compare.",
    "different_metric": "The two tries rank models by different metrics: not comparable.",
    "different_folds": "The two best models were not scored on the same folds: not comparable.",
    "missing_locked_folds": "A locked per-fold score is missing for one of the two models: not comparable.",
    "too_few_folds": "Fewer than 5 cross-validation folds: too few to tell a real gain from noise.",
    "constant_difference": "Every fold moved by exactly the same amount, so the noise cannot be measured: "
                           "not comparable.",
    "time_ordered_folds": "The folds are ordered in time, and this check assumes exchangeable folds: "
                          "not comparable.",
    "suspicious_result": "The result looks too good to be true (a trust check failed: target leakage or an "
                         "implausible score); check the trust checks first.",
}


class VerdictFoldRead(BaseModel):
    fold_number: int
    experiment: float = Field(description="This experiment's winner on the fold (metric units).")
    against: float = Field(description="The other experiment's winner on the fold (metric units).")
    difference: float = Field(description="experiment minus against (metric units).")


class SplitPlanLooksRead(BaseModel):
    """How many times the split plan's final test set has (or may have) been looked at."""

    split_plan_id: UUID | None = None
    looks_on_plan: int = Field(description="Experiments on the split plan counted as a look.")
    experiment_look: int | None = Field(default=None, description="This experiment is look k on the plan.")
    against_look: int | None = Field(default=None, description="The other experiment is look k on the plan.")
    counting: str = LOOK_COUNTING


class ExperimentVerdictRead(BaseModel):
    experiment_id: UUID
    against_experiment_id: UUID | None = None
    against_is_parent: bool = False
    split_plan_id: UUID | None = None
    verdict: VerdictValue
    reason: str | None = Field(
        default=None,
        description="Why: no_change | inside_margin | below_minimum_effect (within_the_noise); a not_comparable "
                    "reason (different_split_plan, different_metric, different_folds, too_few_folds, "
                    "constant_difference, missing_locked_folds, not_finished, evidence_not_locked, no_winner, "
                    "no_parent, same_experiment, time_ordered_folds, suspicious_result).",
    )
    message: str = Field(
        description="A plain sentence, plus the new-candidate note and the split-plan caveat when they apply."
    )
    metric: str | None = Field(default=None, description="The shared ranking metric.")
    direction: Literal["higher_is_better", "lower_is_better"] | None = None
    folds: int | None = Field(default=None, description="K, the paired folds.")
    mean_difference: float | None = None
    margin: float | None = Field(default=None, description="t_{1-0.05/T, K-1} x se_NB (metric units).")
    minimum_effect: float | None = Field(
        default=None,
        description="δ_min (metric units): max(0.001, 1/n_min) for count-based metrics, 0.001 x |the other run's "
                    "mean| otherwise; n_min is the smallest class count for class-sensitive metrics when the locked "
                    "folds carry it (binary), else the smallest validation fold (multiclass: less conservative "
                    "than ADR 0007 §5).",
    )
    minimum_effect_basis: str | None = Field(
        default=None, description="smallest_class_rows | validation_rows | relative | floor."
    )
    standard_error: float | None = Field(
        default=None, description="Nadeau-Bengio corrected: sd_d x sqrt(1/K + 1/(K-1)) (metric units)."
    )
    t_quantile: float | None = None
    alpha: float = 0.05
    comparisons: int = Field(
        default=1, description="T: comparisons the margin is corrected for (= max(1, new_candidates))."
    )
    new_candidates: int = Field(
        default=0,
        description="k: trained non-baseline candidate keys of this experiment that the other one did not train "
                    "(the winner is the best of these on the same folds).",
    )
    comparisons_on_plan: int = Field(
        default=0,
        description="Experiments on the split plan that picked a winner or are still running (root, in-flight "
                    "runs and these two included).",
    )
    multiplicity_note: str = MULTIPLICITY_NOTE
    sign_convention: str = SIGN_CONVENTION
    fold_scores: list[VerdictFoldRead] = Field(default_factory=list)
    test_set_looks: SplitPlanLooksRead
    outcome_scope: Literal["cv"] = "cv"
    basis: Literal["locked_cv_fold_evaluations"] = "locked_cv_fold_evaluations"


def _fmt(value: float) -> str:
    return f"{value:.4g}"


def _tries(new_candidates: int) -> str:
    if new_candidates <= 0:
        return ""
    noun = "model" if new_candidates == 1 else "models"
    return f" This try added {new_candidates} new {noun}; the margin allows for picking the best of them."


def metric_label(metric: str) -> str:
    return METRIC_LABELS.get(metric, metric.replace("_", " "))


def verdict_message(verdict: str, reason: str | None, *, metric: str | None, direction: str | None,
                    folds: int | None, mean_difference: float | None, margin: float | None,
                    new_candidates: int = 0, comparisons_on_plan: int = 0) -> str:
    """The plain sentences, built from the numbers only (the metric by its plain label); the
    best-of-N caveat when more than two runs on the split plan picked a winner or are running."""

    caveat = f" {PLAN_CAVEAT.format(n=comparisons_on_plan)}" if comparisons_on_plan > 2 else ""
    if verdict == "not_comparable":
        return REASON_MESSAGES.get(reason or "", "The two tries cannot be compared.") + caveat
    if reason == "no_change":
        return f"Both tries score the same on every one of the {folds} folds: no change.{caveat}"
    assert metric and direction and folds and mean_difference is not None and margin is not None
    word = "higher" if mean_difference > 0 else "lower"
    better = (mean_difference > 0) == (direction == "higher_is_better")
    gap = (f"This try's model scores {metric_label(metric)} {word} by {_fmt(abs(mean_difference))} on average "
           f"across the same {folds} folds")
    scope = "for this one comparison (evidence, not proof)"
    if verdict == "beats_the_noise":
        text = f"{gap}, more than the noise margin of {_fmt(margin)}: it beats the noise {scope}."
    elif verdict == "worse":
        text = f"{gap}, beyond the noise margin of {_fmt(margin)}: it is worse {scope}."
    elif reason == "below_minimum_effect":
        text = f"{gap}, too small a change to matter: no real difference {scope}."
    else:
        lean = "better" if better else "worse"
        text = (f"{gap} ({lean}), inside the noise margin of {_fmt(margin)}: within the noise, no evidence that "
                f"either model is better {scope}.")
    return text + _tries(new_candidates) + caveat
