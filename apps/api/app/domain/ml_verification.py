"""Strict contracts for advisory OpenAI pipeline verification."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

VerificationStatus = Literal[
    "VERIFIED",
    "VERIFIED_WITH_WARNINGS",
    "NOT_VERIFIABLE",
    "FAILED",
]


class PipelineAuditStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = Field(min_length=1, max_length=80)
    status: VerificationStatus
    summary: str = Field(min_length=1, max_length=600)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    issues: list[str] = Field(default_factory=list, max_length=20)
    recommendations: list[str] = Field(default_factory=list, max_length=20)


class PipelineAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_status: VerificationStatus
    summary: str = Field(min_length=1, max_length=1200)
    stages: list[PipelineAuditStage] = Field(min_length=1, max_length=40)
    critical_issues: list[str] = Field(default_factory=list, max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=30)
    recommendations: list[str] = Field(default_factory=list, max_length=30)
    confidence: float = Field(ge=0.0, le=1.0)


class VerificationAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    experiment_id: UUID | None
    audit_mode: str
    deterministic_status: str
    deterministic_schema_version: int
    llm_status: str
    llm_model: str
    llm_provider: str
    prompt_version: str
    schema_version: int
    input_digest: str
    redaction_summary: dict[str, Any]
    llm_report: dict[str, Any] | None
    error: str | None
    duration_ms: float | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
