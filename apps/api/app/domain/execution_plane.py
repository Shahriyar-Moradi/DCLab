"""Canonical execution-plane vocabularies. Experiment remains the physical PipelineRun."""

from __future__ import annotations

from app.domain.data_plane import sql_in_clause

INITIATED_BY_TYPES = (
    "human",
    "api",
    "system",
    "schedule",
    "agent",
)

CREATABLE_INITIATED_BY_TYPES = frozenset({"human", "api", "system", "schedule"})

PIPELINE_STATUSES = ("active", "archived")

PIPELINE_STAGE_RUN_STATUSES = (
    "queued",
    "running",
    "completed",
    "failed",
    "skipped",
)

CK_WORKFLOW_RUNS_INITIATED_BY = (
    "initiated_by_type IS NULL OR "
    + sql_in_clause("initiated_by_type", INITIATED_BY_TYPES)
)
CK_PIPELINES_STATUS = sql_in_clause("status", PIPELINE_STATUSES)
CK_PIPELINE_STAGE_RUNS_STATUS = sql_in_clause("status", PIPELINE_STAGE_RUN_STATUSES)
CK_WORKFLOW_VERSION_POSITIVE = "version >= 1"
CK_PIPELINE_VERSION_POSITIVE = "version >= 1"
