"""Weekly scheduled scoring: vocabulary, DB CHECK literals and /v1 read models.

One schedule per project (``scoring_schedules``, mutable config a person saves). Each week
on its weekday and hour (UTC) the worker scores the newest file uploaded for scoring
(``purpose=scoring``) since the last run with the model in use, approved for scoring (its
current ``model_releases`` row), as the person who last saved the schedule. Every handled
week is one append-only ``scoring_schedule_runs`` row (``UNIQUE (schedule_id, planned_for)``):
``queued`` with its batch prediction, or why nothing was scored. It never retrains, never
fetches data and never scores a model that no person approved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.data_plane import sql_in_clause
from app.domain.decision_records import RATIONALE_MAX_CHARS

OUTCOME_QUEUED = "queued"
OUTCOME_NO_NEW_FILE = "skipped_no_new_file"
OUTCOME_NO_MODEL_IN_USE = "skipped_no_model_in_use"
OUTCOME_NO_APPROVAL = "skipped_no_approval"
OUTCOME_OWNER_NOT_ALLOWED = "skipped_owner_not_allowed"
OUTCOME_MISSED = "skipped_missed"
OUTCOME_FAILED_TO_QUEUE = "failed_to_queue"
RUN_OUTCOMES = (OUTCOME_QUEUED, OUTCOME_NO_NEW_FILE, OUTCOME_NO_MODEL_IN_USE, OUTCOME_NO_APPROVAL,
                OUTCOME_OWNER_NOT_ALLOWED, OUTCOME_MISSED, OUTCOME_FAILED_TO_QUEUE)
RunOutcome = Literal["queued", "skipped_no_new_file", "skipped_no_model_in_use", "skipped_no_approval",
                     "skipped_owner_not_allowed", "skipped_missed", "failed_to_queue"]

# Plain words for each outcome (the Studio list and agent readers show these, never internals).
OUTCOME_MESSAGES: dict[str, str] = {
    OUTCOME_QUEUED: "Scored the newest file uploaded for scoring; the result is this week's list.",
    OUTCOME_NO_NEW_FILE: "Nothing scored: no new file was uploaded for scoring since the last run.",
    OUTCOME_NO_MODEL_IN_USE: "Nothing scored: the project has no model in use.",
    OUTCOME_NO_APPROVAL: "Nothing scored: no person has approved the model in use for scoring.",
    OUTCOME_OWNER_NOT_ALLOWED: "Nothing scored: the person who saved the schedule can no longer score in this "
                               "workspace. Someone who can should save it again.",
    OUTCOME_MISSED: "Nothing scored: DCLab was not running at the planned time (more than 24 hours ago).",
    OUTCOME_FAILED_TO_QUEUE: "Nothing scored: the scoring could not be started.",
}

# Closed reason codes (``reason_code``): why a run was skipped or could not be queued.
REASON_RUN_LIMIT = "run_limit"
REASON_WORKER_WAS_DOWN = "worker_was_down"
REASON_OWNER_MISSING = "owner_missing"
REASON_MODEL_NOT_SCOREABLE = "model_not_scoreable"
REASON_DATASET_UNAVAILABLE = "dataset_unavailable"
REASON_RELEASE_CHANGED = "release_changed"
REASON_INTERNAL_ERROR = "internal_error"
REASON_CODES = (REASON_RUN_LIMIT, REASON_WORKER_WAS_DOWN, REASON_OWNER_MISSING, REASON_MODEL_NOT_SCOREABLE,
                REASON_DATASET_UNAVAILABLE, REASON_RELEASE_CHANGED, REASON_INTERNAL_ERROR)

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
RECENT_RUNS = 10
RUN_PAGE_MAX = 50
CATCH_UP_HOURS = 24
ABOUT = ("Every week on the chosen day and hour (UTC) DCLab scores the newest file you uploaded for scoring "
         "since the last run, with the model in use approved for scoring, as the person who last saved the "
         "schedule. It does not retrain, does not fetch data and scores only uploaded files; with no new file or "
         "no approved model it scores nothing and says so. The result is the week's list (download it from the "
         "prediction). Any file uploaded for scoring counts, also one an agent uploaded with a service token. "
         "DCLab's worker runs it between jobs: a worker busy or down for more than 24 hours skips that week.")
PLATFORM_PAUSED_MESSAGE = ("Paused by the platform: scheduled scoring is turned off on this server for now "
                           "(SCORING_SCHEDULE_ENABLED; the worker reads it when it starts).")
NO_WORKER_MESSAGE = ("Paused by the platform: this server runs jobs inside the API process "
                     "(ML_JOB_DISPATCHER is not postgres), so no worker runs schedules.")

# DB CHECK backstops (models.py; Alembic 0079 inlines the same literal SQL).
CK_SCORING_SCHEDULES_WEEKDAY = "weekday BETWEEN 0 AND 6"
CK_SCORING_SCHEDULES_HOUR = "hour_utc BETWEEN 0 AND 23"
CK_SCORING_SCHEDULE_RUNS_OUTCOME = sql_in_clause("outcome", RUN_OUTCOMES)
CK_SCORING_SCHEDULE_RUNS_REASON = "reason_code IS NULL OR " + sql_in_clause("reason_code", REASON_CODES)
CK_SCORING_SCHEDULE_RUNS_QUEUED = (
    "(outcome = 'queued') = (batch_prediction_id IS NOT NULL) AND (outcome <> 'queued' "
    "OR (model_release_id IS NOT NULL AND input_dataset_id IS NOT NULL))"
)
CK_SCORING_SCHEDULE_RUNS_RELEASE_VERSION = "model_release_id IS NULL OR model_version_id IS NOT NULL"


class ScoringScheduleWrite(BaseModel):
    """``PUT /v1/projects/{id}/scoring-schedule``: create or change the project's schedule."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6, description="Day of the week in UTC: 0 = Monday ... 6 = Sunday.")
    hour_utc: int = Field(ge=0, le=23, description="Hour of the day in UTC (0-23).")
    enabled: bool = Field(True, description="false turns weekly scoring off; the schedule and its runs stay.")
    rationale: str | None = Field(
        None, max_length=RATIONALE_MAX_CHARS,
        description="Why, in your words (the History entry's reason); a plain default when omitted.",
    )


