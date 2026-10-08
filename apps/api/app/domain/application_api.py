"""Stable /v1 application-boundary resource shapes.

HTTP is one transport. MCP and CLI are not implemented here. Field names are
resources and status, not internal service function names.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.execution_requests import OPERATION_MODEL_BUILD
from app.domain.observability import MlRunEventRead
from app.domain.service_tokens import ServiceTokenPrincipalRead


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
    capability_matrix_version: str
    capabilities: dict[str, bool] = Field(default_factory=dict)
    request_id: str | None = None
    service_token: ServiceTokenPrincipalRead | None = Field(
        None, description="Set when the caller is a service token (P3.2-A)."
    )


class ExecutionRequestCreate(BaseModel):
    operation: str = OPERATION_MODEL_BUILD
    request_spec: dict[str, Any] = Field(default_factory=dict)
    project_id: UUID | None = None
    parent_request_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    external_request_id: str | None = Field(default=None, max_length=128)


class ExecutionTargetConfirmation(BaseModel):
    target_column: str = Field(min_length=1, max_length=256)


class ExecutionSplitConfirmation(BaseModel):
    """A person's answer to ``split_confirmation_required`` (P6.9-A, ADR 0008 §2): keep the
    rule's split; the run plan's split answer is refused for this execution."""

    answer: Literal["keep_rule_split"]


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


class DatasetIngestionRead(BaseModel):
    """The ingestion run that produced a dataset and its ADR 0005 publication state."""

    id: UUID
    status: str
    publication_state: str
    rows_read: int
    bytes_read: int
    completed_at: datetime | None


class DatasetColumnSummaryRead(BaseModel):
    """One column of an uploaded dataset: schema facts only (no values, no row-level data)."""

    name: str
    dtype: str
    missing_fraction: float


class DatasetUploadRead(BaseModel):
    """``POST /v1/datasets`` result: the published DatasetVersion plus its ingestion."""

    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    dataset_asset_id: UUID
    name: str
    version: str
    source_type: str
    content_digest: str | None
    schema_digest: str | None
    size_bytes: int | None
    row_count: int
    column_count: int
    purpose: str = "training"
    created_at: datetime
    ingestion: DatasetIngestionRead
    columns: list[DatasetColumnSummaryRead] = Field(
        default_factory=list,
        description="Name, physical type and missing fraction per column, in file order. Column names are user data.",
    )
