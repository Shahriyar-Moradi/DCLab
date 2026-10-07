"""JSON resource shapes for /v1. These are HTTP DTOs, not ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


class _Versioned(BaseModel):
    """A mutable /v1 resource: carries the response ``ETag`` for ``If-Match``."""

    _etag: str | None = PrivateAttr(default=None)
    _replayed: bool = PrivateAttr(default=False)

    @property
    def etag(self) -> str | None:
        """Strong ETag of the representation this object was read from."""

        return self._etag

    @property
    def idempotent_replay(self) -> bool:
        """True when the server replayed an earlier request with the same Idempotency-Key."""

        return self._replayed


class ErrorDetail(BaseModel):
    """Body of the /v1 error envelope ``{"error": {...}}`` (P3.1-A)."""

    code: str
    message: str
    retryable: bool
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class PrincipalWorkspace(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: str
    role: str | None = None


class ServiceTokenPrincipal(BaseModel):
    """The service token a client authenticates with (``DCLabClient(token="dclab_st_...")``)."""

    id: UUID
    name: str
    workspace_id: UUID
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime


class Principal(BaseModel):
    id: UUID
    email: str
    role: str
    full_name: str
    workspace_id: UUID | None = None
    active_workspace_id: UUID | None = None
    workspaces: list[PrincipalWorkspace] = Field(default_factory=list)
    capability_matrix_version: str = "unknown"
    capabilities: dict[str, bool] = Field(default_factory=dict)
    request_id: str | None = None
    service_token: ServiceTokenPrincipal | None = None


class Workspace(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: str
    created_at: datetime
    max_members: int | None = None
    max_ml_engineer_seats: int


class Project(_Versioned):
    id: UUID
    workspace_id: UUID
    name: str
    slug: str
    description: str
    status: str
    created_by: UUID | None
    provenance: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class DatasetPolicy(BaseModel):
    """ADR 0005 upload policy and the AI data class of one dataset (``GET /v1/datasets/{id}``)."""

    upload_policy: str | None = None
    publication_state: str | None = None
    policy_revision: int | None = None
    policy_complete: bool
    sensitivity_class: str | None = None
    llm_exposure_policy: str
    retention_class: str | None = None
    residency_class: str | None = None
    ai_data_class: str
    workspace_ai_max_class: str | None = None


class Dataset(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None = None
    dataset_asset_id: UUID
    name: str
    version: str
    content_digest: str | None = None
    row_count: int
    column_count: int
    purpose: str = "training"
    created_at: datetime


class DatasetVersion(Dataset):
    """``GET /v1/datasets/{id}``: the dataset plus its read-only policy."""

    policy: DatasetPolicy


class DatasetProfileSplitPlan(BaseModel):
    id: UUID
    version: int
    source: str
    target_column: str
    training_row_count: int


class DatasetProfileExperiment(BaseModel):
    id: UUID
    selection: str
    created_at: datetime


class DatasetProfileColumn(BaseModel):
    name: str
    ordinal_position: int
    physical_dtype: str
    rule_role: str | None = None
    role_used: str | None = None
    role_source: str | None = None
    role_reason: str | None = None
    missing_count: int | None = None
    missing_fraction: float | None = None
    unique_count: int | None = None
    unique_fraction: float | None = None
    transforms: list[str] = Field(default_factory=list)
    importance: float | None = None
    leakage_excluded: bool = False
    leakage_risk: str | None = None
    leakage_reason: str | None = None


class DatasetProfile(BaseModel):
    """``GET /v1/datasets/{id}/profile``: statistics over the current split plan's training rows only
    (``scope == "training_rows"``); ``scope == "upload"`` carries names and types without statistics."""

    dataset_id: UUID
    project_id: UUID | None = None
    scope: str
    statistics_status: str
    split_plan: DatasetProfileSplitPlan | None = None
    experiment: DatasetProfileExperiment | None = None
    importance_method: str | None = None
    columns: list[DatasetProfileColumn]


class ProblemSpec(_Versioned):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    version: int
    task_type: str
    target_column: str | None = None
    prediction_unit: str | None = None
    prediction_time_column: str | None = None
    prediction_horizon: str | None = None
    primary_metric: str | None = None
    business_objective: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    status: str
    content_digest: str
    created_by: UUID
    created_at: datetime
    locked_at: datetime | None = None


class DatasetIngestion(BaseModel):
    id: UUID
    status: str
    publication_state: str
    rows_read: int
    bytes_read: int
    completed_at: datetime | None = None


class DatasetUpload(_Versioned):
    """``POST /v1/datasets`` result: the published dataset version and its ingestion."""

    id: UUID
    workspace_id: UUID
    project_id: UUID | None = None
    dataset_asset_id: UUID
    name: str
    version: str
    source_type: str
    content_digest: str | None = None
    schema_digest: str | None = None
    size_bytes: int | None = None
    row_count: int
    column_count: int
    purpose: str = "training"
    created_at: datetime
    ingestion: DatasetIngestion


class ExecutionRequest(_Versioned):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None = None
    operation: str
    source_surface: str
    requested_by_user_id: UUID | None = None
    idempotency_key: str | None = None
    external_request_id: str | None = None
    parent_request_id: UUID | None = None
    status: str
    request_spec: dict[str, Any]
    result_summary: dict[str, Any] | None = None
    workflow_run_id: UUID | None = None
    pipeline_run_id: UUID | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: str | None = None
    failure_summary: str | None = None


class ModelBuildEvent(BaseModel):
    id: UUID
    workspace_id: UUID
    workflow_run_id: UUID
    experiment_id: UUID
    sequence: int
    stage: str
    event_type: str
    status: str
    timestamp: datetime
    duration_ms: float | None = None
    payload: dict[str, Any]
    created_at: datetime


class EventPage(BaseModel):
    items: list[ModelBuildEvent]
    next_cursor: str | None = None


class Visualization(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None = None
    pipeline_run_id: UUID
    pipeline_stage_run_id: UUID | None = None
    candidate_id: UUID | None = None
    model_evaluation_id: UUID | None = None
    visualization_type: str
    spec_version: str
    renderer_hint: str | None = None
    spec: dict[str, Any]
    data_artifact_id: UUID | None = None
    image_artifact_id: UUID | None = None
    content_digest: str
    created_at: datetime


class Artifact(BaseModel):
    """Artifact metadata.

    ``object_key`` is a legacy compatibility field and is blank in API
    responses. Use the artifact ID for authorized downloads.
    """

    id: UUID
    workspace_id: UUID
    project_id: UUID | None = None
    artifact_type: str
    provider: str
    object_key: str
    content_digest: str
    mime_type: str | None = None
    size_bytes: int
    created_at: datetime


class ModelBuildStage(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    sequence: int
    title: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    rows_in: int | None = None
    rows_out: int | None = None
    decision_summary: str | None = None
    reason: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    evidence_references: list[dict[str, Any]] = Field(default_factory=list)
    related_candidate_ids: list[UUID] = Field(default_factory=list)
    related_fold_ids: list[UUID] = Field(default_factory=list)
    code_generation_support_status: str
    generated_code: dict[str, Any] | None = None


class ModelBuild(_Versioned):
    model_config = ConfigDict(extra="allow")

    workspace_id: UUID
    pipeline_run_id: UUID
    pipeline_run_status: str
    scientific_evidence_locked_at: datetime | None = None
    compatibility_fallback_used: bool = False
    generator_version: str | None = None
    reproduction_spec_digest: str | None = None
    reproduction_notebook: dict[str, Any] | None = None
    reproduction_script: dict[str, Any] | None = None
    stages: list[ModelBuildStage]


class GraphNodeRef(BaseModel):
    """A project-graph node reference; ``key`` is the textual ``kind:uuid`` id."""

    kind: str
    id: UUID
    key: str


class StaleReason(BaseModel):
    ref_kind: str
    expected: GraphNodeRef
    actual: GraphNodeRef


class GraphNode(GraphNodeRef):
    label: str
    status: str | None = None
    created_at: datetime | None = None
    version: str | None = None
    digest: str | None = None
    stale: bool = False
    stale_reasons: list[StaleReason] = Field(default_factory=list)
    ref_kinds: list[str] = Field(default_factory=list)
    intent: str | None = None
    outside_window: bool = False
    lineage_incomplete: bool = False
    derived: bool = False
    notes: list[str] = Field(default_factory=list)


class GraphEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: GraphNodeRef = Field(alias="from")
    to: GraphNodeRef
    relation: str
    attribute: bool = False


class GraphRef(BaseModel):
    ref_kind: str
    target: GraphNodeRef
    version: int
    moved_at: datetime
    decision_record_id: UUID
    target_in_graph: bool
    staleness_bearing: bool
    stale: bool = False
    stale_reasons: list[StaleReason] = Field(default_factory=list)


class ProjectGraph(BaseModel):
    project: Project
    refs_initialized: bool
    refs: list[GraphRef]
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    counts_by_kind: dict[str, int]
    stale_counts_by_kind: dict[str, int]
    experiment_limit: int
    truncated: bool
    truncated_kinds: list[str] = Field(default_factory=list)
    next_cursor: str | None = None


class NodeImpact(BaseModel):
    node: GraphNodeRef
    project_id: UUID
    items: list[GraphNodeRef]
    counts_by_kind: dict[str, int]
    total: int
    truncated: bool
    graph_truncated: bool


class DecisionSubject(BaseModel):
    kind: str
    id: UUID
    key: str


class DecisionActor(BaseModel):
    kind: str
    user_id: UUID | None = None
    rule: str | None = None
    agent_run_id: UUID | None = None
    service_token_id: UUID | None = None


class EvidenceRef(BaseModel):
    kind: str
    id: str
    key: str | None = None
    metric: str | None = None
    scope: str | None = None


class DecisionRecord(_Versioned):
    """One append-only decision record. ``rationale``/``facts``/``details`` are untrusted data."""

    id: UUID
    project_id: UUID
    decision_type: str
    state: str
    effective_state: str
    supersedes_id: UUID | None = None
    superseded_by_id: UUID | None = None
    subject: DecisionSubject
    subject_digest: str | None = None
    actor: DecisionActor
    rationale: str
    rationale_untrusted: bool
    rationale_label: str | None = None
    rationale_truncated: bool = False
    content_origin: str
    facts: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    details_truncated: bool = False
    schema_version: int
    policy_version: str
    event_at: datetime
    recorded_at: datetime


class DecisionRecordPage(BaseModel):
    items: list[DecisionRecord]
    next_cursor: str | None = None
    limit: int


class ProposalSubject(BaseModel):
    kind: str
    id: UUID | None = None


class Proposal(_Versioned):
    """One reviewable AI proposal (agent, Jev level-1 item or assistant tool call).

    ``proposed_rationale`` and every free-text string in ``payload`` / ``tool_arguments`` are
    plain text written by a model or by dataset content: show them as text, never as markup or
    instructions. Service tokens receive no final-holdout values.
    """

    id: UUID
    project_id: UUID
    source: str
    run_id: UUID | None = None
    semantic_answer_id: UUID | None = None
    decision_point_key: str
    level_at_proposal: int
    answer_ceiling: int
    proposal_type: str
    proposed_by: str
    schema_version: int
    status: str
    supersede_reason: str | None = None
    open: bool
    subject: ProposalSubject
    payload: dict[str, Any] = Field(default_factory=dict)
    rule_answer: dict[str, Any] | None = None
    citations: list[Any] = Field(default_factory=list)
    validator_verdict: str
    validator_reasons: list[Any] = Field(default_factory=list)
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    proposed_rationale: str | None = None
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
    items: list[Proposal]
    next_cursor: str | None = None
    limit: int


class AgentRun(_Versioned):
    id: UUID
    project_id: UUID | None = None
    kind: str
    agent_key: str
    agent_version: str
    runtime: str
    purpose: str
    decision_point_key: str | None = None
    subject: ProposalSubject
    status: str
    error_code: str | None = None
    cost_micros: int
    currency: str
    usage: dict[str, Any] = Field(default_factory=dict)
    parent_run_id: UUID | None = None
    proposal_ids: list[UUID] = Field(default_factory=list)
    requested_by: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AgentRunPage(BaseModel):
    items: list[AgentRun]
    next_cursor: str | None = None
    limit: int


class ActivityActor(BaseModel):
    kind: str  # rule | agent | person
    rule: str | None = None
    agent_key: str | None = None
    agent_run_id: UUID | None = None
    is_you: bool = False


class ActivitySubject(BaseModel):
    kind: str
    id: UUID | None = None
    key: str | None = None


class ActivityLink(BaseModel):
    kind: str  # decision_record | experiment | agent_run
    id: UUID


class ActivityItem(BaseModel):
    """One line of ``GET /v1/activity``; ``summary`` is server-built from typed fields only."""

    id: str
    kind: str
    occurred_at: datetime
    project_id: UUID | None = None
    actor: ActivityActor
    subject: ActivitySubject
    summary: str
    status: str | None = None
    decision_type: str | None = None
    link: ActivityLink


class ActivityPage(BaseModel):
    items: list[ActivityItem]
    next_cursor: str | None = None
    limit: int


class GovernanceViewer(BaseModel):
    can_approve: bool
    can_propose: bool
    can_switch_off: bool


class EffectivePolicy(BaseModel):
    digest: str
    platform_version: int
    workspace_version: int | None = None
    document: dict[str, Any] = Field(default_factory=dict)


class ModelRole(BaseModel):
    role: str
    default: str
    allowed: list[str]
    fallback: str


class DataClasses(BaseModel):
    order: list[str]
    max_class: str
    sample_values_per_column: int
    user_text_to_jev: bool
    share_r3_aggregates: bool


class GovernanceSwitch(_Versioned):
    """A workspace kill switch head. ``reason`` is plain text: show it as text, never as markup."""

    id: UUID
    switch_key: str
    state: str
    held_by_incident: bool
    reason: str
    changed_by: str
    changed_at: datetime


class GovernanceSwitches(BaseModel):
    platform_ai_blocking: str | None = None
    workspace: list[GovernanceSwitch] = Field(default_factory=list)


class R3Evidence(BaseModel):
    """A link to a stored R3 run (ids, digests, pair, verdict summary): never the report body."""

    run_id: UUID
    content_digest: str
    run_digest: str
    live: bool
    digest_verified: bool
    verdict_current: bool = False
    pair_release: str
    model_id: str
    cases: int
    recorded_at: datetime
    current_platform_level: int | None = None
    promotion_allowed: dict[str, bool] = Field(default_factory=dict)
    demotion_allowed: bool | None = None


class DecisionPointLevel(BaseModel):
    key: str
    stage: str
    pattern: str
    ai_kind: str
    cap: int
    workspace_level: int
    platform_level: int
    effective_level: int
    pair_current: bool = True
    prompt_release_id: UUID | None = None
    model_id: str | None = None
    open_incidents: dict[str, int] = Field(default_factory=dict)
    r3_evidence: R3Evidence | None = None


class BudgetPeriod(BaseModel):
    scope: str
    period: str
    period_start: str | None = None
    limit_micros: int
    spent_micros: int
    reserved_micros: int
    calls: int
    hard_stop: bool
    alert_fraction: float
    currency: str


class Spend(BaseModel):
    currency: str
    workspace: list[BudgetPeriod] = Field(default_factory=list)
    per_run_limits_micros: dict[str, int] = Field(default_factory=dict)


class GovernanceIncident(BaseModel):
    id: UUID
    kind: str
    subject_kind: str
    subject_key: str
    action: str
    status: str
    opened_at: datetime
    evidence: dict[str, Any] = Field(default_factory=dict)


class PolicyFieldChange(BaseModel):
    path: str
    before: Any = None
    after: Any = None


class PolicyChange(_Versioned):
    """A policy proposal or decision row; ``rationale`` is plain text. Open proposals carry the proposed
    ``document`` and the diffs for owners/admins and the proposer (never for a service token)."""

    id: UUID
    version: int
    base_version: int | None = None
    state: str
    change_kind: str
    open: bool
    rationale: str
    policy_digest: str
    proposed_by: str | None = None
    decided_by: str | None = None
    self_approved: bool
    supersedes_id: UUID | None = None
    created_at: datetime
    document: dict[str, Any] | None = None
    diff_vs_head: list[PolicyFieldChange] | None = None
    diff_vs_effective: list[PolicyFieldChange] | None = None
    consent_change: bool = False


class RecentChange(BaseModel):
    kind: str
    id: UUID
    at: datetime
    subject: str
    state: str
    detail: str
    rationale: str
    actor: str | None = None


class Governance(BaseModel):
    """``GET /v1/governance``: every free-text field (rationales, reasons, incident evidence) is plain text."""

    workspace_id: UUID
    viewer: GovernanceViewer
    ai_enabled_setting: bool
    policy_unavailable: str | None = None
    policy: EffectivePolicy | None = None
    model_allowlist: list[ModelRole] = Field(default_factory=list)
    data_classes: DataClasses | None = None
    switches: GovernanceSwitches
    levels: list[DecisionPointLevel] = Field(default_factory=list)
    spend: Spend | None = None
    open_incidents: list[GovernanceIncident] = Field(default_factory=list)
    policy_changes: list[PolicyChange] = Field(default_factory=list)
    recent_changes: list[RecentChange] = Field(default_factory=list)


class ReplayTool(BaseModel):
    tool: str
    argument_digest: str


class Replay(BaseModel):
    run_id: UUID
    equal: bool
    mismatches: list[str] = Field(default_factory=list)
    tool_sequence: list[ReplayTool] = Field(default_factory=list)
    output_digest: str | None = None
    incident_id: UUID | None = None
    same_failure: bool = False
    not_comparable: bool = False


class ExperimentCodeInput(BaseModel):
    """A local file the generated code reads (by placeholder or environment variable)."""

    name: str
    placeholder: str
    env_var: str
    artifact_id: UUID | None = None
    content_digest: str | None = None
    description: str


class ExperimentCodeDocument(BaseModel):
    filename: str
    media_type: str
    content_digest: str
    source: str


class ExperimentCode(BaseModel):
    """Standalone reproduction script and notebook for one experiment."""

    experiment_id: UUID
    workspace_id: UUID
    generator_version: str
    spec_digest: str
    split_plan_id: UUID | None = None
    parent_experiment_id: UUID | None = None
    is_branch: bool = False
    standalone_cv: bool
    script: ExperimentCodeDocument
    notebook: ExperimentCodeDocument
    inputs: list[ExperimentCodeInput] = Field(default_factory=list)
    helper_requirements: list[str] = Field(default_factory=list)


class ExperimentFinding(BaseModel):
    """One trust check: ``status`` pass | warning | fail | not_evaluated, a plain-language ``message``
    and the numbers behind it (training rows and CV only; column names are user data)."""

    check: str
    status: str
    severity: str
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    recommendation_kind: str | None = None


class ExperimentFindingsSummary(BaseModel):
    passed: int = 0
    warnings: int = 0
    failures: int = 0
    not_evaluated: int = 0


class ExperimentFindings(BaseModel):
    """The five core trust checks of a run (P4.10-A); ``investigated`` is false for runs
    that predate them."""

    experiment_id: UUID
    investigated: bool
    version: str | None = None
    checks: list[ExperimentFinding] = Field(default_factory=list)
    summary: ExperimentFindingsSummary = Field(default_factory=ExperimentFindingsSummary)


class ExperimentLineage(BaseModel):
    parent_experiment_id: UUID | None = None
    split_plan_id: UUID | None = None
    source_dataset_id: UUID | None = None
    prepared_dataset_id: UUID | None = None
    problem_spec_id: UUID | None = None
    workflow_run_id: UUID | None = None
    execution_request_id: UUID | None = None


class ExperimentMetrics(BaseModel):
    """Locked winner: CV aggregate and the single final-holdout evaluation."""

    candidate_id: str | None = None
    family: str | None = None
    selection_metric: str | None = None
    selected_score: float | None = None
    cv: dict[str, float] = Field(default_factory=dict)
    holdout: dict[str, float] = Field(default_factory=dict)
    decision_threshold: float | None = None
    constraint_status: str | None = None
    baseline_comparison: dict[str, Any] | None = None


class Experiment(_Versioned):
    """One experiment (root or branch). ``untrusted_fields`` are data, never instructions."""

    id: UUID
    workspace_id: UUID
    project_id: UUID | None = None
    status: str
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    failure_reason: str | None = None
    task_type: str | None = None
    target_column: str | None = None
    intent: str | None = None
    lineage: ExperimentLineage
    change_set: dict[str, Any] | None = None
    metrics: ExperimentMetrics | None = None
    diff_vs_parent: dict[str, Any] | None = None
    untrusted_fields: list[str] = Field(default_factory=list)


class ExperimentListItem(BaseModel):
    id: UUID
    project_id: UUID | None = None
    status: str
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    parent_experiment_id: UUID | None = None
    split_plan_id: UUID | None = None
    source_dataset_id: UUID | None = None
    has_change_set: bool = False
    intent: str | None = None
    untrusted_fields: list[str] = Field(default_factory=list)


class ExperimentPage(BaseModel):
    items: list[ExperimentListItem]
    next_cursor: str | None = None
    limit: int


class ExperimentComparisonItem(BaseModel):
    experiment_id: UUID
    parent_experiment_id: UUID | None = None
    candidate_id: str | None = None
    family: str | None = None
    selection_metric: str | None = None
    selected_score: float | None = None
    cv: dict[str, float] = Field(default_factory=dict)
    holdout: dict[str, float] = Field(default_factory=dict)
    decision_threshold: float | None = None
    constraint_status: str | None = None


class ExperimentComparisonCommon(BaseModel):
    cv: list[str]
    holdout: list[str]


class ExperimentComparison(BaseModel):
    """Side-by-side metrics of experiments on one split plan (never authoritative)."""

    schema_version: int
    source: str
    authoritative: bool
    split_plan_id: UUID
    experiments: list[ExperimentComparisonItem]
    common: ExperimentComparisonCommon


class ProjectRef(BaseModel):
    """A project's current pointer of one kind; ``etag`` (``"<version>"``) is the If-Match to move it."""

    ref_kind: str
    target: GraphNodeRef
    version: int
    etag: str
    decision_record_id: UUID
    moved_at: datetime


