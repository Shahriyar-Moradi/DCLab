"""Safe, workspace-scoped read model for a persisted model build."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.model_build_reproduction import ModelBuildGeneratedCode


class ModelBuildEvidenceReference(BaseModel):
    """An identifier for canonical evidence, never the evidence payload itself."""

    entity_type: str
    id: UUID | None = None
    source: Literal["canonical", "compatibility"] = "canonical"


class ModelBuildStageRead(BaseModel):
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
    evidence_references: list[ModelBuildEvidenceReference] = Field(default_factory=list)
    related_candidate_ids: list[UUID] = Field(default_factory=list)
    related_fold_ids: list[UUID] = Field(default_factory=list)
    code_generation_support_status: Literal[
        "supported", "not_available", "not_applicable"
    ]
    generated_code: ModelBuildGeneratedCode | None = None


class ModelBuildReproductionArtifactRead(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None = None
    pipeline_run_id: UUID
    artifact_type: str
    filename: str
    content_digest: str
    mime_type: str | None = None
    size_bytes: int
    generator_version: str | None = None
    spec_digest: str | None = None
    role: str | None = None


class ModelBuildReproductionArtifactsRead(BaseModel):
    workspace_id: UUID
    pipeline_run_id: UUID
    generator_version: str | None = None
    spec_digest: str | None = None
    notebook: ModelBuildReproductionArtifactRead | None = None
    script: ModelBuildReproductionArtifactRead | None = None


class PipelineModelBuildRead(BaseModel):
    workspace_id: UUID
    pipeline_run_id: UUID
    pipeline_run_status: str
    scientific_evidence_locked_at: datetime | None = None
    compatibility_fallback_used: bool = False
    generator_version: str | None = None
    reproduction_spec_digest: str | None = None
    reproduction_notebook: ModelBuildReproductionArtifactRead | None = None
    reproduction_script: ModelBuildReproductionArtifactRead | None = None
    stages: list[ModelBuildStageRead]
