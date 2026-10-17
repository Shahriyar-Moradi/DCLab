"""Operator read models (P8.5-A): platform-staff views across workspaces, ids and counts only.

No row of customer data, file name, prompt, payload or free-text failure reason is ever part of
these shapes. ``OperatorWorkspaceRead.name`` is the customer-chosen workspace name (the platform
explorer shows it too): untrusted plain text, render it as text only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CheckStatus = Literal["ok", "failing", "stale", "unknown", "skipped"]


class _Read(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadinessCheckRead(_Read):
    name: Literal["database", "migrations", "object_storage", "worker"]
    status: CheckStatus
    required: bool = Field(description="A failing required check makes GET /health/ready answer 503.")
    code: str | None = Field(None, description="Stable reason code (no secrets, no versions).")


class ReadinessRead(_Read):
    status: Literal["ready", "degraded", "not_ready"]
    checks: list[ReadinessCheckRead]


class WorkersRead(_Read):
    dispatcher: str = Field(description="ML_JOB_DISPATCHER (postgres: separate worker processes).")
    count: int = Field(description="Worker processes connected to the database now (no ids).")
    heartbeat_age_seconds: list[float] = Field(description="Seconds since each worker was last seen, freshest first.")
    visible: bool = Field(description="False when the database does not show other sessions to this role.")


class QueueRead(_Read):
    depth: int
    oldest_age_seconds: float | None


class JobCountRead(_Read):
    job_type: str
    status: str
    count: int


class FailureCodeCountRead(_Read):
    code: str
    count: int


class WorkspaceTotalsRead(_Read):
    total: int
    active_last_7d: int = Field(description="Workspaces that queued at least one ML job in the last 7 days.")


class PlatformAiCapsRead(_Read):
    """Code-owned caps (``agents/governance/platform_default``) and the AI_ENABLED setting. Read-only."""

    ai_enabled: bool
    max_data_class: str
    sample_values_per_column: int
    user_text_to_jev: bool
    auto_release: bool
    model_allowlist: list[str]
    budgets: dict[str, int | float | bool]


class OperatorOverviewRead(_Read):
    generated_at: datetime
    workspaces: WorkspaceTotalsRead
    jobs_last_24h: list[JobCountRead]
    queue: QueueRead
    workers: WorkersRead
    failed_jobs_last_24h: list[FailureCodeCountRead]
    failed_jobs_last_24h_truncated: bool = Field(
        description="True when more failed jobs than the scan bound (5000, newest first) exist: counts are a lower bound.")
    platform_ai_caps: PlatformAiCapsRead
    readiness: ReadinessRead


class OperatorWorkspaceRead(_Read):
    # TODO(P8.4-A): plan and usage vs limits join here once the usage service exists; absent until then.
    id: UUID
    name: str = Field(description="Customer-chosen text: render as plain text.")
    kind: str
    created_at: datetime
    member_count: int
    project_count: int
    jobs_last_30d: int
    failed_jobs_last_24h: int
    active_last_7d: bool
    assistant_beta: bool = Field(description="The ai_assistant_beta flag (set with `dclab entitlements set`).")


class OperatorWorkspacePage(_Read):
    items: list[OperatorWorkspaceRead]
    next_cursor: str | None


class OperatorFailedJobRead(_Read):
    id: UUID
    workspace_id: UUID
    job_type: str
    handler_key: str
    code: str = Field(description="A stable failure code, or see_logs (free-text reasons are never returned).")
    attempts: int
    failed_at: datetime


class OperatorFailedJobPage(_Read):
    items: list[OperatorFailedJobRead]
    next_cursor: str | None