class ProjectRefList(BaseModel):
    project_id: UUID
    refs_initialized: bool
    items: list[ProjectRef]
    missing_kinds: list[str] = Field(default_factory=list)


class RefMoveResult(_Versioned):
    """The accepted record of a ref move and every ref after it (``etag``: the moved ref)."""

    decision: DecisionRecord
    refs: list[ProjectRef]


class ModelVersionLineage(BaseModel):
    experiment_id: UUID
    candidate_id: UUID
    split_plan_id: UUID | None = None
    source_dataset_id: UUID | None = None
    prepared_dataset_id: UUID | None = None
    problem_spec_id: UUID | None = None
    feature_recipe_id: UUID | None = None


class ModelVersionMetrics(BaseModel):
    """Locked winner metrics: CV aggregate and the final holdout at the locked threshold."""

    candidate_id: str | None = None
    family: str | None = None
    selection_metric: str | None = None
    selected_score: float | None = None
    cv: dict[str, float] = Field(default_factory=dict)
    holdout: dict[str, float] = Field(default_factory=dict)
    decision_threshold: float | None = None
    constraint_status: str | None = None


class ModelVersionArtifact(BaseModel):
    """An artifact by id + digest (no storage locations)."""

    role: str
    id: UUID
    artifact_type: str
    content_digest: str
    size_bytes: int
    mime_type: str | None = None


