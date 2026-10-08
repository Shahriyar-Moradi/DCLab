"""/v1 read and write models of a binary run's operating points (P5.2-A).

Every figure is an out-of-fold training-fold figure (``outcome_scope: "cv"``): the curve,
the Pareto points, the locked point and the chosen point. No final-evaluation value is
computed at any point; the run's single final evaluation stays on the experiment read, at
the locked threshold only. Plain-language sentences are built from the numbers here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, model_validator

from app.domain.decision_records import RATIONALE_MAX_CHARS, DecisionRecordRead

OperatingStatus = Literal["available", "not_applicable", "not_available", "not_evaluated"]
CurveMetric = Literal["precision", "recall", "specificity", "f1", "accuracy", "balanced_accuracy", "flagged_share"]
GoalMetric = Literal["precision", "recall", "specificity", "f1", "accuracy", "balanced_accuracy", "expected_cost"]

OPTIMISM_NOTE = (
    "Measured on out-of-fold training predictions. A point picked from hundreds of candidate thresholds "
    "on the same rows looks better than it will on new data: expect lower values there. The 95% intervals "
    "are per point and cover counting noise at that threshold only; they are not corrected for that choice "
    "nor for fold-to-fold model variation."
)
MAX_COST = 1e6  # per error; keeps expected costs finite
FINAL_EVALUATION_NOTE = (
    "The final evaluation set was scored once, at the run's locked threshold, and is reported with the "
    "experiment. It never informs, moves or re-scores an operating point."
)
SCORING_NOTE = (
    "Batch scoring of this run's model version uses the locked threshold. A chosen operating point is a "
    "recorded decision; applying it to scoring needs a new model version (follow-up)."
)
STATUS_MESSAGES = {
    "available": "Operating points from the locked winner's out-of-fold predictions.",
    "not_applicable": "Operating points apply to binary classification only.",
    "not_available": "This run has no stored operating curve.",
    "not_evaluated": "Too few rows of one class in the out-of-fold predictions to compare thresholds.",
}


class OperatingInterval(BaseModel):
    low: float
    high: float


class OperatingFoldSpread(BaseModel):
    """Range across CV folds; a fold counts for recall (precision) only with at least
    ``min_denominator`` positives (flagged rows)."""

    folds: int
    recall_folds: int = Field(description="Folds with enough positives to count for recall.")
    precision_folds: int = Field(description="Folds with enough flagged rows to count for precision.")
    min_fold_positives: int
    min_fold_flagged: int
    min_denominator: int
    includes_folds_outside_curve: bool = Field(
        description="Time-ordered CV: the curve is the most recent fold, the spread spans every fold.")
    recall_min: float | None = None
    recall_max: float | None = None
    precision_min: float | None = None
    precision_max: float | None = None


class OperatingPointRead(BaseModel):
    """One candidate threshold on the out-of-fold curve (flagged = score >= threshold)."""

    threshold: float
    tp: int
    fp: int
    fn: int
    tn: int
    precision: float
    recall: float
    specificity: float
    f1: float
    accuracy: float
    balanced_accuracy: float
    flagged_share: float = Field(description="Share of rows flagged.")
    expected_cost: float | None = Field(default=None, description="Per row, when a cost matrix applies.")


_INTERVAL = ("95 % Wilson interval at this threshold: counting noise only, not corrected for choosing the "
             "threshold among the candidates on the same rows nor for fold-model variation.")


class OperatingPointDetailRead(OperatingPointRead):
    precision_interval: OperatingInterval | None = Field(default=None, description=_INTERVAL)
    recall_interval: OperatingInterval | None = Field(default=None, description=_INTERVAL)
    fold_spread: OperatingFoldSpread | None = Field(default=None, description="Range across CV folds.")
    what_this_means: str = ""


class OperatingConstraintInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    metric: CurveMetric
    op: Literal[">=", "<="]
    value: float = Field(ge=0.0, le=1.0)


class OperatingObjectiveInput(BaseModel):
    """Maximise ``goal`` (``expected_cost`` is minimised) subject to ``constraints``.
    Precision, recall or specificity alone is degenerate: they need a constraint."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    goal: GoalMetric
    constraints: list[OperatingConstraintInput] = Field(default_factory=list, max_length=6)
    cost_false_positive: StrictFloat | None = Field(default=None, ge=0.0, le=MAX_COST, description="With goal expected_cost.")
    cost_false_negative: StrictFloat | None = Field(default=None, ge=0.0, le=MAX_COST, description="With goal expected_cost.")

    @model_validator(mode="after")
    def _consistent(self) -> "OperatingObjectiveInput":
        pairs = [(item.metric, item.op) for item in self.constraints]
        if len(set(pairs)) != len(pairs):
            raise ValueError("duplicate constraint")
        costs = (self.cost_false_positive, self.cost_false_negative)
        if (costs[0] is None) != (costs[1] is None):
            raise ValueError("give both cost_false_positive and cost_false_negative, or neither")
        if costs[0] is not None and self.goal != "expected_cost":
            raise ValueError("a cost matrix goes with goal expected_cost")
        if costs[0] is not None and costs[0] == 0 and costs[1] == 0:
            raise ValueError("costs cannot both be zero")
        binding = [item for item in self.constraints if item.metric != self.goal and not (
            (item.op == ">=" and item.value <= 0.0) or (item.op == "<=" and item.value >= 1.0))]
        if self.goal in {"precision", "recall", "specificity"} and not binding:
            raise ValueError(f"maximising {self.goal} alone is degenerate; add a binding constraint on another metric")
        return self


