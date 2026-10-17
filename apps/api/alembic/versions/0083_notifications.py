"""Notifications: email preferences, webhook endpoints, the outbox, webhook deliveries and changes.

Revision ID: 0083_notifications
Revises: 0082_upload_attribution
Create Date: 2026-10-16

P8.8-A. Expand-only: five new tables, no change to existing ones.

- ``notification_preferences``: a person's email choice per (workspace, kind); rows only for
  overrides of the code defaults. Mutable config.
- ``webhook_endpoints``: owner/admin config of a customer system receiving signed webhooks. No
  secret column: signing secrets are derived from the server key ``secret_key_id`` names, the ids
  and ``secret_version``. ``consecutive_failures`` / ``paused_until``: the delivery circuit
  breaker. Disabled, never deleted.
- ``notification_outbox``: one email (to a person) or webhook (to an endpoint) to send. ``UNIQUE
  (workspace_id, channel, recipient_user_id, webhook_endpoint_id, dedupe_key) NULLS NOT
  DISTINCT`` makes a re-run or recompute a no-op. Payload is a capped JSON object (ids, closed
  codes, the cleaned project name). Mutable status (the worker's state machine; ``claim_token``
  marks the worker that may write a row's result; ``sent_at`` iff ``sent``). Composite tenant FKs
  to the endpoint and the project (NO ACTION to the project, like ``usage_records``: a cascade
  could never pass the append-only deliveries); partial index on the due rows.
- ``webhook_deliveries``: append-only attempts log (``prevent_canonical_row_mutation()``
  trigger; ``created_at`` is ``clock_timestamp()``), composite tenant FKs to the endpoint and the
  outbox row; an index on ``(workspace_id, created_at DESC)`` serves the per-minute rate limit.
- ``webhook_changes``: append-only trail of endpoint changes (create, URL / events changes,
  enable, disable, rotate), written in the change's transaction; ``url_host`` only.

Retention is not addressed: terminal outbox rows and the two append-only logs stay. Offboarding a
workspace, or purging webhook rows that have deliveries or changes, needs an operator to remove
the append-only rows first (TRUNCATE or the trigger disabled in a maintenance transaction), like
the other append-only tables.

Locking: new tables only; ``lock_timeout`` 5s. Downgrade drops the five tables (notifications
not yet sent and the delivery log are lost; nothing else references them).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0083_notifications"
down_revision: Union[str, Sequence[str], None] = "0082_upload_attribution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Identical literals in app/domain/notifications.py (and the trigger in app/db/integrity.py).
_KINDS = "'decision_needed', 'run_finished', 'run_failed', 'scheduled_scoring_result', 'drift_alert', 'quota_warning'"
_ERRORS = (
    "'not_configured', 'recipient_unavailable', 'endpoint_disabled', 'expired', 'destination_refused', "
    "'dns_failed', 'connect_failed', 'tls_failed', 'timeout', 'redirect_not_followed', 'http_error', "
    "'invalid_response', 'body_too_large', 'auth_failed', 'provider_rejected', 'provider_unavailable', "
    "'internal_error', 'email_unverified', 'daily_limit'"
)
_CK_PREFERENCES_KIND = f"kind IN ({_KINDS})"
_CK_ENDPOINTS_NAME = "char_length(btrim(name)) BETWEEN 1 AND 80"
_CK_ENDPOINTS_URL = (
    "char_length(url) BETWEEN 9 AND 2048 AND url ~ '^https://[^@/?#[:space:]]+([/?#][^[:space:]]*)?$'"
)
_CK_ENDPOINTS_EVENTS = (
    "CASE WHEN jsonb_typeof(events) = 'array' THEN jsonb_array_length(events) BETWEEN 1 AND 6 "
    "AND events <@ '[\"decision_needed\", \"run_finished\", \"run_failed\", \"scheduled_scoring_result\", "
    "\"drift_alert\", \"quota_warning\"]'::jsonb AND jsonb_array_length(events) = "
    "jsonb_exists(events, 'decision_needed')::int + jsonb_exists(events, 'run_finished')::int "
    "+ jsonb_exists(events, 'run_failed')::int + jsonb_exists(events, 'scheduled_scoring_result')::int "
    "+ jsonb_exists(events, 'drift_alert')::int + jsonb_exists(events, 'quota_warning')::int ELSE false END"
)
_CK_ENDPOINTS_SECRET_VERSION = "secret_version >= 1"
_CK_ENDPOINTS_SECRET_KEY_ID = "secret_key_id ~ '^[a-z0-9_-]{1,16}$'"
_CK_ENDPOINTS_FAILURES = "consecutive_failures >= 0"
_CK_OUTBOX_CHANNEL = "channel IN ('email', 'webhook')"
_CK_OUTBOX_KIND = f"kind IN ({_KINDS}, 'ping')"
_CK_OUTBOX_STATUS = (
    "status IN ('pending', 'sending', 'sent', 'failed', 'skipped_disabled', 'skipped_preference', 'skipped_limit', "
    "'dead')"
)
_CK_OUTBOX_ERROR = f"last_error_code IS NULL OR last_error_code IN ({_ERRORS})"
_CK_OUTBOX_RECIPIENT = (
    "(channel = 'email' AND recipient_user_id IS NOT NULL AND webhook_endpoint_id IS NULL) OR "
    "(channel = 'webhook' AND webhook_endpoint_id IS NOT NULL AND recipient_user_id IS NULL)"
)
_CK_OUTBOX_PING = "kind <> 'ping' OR channel = 'webhook'"
_CK_OUTBOX_DEDUPE = "dedupe_key ~ '^[a-z0-9][a-z0-9:._-]{0,199}$'"
_CK_OUTBOX_PAYLOAD = "jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 4096"
_CK_OUTBOX_ATTEMPTS = "attempts BETWEEN 0 AND 5"
_CK_OUTBOX_SENT = "(status = 'sent') = (sent_at IS NOT NULL)"
_OUTBOX_DUE = "status IN ('pending', 'sending', 'failed')"
_CK_DELIVERIES_OUTCOME = "outcome IN ('delivered', 'failed')"
_CK_DELIVERIES_ERROR = f"error_code IS NULL OR error_code IN ({_ERRORS})"
_CK_DELIVERIES_STATUS_CODE = "status_code IS NULL OR status_code BETWEEN 100 AND 599"
_CK_DELIVERIES_NUMBERS = "attempt BETWEEN 1 AND 10 AND duration_ms BETWEEN 0 AND 600000"
_CK_DELIVERIES_EVENT = f"event IN ({_KINDS}, 'ping')"
_CK_CHANGES_ACTION = (
    "action IN ('create', 'update_url', 'update_events', 'enable', 'disable', 'rotate', 'delete')"
)
_CK_CHANGES_URL_HOST = "url_host IS NULL OR url_host ~ '^[a-z0-9.:-]{1,253}$'"
_APPEND_ONLY = ("webhook_deliveries", "webhook_changes")
_TRIGGERS = tuple(
    f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
    "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()"
    for table in _APPEND_ONLY
)


def _workspace(uuid: sa.types.TypeEngine) -> sa.Column:
    return sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)


def _user(name: str, uuid: sa.types.TypeEngine, ondelete: str | None, nullable: bool = True) -> sa.Column:
    return sa.Column(name, uuid, sa.ForeignKey("users.id", ondelete=ondelete), nullable=nullable)


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "notification_preferences",
        sa.Column("id", uuid, primary_key=True),
        _workspace(uuid),
        _user("user_id", uuid, "CASCADE", nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False),
        _now("updated_at"),
        sa.UniqueConstraint("workspace_id", "user_id", "kind", name="uq_notification_preferences_user_kind"),
        sa.CheckConstraint(_CK_PREFERENCES_KIND, name="ck_notification_preferences_kind"),
    )
    op.create_index("ix_notification_preferences_user_id", "notification_preferences", ["user_id"])

    op.create_table(
        "webhook_endpoints",
        sa.Column("id", uuid, primary_key=True),
        _workspace(uuid),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("events", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("secret_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("secret_key_id", sa.String(length=16), server_default=sa.text("'k1'"), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
        _user("created_by_user_id", uuid, "SET NULL"),
        _user("updated_by_user_id", uuid, "SET NULL"),
        _now("created_at"),
        _now("updated_at"),
        sa.Column("secret_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "id", name="uq_webhook_endpoints_workspace_id"),
        sa.CheckConstraint(_CK_ENDPOINTS_NAME, name="ck_webhook_endpoints_name"),
        sa.CheckConstraint(_CK_ENDPOINTS_URL, name="ck_webhook_endpoints_url"),
        sa.CheckConstraint(_CK_ENDPOINTS_EVENTS, name="ck_webhook_endpoints_events"),
        sa.CheckConstraint(_CK_ENDPOINTS_SECRET_VERSION, name="ck_webhook_endpoints_secret_version"),
        sa.CheckConstraint(_CK_ENDPOINTS_SECRET_KEY_ID, name="ck_webhook_endpoints_secret_key_id"),
        sa.CheckConstraint(_CK_ENDPOINTS_FAILURES, name="ck_webhook_endpoints_consecutive_failures"),
    )
    op.create_index("ix_webhook_endpoints_created_by_user_id", "webhook_endpoints", ["created_by_user_id"])
    op.create_index("ix_webhook_endpoints_updated_by_user_id", "webhook_endpoints", ["updated_by_user_id"])

    op.create_table(
        "notification_outbox",
        sa.Column("id", uuid, primary_key=True),
        _workspace(uuid),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        _user("recipient_user_id", uuid, "CASCADE"),
        sa.Column("webhook_endpoint_id", uuid, nullable=True),
        sa.Column("project_id", uuid, nullable=True),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        _now("next_attempt_at"),
        sa.Column("last_error_code", sa.String(length=48), nullable=True),
        sa.Column("claim_token", uuid, nullable=True),
        _now("created_at"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "id", name="uq_notification_outbox_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "channel", "recipient_user_id", "webhook_endpoint_id", "dedupe_key",
            name="uq_notification_outbox_dedupe", postgresql_nulls_not_distinct=True,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "webhook_endpoint_id"], ["webhook_endpoints.workspace_id", "webhook_endpoints.id"],
            name="fk_notification_outbox_workspace_webhook_endpoint", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
            name="fk_notification_outbox_workspace_project",
        ),
        sa.CheckConstraint(_CK_OUTBOX_CHANNEL, name="ck_notification_outbox_channel"),
        sa.CheckConstraint(_CK_OUTBOX_KIND, name="ck_notification_outbox_kind"),
        sa.CheckConstraint(_CK_OUTBOX_STATUS, name="ck_notification_outbox_status"),
        sa.CheckConstraint(_CK_OUTBOX_ERROR, name="ck_notification_outbox_error_code"),
        sa.CheckConstraint(_CK_OUTBOX_RECIPIENT, name="ck_notification_outbox_recipient"),
        sa.CheckConstraint(_CK_OUTBOX_PING, name="ck_notification_outbox_ping"),
        sa.CheckConstraint(_CK_OUTBOX_DEDUPE, name="ck_notification_outbox_dedupe_key"),
        sa.CheckConstraint(_CK_OUTBOX_PAYLOAD, name="ck_notification_outbox_payload"),
        sa.CheckConstraint(_CK_OUTBOX_ATTEMPTS, name="ck_notification_outbox_attempts"),
        sa.CheckConstraint(_CK_OUTBOX_SENT, name="ck_notification_outbox_sent_at"),
    )
    op.create_index("ix_notification_outbox_due", "notification_outbox", ["next_attempt_at"],
                    postgresql_where=sa.text(_OUTBOX_DUE))
    op.create_index("ix_notification_outbox_recipient_user_id", "notification_outbox", ["recipient_user_id"],
                    postgresql_where=sa.text("recipient_user_id IS NOT NULL"))
    op.create_index("ix_notification_outbox_webhook_endpoint_id", "notification_outbox",
                    ["workspace_id", "webhook_endpoint_id"], postgresql_where=sa.text("webhook_endpoint_id IS NOT NULL"))
    op.create_index("ix_notification_outbox_project_id", "notification_outbox", ["workspace_id", "project_id"],
                    postgresql_where=sa.text("project_id IS NOT NULL"))

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", uuid, primary_key=True),
        _workspace(uuid),
        sa.Column("webhook_endpoint_id", uuid, nullable=False),
        sa.Column("outbox_id", uuid, nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                  nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_webhook_deliveries_workspace_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "webhook_endpoint_id"], ["webhook_endpoints.workspace_id", "webhook_endpoints.id"],
            name="fk_webhook_deliveries_workspace_webhook_endpoint",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "outbox_id"], ["notification_outbox.workspace_id", "notification_outbox.id"],
            name="fk_webhook_deliveries_workspace_outbox",
        ),
        sa.CheckConstraint(_CK_DELIVERIES_OUTCOME, name="ck_webhook_deliveries_outcome"),
        sa.CheckConstraint(_CK_DELIVERIES_ERROR, name="ck_webhook_deliveries_error_code"),
        sa.CheckConstraint(_CK_DELIVERIES_STATUS_CODE, name="ck_webhook_deliveries_status_code"),
        sa.CheckConstraint(_CK_DELIVERIES_NUMBERS, name="ck_webhook_deliveries_numbers"),
        sa.CheckConstraint(_CK_DELIVERIES_EVENT, name="ck_webhook_deliveries_event"),
    )
    op.create_index("ix_webhook_deliveries_endpoint_created", "webhook_deliveries",
                    ["workspace_id", "webhook_endpoint_id", sa.text("created_at DESC")])
    op.create_index("ix_webhook_deliveries_outbox", "webhook_deliveries", ["workspace_id", "outbox_id"])
    op.create_index("ix_webhook_deliveries_workspace_created", "webhook_deliveries",
                    ["workspace_id", sa.text("created_at DESC")])

    op.create_table(
        "webhook_changes",
        sa.Column("id", uuid, primary_key=True),
        _workspace(uuid),
        sa.Column("webhook_endpoint_id", uuid, nullable=False),
        _user("actor_user_id", uuid, None, nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("url_host", sa.String(length=253), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                  nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_webhook_changes_workspace_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "webhook_endpoint_id"], ["webhook_endpoints.workspace_id", "webhook_endpoints.id"],
            name="fk_webhook_changes_workspace_webhook_endpoint",
        ),
        sa.CheckConstraint(_CK_CHANGES_ACTION, name="ck_webhook_changes_action"),
        sa.CheckConstraint(_CK_CHANGES_URL_HOST, name="ck_webhook_changes_url_host"),
    )
    op.create_index("ix_webhook_changes_endpoint_created", "webhook_changes",
                    ["workspace_id", "webhook_endpoint_id", sa.text("created_at DESC")])
    op.create_index("ix_webhook_changes_actor_user_id", "webhook_changes", ["actor_user_id"])
    for statement in _TRIGGERS:
        op.execute(sa.text(statement))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in _APPEND_ONLY:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}"))
    op.drop_table("webhook_changes")
    op.drop_table("webhook_deliveries")
    op.drop_table("notification_outbox")
    op.drop_table("webhook_endpoints")
    op.drop_table("notification_preferences")
