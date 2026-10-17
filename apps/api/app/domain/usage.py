"""Usage metering, plans and quotas (P8.4-A): vocabulary, DB CHECK literals and the /v1 read.

Usage is derived from ledgers DCLab already keeps, never from new per-request instrumentation:

* ``compute_seconds``: worker wall-clock of finished training and scoring jobs
  (``ml_jobs.completed_at - started_at`` of the last attempt only: an estimate), counted on the UTC
  day the job finished. Agent runs are bounded by the AI budget; monitoring and session clean-up
  jobs are platform work and are not counted.
* ``storage_bytes``: sum of ``artifacts.size_bytes`` of the workspace (every stored file: uploads,
  prepared tables, models, reports, prediction files): a snapshot, not a sum over days.
* ``llm_tokens`` / ``llm_cost_micros``: ``llm_invocations`` totals as the gateway recorded them
  (cost in micro-units of the recorded currency, USD).
* ``agent_runs``: ``agent_runs`` rows of agents (lead, specialist, ops) other than assistant turns.
* ``assistant_runs``: assistant turns answered by a model (``purpose = 'assistant.turn'``);
  templated replies with AI off make no run and are not counted.

A worker tick writes append-only ``usage_records`` per (workspace, kind, UTC day). A recompute
writes a new row only when the number changed, with a ``source_ref`` naming the ledger version and
the hour it was computed (``ledgers.v1:asof:2026-10-15t13``); ``UNIQUE (workspace_id, kind,
period_day, project_id, source_ref)`` (nulls not distinct) makes a repeated tick a no-op, and the
read takes the latest row per (kind, day), so nothing is counted twice.

Plans are ``workspace_entitlements`` rows (platform staff set them; tenants cannot). A key without
a row takes the ``beta`` default; a row whose value is JSON ``null`` is unlimited. The AI spend
limit stays ``workspace_llm_budgets`` (the gateway enforces it): the read shows it, nothing here
duplicates it. Member seats stay ``max_members`` (enforced when a member is added).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.data_plane import sql_in_clause

KIND_COMPUTE_SECONDS = "compute_seconds"
KIND_STORAGE_BYTES = "storage_bytes"
KIND_LLM_TOKENS = "llm_tokens"
KIND_LLM_COST_MICROS = "llm_cost_micros"
KIND_AGENT_RUNS = "agent_runs"
KIND_ASSISTANT_RUNS = "assistant_runs"
USAGE_KINDS = (KIND_COMPUTE_SECONDS, KIND_STORAGE_BYTES, KIND_LLM_TOKENS, KIND_LLM_COST_MICROS,
               KIND_AGENT_RUNS, KIND_ASSISTANT_RUNS)
UsageKind = Literal["compute_seconds", "storage_bytes", "llm_tokens", "llm_cost_micros", "agent_runs",
                    "assistant_runs"]
KIND_UNITS: dict[str, str] = {
    KIND_COMPUTE_SECONDS: "seconds", KIND_STORAGE_BYTES: "bytes", KIND_LLM_TOKENS: "tokens",
    KIND_LLM_COST_MICROS: "usd_micros", KIND_AGENT_RUNS: "runs", KIND_ASSISTANT_RUNS: "runs",
}
# Storage is a point-in-time snapshot: a period shows its latest value; every other kind is summed.
SNAPSHOT_KINDS = frozenset({KIND_STORAGE_BYTES})

LEDGER_VERSION = "ledgers.v1"
SOURCE_REF_MAX_CHARS = 128
ASSISTANT_TURN_PURPOSE = "assistant.turn"
AGENT_RUN_KINDS_COUNTED = ("lead", "specialist", "ops")

# Identical literals in alembic 0081_usage_records.
CK_USAGE_RECORDS_KIND = sql_in_clause("kind", USAGE_KINDS)
CK_USAGE_RECORDS_QUANTITY = "quantity >= 0"
CK_USAGE_RECORDS_SOURCE_REF = (
    f"char_length(source_ref) BETWEEN 1 AND {SOURCE_REF_MAX_CHARS} AND source_ref ~ '^[a-z0-9][a-z0-9:._-]*$'"
)
CK_USAGE_RECORDS_PERIOD_DAY = (
    "period_day >= DATE '2020-01-01' AND period_day <= (created_at AT TIME ZONE 'UTC')::date + 1"
)

# --- plans and quotas --------------------------------------------------------------------------

PLAN_ENTITLEMENT_KEY = "usage_plan"  # value: the plan name (a label; the limits are the keys below)
DEFAULT_PLAN = "beta"
QUOTA_COMPUTE_SECONDS_MONTH = "compute_seconds_month"
QUOTA_STORAGE_BYTES = "storage_bytes"
QUOTA_RUNS_PER_DAY = "runs_per_day"
QUOTA_MEMBERS = "members"  # read only here: ``max_members`` (workspace_entitlement_service) enforces it
ENFORCED_QUOTAS = (QUOTA_COMPUTE_SECONDS_MONTH, QUOTA_STORAGE_BYTES, QUOTA_RUNS_PER_DAY)
QuotaKey = Literal["compute_seconds_month", "storage_bytes", "runs_per_day", "members"]
QUOTA_UNITS: dict[str, str] = {QUOTA_COMPUTE_SECONDS_MONTH: "seconds", QUOTA_STORAGE_BYTES: "bytes",
                               QUOTA_RUNS_PER_DAY: "runs", QUOTA_MEMBERS: "members"}
QUOTA_PERIODS: dict[str, str] = {QUOTA_COMPUTE_SECONDS_MONTH: "month", QUOTA_STORAGE_BYTES: "none",
                                 QUOTA_RUNS_PER_DAY: "day", QUOTA_MEMBERS: "none"}
# The default ("beta") plan when a workspace has no row for a key: generous, so it only stops runaways.
BETA_LIMITS: dict[str, int] = {
    QUOTA_COMPUTE_SECONDS_MONTH: 200 * 3600,  # 200 worker hours a month
    QUOTA_STORAGE_BYTES: 50 * 1024**3,  # 50 GiB stored
    QUOTA_RUNS_PER_DAY: 500,  # training runs + scorings started per UTC day
}
WARNING_FRACTION = 0.8
ENTITLEMENT_SOURCE_PLATFORM = "platform_staff"
PLAN_NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"

DISCLAIMER = "Measured from DCLab's own records: an estimate, not an invoice."
NOTES = (
    "Compute time is the worker's wall-clock time of finished training runs and scorings (the last attempt of "
    "each), counted on the day each finished; work still running is not counted yet. Agent work is limited by "
    "the AI budget instead; monitoring and clean-up are DCLab's own work and are not counted.",
    "Storage is the size of every stored file of the workspace (uploads, prepared tables, models, reports, "
    "prediction files); the daily series shows the snapshot taken at each aggregation.",
    "AI tokens and cost are what DCLab's AI gateway recorded for each call; the AI budget is the workspace's "
    "existing AI spend limit.",
    "Agent runs count agent tasks; assistant runs count assistant replies written by a model (replies made "
    "with AI switched off are not counted).",
    "Limits are checked when new work is asked for; work already running is never stopped. Monthly totals "
    "can go slightly past a limit when several requests arrive at once.",
)
SERIES_DAYS = 31


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UsageQuotaRead(_Wire):
    key: QuotaKey
    unit: str
    period: Literal["month", "day", "none"] = Field(description="When `used` starts again from zero (UTC).")
    limit: int | None = Field(description="null: unlimited.")
    used: int = Field(description="Measured now from the ledgers (the same number the limit check uses).")
    percent: float | None = Field(description="used / limit * 100, rounded to 0.1; null when unlimited.")
    warning: bool = Field(description="At or past 80% of the limit and not yet over it.")
    over: bool = Field(description="At or past the limit: new work of this kind is refused (429 quota_exceeded).")
    resets_at: datetime | None = Field(description="Start of the next period (UTC); null for storage and members.")
    enforced: bool = Field(description="Whether reaching the limit refuses new work right now.")
    source: Literal["plan_default", "workspace"] = Field(
        description="`plan_default`: the default plan's value; `workspace`: set for this workspace by DCLab staff.")


class UsageTotalsRead(_Wire):
    kind: UsageKind
    unit: str
    aggregate: Literal["sum", "latest"] = Field(description="`latest` for storage (a snapshot), else `sum`.")
    this_month: int
    last_month: int


class UsageDayRead(_Wire):
    day: date
    compute_seconds: int
    storage_bytes: int
    llm_tokens: int
    llm_cost_micros: int
    agent_runs: int
    assistant_runs: int


class AiSpendRead(_Wire):
    """The workspace's existing AI budget (``workspace_llm_budgets``; the AI gateway enforces it)."""

    period_start: date
    limit_micros: int
    spent_micros: int
    reserved_micros: int
    currency: str
    percent: float | None
    warning: bool
    over: bool
    hard_stop: bool


class UsageRead(_Wire):
    """``GET /v1/workspaces/{workspace_id}/usage``: no person ids, no per-user split, no file names."""

    workspace_id: UUID
    plan: str
    quotas_enforced: bool = Field(description="Platform switch: false means limits are shown but never refuse work.")
    metering_enabled: bool = Field(description="Platform switch for the aggregation that fills the daily series.")
    measured_at: datetime | None = Field(description="When the newest usage record was written; null if never.")
    month_start: date
    resets_at: datetime = Field(description="Start of next month (UTC): when monthly limits start again.")
    quotas: list[UsageQuotaRead]
    totals: list[UsageTotalsRead]
    daily: list[UsageDayRead] = Field(description="The last 31 UTC days, oldest first (today so far included).")
    ai_spend: AiSpendRead | None = Field(description="null when the workspace's AI policy cannot be resolved.")
    disclaimer: str
    notes: list[str]
