"""Technical platform-administration contracts for business and ML lineage."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class BusinessSummaryRead(BaseModel):
    id: UUID
    slug: str
    name: str
    legal_name: str | None
    industry: str | None
    created_at: datetime
    domain_count: int
    workflow_count: int
    run_count: int
    pipeline_count: int
    model_count: int
    membership_count: int


class BusinessDomainRead(BaseModel):
    id: UUID
    business_domain_id: UUID
    slug: str
    name: str
    description: str
    enabled: bool
    config: dict[str, Any]
    workflow_count: int
    run_count: int


class WorkflowRead(BaseModel):
    id: UUID
    workspace_id: UUID
    workspace_domain_id: UUID
    domain_slug: str
    domain_name: str
    name: str
    slug: str
    description: str
    business_objective: str
    status: str
    config: dict[str, Any]
    run_count: int
    model_count: int
    created_at: datetime
    updated_at: datetime


class WorkflowRunRead(BaseModel):
    id: UUID
    workspace_id: UUID
    workflow_id: UUID
    workflow_name: str
    workspace_domain_id: UUID
    domain_slug: str
    domain_name: str
    trigger_type: str
    source_type: str
    source_upload_id: UUID | None
    source_filename: str | None
    explicit_target: str | None
    resolved_target: str | None
    task_type: str | None
    status: str
    failure_reason: str | None
    pipeline_count: int
    model_version_count: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class PipelineRunRead(BaseModel):
    id: UUID
    workspace_id: UUID
    workflow_run_id: UUID
    pipeline_name: str
    pipeline_index: int
    pipeline_purpose: str
    status: str
    failure_reason: str | None
    task_type: str | None
    dataset_id: UUID
    dataset_name: str
    candidate_count: int
    event_count: int
    latest_sequence: int
    model_version_id: UUID | None
    model_asset_id: UUID | None
    model_name: str | None
    model_version: str | None
    started_at: datetime | None
    ended_at: datetime | None
    current_stage: str | None = None
    current_stage_status: str | None = None


class ModelVersionRead(BaseModel):
    id: UUID
    version: str
    workflow_run_id: UUID
    pipeline_run_id: UUID
    selected_candidate_id: UUID
    dataset_id: UUID
    content_digest: str
    metrics: dict[str, Any]
    created_at: datetime


class ModelAssetRead(BaseModel):
    id: UUID
    workspace_id: UUID
    workflow_id: UUID
    workflow_name: str
    name: str
    slug: str
    description: str
    status: str
    versions: list[ModelVersionRead]
    created_at: datetime
    updated_at: datetime


class MembershipRead(BaseModel):
    id: UUID
    user_id: UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime


class BusinessDetailRead(BusinessSummaryRead):
    profile_data: dict[str, Any]
    domains: list[BusinessDomainRead]
    workflows: list[WorkflowRead]
    models: list[ModelAssetRead]
    runs: list[WorkflowRunRead]
    memberships: list[MembershipRead]


class BusinessWorkspaceSummaryRead(BusinessSummaryRead):
    role: str
    can_write: bool
    capabilities: dict[str, bool]


class BusinessWorkspaceDetailRead(BusinessDetailRead):
    role: str
    can_write: bool
    capabilities: dict[str, bool]


class DomainDetailRead(BusinessDomainRead):
    workspace_id: UUID
    business_name: str
    workflows: list[WorkflowRead]
    runs: list[WorkflowRunRead]


class WorkflowDetailRead(WorkflowRead):
    business_name: str
    runs: list[WorkflowRunRead]
    models: list[ModelAssetRead]


class WorkflowRunDetailRead(WorkflowRunRead):
    business_name: str
    pipelines: list[PipelineRunRead]


class BusinessWorkflowRunDetailRead(WorkflowRunDetailRead):
    capabilities: dict[str, bool]
    can_write: bool


class ModelDetailRead(ModelAssetRead):
    business_name: str
    domain_slug: str
    domain_name: str


class BusinessModelDetailRead(ModelDetailRead):
    capabilities: dict[str, bool]
    can_write: bool


class PipelineMonitorRead(BaseModel):
    capabilities: dict[str, bool]
    hierarchy: dict[str, Any]
    summary: PipelineRunRead
    stages: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]]
    llm_invocations: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    preprocessing: dict[str, Any]
    predictions: dict[str, Any]
    deterministic_verification: dict[str, Any]
    openai_audits: list[dict[str, Any]]
    reports: dict[str, Any]
    sanitized_evidence: dict[str, Any]
    scientific_plan: dict[str, Any]
