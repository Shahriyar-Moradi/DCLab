"""Wire models of the governance console (P6.11-A; ADR 0009 §3, §4, §5.5; ADR 0008 §3, §4).

Every free-text field here (rationales, reasons, incident evidence strings) is plain text
written by a person, a rule or a model: render it with linkify off, never as markup or as
instructions. Nothing here carries tenant evidence beyond the caller's own workspace, R3
report bodies, pseudonyms or operator identities: R3 evidence is a link (run id, digests,
pair) and a verdict summary only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.domain.ai_governance import POLICY_MAX_BYTES, SWITCH_KEY_PATTERN

Text4000 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Text2000 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
# Workspace switch keys: ``SWITCH_KEY_PATTERN`` without the platform-only ``global_ai``.
WORKSPACE_SWITCH_KEY_PATTERN = SWITCH_KEY_PATTERN.replace("|global_ai", "")
# "user:<uuid>" | "platform_staff" (operator identity is never shown to tenants) | "rule:<name>"
ActorRef = str


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- reads ----------------------------------------------------------------------------------------


class GovernanceViewer(_Wire):
    can_approve: bool = Field(description="Workspace owner/admin: may accept policy proposals and re-enable switches.")
    can_propose: bool = Field(description="ML-write member: may propose a policy change.")
    can_switch_off: bool = Field(description="May switch a workspace key off (approvers, and platform staff).")


class EffectivePolicyRead(_Wire):
    digest: str
    platform_version: int
    workspace_version: int | None = None
    document: dict[str, Any] = Field(description="The merged AiPolicyV1 (platform narrowed by the workspace head).")


class ModelRoleRead(_Wire):
    role: str
    default: str
    allowed: list[str]
    fallback: str


class DataClassesRead(_Wire):
    order: list[str]
    max_class: str
    sample_values_per_column: int
    user_text_to_jev: bool
    share_r3_aggregates: bool


class SwitchRead(_Wire):
    id: UUID
    switch_key: str
    state: Literal["on", "off"]
    held_by_incident: bool = Field(description="An open incident keeps this key off whatever its head says.")
    reason: str
    changed_by: ActorRef
    changed_at: datetime


class SwitchesRead(_Wire):
    platform_ai_blocking: str | None = Field(
        description="First platform-level reason AI is off for every workspace (setting / global_ai), else null.")
    workspace: list[SwitchRead]


class R3EvidenceRead(_Wire):
    """A link to a stored R3 run: ids, digests, pair and a verdict summary. Never the report body."""

    run_id: UUID
    content_digest: str
    run_digest: str
    live: bool
    digest_verified: bool = Field(description="The stored report matches its digests, run id, live flag and pair.")
    verdict_current: bool = Field(default=False, description="Verdicts are shown only when the run is live, verified, "
                                  "partition 'both', of the current (release, model) pair, recorded after the latest "
                                  "demotion, with no open platform incident on the point (so it also tells that none is open). A "
                                  "promotion re-verifies: ledger backing and the stored previous run's stability "
                                  "are checked only then.")
    pair_release: str
    model_id: str
    cases: int
    recorded_at: datetime
    current_platform_level: int | None = None
    promotion_allowed: dict[str, bool] = Field(default_factory=dict, description="As recorded in the run.")
    demotion_allowed: bool | None = None


class DecisionPointLevelRead(_Wire):
    key: str
    stage: str
    pattern: str
    ai_kind: str
    cap: int
    workspace_level: int
    platform_level: int
    effective_level: int = Field(description="min(workspace, platform, cap, release max) for the current (release, "
                                 "model) pair at the strictest answer ceiling (mixed points: L1); ADR 0008 §1b "
                                 "ceilings per answer kind are applied at decision time.")
    pair_current: bool = Field(
        default=True, description="The platform head's (release, model) pair AND the cited run's own pair are the "
        "current ones; false for a Jev point whose code-pinned release is not yet released.")
    prompt_release_id: UUID | None = None
    model_id: str | None = None
    open_incidents: dict[str, int] = Field(default_factory=dict)
    r3_evidence: R3EvidenceRead | None = None


class BudgetPeriodRead(_Wire):
    scope: str
    period: str
    period_start: str | None = None
    limit_micros: int = Field(description="min(counter limit, effective policy limit).")
    spent_micros: int
    reserved_micros: int
    calls: int
    hard_stop: bool
    alert_fraction: float
    currency: str


class SpendRead(_Wire):
    currency: str
    workspace: list[BudgetPeriodRead]
    per_run_limits_micros: dict[str, int]


class IncidentRead(_Wire):
    id: UUID
    kind: str
    subject_kind: str
    subject_key: str
    action: str
    status: str
    opened_at: datetime
    evidence: dict[str, Any] = Field(description="Ids, codes, counts, digests only (scalars, capped).")


class PolicyFieldChange(_Wire):
    path: str
    before: Any = None
    after: Any = None


class PolicyChangeRead(_Wire):
    id: UUID
    version: int
    base_version: int | None = None
    state: Literal["proposed", "accepted", "rejected"]
    change_kind: str
    open: bool = Field(description="A proposal nobody has decided yet.")
    rationale: str
    policy_digest: str
    proposed_by: ActorRef | None = None
    decided_by: ActorRef | None = None
    self_approved: bool
    supersedes_id: UUID | None = None
    created_at: datetime
    # Open proposals only, for owners/admins and the proposer in a human session (never for a service token):
    document: dict[str, Any] | None = Field(default=None, description="The proposed AiPolicyV1 document.")
    diff_vs_head: list[PolicyFieldChange] | None = Field(default=None, description="Changes against the workspace head.")
    diff_vs_effective: list[PolicyFieldChange] | None = Field(default=None, description="Changes against the effective policy.")
    consent_change: bool = Field(default=False, description="Flips data.share_r3_aggregates (the R3 sharing consent).")


class RecentChangeRead(_Wire):
    kind: Literal["policy", "level", "switch"]
    id: UUID
    at: datetime
    subject: str
    state: str
    detail: str
    rationale: str
    actor: ActorRef | None = None


class GovernanceRead(_Wire):
    workspace_id: UUID
    viewer: GovernanceViewer
    ai_enabled_setting: bool
    policy_unavailable: str | None = Field(default=None, description="Set when the policy cannot load: AI is refused.")
    policy: EffectivePolicyRead | None = None
    model_allowlist: list[ModelRoleRead] = Field(default_factory=list)
    data_classes: DataClassesRead | None = None
    switches: SwitchesRead
    levels: list[DecisionPointLevelRead]
    spend: SpendRead | None = None
    open_incidents: list[IncidentRead]
    policy_changes: list[PolicyChangeRead]
    recent_changes: list[RecentChangeRead]


# --- commands -------------------------------------------------------------------------------------


class PolicyProposalRequest(_Wire):
    policy: dict[str, Any] = Field(description="A full AiPolicyV1 document; it may only narrow the platform policy.")
    rationale: Text4000

    @field_validator("policy")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        import json

        if len(json.dumps(value, default=str)) > POLICY_MAX_BYTES:
            raise ValueError("policy document too large")
        return value


class SwitchChangeRequest(_Wire):
    switch_key: str = Field(pattern=WORKSPACE_SWITCH_KEY_PATTERN, max_length=64,
                            description="all_ai | agent:<key> | provider:<name> | purpose:<key> (never global_ai).")
    state: Literal["on", "off"]
    reason: Text2000


class ReplayToolRead(_Wire):
    tool: str
    argument_digest: str


class PolicyAcceptRequest(_Wire):
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$", description="The policy_digest of the proposal you reviewed.")
    acknowledge_consent_change: bool = Field(default=False, description="Required when the proposal changes R3 sharing.")


class ReplayRead(_Wire):
    run_id: UUID
    equal: bool = Field(description="The replay reproduced the recorded tool sequence, output and proposals.")
    mismatches: list[str]
    tool_sequence: list[ReplayToolRead]
    output_digest: str | None = None
    incident_id: UUID | None = Field(default=None, description="The replay_mismatch incident, when not equal.")
    same_failure: bool = Field(default=False, description="The recorded run failed with the same code: not a mismatch.")
    not_comparable: bool = Field(default=False, description="The record lacks its final digest; output not compared.")