class ScoringScheduleRunRead(BaseModel):
    """One handled week: queued (with its prediction) or why nothing was scored."""

    id: UUID
    planned_for: datetime = Field(description="The weekly slot (UTC) this run handled.")
    outcome: RunOutcome
    message: str = Field(description="The outcome in plain words.")
    reason_code: str | None = Field(default=None, description="A closed code with more detail, e.g. run_limit.")
    batch_prediction_id: UUID | None = Field(default=None, description="The week's list (queued runs only).")
    model_version_id: UUID | None = None
    model_release_id: UUID | None = Field(default=None, description="The approval it scored under.")
    input_dataset_id: UUID | None = Field(default=None, description="The file it scored.")
    created_at: datetime


class ScoringScheduleRead(BaseModel):
    id: UUID
    weekday: int = Field(description="0 = Monday ... 6 = Sunday (UTC).")
    weekday_name: str
    hour_utc: int
    timezone: Literal["UTC"] = "UTC"
    enabled: bool
    next_planned_for: datetime | None = Field(
        default=None, description="The next weekly slot (UTC); null while it is turned off."
    )
    last_planned_for: datetime | None = Field(default=None, description="The last slot a run handled.")
    created_at: datetime
    updated_at: datetime
    created_by_user_id: UUID | None = Field(default=None, description="Null for service-token readers.")
    updated_by_user_id: UUID | None = Field(
        default=None, description="The person scheduled runs act as (who last saved it); null for service tokens."
    )
    owner_can_score: bool = Field(description="That person can still score in this workspace.")
    warning: str | None = Field(default=None, description="A plain warning when runs would be skipped.")


class ScoringScheduleState(BaseModel):
    """``GET``/``PUT /v1/projects/{id}/scoring-schedule``: the schedule (or null) and recent runs."""

    project_id: UUID
    schedule: ScoringScheduleRead | None = None
    status: Literal["off", "on", "paused_by_platform"] = Field(
        description="off: no schedule or turned off; paused_by_platform: saved and on, but the server's "
        "kill switch (SCORING_SCHEDULE_ENABLED) holds every run, or no worker runs (ML_JOB_DISPATCHER is not "
        "postgres); `status_message` says which."
    )
    status_message: str | None = None
    about: str = Field(default=ABOUT, description="What scheduled scoring does and does not do.")
    recent_runs: list[ScoringScheduleRunRead] = Field(
        default_factory=list, description=f"The newest {RECENT_RUNS} handled weeks, newest first."
    )


class ScoringScheduleRunPage(BaseModel):
    project_id: UUID
    items: list[ScoringScheduleRunRead] = Field(default_factory=list, description="Newest first.")
    next_cursor: str | None = None
