"""The improve loop (ADR 0007 option A, P5.4-A2): vocabulary, founder defaults (Revision 3, Q4), the
goal and budget shapes, and the CHECK literals of ``improve_runs`` / ``improve_iterations`` (identical
literals in Alembic 0087)."""

from __future__ import annotations

import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.data_plane import sql_in_clause

# --- run state ------------------------------------------------------------------------------------
RUN_QUEUED, RUN_RUNNING, RUN_WAITING_USER, RUN_FINALIZING = "queued", "running", "waiting_user", "finalizing"
RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED = "completed", "failed", "cancelled"
RUN_STATUSES = (RUN_QUEUED, RUN_RUNNING, RUN_WAITING_USER, RUN_FINALIZING, RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED)
LIVE_RUN_STATUSES = (RUN_QUEUED, RUN_RUNNING, RUN_WAITING_USER, RUN_FINALIZING)  # one per split plan
CAPPED_RUN_STATUSES = (RUN_QUEUED, RUN_RUNNING, RUN_FINALIZING)  # the workspace cap (waiting_user expires)
STEP_RUN_STATUSES = (RUN_QUEUED, RUN_RUNNING, RUN_WAITING_USER, RUN_FINALIZING)  # a step job acts on these
TERMINAL_RUN_STATUSES = (RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED)
OUTCOMES = ("improved", "kept_current", "goal_met", "blocked", "nothing_comparable", "failed", "cancelled")
# §4 precedence (index = order); the first that fires stops the loop.
STOP_REASONS = (
    "cancelled", "failed", "blocking_finding", "goal_met", "no_improvement", "unstable_evidence",
    "comparison_bound", "budget_iterations", "budget_wall", "budget_compute", "proposer_exhausted",
)
SEALED_SET_LABELS = ("clean", "carried_over", "reported_before", "reused")

# --- iterations -----------------------------------------------------------------------------------
KIND_OPERATING_POINT, KIND_FAMILY_INCLUDE = "operating_point", "family_include"
ITERATION_KINDS = (KIND_OPERATING_POINT, KIND_FAMILY_INCLUDE)
IT_PLANNED, IT_RUNNING, IT_RECORDED = "planned", "running", "recorded"
IT_ACCEPTED, IT_REJECTED, IT_NOT_COMPARABLE, IT_FAILED, IT_CANCELLED = (
    "accepted", "rejected", "not_comparable", "failed", "cancelled")
ITERATION_STATUSES = (IT_PLANNED, IT_RUNNING, IT_RECORDED, IT_ACCEPTED, IT_REJECTED, IT_NOT_COMPARABLE,
                      IT_FAILED, IT_CANCELLED)
LIVE_ITERATION_STATUSES = (IT_PLANNED, IT_RUNNING)
AGENT_STATES = ("off", "agree", "disagree", "abstain", "unavailable", "late")  # AI off -> "off" (ADR 0008 §8)

# --- founder defaults (Revision 3, Q4) and hard maxima (§3) ---------------------------------------
DEFAULT_MAX_ITERATIONS, HARD_MAX_ITERATIONS = 6, 20
DEFAULT_MAX_WALL_SECONDS, HARD_MAX_WALL_SECONDS = 7_200, 43_200
HARD_MAX_TRAINING_SECONDS_TOTAL = 28_800
DEFAULT_MAX_LLM_MICROS = 2_000_000
DEFAULT_PATIENCE = 3
CV_COMPARISON_BOUND_PER_SPLIT_PLAN, HARD_COMPARISON_BOUND = 30, 60
MAX_LIVE_IMPROVE_RUNS_PER_WORKSPACE = 2
WAITING_USER_EXPIRY_DAYS = 14
MAX_FINAL_ATTEMPTS = 3  # the first final run plus at most two retries (§6.2)
MIN_LOOP_FOLDS = 5
TOKEN_STARTS_PER_HOUR = 5  # enforced by the /v1 route (P5.4-A3)
GOAL_MAX_BYTES = 4096
QUOTA_BACKOFF_SECONDS = 60.0  # a 429 when enqueueing a probe (§3): retried within the wall budget
MAX_STEP_ERRORS = 3  # consecutive step failures before the loop stops ``failed``

# Plain goals (§1b): threshold-free, and equal to the run's primary metric.
PLAIN_GOAL_METRICS = {
    "binary": frozenset({"roc_auc", "pr_auc", "log_loss", "brier", "brier_score"}),
    "multiclass": frozenset({"log_loss", "accuracy", "macro_f1"}),
    "regression": frozenset({"rmse", "mae", "r2", "mse", "median_absolute_error", "mape"}),
}
# Constrained goals: maximise a curve metric (never flagged_share) under threshold-metric constraints.
# flagged_share is refused as a constraint too: the final run's threshold lock (ServiceOverrides)
# accepts threshold metrics only (D4c gate 2).
GOAL_CURVE_METRICS = ("precision", "recall", "specificity", "f1", "accuracy", "balanced_accuracy")
GOAL_NEEDS_CONSTRAINT = frozenset({"precision", "recall", "specificity"})
GOAL_MAX_CONSTRAINTS = 6


class GoalConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    op: Literal[">=", "<="]
    value: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class GoalObjective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maximize: str
    constraints: list[GoalConstraint] = Field(default_factory=list, max_length=GOAL_MAX_CONSTRAINTS)


