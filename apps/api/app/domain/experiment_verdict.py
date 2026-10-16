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
# A sealed improve try (ADR 0007 §6.1) never scored the final test set.
PLAN_CAVEAT_SEALED = (
    "Best of {n} tries on the same folds: the cross-validation number is a little optimistic; this try did not "
    "use the final test set."
)
# This try scored the final test set but the one compared with is a sealed try.
PLAN_CAVEAT_MIXED = "Best of {n} tries on the same folds: the cross-validation number is a little optimistic."
_CAVEATS = {"scored": PLAN_CAVEAT, "sealed": PLAN_CAVEAT_SEALED, "mixed": PLAN_CAVEAT_MIXED}
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
    # The goal verdict (P5.4-A1b): each model scored per fold at its own threshold for the goal.
    "no_goal_curve": "One of the two models has no stored out-of-fold curve, so the goal cannot be scored per "
                     "fold: not comparable on the goal.",
    "goal_not_per_fold": "A cost-based goal is not scored per fold: not comparable on the goal.",
    "no_folds": "The stored curves carry no per-fold counts: not comparable on the goal.",
    "fold_counts_differ": "The two curves were not built on the same folds: not comparable.",
    "fold_below_min_denominator": "On some fold fewer than 10 rows stand behind the goal's numbers at the chosen "
                                  "threshold, too few to score it: not comparable on the goal.",
    "too_few_positives": "Too few positive rows in the out-of-fold predictions to score the goal: not comparable.",
    "too_few_negatives": "Too few negative rows in the out-of-fold predictions to score the goal: not comparable.",
    "try_misses_rule": "This try's model cannot meet the rule on cross-validation while the other one can, so it "
                       "does not beat it, however small its shortfall.",
}
GOAL_SHORTFALL = "constraint_shortfall"  # the per-fold score when a model cannot meet the rule


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


class GoalConstraintRead(BaseModel):
    metric: str
    op: Literal[">=", "<="]
    value: float


class GoalFoldRead(BaseModel):
    fold: int = Field(description="Position of the fold (curve folds carry no fold number; the per-fold "
                                  "class counts are identical on both sides).")
    experiment: float = Field(description="This experiment's winner on the fold, at its own threshold.")
    against: float = Field(description="The other experiment's winner on the fold, at its own threshold.")
    difference: float = Field(description="experiment minus against.")


class GoalVerdictRead(BaseModel):
    """The §5 verdict on the goal (P5.4-A1b): the paired per-fold rule of the ranking verdict applied to
    the goal metric. ``plain``: the goal is the ranking metric (the same numbers as the ranking verdict).
    ``objective``: the goal a person set with an operating-point choice on the compared-against run BEFORE
    this run was created (pre-registered; a goal chosen after the try is never used), maximise ``metric``
    subject to ``constraints``, each run scored per fold at its own threshold solved on its out-of-fold
    curve; when either run cannot meet the constraints the runs are compared by the per-fold constraint
    shortfall (``scored_by: constraint_shortfall``, lower is better), and a try that misses the rule never
    beats one that meets it (reason ``try_misses_rule``). δ_min = max(0.001, 1/n_min) of the smallest
    per-fold denominator (basis ``fold_denominator``). Cross-validation only."""

    kind: Literal["plain", "objective"]
    source: Literal["ranking_metric", "operating_point_choice"]
    choice_decision_id: UUID | None = Field(default=None, description="The operating-point choice the goal is from.")
    metric: str = Field(description="The goal metric.")
    constraints: list[GoalConstraintRead] = Field(default_factory=list)
    scored_by: Literal["goal_metric", "constraint_shortfall"] = "goal_metric"
    verdict: VerdictValue
    reason: str | None = None
    message: str
    direction: Literal["higher_is_better", "lower_is_better"] | None = None
    folds: int | None = None
    mean_difference: float | None = None
    margin: float | None = None
    minimum_effect: float | None = None
    minimum_effect_basis: str | None = None
    standard_error: float | None = None
    t_quantile: float | None = None
    comparisons: int = 1
    experiment_threshold: float | None = Field(default=None, description="This run's threshold for the goal (CV).")
    against_threshold: float | None = Field(default=None, description="The other run's threshold for the goal (CV).")
    experiment_meets_rule: bool | None = Field(default=None, description="Its threshold meets every constraint (CV).")
    against_meets_rule: bool | None = None
    experiment_curve_digest: str | None = None
    against_curve_digest: str | None = None
    fold_scores: list[GoalFoldRead] = Field(default_factory=list)
    outcome_scope: Literal["cv"] = "cv"
    basis: Literal["locked_cv_fold_evaluations", "out_of_fold_operating_curves"] = "locked_cv_fold_evaluations"


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
    goal: GoalVerdictRead | None = Field(
        default=None, description="The same rule on the goal metric (null when the runs cannot be compared at all).")
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
    if metric == GOAL_SHORTFALL:
        return "the shortfall from the rule"
    return METRIC_LABELS.get(metric, metric.replace("_", " "))


def verdict_message(verdict: str, reason: str | None, *, metric: str | None, direction: str | None,
                    folds: int | None, mean_difference: float | None, margin: float | None,
                    new_candidates: int = 0, comparisons_on_plan: int = 0, caveat_kind: str = "scored") -> str:
    """The plain sentences, built from the numbers only (the metric by its plain label); the
    best-of-N caveat when more than two runs on the split plan picked a winner or are running
    (``caveat_kind``: ``scored`` | ``sealed`` (this try is sealed) | ``mixed`` (the other one is))."""

    caveat = f" {_CAVEATS[caveat_kind].format(n=comparisons_on_plan)}" if comparisons_on_plan > 2 else ""
    if verdict == "not_comparable" or reason == "try_misses_rule":
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


def _bound(constraint: GoalConstraintRead) -> str:
    word = "at least" if constraint.op == ">=" else "at most"
    return f"{metric_label(constraint.metric)} {word} {_fmt(constraint.value)}"


def goal_message(verdict: str, reason: str | None, *, kind: str, metric: str, constraints: list[GoalConstraintRead],
                 scored_by: str, direction: str | None, folds: int | None, mean_difference: float | None,
                 margin: float | None, new_candidates: int = 0, comparisons_on_plan: int = 0,
                 caveat_kind: str = "scored") -> str:
    """The goal verdict in plain words: the goal first, then the ranking verdict's sentences."""

    if kind == "plain":
        lead = f"Goal: the best {metric_label(metric)} (the ranking measure). "
    else:
        rule = " and ".join(_bound(item) for item in constraints)
        lead = (f"Goal: the best {metric_label(metric)}" + (f" with {rule}" if rule else "")
                + ", each model at its own threshold on cross-validation. ")
        if scored_by == GOAL_SHORTFALL:
            lead += ("One of the two models cannot meet the rule there, so they are compared by how far each "
                     "falls short of it. ")
    scored = GOAL_SHORTFALL if scored_by == GOAL_SHORTFALL else metric
    return lead + verdict_message(verdict, reason, metric=scored, direction=direction, folds=folds,
                                  mean_difference=mean_difference, margin=margin, new_candidates=new_candidates,
                                  comparisons_on_plan=comparisons_on_plan, caveat_kind=caveat_kind)