class ModelVersion(_Versioned):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None = None
    version: str
    created_at: datetime
    content_digest: str
    family: str | None = None
    algorithm: str | None = None
    candidate_key: str | None = None
    lineage: ModelVersionLineage
    metrics: ModelVersionMetrics | None = None
    holdout_report_only: dict[str, float] | None = None
    is_champion: bool
    ref_kinds: list[str] = Field(default_factory=list)
    artifacts: list[ModelVersionArtifact] = Field(default_factory=list)


class ModelCardMetricInWords(BaseModel):
    text: str
    basis: str
    numbers: dict[str, float] = Field(default_factory=dict)
    caveat: str | None = None


class ModelCardBaseline(BaseModel):
    """The winner vs the dummy baseline on the selection metric (cross-validation)."""

    available: bool
    metric: str | None = None
    baseline_candidate_id: str | None = None
    baseline_score: float | None = None
    winner_score: float | None = None
    margin: float | None = None
    beats_baseline: bool | None = None
    clear_margin: bool | None = None
    text: str


class ModelCardDriver(BaseModel):
    rank: int
    column: str
    importance_mean: float | None = None
    importance_std: float | None = None
    importance_se: float | None = None
    distinguishable: bool | None = None


class ModelCardDrivers(BaseModel):
    """Top drivers: permutation importance on CV validation folds (``status`` computed |
    skipped | not_applicable | not_computed)."""

    status: str
    method: str | None = None
    scoring: str | None = None
    n_repeats: int | None = None
    folds: int | None = None
    reason: str | None = None
    columns_tested: int | None = None
    critical_value: float | None = None
    clear_drivers: list[str] = Field(default_factory=list)
    features: list[ModelCardDriver] = Field(default_factory=list)
    text: str