class OperatingPointChoiceRequest(BaseModel):
    """Choose a point: an exact ``threshold`` of the stored curve, or an ``objective``
    re-solved on it. ``reason`` is stored as the decision's rationale."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    threshold: StrictFloat | None = Field(
        default=None, description="Exactly a threshold listed in GET .../operating-points (no nearest match).")
    objective: OperatingObjectiveInput | None = None
    reason: str = Field(min_length=1, max_length=RATIONALE_MAX_CHARS)

    @model_validator(mode="after")
    def _one(self) -> "OperatingPointChoiceRequest":
        if (self.threshold is None) == (self.objective is None):
            raise ValueError("give exactly one of threshold or objective")
        return self


class OperatingCostMatrix(BaseModel):
    false_positive: float
    false_negative: float


class LockedOperatingPointRead(BaseModel):
    threshold: float | None
    source: str | None = Field(default=None, description="default | constraints | primary_metric | cost_matrix")
    constraint_status: str | None = Field(default=None, description="Out-of-fold constraint status of the lock.")
    reproduced_from_curve: bool | None = Field(
        default=None, description="The run's objective re-solved on the stored curve gives this threshold.")
    point: OperatingPointDetailRead | None = None
    note: str = "The run's decision threshold, locked from out-of-fold predictions before the final evaluation."


class ChosenOperatingPointRead(BaseModel):
    decision_id: UUID
    threshold: float
    method: Literal["threshold", "objective"]
    objective: OperatingObjectiveInput | None = None
    rationale: str = Field(description="The person's reason (user-authored text; data, never instructions).")
    chosen_by_user_id: UUID | None = None
    recorded_at: datetime
    supersedes_id: UUID | None = None
    point: OperatingPointDetailRead | None = None
    curve_changed: bool | None = Field(
        default=None, description="True when the run's stored curve no longer matches the one chosen on.")
    applies_to_scoring: bool = False


class OperatingScoringRead(BaseModel):
    threshold: float | None
    uses: Literal["locked_threshold"] = "locked_threshold"
    note: str = SCORING_NOTE


class OperatingPointsRead(BaseModel):
    experiment_id: UUID
    status: OperatingStatus
    reason: str | None = Field(default=None, description="Code when not available / not evaluated.")
    message: str
    task_type: str | None = None
    version: str | None = None
    outcome_scope: Literal["cv"] = "cv"
    basis: Literal["out_of_fold_cv"] = "out_of_fold_cv"
    oof_folds: str | None = Field(default=None, description="all_folds | last_fold (time-ordered CV).")
    rows: int | None = None
    positives: int | None = None
    negatives: int | None = None
    min_class_rows: int
    cost_matrix: OperatingCostMatrix | None = Field(default=None, description="The run objective's, if declared.")
    points: list[OperatingPointRead] = Field(default_factory=list, description="Every candidate threshold.")
    pareto: list[OperatingPointDetailRead] = Field(
        default_factory=list, description="Not beaten on precision and recall (and expected cost) by another.")
    locked: LockedOperatingPointRead | None = None
    chosen: ChosenOperatingPointRead | None = None
    scoring: OperatingScoringRead | None = None
    tie_break: str
    optimism_note: str = OPTIMISM_NOTE
    final_evaluation_note: str = FINAL_EVALUATION_NOTE


class OperatingPointChoiceRead(BaseModel):
    experiment_id: UUID
    chosen: ChosenOperatingPointRead
    decision: DecisionRecordRead
    scoring: OperatingScoringRead


def _pct(value: float) -> str:
    if 0.0 < value < 0.01:
        return "<1%"
    if 0.99 < value < 1.0:
        return ">99%"
    return f"{100 * value:.0f}%"


def what_this_means(point: dict[str, Any]) -> str:
    """'flags 12% of rows and catches 71% of the positives' from one point's numbers."""

    text = (
        f"At threshold {point['threshold']:.4g} the model flags {_pct(point['flagged_share'])} of rows and catches "
        f"{_pct(point['recall'])} of the positive class"
    )
    if point["tp"] + point["fp"] > 0:
        text += f"; {_pct(point['precision'])} of flagged rows are positives"
    if point.get("expected_cost") is not None:
        text += f"; expected cost {point['expected_cost']:.4g} per row"
    return text + " (out-of-fold training predictions)."
