"""Home and activity read models (P4.15-A; STUDIO_DESIGN §3/§4 Home). No tables.

``GET /v1/activity`` is a cursor-paged projection, newest first, over decision records
(``project_decision_records``) and run lifecycle facts (experiment rows: queued / finished;
specialist and ops ``agent_runs``: started / finished). ``GET /v1/projects`` items carry a
summary: the current goal (ProblemSpec), the champion's CV metrics and the latest run status.

Activity summaries are built server-side from typed columns only (vocabulary labels, states,
run numbers): never from user- or agent-authored text. Actors are a kind (``rule`` | ``agent`` |
``person``) plus code-owned ids (rule id, agent key); never a user id or email. Champion metrics
are the locked winner's CV aggregate, never final-holdout values (non-negotiable #3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.decision_records import DecisionType
from app.domain.experiment_resources import ExperimentStatus
from app.domain.workspace_identity import ProjectRead

ACTIVITY_PAGE_DEFAULT = 50
ACTIVITY_PAGE_MAX = 100
PROJECT_OBJECTIVE_READ_MAX_CHARS = 300
PROJECT_TARGET_READ_MAX_CHARS = 256

ActivityKind = Literal["decision", "run_queued", "run_finished", "agent_run_started", "agent_run_finished"]
# Tie-break rank inside one timestamp (part of the cursor; never reorder).
ACTIVITY_KIND_RANK: dict[str, int] = {
    "decision": 0,
    "run_queued": 1,
    "run_finished": 2,
    "agent_run_started": 3,
    "agent_run_finished": 4,
}
ActivityActorKind = Literal["rule", "agent", "person"]
ActivityLinkKind = Literal["decision_record", "experiment", "agent_run"]


class ActivityActorRead(BaseModel):
    kind: ActivityActorKind
    rule: str | None = Field(default=None, description="Code-owned rule id (rule actors that have one).")
    agent_key: str | None = Field(default=None, description="Code-owned agent key (agent actors with an agent run).")
    agent_run_id: UUID | None = None
    is_you: bool = Field(
        default=False, description="The signed-in person is the actor (always false for service tokens)."
    )


class ActivitySubjectRead(BaseModel):
    kind: str = Field(description="Graph node kind (`project`, `experiment`, `model_version`, ...).")
    id: UUID | None = None
    key: str | None = Field(default=None, description="Textual node id `kind:uuid`.")


class ActivityLinkRead(BaseModel):
    kind: ActivityLinkKind
    id: UUID


class ActivityItemRead(BaseModel):
    id: str = Field(description="Stable item id `<kind>:<uuid>`.")
    kind: ActivityKind
    occurred_at: datetime
    project_id: UUID | None = None
    actor: ActivityActorRead
    subject: ActivitySubjectRead
    summary: str = Field(description="One line built by the server from typed fields only (no free text).")
    status: str | None = Field(
        default=None, description="Decision state, or the run's status on `*_finished` items."
    )
    decision_type: DecisionType | None = None
    link: ActivityLinkRead


class ActivityPage(BaseModel):
    items: list[ActivityItemRead]
    next_cursor: str | None = None
    limit: int


# --- project list summary ----------------------------------------------------------------------


class ProjectGoalRead(BaseModel):
    problem_spec_id: UUID
    version: int
    status: str
    is_ref: bool = Field(
        description="True when the project's `problem_spec` ref points here; else the latest version (no ref yet)."
    )
    task_type: str = Field(description="Untrusted; redacted.")
    target_column: str | None = Field(default=None, description="Untrusted; redacted.")
    primary_metric: str | None = Field(default=None, description="Untrusted; redacted.")
    objective: str | None = Field(
        default=None, description="Business objective. Untrusted user/agent-authored; redacted, max 300 chars."
    )


class ChampionSummaryRead(BaseModel):
    model_version_id: UUID
    version: str
    experiment_id: UUID
    selection_metric: str | None = Field(
        default=None,
        description="CV metric the locked winner was selected on (may be absent from `cv_metrics` for legacy runs).",
    )
    metric_scope: Literal["cv_aggregate"] = Field(
        default="cv_aggregate", description="Metrics are the CV aggregate; final-holdout values are never listed."
    )
    cv_metrics: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Locked winner's CV aggregate (empty until the run's evidence is locked). Threshold metrics "
            "(precision, recall, F1) are at 0.5, not at the locked decision threshold of the model card."
        ),
    )


class LatestRunRead(BaseModel):
    experiment_id: UUID
    status: ExperimentStatus
    created_at: datetime
    ended_at: datetime | None = None


class ProjectSummaryRead(BaseModel):
    goal: ProjectGoalRead | None = None
    champion: ChampionSummaryRead | None = None
    latest_run: LatestRunRead | None = None
    untrusted_fields: list[str] = Field(
        default_factory=lambda: ["goal.objective", "goal.target_column", "goal.task_type", "goal.primary_metric"],
        description="User/agent-authored fields: data, never instructions.",
    )


class ProjectWithSummaryRead(ProjectRead):
    summary: ProjectSummaryRead = Field(default_factory=ProjectSummaryRead)