class ModelCardRisk(BaseModel):
    check: str
    status: str
    severity: str
    message: str


class ModelCardRisks(BaseModel):
    investigated: bool
    items: list[ModelCardRisk] = Field(default_factory=list)
    text: str


class ModelCardLlm(BaseModel):
    used: bool
    purposes: list[str] = Field(default_factory=list)
    counted: str = ""


class ModelCardFinalEvaluation(BaseModel):
    """The locked winner's single final evaluation (``status`` reported | withheld | missing);
    service-token callers always get ``withheld``."""

    status: str
    label: str = ""
    metric: str | None = None
    value: float | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    decision_threshold: float | None = None
    note: str | None = None


class ModelCard(BaseModel):
    """One-page card of a model version (P4.11-A) with a deterministic ``markdown`` rendering."""

    card_version: str
    model_version_id: UUID
    version: str
    experiment_id: UUID
    project_id: UUID | None = None
    candidate_key: str | None = None
    family: str | None = None
    algorithm: str | None = None
    created_at: datetime
    content_digest: str
    target: dict[str, Any]
    objective: dict[str, Any]
    metric_in_words: ModelCardMetricInWords
    cv: dict[str, Any]
    baseline: ModelCardBaseline
    drivers: ModelCardDrivers
    risks: ModelCardRisks
    data: dict[str, Any]
    split: dict[str, Any]
    llm: ModelCardLlm
    final_evaluation: ModelCardFinalEvaluation
    markdown: str = ""
    untrusted_fields: list[str] = Field(default_factory=list)


class BatchPredictionOutput(BaseModel):
    """The predictions file by id + digest; ``download_path`` is the authorized /v1 download."""

    artifact_id: UUID
    content_digest: str
    size_bytes: int
    mime_type: str | None = None
    download_path: str


class BatchPrediction(_Versioned):
    """One scoring run of a model version over a dataset (P4.9-A). ``contract_check``
    names required/missing/ignored columns; ``error_message`` is generic text."""

    id: UUID
    workspace_id: UUID
    project_id: UUID | None = None
    model_version_id: UUID
    model_release_id: UUID | None = None
    input_dataset_id: UUID
    execution_request_id: UUID | None = None
    status: str
    output_format: str
    rows_in: int | None = None
    rows_out: int | None = None
    decision_threshold: float | None = None
    contract_check: dict[str, Any] | None = None
    output: BatchPredictionOutput | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {"completed", "failed"}