class GoalPlain(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str


class GoalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    value: float = Field(allow_inf_nan=False)


class ImproveGoal(BaseModel):
    """Exactly one of ``plain`` / ``objective`` (§1b), plus an optional ``target`` for ``goal_met``."""

    model_config = ConfigDict(extra="forbid")

    plain: GoalPlain | None = None
    objective: GoalObjective | None = None
    target: GoalTarget | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> "ImproveGoal":
        if (self.plain is None) == (self.objective is None):
            raise ValueError("a goal is exactly one of plain or objective")
        return self

    @property
    def metric(self) -> str:
        return self.plain.metric if self.plain is not None else self.objective.maximize  # type: ignore[union-attr]

    @property
    def constrained(self) -> bool:
        return self.objective is not None


class ImproveBudget(BaseModel):
    """§3 budgets a caller may set (each clamped to its hard maximum; tokens may only lower them, A3)."""

    model_config = ConfigDict(extra="forbid")

    max_iterations: int = Field(DEFAULT_MAX_ITERATIONS, ge=1, le=HARD_MAX_ITERATIONS)
    max_wall_seconds: int = Field(DEFAULT_MAX_WALL_SECONDS, ge=60, le=HARD_MAX_WALL_SECONDS)
    max_training_seconds_total: float | None = Field(None, gt=0, le=HARD_MAX_TRAINING_SECONDS_TOTAL,
                                                     allow_inf_nan=False)
    max_llm_micros: int = Field(DEFAULT_MAX_LLM_MICROS, ge=0)
    patience: int = Field(DEFAULT_PATIENCE, ge=1, le=HARD_MAX_ITERATIONS)
    comparison_bound: int = Field(CV_COMPARISON_BOUND_PER_SPLIT_PLAN, ge=1, le=HARD_COMPARISON_BOUND)

    @model_validator(mode="after")
    def _patience(self) -> "ImproveBudget":
        self.patience = min(self.patience, self.max_iterations)  # §3: patience's hard max is max_iterations
        return self


def goal_size_ok(goal: ImproveGoal) -> bool:
    return len(json.dumps(goal.model_dump(mode="json"), separators=(",", ":")).encode("utf-8")) <= GOAL_MAX_BYTES


def sealed_set_label(looks_before: int, *, fresh_split: bool = False, carried_over: bool = False) -> str:
    """How fresh the plan's final test set is when the loop starts (§6.4): ``carried_over`` (a fresh
    split that carried the start's overrides), ``clean`` (no look yet), ``reported_before`` (one look:
    the start's own final-test number was already reported) or ``reused`` (two or more looks)."""

    if fresh_split and carried_over:
        return "carried_over"
    if looks_before <= 0:
        return "clean"
    return "reported_before" if looks_before == 1 else "reused"


def finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# --- CHECK literals (identical in Alembic 0087) ---------------------------------------------------
CK_IMPROVE_RUNS_STATUS = sql_in_clause("status", RUN_STATUSES)
CK_IMPROVE_RUNS_OUTCOME = f"outcome IS NULL OR {sql_in_clause('outcome', OUTCOMES)}"
CK_IMPROVE_RUNS_STOP_REASON = f"stop_reason IS NULL OR {sql_in_clause('stop_reason', STOP_REASONS)}"
CK_IMPROVE_RUNS_LABEL = sql_in_clause("sealed_set_label", SEALED_SET_LABELS)
CK_IMPROVE_RUNS_GOAL = (
    "jsonb_typeof(goal) = 'object' AND octet_length(goal::text) <= 4096 "
    "AND jsonb_typeof(budget) = 'object' AND jsonb_typeof(plan) = 'array'"
)
CK_IMPROVE_RUNS_COUNTS = (
    "planned_comparisons >= 1 AND validation_folds >= 5 AND final_attempts BETWEEN 0 AND 3 "
    "AND step_errors >= 0 AND training_seconds_spent >= 0 AND llm_micros_spent >= 0 AND looks_before >= 0"
)
# Terminal <=> an outcome and an end time; waiting_user always has its expiry.
CK_IMPROVE_RUNS_SHAPE = (
    "(status IN ('completed', 'failed', 'cancelled')) = (outcome IS NOT NULL AND finished_at IS NOT NULL) "
    "AND (status <> 'waiting_user' OR waiting_user_expires_at IS NOT NULL)"
)
# §7: a final run exists only after a person accepted (and the acceptance record exists).
CK_IMPROVE_RUNS_ACCEPTANCE = (
    "(final_experiment_id IS NULL OR (accepted_by_user_id IS NOT NULL AND acceptance_record_id IS NOT NULL "
    "AND accepted_iteration_seq IS NOT NULL)) AND (status <> 'finalizing' OR final_experiment_id IS NOT NULL)"
)
CK_IMPROVE_ITERATIONS_KIND = sql_in_clause("kind", ITERATION_KINDS)
CK_IMPROVE_ITERATIONS_STATUS = sql_in_clause("status", ITERATION_STATUSES)
CK_IMPROVE_ITERATIONS_AGENT = sql_in_clause("agent", AGENT_STATES)
CK_IMPROVE_ITERATIONS_SHAPE = (
    "seq >= 0 AND plan_index >= 0 AND planned_new_candidates >= 0 AND jsonb_typeof(action) = 'object' "
    "AND (comparison IS NULL OR jsonb_typeof(comparison) = 'object') "
    "AND (kind = 'operating_point') = (status = 'recorded') "
    "AND (status NOT IN ('running', 'accepted', 'rejected', 'not_comparable') OR experiment_id IS NOT NULL)"
)
