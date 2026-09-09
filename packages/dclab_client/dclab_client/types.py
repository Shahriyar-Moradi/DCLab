"""JSON resource shapes for /v1. These are HTTP DTOs, not ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Principal(BaseModel):
    id: UUID
    email: str
    role: str
    full_name: str
    workspace_id: UUID | None = None


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
