"""JSON resource shapes for /v1. These are HTTP DTOs, not ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PrincipalWorkspace(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: str
    role: str | None = None


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


class Workspace(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: str
    created_at: datetime
    max_members: int | None = None
    max_ml_engineer_seats: int


class Project(BaseModel):
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


class ExecutionRequest(BaseModel):
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


class ModelBuild(BaseModel):
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
