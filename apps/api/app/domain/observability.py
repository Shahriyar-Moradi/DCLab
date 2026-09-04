from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class MlRunEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    workflow_run_id: UUID
    experiment_id: UUID
    sequence: int
    stage: str
    event_type: str
    status: str
    timestamp: datetime
    duration_ms: float | None
    payload: dict[str, Any]
    created_at: datetime


class LlmInvocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    workflow_run_id: UUID
    experiment_id: UUID
    purpose: str
    provider: str | None
    model: str | None
    mode: str
    prompt_version: str
    schema_version: str
    input_evidence_digest: str
    redaction_summary: dict[str, Any]
    llm_used: bool
    reason: str
    status: str
    validator_verdict: str
    safe_output: dict[str, Any] | None
    final_decision: dict[str, Any] | None
    latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    estimated_cost: float | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime


class PipelineSummaryRead(BaseModel):
    id: UUID
    workspace_id: UUID
    workflow_run_id: UUID
    pipeline_name: str
    pipeline_index: int
    pipeline_purpose: str
    status: str
    failure_reason: str | None
    started_at: datetime | None
    ended_at: datetime | None
    latest_sequence: int
    event_count: int
    candidate_count: int
    model_version_id: UUID | None
    semantic_llm_count: int
    pipeline_audit_count: int


class WorkflowRunPipelineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    workflow_run_id: UUID
    pipeline_name: str
    pipeline_index: int
    pipeline_purpose: str
    status: str
    failure_reason: str | None
    started_at: datetime | None
    ended_at: datetime | None
