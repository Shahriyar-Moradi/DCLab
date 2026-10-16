"""Service tokens (P3.2-A): hashed, scoped, expiring machine credentials for /v1.

A token belongs to one workspace and to the human who created it. It acts with
that creator's *current* authority (re-checked on every request), narrowed to
the token's workspace and capability scopes. It authenticates ``/v1`` only, as
``Authorization: Bearer dclab_st_<id>_<secret>``; only an HMAC/SHA-256 of the
whole string is stored and the secret is shown once, on create.

Scopes are a fixed code-owned set. Every /v1 operation a token may call is
listed in ``TOKEN_ROUTE_SCOPES``; anything else (token management, accepting or
rejecting decisions, ref moves, non-/v1 surfaces) refuses tokens (fail closed).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

# Tokens act as *agents*: no scope may ever let a token accept, reject, record or
# supersede a decision, move a ref, or manage tokens. Those stay human-only
# (HUMAN_ONLY_ROUTES below; the decision service also refuses agent actors).
SCOPE_READ = "read"
SCOPE_PROJECTS_WRITE = "projects:write"
SCOPE_DATASETS_WRITE = "datasets:write"
SCOPE_EXPERIMENTS_WRITE = "experiments:write"
SCOPE_DECISIONS_PROPOSE = "decisions:propose"
SERVICE_TOKEN_SCOPES = (
    SCOPE_READ,
    SCOPE_PROJECTS_WRITE,
    SCOPE_DATASETS_WRITE,
    SCOPE_EXPERIMENTS_WRITE,
    SCOPE_DECISIONS_PROPOSE,
)
# Scopes that need the creator's explicit ML-write membership on every use (creating
# any token needs it too).
WRITE_SCOPES = frozenset(SERVICE_TOKEN_SCOPES) - {SCOPE_READ}
ServiceTokenScope = Literal["read", "projects:write", "datasets:write", "experiments:write", "decisions:propose"]

TOKEN_PREFIX = "dclab_st_"
TOKEN_PATTERN = re.compile(r"^dclab_st_([0-9a-f]{32})_([A-Za-z0-9_-]{43})$")
TTL_DAYS_DEFAULT = 30
TTL_DAYS_MAX = 90
NAME_MAX_CHARS = 80
ACTIVE_TOKENS_PER_CREATOR_MAX = 25
LIST_MAX = 200
LAST_USED_RESOLUTION_SECONDS = 60

# (method, route template) -> required scope. Absent = tokens refused.
TOKEN_ROUTE_SCOPES: dict[tuple[str, str], str] = {
    **{
        ("GET", path): SCOPE_READ
        for path in (
            "/v1/me",
            "/v1/workspaces",
            "/v1/projects",
            "/v1/projects/{project_id}",
            "/v1/projects/{project_id}/graph",
            "/v1/projects/{project_id}/decisions",
            "/v1/projects/{project_id}/refs",
            "/v1/projects/{project_id}/refs/{ref_kind}",
            # P7.2-A: approvals of the model in use; tokens never get the approving person's id.
            "/v1/projects/{project_id}/releases",
            "/v1/nodes/{kind}/{node_id}/impact",
            "/v1/datasets",
            "/v1/datasets/{dataset_id}",
            "/v1/datasets/{dataset_id}/profile",  # P4.1-C: training-row statistics, holdout-free
            # V7 BE-C: goal versions and the test design; no user or token ids, final test = a row count only
            # (no dates, rates or statistics) and the rule's reason text withheld from tokens.
            "/v1/projects/{project_id}/problem-specs",
            "/v1/projects/{project_id}/problem-specs/{spec_id}",
            "/v1/split-plans/{split_plan_id}",
            "/v1/execution-requests/{request_id}",
            "/v1/model-builds/{pipeline_run_id}",
            "/v1/model-builds/{pipeline_run_id}/events",
            "/v1/model-builds/{pipeline_run_id}/visualizations",
            "/v1/model-builds/{pipeline_run_id}/artifacts",
            "/v1/experiments",
            "/v1/experiments/compare",
            "/v1/experiments/{experiment_id}",
            "/v1/experiments/{experiment_id}/code",
            "/v1/experiments/{experiment_id}/findings",
            # P5.2-A: out-of-fold operating points only (no final-evaluation figure at any point).
            "/v1/experiments/{experiment_id}/operating-points",
            # P5.5-B-read: CV-only verdict + test-set look counts (metadata, no final-evaluation value).
            "/v1/experiments/{experiment_id}/verdict",
            "/v1/decisions/{decision_id}",
            "/v1/model-versions/{model_version_id}",
            "/v1/model-versions/{model_version_id}/card",
            # V7 B2/B3: versions with CV metrics only (no final-test value); input column names + types only.
            "/v1/projects/{project_id}/model-versions",
            "/v1/model-versions/{model_version_id}/feature-contract",
            "/v1/predictions/{prediction_id}",
            "/v1/predictions/{prediction_id}/download",
            # V7 B6: capped pages of the same file the download returns (same scope, no extra columns).
            "/v1/predictions/{prediction_id}/rows",
            # Weekly scheduled scoring: the schedule and its runs (no user ids for tokens).
            "/v1/projects/{project_id}/scoring-schedule",
            "/v1/projects/{project_id}/scoring-schedule/runs",
            # P7.4-A: monitoring aggregates and linked outcome files (no rows, no holdout, no person ids).
            "/v1/projects/{project_id}/monitoring",
            "/v1/projects/{project_id}/monitoring/windows/{window_start}",
            "/v1/projects/{project_id}/monitoring/outcomes",
            # P7.6-A: the Business view; tokens get the final-test headline withheld (no person ids).
            "/v1/projects/{project_id}/outcomes",
            "/v1/outcomes/projects",  # id and name of projects with a model in use
            # P6.6-A: proposals and agent runs, read with holdout-free output.
            "/v1/agent-runs",
            "/v1/agent-runs/{run_id}",
            "/v1/proposals",
            "/v1/proposals/{proposal_id}",
            # P6.11-A: the governance console is read-only metadata of the token creator's own workspace
            # (the creator's ML-write / owner / platform authority is re-checked on every request).
            "/v1/governance",
            # P4.15-A: typed activity projection (no free text, no user ids, no holdout values).
            "/v1/activity",
            # P4.16-A: inbox projection; tokens get no answers or free text and never an assistant
            # tool call, and every action is `allowed: false` (deciding stays with people).
            "/v1/inbox",
            "/v1/inbox/counts",
            # D3a: training-row monthly target rate (final test always sealed for tokens); the upload log
            # (metadata, no person ids, file names untrusted); who decided the goal (never "you", no ids).
            "/v1/datasets/{dataset_id}/target-by-period",
            "/v1/projects/{project_id}/upload-attempts",
            "/v1/projects/{project_id}/goal/decisions",
        )
    },
    ("POST", "/v1/projects"): SCOPE_PROJECTS_WRITE,
    ("POST", "/v1/projects/{project_id}/problem-specs"): SCOPE_PROJECTS_WRITE,
    ("POST", "/v1/datasets"): SCOPE_DATASETS_WRITE,
    ("POST", "/v1/execution-requests"): SCOPE_EXPERIMENTS_WRITE,
    ("POST", "/v1/execution-requests/{request_id}/target-confirmation"): SCOPE_EXPERIMENTS_WRITE,
    ("POST", "/v1/experiments"): SCOPE_EXPERIMENTS_WRITE,
    ("POST", "/v1/experiments/{experiment_id}/branches"): SCOPE_EXPERIMENTS_WRITE,
    ("POST", "/v1/experiments/{experiment_id}/cancel"): SCOPE_EXPERIMENTS_WRITE,
    # Scoring runs a worker job like a run; it never changes the model or a ref.
    ("POST", "/v1/model-versions/{model_version_id}/predictions"): SCOPE_EXPERIMENTS_WRITE,
    # P6.6-A: queues a specialist run that only proposes (the harness authorizes the token again).
    ("POST", "/v1/agent-reviews"): SCOPE_EXPERIMENTS_WRITE,
    # The token acts as an agent: the decision service lets agents only propose.
    ("POST", "/v1/projects/{project_id}/decisions"): SCOPE_DECISIONS_PROPOSE,
}
# Humans only (documented; anything not in TOKEN_ROUTE_SCOPES is refused anyway).
HUMAN_ONLY_ROUTES = frozenset(
    {
        ("GET", "/v1/service-tokens"),
        # V7-B6: members' names and emails are for signed-in administrators (session_required for any bearer).
        ("GET", "/v1/workspaces/{workspace_id}/members"),
        # P8.4-A: usage and plan limits are for signed-in owners, administrators and ML engineers.
        ("GET", "/v1/workspaces/{workspace_id}/usage"),
        # V7 BE-A: the Studio flow bar's composed read (tokens use the underlying reads instead).
        ("GET", "/v1/projects/{project_id}/flow"),
        ("POST", "/v1/service-tokens"),
        ("POST", "/v1/service-tokens/{token_id}/revoke"),
        ("POST", "/v1/decisions/{decision_id}/accept"),
        ("POST", "/v1/decisions/{decision_id}/reject"),
        ("POST", "/v1/decisions/{decision_id}/supersede"),
        ("POST", "/v1/projects/{project_id}/refs/{ref_kind}"),
        # P5.2-A: choosing an operating point records a person's decision (tokens: 403
        # service_token_not_permitted; the route refuses any token again as human_session_required).
        ("POST", "/v1/experiments/{experiment_id}/operating-point"),
        # Weekly scheduled scoring is set by people (the route refuses any token as human_session_required).
        ("PUT", "/v1/projects/{project_id}/scoring-schedule"),
        # P6.9-A: a split confirmation is a person's answer (ADR 0008 §2).
        ("POST", "/v1/execution-requests/{request_id}/split-confirmation"),
        # P6.6-A: deciding a proposal is a person's act (403 human_session_required for any bearer).
        ("POST", "/v1/proposals/{proposal_id}/accept"),
        ("POST", "/v1/proposals/{proposal_id}/reject"),
        ("POST", "/v1/proposals/{proposal_id}/revert"),
        # P6.11-A: policy, switches and replay are people's acts (policy needs an approver; replay reads
        # recorded runs and may open an incident); agents only read the shaped GET /v1/governance.
        ("POST", "/v1/governance/policy"),
        ("POST", "/v1/governance/policy/{proposal_id}/accept"),
        ("POST", "/v1/governance/switches"),
        ("POST", "/v1/agent-runs/{run_id}/replay"),
        # P7.4-A: linking real outcomes is a person's act (tokens: 403 service_token_not_permitted; the
        # route refuses any token again as human_session_required).
        ("POST", "/v1/projects/{project_id}/monitoring/outcomes"),
        # P6.3-B2: the in-app assistant is for signed-in people (403 human_session_required).
        ("GET", "/v1/assistant/threads"),
        ("POST", "/v1/assistant/threads"),
        ("GET", "/v1/assistant/threads/{thread_id}"),
        ("POST", "/v1/assistant/threads/{thread_id}/messages"),
        # P8.5-A: the operator reads are for signed-in platform staff (403 human_session_required).
        ("GET", "/v1/operator/overview"),
        ("GET", "/v1/operator/workspaces"),
        ("GET", "/v1/operator/failed-jobs"),
        # P8.8-A: your notification preferences and the workspace's webhooks are for signed-in sessions
        # (any bearer 403 session_required; webhooks need an owner or administrator).
        ("GET", "/v1/me/notification-preferences"),
        ("PUT", "/v1/me/notification-preferences"),
        ("GET", "/v1/workspaces/{workspace_id}/webhooks"),
        ("POST", "/v1/workspaces/{workspace_id}/webhooks"),
        ("PATCH", "/v1/workspaces/{workspace_id}/webhooks/{webhook_id}"),
        ("POST", "/v1/workspaces/{workspace_id}/webhooks/{webhook_id}/rotate-secret"),
        ("POST", "/v1/workspaces/{workspace_id}/webhooks/{webhook_id}/test"),
        ("GET", "/v1/workspaces/{workspace_id}/webhooks/{webhook_id}/deliveries"),
        # No principal at all: the signed link from an email is the only authority (one kind, off only).
        ("POST", "/v1/notifications/unsubscribe"),
    }
)


def token_route_scope(method: str, route_template: str | None) -> str | None:
    if not route_template:
        return None
    return TOKEN_ROUTE_SCOPES.get((method.upper(), route_template))


def parse_service_token(raw: str) -> UUID | None:
    """The token id of a well-formed token string, else None (never raises)."""

    match = TOKEN_PATTERN.fullmatch(raw or "")
    return UUID(hex=match.group(1)) if match else None


def is_service_token(raw: str | None) -> bool:
    """Shape check used to route a bearer to service-token resolution (prefix only)."""

    return bool(raw) and raw.startswith(TOKEN_PREFIX)


def display_prefix(token_id: UUID) -> str:
    return f"{TOKEN_PREFIX}{token_id.hex[:8]}"


# --- DB CHECK backstops (models.py + Alembic 0067 inline the same text) ------------------

CK_SERVICE_TOKENS_NAME = f"char_length(btrim(name)) BETWEEN 1 AND {NAME_MAX_CHARS}"
# Non-empty subset of the known scopes without duplicates: the length must equal
# the number of distinct known scopes present.
CK_SERVICE_TOKENS_SCOPES = (
    "CASE WHEN jsonb_typeof(scopes) = 'array' THEN jsonb_array_length(scopes) BETWEEN 1 AND 5 "
    "AND scopes <@ '[\"read\", \"projects:write\", \"datasets:write\", \"experiments:write\", "
    "\"decisions:propose\"]'::jsonb AND jsonb_array_length(scopes) = "
    "jsonb_exists(scopes, 'read')::int + jsonb_exists(scopes, 'projects:write')::int "
    "+ jsonb_exists(scopes, 'datasets:write')::int + jsonb_exists(scopes, 'experiments:write')::int "
    "+ jsonb_exists(scopes, 'decisions:propose')::int ELSE false END"
)
CK_SERVICE_TOKENS_SECRET_HASH = "secret_hash ~ '^[a-f0-9]{64}$'"
# Hours, not days: timestamptz + days depends on the session TimeZone (DST).
CK_SERVICE_TOKENS_EXPIRY = (
    f"expires_at > created_at AND expires_at <= created_at + interval '{TTL_DAYS_MAX * 24} hours'"
)
CK_SERVICE_TOKENS_REVOKED = "revoked_at IS NULL OR revoked_at >= created_at"
CK_SERVICE_TOKENS_REVOKED_BY = "revoked_by_user_id IS NULL OR revoked_at IS NOT NULL"


# --- API models -------------------------------------------------------------------------------


class ServiceTokenCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=NAME_MAX_CHARS, description="Label shown in Studio.")
    scopes: list[ServiceTokenScope] = Field(
        min_length=1, max_length=len(SERVICE_TOKEN_SCOPES),
        description="Capabilities. Creating any token needs an ML-write role as a workspace member.",
    )
    expires_in_days: int = Field(
        TTL_DAYS_DEFAULT, ge=1, le=TTL_DAYS_MAX, description=f"Lifetime in days (max {TTL_DAYS_MAX})."
    )
    current_password: SecretStr = Field(
        min_length=1, max_length=256,
        description="Re-authentication: your account password. Never stored, logged or part of the "
        "Idempotency-Key digest.",
    )


class ServiceTokenRead(BaseModel):
    """Token metadata. Never contains the secret or its hash."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    prefix: str
    scopes: list[str]
    status: Literal["active", "expired", "revoked"]
    created_by_user_id: UUID
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by_user_id: UUID | None = None


class ServiceTokenCreatedRead(ServiceTokenRead):
    secret: str | None = Field(
        description="The bearer token. Returned only by the first create response; store it now. "
        "An Idempotency-Key replay returns null."
    )


class ServiceTokenPrincipalRead(BaseModel):
    """``/v1/me`` for a service-token principal (the token, not its creator's sessions)."""

    id: UUID
    name: str
    workspace_id: UUID
    scopes: list[str]
    expires_at: datetime
