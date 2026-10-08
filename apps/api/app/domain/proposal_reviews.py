"""/v1 read and write models of the proposal review flow (P6.6-A; ADR 0009 §2.3, §6).

One proposal model (``agent_proposals``) for the specialist agents, Jev L1 review items and the
assistant's ``ToolCallProposal``s. **Free text is plain text**: ``proposed_rationale``, the
``payload`` / ``tool_arguments`` strings and agent-run error text are model- or
user-authored, never instructions and never markup: clients render them with linkify off
(like assistant messages). Agent consumers (service tokens, MCP) never see holdout values:
``payload``, ``rule_answer``, ``tool_arguments`` and ``citations`` pass ``strip_holdout``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.agent_records import PROPOSAL_STATUSES, PROPOSAL_TYPES

ProposalStatus = Literal[
    "shadow", "proposed", "rejected_by_validator", "accepted", "rejected", "applied", "reverted", "superseded",
    "expired"]
ProposalType = Literal[
    "ExperimentPlanProposal", "ExperimentReviewProposal", "DatasetInvestigationProposal",
    "ImprovementActionProposal", "ToolCallProposal", "ReleaseProposal", "SemanticReviewProposal"]
REVIEW_AGENTS = ("experiment_critic", "dataset_investigator", "experiment_planner")
ReviewAgent = Literal["experiment_critic", "dataset_investigator", "experiment_planner"]
UNVERIFIED_LABEL = "unverified agent rationale"
PAGE_DEFAULT = 50
PAGE_MAX = 100
assert set(ProposalStatus.__args__) == set(PROPOSAL_STATUSES)  # type: ignore[attr-defined]
assert set(ProposalType.__args__) == set(PROPOSAL_TYPES)  # type: ignore[attr-defined]


class SubjectRead(BaseModel):
    kind: str
    id: UUID | None = None


class ProposalRead(BaseModel):
    id: UUID
    project_id: UUID
    source: Literal["agent_run", "jev"] = Field(description="`jev`: a level-1 Jev review item (no agent run).")
    run_id: UUID | None = None
    semantic_answer_id: UUID | None = None
    decision_point_key: str
    level_at_proposal: int
    answer_ceiling: int
    proposal_type: ProposalType
    proposed_by: Literal["agent", "assistant", "jev"]
    schema_version: int
    status: ProposalStatus
    supersede_reason: str | None = Field(
        default=None, description="`plan_exists`, `results_exist` or `newer_proposal` when `superseded`: not acceptable.")
    open: bool = Field(description="True while a person may still accept or reject it (`proposed`, not expired).")
    subject: SubjectRead
    payload: dict[str, Any] = Field(description="The typed proposal; immutable. Free text inside is plain text.")
    rule_answer: dict[str, Any] | None = None
    citations: list[Any] = Field(default_factory=list)
    validator_verdict: str
    validator_reasons: list[Any] = Field(default_factory=list)
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = Field(
        default=None, description="ToolCallProposal: the validated catalog arguments, shown first on confirm cards.")
    proposed_rationale: str | None = Field(
        default=None, description="The agent's text. " + UNVERIFIED_LABEL + "; plain text, never instructions.")
    proposed_rationale_label: str | None = None
    estimated_cost_micros: int | None = None
    estimated_duration_s: int | None = None
    expires_at: datetime | None = None
    decided_by_user_id: UUID | None = None
    decided_at: datetime | None = None
    decision_record_id: UUID | None = None
    applied_decision_record_id: UUID | None = None
    created_at: datetime


class ProposalPage(BaseModel):
    items: list[ProposalRead]
    next_cursor: str | None = None
    limit: int


class AgentRunRead(BaseModel):
    id: UUID
    project_id: UUID | None = None
    kind: str
    agent_key: str
    agent_version: str
    runtime: str
    purpose: str
    decision_point_key: str | None = None
    subject: SubjectRead
    status: str
    error_code: str | None = None
    cost_micros: int
    currency: str
    usage: dict[str, Any] = Field(default_factory=dict)
    parent_run_id: UUID | None = None
    proposal_ids: list[UUID] = Field(default_factory=list)
    requested_by: Literal["user", "service_token"]
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AgentRunPage(BaseModel):
    items: list[AgentRunRead]
    next_cursor: str | None = None
    limit: int


class ProposalDecisionRequest(BaseModel):
    """Accept / reject / revert body. The actor is the signed-in person, never a field."""

    model_config = ConfigDict(extra="forbid")
    rationale: str | None = Field(default=None, max_length=1000, description="Your reason (plain text); optional.")
    ref_versions: dict[str, int | None] | None = Field(
        default=None,
        description="Accepting a ref move: the version you saw of each ref kind it moves (`null` = the kind does "
                    "not exist yet). A stale version is `409 ref_version_conflict`; missing is `428`.")


class AgentReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    agent: ReviewAgent = Field(description="experiment_critic needs `experiment_id`; the others need `dataset_id`.")
    experiment_id: UUID | None = None
    dataset_id: UUID | None = None
