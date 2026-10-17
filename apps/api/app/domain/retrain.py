"""Retrain on newest data (D5a, ADR 0012): vocabulary, DB CHECK literals and /v1 read models.

A project's retrain policy (``retrain_policies``: off | ask | auto, default off; set by a workspace
owner or administrator with a reason). ``retrain_requests`` is the append-only log of retrains: a
person's start, the rule's question ("Retrain on newest data?", ``ask``: no run) or the rule's
automatic start (``auto``). ``retrain_comparisons`` stores, once per started retrain, the
comparison of the model in use with the retrained model on the retrained model's final-test rows
(only when every one of them is later than the training rows of the model in use) and the
challenger proposal it opened. Nothing here switches a model: a person accepts or rejects the
proposal through the existing ref-move approval. No AI anywhere in the path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.decision_records import RATIONALE_MAX_CHARS

POLICY_OFF, POLICY_ASK, POLICY_AUTO = "off", "ask", "auto"
RETRAIN_POLICIES = (POLICY_OFF, POLICY_ASK, POLICY_AUTO)
RetrainPolicyValue = Literal["off", "ask", "auto"]
ORIGIN_PERSON, ORIGIN_ASK, ORIGIN_AUTO = "person", "ask", "auto"
RetrainOrigin = Literal["person", "ask", "auto"]
STATUS_COMPARED, STATUS_NOT_COMPARABLE = "compared", "not_comparable"

# Identical literals in alembic 0087_retrain_policy.
CK_RETRAIN_POLICY = "policy IN ('off', 'ask', 'auto')"
CK_RETRAIN_POLICY_VERSION = "version >= 1"
CK_RETRAIN_ORIGIN = "origin IN ('person', 'ask', 'auto')"
CK_RETRAIN_RUN = "(origin = 'ask') = (experiment_id IS NULL)"
CK_RETRAIN_WINDOW = ("(origin = 'person') = (window_start IS NULL) "
                     "AND (origin = 'person') = (monitoring_window_id IS NULL)")
CK_COMPARISON_STATUS = "status IN ('compared', 'not_comparable')"
CK_COMPARISON_REASON = "(status = 'compared') = (reason_code IS NULL)"
CK_COMPARISON_REASON_CODE = (
    "reason_code IS NULL OR reason_code IN ('final_test_overlaps_training', 'no_time_column', 'time_missing', "
    "'time_kinds_differ', 'data_unavailable', 'incumbent_cannot_score', 'target_coding_differs', "
    "'challenger_not_scored', 'not_enough_labels', 'comparison_failed', 'challenger_test_not_latest', "
    "'task_differs')"
)
CK_COMPARISON_RESULT = "jsonb_typeof(result) = 'object' AND octet_length(CAST(result AS TEXT)) <= 16384"

RULE_TRIGGERS_PER_WEEK = 1  # questions + automatic starts per project in any 7 days
QUOTA_BACKOFF_HOURS = 24  # an automatic start refused by a plan limit waits this long
TICK_MAX_PROJECTS = 100
TICK_MAX_COMPARISONS = 5
WORDING = "same kind of test, latest months"
REAL_OUTCOMES_LABEL = "not the same data"
COMPARED_MESSAGE = ("Both models scored on the retrained model's final-test rows, all later than the training "
                    "rows of the model in use: " + WORDING + ".")
# reason_code -> plain words (the Monitoring card and agent readers show these, never internals).
NOT_COMPARABLE_MESSAGES: dict[str, str] = {
    "final_test_overlaps_training": "not comparable: the new final test overlaps the training period of the "
                                    "model in use",
    "no_time_column": "not comparable: the files have no shared date column to show the new final test is later",
    "time_missing": "not comparable: some rows have no readable date, so the new final test cannot be shown "
                    "to be later",
    "time_kinds_differ": "not comparable: the date column is read differently in the two files",
    "data_unavailable": "not comparable: a training file or its split could not be read",
    "incumbent_cannot_score": "not comparable: the model in use cannot score the new final-test rows",
    "target_coding_differs": "not comparable: the two models read the outcome labels differently",
    "challenger_not_scored": "not comparable: the retrained model has no final-test result",
    "not_enough_labels": "not comparable: too few final-test rows of each class",
    "comparison_failed": "not comparable: the comparison could not be computed",
    "challenger_test_not_latest": "not comparable: the retrained model's final test is not its latest months",
    "task_differs": "not comparable: the two models predict different kinds of target",
}
COMPARISON_REASONS = tuple(NOT_COMPARABLE_MESSAGES)  # the CK_COMPARISON_REASON_CODE vocabulary
# D5b: ``below_floor`` when the newest week's alert was raised by a precision / recall floor, not by drift.
RetrainState = Literal["idle", "drift_found", "below_floor", "retraining", "ready_to_compare", "waiting_for_you",
                       "switched", "kept"]
NOTES = (
    "Automatic only starts the retrain; switching the model in use is always a person's decision.",
    "The comparison is the same kind of test, latest months, never the same test: each model has its own "
    "final test.",
    "Real outcomes are measured on scored rows of the model in use: not the same data.",
)


class RetrainPolicyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: RetrainPolicyValue
    rationale: str = Field(min_length=1, max_length=RATIONALE_MAX_CHARS, description="Your reason (History).")


class RetrainPolicyRead(BaseModel):
    project_id: UUID
    policy: RetrainPolicyValue = Field(description="`off` (default), `ask` (Inbox question) or `auto` (starts it).")
    version: int = Field(description="0 when never set; send `If-Match: \"<version>\"` to change it.")
    etag: str
    updated_at: datetime | None = None
    updated_by_user_id: UUID | None = Field(default=None, description="Never shown to service tokens.")
    owner_can_start: bool | None = Field(
        default=None, description="`auto`: the person who set it can still start runs (automatic starts act as them)."
    )
    auto_paused_until: datetime | None = Field(
        default=None, description="`auto`: a plan limit refused the last automatic start; none before this time."
    )


class RetrainStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID = Field(description="A labelled file uploaded into this project, newer than the training file "
                                         "of the model in use.")


class RetrainSideRead(BaseModel):
    metric: str | None = None
    metric_value: float | None = None
    metrics: dict[str, float | None] = Field(default_factory=dict)
    decision_threshold: float | None = Field(default=None, description="This model's own locked threshold.")
    n_rows: int | None = Field(default=None, description="Final-test rows the figures are measured on.")


class RetrainComparisonRead(BaseModel):
    status: Literal["compared", "not_comparable"]
    reason_code: str | None = None
    message: str
    wording: str = WORDING
    time_column: str | None = None
    incumbent_training_end: str | None = None
    final_test_start: str | None = None
    final_test_rows: int | None = None
    challenger: RetrainSideRead | None = Field(default=None, description="The retrained model's own final test.")
    incumbent: RetrainSideRead | None = Field(default=None, description="The model in use on the same rows.")
    proposal_refusal: str | None = Field(default=None, description="Why no switch proposal was opened.")
    computed_at: datetime


class RetrainRequestRead(BaseModel):
    id: UUID
    origin: RetrainOrigin
    created_at: datetime
    incumbent_model_version_id: UUID
    dataset_id: UUID
    experiment_id: UUID | None = None
    experiment_status: str | None = None
    window_start: datetime | None = None
    carried: list[str] = Field(description="Settings of the model in use the retrain uses (target, goal).")
    not_carried: list[str] = Field(description="Branch settings of the model in use a new root run re-decides.")


class RetrainRealOutcomesRead(BaseModel):
    label: Literal["not the same data"] = REAL_OUTCOMES_LABEL
    window_start: datetime
    precision: float | None = None
    recall: float | None = None
    n_labelled: int | None = None


class RetrainStatusRead(BaseModel):
    project_id: UUID
    policy: RetrainPolicyValue
    state: RetrainState
    model_in_use_id: UUID | None = None
    latest_window_start: datetime | None = None
    latest_window_status: str | None = None
    newer_dataset_id: UUID | None = Field(default=None, description="Newest labelled file a person uploaded after the "
                                                                    "training file of the model in use.")
    request: RetrainRequestRead | None = None
    comparison: RetrainComparisonRead | None = None
    comparison_withheld: bool = Field(default=False, description="Service tokens never get final-test figures.")
    real_outcomes: RetrainRealOutcomesRead | None = None
    proposal_id: UUID | None = None
    proposal_state: Literal["proposed", "accepted", "rejected"] | None = None
    can_start: bool = Field(
        default=False,
        description=(
            "The caller may retrain now on `newer_dataset_id` (the server's rule: a person with workspace ML "
            "execution, a model in use, a newer labelled file not yet retrained on, no retrain running). "
            "Always false for service tokens and agents. Plan limits may still refuse the start (429)."
        ),
    )
    notes: list[str] = Field(default_factory=lambda: list(NOTES))
