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
    created_at: datetime


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
    is_champion: bool
    ref_kinds: list[str] = Field(default_factory=list)
    artifacts: list[ModelVersionArtifact] = Field(default_factory=list)
