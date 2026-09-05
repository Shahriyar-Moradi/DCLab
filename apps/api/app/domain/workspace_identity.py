"""Canonical workspace / project / problem-spec API shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# One compatibility Project per Workspace for historical rows that have no
# Project. Attachment does not mean those rows were the same case study.
LEGACY_IMPORT_PROJECT_SLUG = "legacy-import"
LEGACY_IMPORT_PROJECT_NAME = "Legacy import"
LEGACY_IMPORT_PROJECT_DESCRIPTION = (
    "Compatibility project for historical records that had no Project. "
    "Attaching multiple Workflows here does not mean they were the same case study."
)


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    slug: str | None = Field(default=None, max_length=64)


class WorkspaceMemberCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)
    full_name: str = Field(default="", max_length=256)
    role: str = Field(min_length=1, max_length=32)


class WorkspaceMembershipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    user_id: UUID
    role: str
    created_at: datetime


class WorkspaceRead(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: str
    created_at: datetime
    max_members: int


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    slug: str | None = Field(default=None, max_length=64)
    description: str = Field(default="", max_length=4000)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    slug: str
    description: str
    status: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ProblemSpecCreateRequest(BaseModel):
    task_type: str = Field(min_length=1, max_length=64)
    business_objective: str = Field(min_length=1, max_length=4000)
    target_column: str | None = Field(default=None, max_length=256)
    prediction_unit: str | None = Field(default=None, max_length=128)
    prediction_time_column: str | None = Field(default=None, max_length=256)
    prediction_horizon: str | None = Field(default=None, max_length=128)
    primary_metric: str | None = Field(default=None, max_length=128)
    constraints: dict[str, Any] = Field(default_factory=dict)
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="draft", max_length=32)


class ProblemSpecRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    project_id: UUID
    version: int
    task_type: str
    target_column: str | None
    prediction_unit: str | None
    prediction_time_column: str | None
    prediction_horizon: str | None
    primary_metric: str | None
    business_objective: str
    constraints: dict[str, Any]
    success_criteria: dict[str, Any]
    status: str
    content_digest: str
    created_by: UUID
    created_at: datetime
    locked_at: datetime | None
