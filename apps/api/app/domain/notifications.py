"""Notifications (P8.8-A): email to people and signed webhooks to customer systems.

Vocabulary, DB CHECK literals (identical in alembic 0083_notifications) and the /v1 read and
write models. Producers enqueue one ``notification_outbox`` row per recipient (a person for
email, an endpoint for a webhook) through ``notification_service.enqueue_notification``; the
worker tick sends them. Payloads carry ids, closed codes and the project NAME (untrusted text,
cleaned and capped): never data values, rows, file names or holdout numbers.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.data_plane import sql_in_clause

KIND_DECISION_NEEDED = "decision_needed"
KIND_RUN_FINISHED = "run_finished"
KIND_RUN_FAILED = "run_failed"
KIND_SCHEDULED_SCORING = "scheduled_scoring_result"
KIND_DRIFT_ALERT = "drift_alert"
KIND_QUOTA_WARNING = "quota_warning"
# D7a: business-view questions, person to person (email only: never sent to webhooks / customer systems).
KIND_QUESTION_ASKED = "question_asked"
KIND_QUESTION_ANSWERED = "question_answered"
WEBHOOK_KINDS = (KIND_DECISION_NEEDED, KIND_RUN_FINISHED, KIND_RUN_FAILED, KIND_SCHEDULED_SCORING, KIND_DRIFT_ALERT,
                 KIND_QUOTA_WARNING)
KINDS = (*WEBHOOK_KINDS, KIND_QUESTION_ASKED, KIND_QUESTION_ANSWERED)  # append only (unsubscribe links index it)
EVENT_PING = "ping"  # webhook test deliveries only
OUTBOX_KINDS = (*KINDS, EVENT_PING)
WebhookKind = Literal["decision_needed", "run_finished", "run_failed", "scheduled_scoring_result",
                      "drift_alert", "quota_warning"]
NotificationKind = Literal["decision_needed", "run_finished", "run_failed", "scheduled_scoring_result",
                           "drift_alert", "quota_warning", "question_asked", "question_answered"]

# Email defaults (rows in notification_preferences exist only for overrides).
DEFAULT_EMAIL: dict[str, bool] = {
    KIND_DECISION_NEEDED: True, KIND_RUN_FINISHED: False, KIND_RUN_FAILED: True,
    KIND_SCHEDULED_SCORING: False, KIND_DRIFT_ALERT: True, KIND_QUOTA_WARNING: True,
    KIND_QUESTION_ASKED: True, KIND_QUESTION_ANSWERED: True,
}
# Who receives each kind by email (people only; never service tokens or agents).
AUDIENCE_ML_WRITE = "ml_write"  # owners, administrators, ML engineers
AUDIENCE_ADMINS = "admins"  # owners and administrators
AUDIENCE_REQUESTER = "requester"  # the person who started the run (runs a service token started: nobody)
AUDIENCE_BUSINESS = "business"  # D7a: the workspace's business users (client accounts) only
AUDIENCE: dict[str, str] = {
    KIND_DECISION_NEEDED: AUDIENCE_ML_WRITE, KIND_RUN_FINISHED: AUDIENCE_REQUESTER,
    KIND_RUN_FAILED: AUDIENCE_REQUESTER, KIND_SCHEDULED_SCORING: AUDIENCE_ML_WRITE,
    KIND_DRIFT_ALERT: AUDIENCE_ML_WRITE, KIND_QUOTA_WARNING: AUDIENCE_ADMINS,
    KIND_QUESTION_ASKED: AUDIENCE_BUSINESS, KIND_QUESTION_ANSWERED: AUDIENCE_REQUESTER,
}
LABELS: dict[str, str] = {
    KIND_DECISION_NEEDED: "A decision is waiting for a person",
    KIND_RUN_FINISHED: "A run or scoring you started finished",
    KIND_RUN_FAILED: "A run or scoring you started failed",
    KIND_SCHEDULED_SCORING: "Weekly scheduled scoring result",
    KIND_DRIFT_ALERT: "Drift alert on the model in use",
    KIND_QUOTA_WARNING: "Usage at 80% or 100% of a plan limit",
    KIND_QUESTION_ASKED: "The data team asked the business a question",
    KIND_QUESTION_ANSWERED: "A business user replied to your question",
}
# Footer: "You get this because <reason>; change this in Settings."
REASONS: dict[str, str] = {
    KIND_DECISION_NEEDED: "you can decide proposals in this workspace",
    KIND_RUN_FINISHED: "you started this run",
    KIND_RUN_FAILED: "you started this run",
    KIND_SCHEDULED_SCORING: "you can score with this project's model and turned on weekly scoring emails",
    KIND_DRIFT_ALERT: "you work on models in this workspace",
    KIND_QUOTA_WARNING: "you administer this workspace",
    KIND_QUESTION_ASKED: "you are a business user of this workspace",
    KIND_QUESTION_ANSWERED: "you asked this question",
}

CHANNEL_EMAIL = "email"
CHANNEL_WEBHOOK = "webhook"
CHANNELS = (CHANNEL_EMAIL, CHANNEL_WEBHOOK)

STATUS_PENDING = "pending"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"  # the last attempt failed; retried at next_attempt_at
STATUS_SKIPPED_DISABLED = "skipped_disabled"  # EMAIL_PROVIDER=none: nothing sent
STATUS_SKIPPED_PREFERENCE = "skipped_preference"  # the person turned this kind off
STATUS_SKIPPED_LIMIT = "skipped_limit"  # the person's daily email cap was reached (code daily_limit)
STATUS_DEAD = "dead"  # given up (attempts exhausted or a final error)
STATUSES = (STATUS_PENDING, STATUS_SENDING, STATUS_SENT, STATUS_FAILED, STATUS_SKIPPED_DISABLED,
            STATUS_SKIPPED_PREFERENCE, STATUS_SKIPPED_LIMIT, STATUS_DEAD)
DUE_STATUSES = (STATUS_PENDING, STATUS_SENDING, STATUS_FAILED)

# Closed error codes: never exception text, never a response body.
ERROR_CODES = (
    "not_configured", "recipient_unavailable", "endpoint_disabled", "expired", "destination_refused",
    "dns_failed", "connect_failed", "tls_failed", "timeout", "redirect_not_followed", "http_error",
    "invalid_response", "body_too_large", "auth_failed", "provider_rejected", "provider_unavailable",
    "internal_error", "email_unverified", "daily_limit",
)
FINAL_ERROR_CODES = frozenset({"recipient_unavailable", "endpoint_disabled", "expired", "destination_refused",
                               "body_too_large", "email_unverified"})
BACKOFF_SECONDS = (60, 300, 1800, 21600)  # 1 min, 5 min, 30 min, 6 h; the 5th failure is final
MAX_ATTEMPTS = len(BACKOFF_SECONDS) + 1
MAX_AGE_HOURS = 24  # a row older than this (e.g. after the kill switch) is never sent
SEND_LEASE_SECONDS = 120  # stamped on each row when THAT row is claimed (one row at a time)
TICK_BATCH = 50
# Worker fairness (code-owned; the tick shares its loop with ML jobs). A tick serves workspaces
# round-robin (oldest waiting first), stops claiming once TICK_BUDGET_SECONDS have passed, and
# stops serving a workspace once it used TICK_BUDGET_PER_WORKSPACE_SECONDS or TICK_MAX_PER_WORKSPACE
# rows. A webhook send starts only with its full deadline left (else the row waits for the next
# tick, no attempt used); an email's deadline is cut to what is left, and a timeout under a cut
# deadline is handed back the same way.
TICK_BUDGET_SECONDS = 10.0
TICK_BUDGET_PER_WORKSPACE_SECONDS = 3.0
MIN_SEND_SECONDS = 1.0
TICK_MAX_PER_WORKSPACE = 5
TICK_MAX_PER_ENDPOINT = 5
PINGS_PER_ENDPOINT_PER_HOUR = 6
# Circuit breaker: after this many consecutive failed deliveries an endpoint is paused (its rows
# wait, no attempt is used); after the pause one delivery is tried again (fail: paused again).
BREAKER_FAILURES = 5
BREAKER_PAUSE_SECONDS = 3600
EMAILS_PER_RECIPIENT_PER_DAY = 20  # per person, all workspaces, UTC day; over it: skipped_limit
EMAILS_PER_RECIPIENT_PER_WORKSPACE_PER_DAY = 10  # one workspace cannot use up a person's whole day

PAYLOAD_MAX_BYTES = 4096
PROJECT_NAME_MAX = 80
DEDUPE_KEY_MAX = 200
DEDUPE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,199}$")
CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
SUBJECT_KINDS = ("proposal", "decision", "agent_run", "experiment", "batch_prediction", "scoring_schedule",
                 "model_version", "webhook_endpoint", "workspace", "business_question")

# Webhooks
WEBHOOKS_PER_WORKSPACE_MAX = 5
WEBHOOK_NAME_MAX = 80
WEBHOOK_URL_MAX = 2048
WEBHOOK_BODY_MAX_BYTES = 16 * 1024
WEBHOOK_RESPONSE_READ_MAX = 4 * 1024
WEBHOOK_CONNECT_TIMEOUT_S = 3.0  # DNS lookup and TCP connect, each
WEBHOOK_TOTAL_TIMEOUT_S = 5.0  # one attempt in total; slower is a timeout (a breaker failure)
WEBHOOK_SECRET_PREFIX = "dclab_whsec_"
WEBHOOK_SECRET_KEY_ID = "k1"  # the id of WEBHOOK_SECRET_KEY in the derivation (a key map can add ids later)
WEBHOOK_CHANGE_ACTIONS = ("create", "update_url", "update_events", "enable", "disable", "rotate", "delete")
SIGNATURE_SCHEME = "v1"
REPLAY_WINDOW_SECONDS = 300
HEADER_EVENT = "X-DCLab-Event"
HEADER_DELIVERY = "X-DCLab-Delivery"
HEADER_TIMESTAMP = "X-DCLab-Timestamp"
HEADER_SIGNATURE = "X-DCLab-Signature"
WEBHOOK_USER_AGENT = "DCLab-Webhooks/1"
DELIVERY_OUTCOMES = ("delivered", "failed")
DELIVERIES_LIST_MAX = 50
UNSUBSCRIBE_TOKEN_PREFIX = "dclab_unsub_"
UNSUBSCRIBE_TTL_DAYS = 30

# --- untrusted text -----------------------------------------------------------------------------

_BIDI = frozenset("؜‎‏‪‫‬‭‮⁦⁧⁨⁩")


def clean_untrusted_text(value: object, limit: int = PROJECT_NAME_MAX) -> str:
    """A display-only string: control, format (bidi, zero-width) and line characters removed,
    whitespace collapsed, capped. Safe inside an email header (no CR/LF can survive)."""

    out: list[str] = []
    for ch in str(value or ""):
        if ch.isspace():  # CR, LF, tab, NEL, line/paragraph separators -> one space
            out.append(" ")
        elif ch not in _BIDI and unicodedata.category(ch) not in {"Cc", "Cf", "Cs", "Co", "Cn"}:
            out.append(ch)
    return " ".join("".join(out).split())[:limit]


# --- DB CHECK backstops (models.py; alembic 0083 inlines the same literals) -----------------------

CK_NOTIFICATION_PREFERENCES_KIND = sql_in_clause("kind", KINDS)
CK_WEBHOOK_ENDPOINTS_NAME = f"char_length(btrim(name)) BETWEEN 1 AND {WEBHOOK_NAME_MAX}"
CK_WEBHOOK_ENDPOINTS_URL = (
    f"char_length(url) BETWEEN 9 AND {WEBHOOK_URL_MAX} AND url ~ '^https://[^@/?#[:space:]]+([/?#][^[:space:]]*)?$'"
)
CK_WEBHOOK_ENDPOINTS_EVENTS = (
    "CASE WHEN jsonb_typeof(events) = 'array' THEN jsonb_array_length(events) BETWEEN 1 AND 6 "
    "AND events <@ '[\"decision_needed\", \"run_finished\", \"run_failed\", \"scheduled_scoring_result\", "
    "\"drift_alert\", \"quota_warning\"]'::jsonb AND jsonb_array_length(events) = "
    "jsonb_exists(events, 'decision_needed')::int + jsonb_exists(events, 'run_finished')::int "
    "+ jsonb_exists(events, 'run_failed')::int + jsonb_exists(events, 'scheduled_scoring_result')::int "
    "+ jsonb_exists(events, 'drift_alert')::int + jsonb_exists(events, 'quota_warning')::int ELSE false END"
)
CK_WEBHOOK_ENDPOINTS_SECRET_VERSION = "secret_version >= 1"
CK_WEBHOOK_ENDPOINTS_SECRET_KEY_ID = "secret_key_id ~ '^[a-z0-9_-]{1,16}$'"
CK_WEBHOOK_ENDPOINTS_FAILURES = "consecutive_failures >= 0"
CK_NOTIFICATION_OUTBOX_CHANNEL = sql_in_clause("channel", CHANNELS)
CK_NOTIFICATION_OUTBOX_KIND = sql_in_clause("kind", OUTBOX_KINDS)
CK_NOTIFICATION_OUTBOX_STATUS = sql_in_clause("status", STATUSES)
CK_NOTIFICATION_OUTBOX_ERROR = "last_error_code IS NULL OR " + sql_in_clause("last_error_code", ERROR_CODES)
CK_NOTIFICATION_OUTBOX_RECIPIENT = (
    "(channel = 'email' AND recipient_user_id IS NOT NULL AND webhook_endpoint_id IS NULL) OR "
    "(channel = 'webhook' AND webhook_endpoint_id IS NOT NULL AND recipient_user_id IS NULL)"
)
CK_NOTIFICATION_OUTBOX_PING = "kind <> 'ping' OR channel = 'webhook'"
CK_NOTIFICATION_OUTBOX_DEDUPE = "dedupe_key ~ '^[a-z0-9][a-z0-9:._-]{0,199}$'"
CK_NOTIFICATION_OUTBOX_PAYLOAD = (
    f"jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= {PAYLOAD_MAX_BYTES}"
)
CK_NOTIFICATION_OUTBOX_ATTEMPTS = f"attempts BETWEEN 0 AND {MAX_ATTEMPTS}"
CK_NOTIFICATION_OUTBOX_SENT = "(status = 'sent') = (sent_at IS NOT NULL)"
CK_WEBHOOK_DELIVERIES_OUTCOME = sql_in_clause("outcome", DELIVERY_OUTCOMES)
CK_WEBHOOK_DELIVERIES_EVENT = sql_in_clause("event", (*WEBHOOK_KINDS, EVENT_PING))
CK_WEBHOOK_DELIVERIES_ERROR = "error_code IS NULL OR " + sql_in_clause("error_code", ERROR_CODES)
CK_WEBHOOK_DELIVERIES_STATUS_CODE = "status_code IS NULL OR status_code BETWEEN 100 AND 599"
CK_WEBHOOK_DELIVERIES_NUMBERS = "attempt BETWEEN 1 AND 10 AND duration_ms BETWEEN 0 AND 600000"
CK_WEBHOOK_CHANGES_ACTION = sql_in_clause("action", WEBHOOK_CHANGE_ACTIONS)
CK_WEBHOOK_CHANGES_URL_HOST = "url_host IS NULL OR url_host ~ '^[a-z0-9.:-]{1,253}$'"

# --- /v1 models ---------------------------------------------------------------------------------

EMAIL_OFF_NOTE = ("Email is turned off on this server (EMAIL_PROVIDER=none): DCLab sends no email. "
                  "Your choices are kept for when it is turned on.")
PAUSED_NOTE = "Notifications are paused on this server (NOTIFICATIONS_ENABLED=false): nothing is sent."
UNVERIFIED_NOTE = ("Your email address is not verified, so DCLab sends you no email. Your choices are kept for "
                   "when it is verified.")


class NotificationPreferenceRead(BaseModel):
    kind: NotificationKind
    label: str
    email_enabled: bool = Field(description="Your choice (the default unless you changed it).")
    default: bool = Field(description="The default for this kind.")
    applies_to_you: bool = Field(description="Whether your role in this workspace receives this kind at all.")


class NotificationPreferencesRead(BaseModel):
    """``GET /v1/me/notification-preferences``: your email choices in the selected workspace."""

    workspace_id: UUID
    email_delivery: Literal["on", "off", "paused"] = Field(
        description="`off`: EMAIL_PROVIDER=none (nothing is sent); `paused`: NOTIFICATIONS_ENABLED=false.")
    email_verified: bool = Field(description="DCLab emails only people whose address is verified.")
    note: str | None = None
    items: list[NotificationPreferenceRead]


class NotificationPreferenceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: NotificationKind
    email_enabled: bool


class NotificationPreferencesWrite(BaseModel):
    """``PUT /v1/me/notification-preferences``: set one or more kinds (others keep their value)."""

    model_config = ConfigDict(extra="forbid")

    items: list[NotificationPreferenceWrite] = Field(min_length=1, max_length=len(KINDS))


class UnsubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=256, description="The token from the email's unsubscribe link.")


class UnsubscribeRead(BaseModel):
    unsubscribed: Literal[True] = True
    kind: NotificationKind


class WebhookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=WEBHOOK_NAME_MAX)
    url: str = Field(min_length=9, max_length=WEBHOOK_URL_MAX,
                     description="https only, port 443 (or a port the platform allows), no user:password.")
    events: list[WebhookKind] = Field(min_length=1, max_length=len(WEBHOOK_KINDS))
    enabled: bool = True


class WebhookUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=WEBHOOK_NAME_MAX)
    url: str | None = Field(None, min_length=9, max_length=WEBHOOK_URL_MAX)
    events: list[WebhookKind] | None = Field(None, min_length=1, max_length=len(WEBHOOK_KINDS))
    enabled: bool | None = None


class WebhookRead(BaseModel):
    """A webhook endpoint. Never contains its signing secret."""

    id: UUID
    workspace_id: UUID
    name: str
    url: str
    events: list[str]
    enabled: bool
    secret_version: int
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    secret_rotated_at: datetime | None = None
    consecutive_failures: int = 0
    paused_until: datetime | None = Field(
        None, description="Set after 5 failed deliveries in a row: deliveries wait until then, then one is tried.")
    last_delivery_at: datetime | None = None
    last_delivery_outcome: Literal["delivered", "failed"] | None = None
    last_error_code: str | None = None


class WebhookSecretRead(WebhookRead):
    secret: str | None = Field(
        description="The signing secret. Returned only by the create / rotate response; store it now. "
        "An Idempotency-Key replay returns null.")


class WebhookTestRead(BaseModel):
    outbox_id: UUID
    status: str
    note: str


class WebhookDeliveryRead(BaseModel):
    id: UUID
    delivery_id: UUID = Field(description="The X-DCLab-Delivery value (the same on every retry).")
    event: str
    attempt: int
    outcome: Literal["delivered", "failed"]
    status_code: int | None = None
    error_code: str | None = None
    duration_ms: int
    created_at: datetime
