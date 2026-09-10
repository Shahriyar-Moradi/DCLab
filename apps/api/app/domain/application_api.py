"""Stable /v1 application-boundary resource shapes.

HTTP is one transport. MCP and CLI are not implemented here. Field names are
resources and status, not internal service function names.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.execution_requests import OPERATION_MODEL_BUILD
from app.domain.observability import MlRunEventRead


class PrincipalWorkspaceRead(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: str
    role: str | None = None


class PrincipalRead(BaseModel):
    id: UUID
    email: str
    role: str
    full_name: str
    workspace_id: UUID | None = None
    active_workspace_id: UUID | None = None
    workspaces: list[PrincipalWorkspaceRead] = Field(default_factory=list)
    request_id: str | None = None


class ExecutionRequestCreate(BaseModel):
    operation: str = OPERATION_MODEL_BUILD
    request_spec: dict[str, Any] = Field(default_factory=dict)
    project_id: UUID | None = None
    parent_request_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    external_request_id: str | None = Field(default=None, max_length=128)


class ExecutionTargetConfirmation(BaseModel):
    target_column: str = Field(min_length=1, max_length=256)


class ExecutionRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class VisualizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class EventPage(BaseModel):
    items: list[MlRunEventRead]
    next_cursor: str | None = None
