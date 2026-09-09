"""Runtime, code-snapshot, and developer artifact-access contracts.

Source bytes live in object storage via Artifact. PostgreSQL stores only
queryable reproducibility metadata.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.data_plane import sql_in_clause

CODE_LANGUAGES = ("python",)

CK_CODE_LANGUAGE = sql_in_clause("language", CODE_LANGUAGES)

ENGINE_ENTRYPOINT = "app.engine.experiments.runner:run_experiment"


class ArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    artifact_type: str
    provider: str
    object_key: str
    content_digest: str
    mime_type: str | None
    size_bytes: int
    created_at: datetime


class RuntimeEnvironmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    python_version: str
    os_name: str
    os_version: str
    architecture: str
    container_image: str | None
    container_digest: str | None
    hardware: dict[str, Any]
    environment_digest: str
    created_at: datetime


class CodeSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    pipeline_run_id: UUID
    pipeline_stage_run_id: UUID | None
    candidate_id: UUID | None
    artifact_id: UUID
    language: str
    entrypoint: str
    git_commit: str | None
    code_digest: str
    dependency_lock_digest: str | None
    dependency_lock_artifact_id: UUID | None
    runtime_environment_id: UUID
    created_at: datetime


class ReproducibilityRead(BaseModel):
    model_version_id: UUID
    workspace_id: UUID
    project_id: UUID | None
    workflow_id: UUID
    workflow_version_id: UUID | None
    workflow_run_id: UUID
    pipeline_id: UUID | None
    pipeline_version_id: UUID | None
    pipeline_run_id: UUID
    selected_candidate_id: UUID
    dataset_id: UUID
    feature_set_version_id: UUID | None
    artifact_uri: str | None
    model_artifact_id: UUID | None
    preprocessor_artifact_id: UUID | None
    feature_manifest_artifact_id: UUID | None
    runtime_environment: RuntimeEnvironmentRead | None
    code_snapshot: CodeSnapshotRead | None
    artifacts: list[ArtifactRead] = Field(default_factory=list)


class SignedArtifactUrlRead(BaseModel):
    artifact_id: UUID
    url: str
    expires_in: int
