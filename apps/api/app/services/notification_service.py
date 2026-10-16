"""Notifications (P8.8-A): the outbox, its producers, email preferences and the worker tick.

Enqueue: ``enqueue_notification`` inserts one ``notification_outbox`` row per person (email) and
per enabled webhook endpoint subscribed to the kind, in the caller's transaction, inside a
savepoint: it never raises into the caller (a notification never fails the business operation),
and ``UNIQUE (..., dedupe_key)`` makes a re-run or recompute a no-op. Email recipients are people
only (explicit, unsuspended members whose account is active and whose email address is VERIFIED;
never a legacy client account, a service token or an agent), chosen by ``AUDIENCE`` (ML-write
roles, administrators, or the person who started the run). "Decision waiting" emails collapse to one
per person, workspace and UTC hour (webhooks keep one per event). ``NOTIFICATIONS_ENABLED=false``
enqueues and sends nothing (read at process start: a restart applies a change).

Producers are hooks in the existing services (no new event system): proposals waiting for a
person (agent harness, Jev review items, proposed decision records), terminal training and
scoring jobs (``ml_job_service``), weekly scoring outcomes that need attention, monitoring
windows at ``alert``, and usage at 80% / 100% of a plan limit (the usage tick).

Tick (worker; it shares its loop with ML jobs): ``run_notification_tick`` serves workspaces
round-robin (oldest waiting first) and claims ONE due row at a time (``FOR UPDATE SKIP LOCKED``;
emails before webhooks within a workspace), stamps that row's own lease and a fresh
``claim_token``, commits, sends outside any transaction, and writes the result in its own
transaction only while it still holds the token (a second worker that re-claimed an expired lease
wins; nothing is written twice). It stops after ``TICK_BUDGET_SECONDS``, stops serving a workspace
after ``TICK_BUDGET_PER_WORKSPACE_SECONDS`` / ``TICK_MAX_PER_WORKSPACE`` rows, takes at most
``TICK_MAX_PER_ENDPOINT`` rows of one endpoint, skips endpoints whose breaker is open
(``paused_until``), starts a webhook only with its full deadline left, and hands back (no attempt,
no breaker change) a webhook whose DNS lookup could not start (``dns_busy``) or an email that timed
out under a budget-cut deadline. Email: ``EMAIL_PROVIDER=none`` -> ``skipped_disabled``;
preference off -> ``skipped_preference``; the person's daily cap (per workspace, then overall,
counting rows other workers are sending) -> ``skipped_limit`` (``daily_limit``).
Failures back off 1 min, 5 min, 30 min, 6 h; the fifth failure (or a final error) is ``dead``; a
``ping`` is tried once. Rows older than 24 h are never sent (``expired``), so turning the kill
switch back on sends no backlog. Terminal rows are kept (retention is not addressed).
"""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    BatchPrediction,
    ClientLabUpload,
    ExecutionRequest,
    MlJob,
    NotificationOutbox,
    NotificationPreference,
    Project,
    ScoringScheduleRun,
    User,
    UserRole,
    WebhookDelivery,
    WebhookEndpoint,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.domain import notifications as n
from app.services import notification_delivery as delivery
from app.services.authorization_service import canonical_workspace_role

logger = logging.getLogger("dclab.notifications")

_ROLES = {
    n.AUDIENCE_ML_WRITE: frozenset({WorkspaceRole.WORKSPACE_OWNER, WorkspaceRole.WORKSPACE_ADMIN,
                                    WorkspaceRole.ML_ENGINEER}),
    n.AUDIENCE_ADMINS: frozenset({WorkspaceRole.WORKSPACE_OWNER, WorkspaceRole.WORKSPACE_ADMIN}),
}
_ROLES[n.AUDIENCE_REQUESTER] = _ROLES[n.AUDIENCE_ML_WRITE]  # who can start runs at all
_LINK_RE = re.compile(r"^/[A-Za-z0-9/_-]{0,200}$")


class NotificationError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _enabled() -> bool:
    return bool(get_settings().notifications_enabled)


# --- recipients ---------------------------------------------------------------------------------


def recipient_role(membership_role: str, account_role: str) -> WorkspaceRole | None:
    """THE role check for notifications (recipients and ``applies_to_you`` alike): the canonical
    membership role; a legacy client account receives nothing."""

    if account_role == UserRole.CLIENT_USER.value:
        return None
    try:
        return canonical_workspace_role(WorkspaceRole(membership_role))
    except ValueError:
        return None


def _member_rows(db: Session, workspace_id: UUID, *, user_id: UUID | None = None) -> list[Any]:
    query = (select(WorkspaceMembership.user_id, WorkspaceMembership.role, User.role, User.email_verified_at)
             .join(User, User.id == WorkspaceMembership.user_id)
             .where(WorkspaceMembership.workspace_id == workspace_id, WorkspaceMembership.suspended_at.is_(None),
                    User.is_active.is_(True)))
    if user_id is not None:
        query = query.where(WorkspaceMembership.user_id == user_id)
    return db.execute(query).all()


def audience_members(db: Session, workspace_id: UUID, audience: str) -> list[UUID]:
    """People of the workspace in ``audience`` who may receive email: explicit unsuspended
    memberships of active users with a VERIFIED email address."""

    wanted = _ROLES[audience]
    return sorted({user_id for user_id, role, account_role, verified in _member_rows(db, workspace_id)
                   if verified is not None and recipient_role(role, account_role) in wanted}, key=str)


def _applies(db: Session, user: User, workspace_id: UUID) -> dict[str, bool]:
    rows = _member_rows(db, workspace_id, user_id=user.id)
    role = recipient_role(rows[0][1], rows[0][2]) if rows else None
    return {kind: role in _ROLES[n.AUDIENCE[kind]] for kind in n.KINDS}


# --- enqueue ------------------------------------------------------------------------------------


def _guarded(fn: Callable[..., int]) -> Callable[..., int]:
    """Run a producer inside a savepoint; any failure is logged (type only) and returns 0."""

    @functools.wraps(fn)
    def wrapper(db: Session, *args: Any, **kwargs: Any) -> int:
        if not _enabled():
            return 0
        db.flush()  # the caller's pending rows: their errors stay the caller's, never swallowed here
        try:
            with db.begin_nested():
                return int(fn(db, *args, **kwargs) or 0)
        except Exception as exc:  # noqa: BLE001 - a notification never fails the business operation
            logger.warning("notification not enqueued (%s): %s", fn.__name__, type(exc).__name__)
            return 0

    return wrapper


def _payload(db: Session, *, kind: str, workspace_id: UUID, project_id: UUID | None, subject_kind: str | None,
             subject_id: UUID | None, code: str | None, link: str | None) -> dict[str, Any]:
    if subject_kind is not None and subject_kind not in n.SUBJECT_KINDS:
        raise ValueError("unknown subject kind")
    if code is not None and not n.CODE_RE.fullmatch(code):
        raise ValueError("codes are closed snake_case values")
    if link is not None and not _LINK_RE.fullmatch(link):
        raise ValueError("links are app-relative paths")
    name = None
    if project_id is not None:
        name = db.scalar(select(Project.name).where(Project.workspace_id == workspace_id, Project.id == project_id))
    payload = {
        "kind": kind, "workspace_id": str(workspace_id), "project_id": str(project_id) if project_id else None,
        "project_name": n.clean_untrusted_text(name) or None if name else None,
        "subject": {"kind": subject_kind, "id": str(subject_id) if subject_id else None} if subject_kind else None,
        "code": code, "link": link, "occurred_at": _now().isoformat(),
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    if len(json.dumps(payload).encode()) > n.PAYLOAD_MAX_BYTES:
        raise ValueError("payload too large")
    return payload


def insert_outbox(db: Session, *, kind: str, workspace_id: UUID, dedupe_key: str, payload: dict[str, Any],
                  project_id: UUID | None, recipients: Iterable[UUID] = (),
                  endpoint_ids: Iterable[UUID] = (), email_dedupe_key: str | None = None) -> int:
    """``email_dedupe_key`` (optional) collapses email rows more coarsely than webhook rows."""

    email_key = email_dedupe_key or dedupe_key
    if kind not in n.OUTBOX_KINDS or not all(n.DEDUPE_KEY_RE.fullmatch(key) for key in (dedupe_key, email_key)):
        raise ValueError("unknown kind or malformed dedupe key")
    base = {"workspace_id": workspace_id, "kind": kind, "project_id": project_id, "dedupe_key": dedupe_key,
            "payload": payload, "status": n.STATUS_PENDING, "attempts": 0}
    rows = [{**base, "id": uuid4(), "channel": n.CHANNEL_EMAIL, "recipient_user_id": user_id,
             "webhook_endpoint_id": None, "dedupe_key": email_key}
            for user_id in sorted(set(recipients), key=str) if kind != n.EVENT_PING]
    rows += [{**base, "id": uuid4(), "channel": n.CHANNEL_WEBHOOK, "recipient_user_id": None,
              "webhook_endpoint_id": endpoint_id} for endpoint_id in sorted(set(endpoint_ids), key=str)]
    if not rows:
        return 0
    inserted = db.execute(pg_insert(NotificationOutbox).values(rows).on_conflict_do_nothing()
                          .returning(NotificationOutbox.id)).all()
    return len(inserted)


@_guarded
def enqueue_notification(db: Session, *, kind: str, workspace_id: UUID, dedupe_key: str, audience: str | None = None,
                         recipients: Iterable[UUID | None] = (), exclude_user_id: UUID | None = None,
                         project_id: UUID | None = None, subject_kind: str | None = None,
                         subject_id: UUID | None = None, code: str | None = None, link: str | None = None,
                         email_dedupe_key: str | None = None) -> int:
    """THE way to notify (idempotent per dedupe key; never raises). ``audience`` picks people by
    role; ``recipients`` names them (the requester of a run). Webhook rows go to every enabled
    endpoint of the workspace subscribed to ``kind``. Returns rows inserted."""

    if kind not in n.KINDS:
        raise ValueError("unknown kind")
    people = {user_id for user_id in recipients if user_id is not None}
    if audience is not None:
        people |= set(audience_members(db, workspace_id, audience))
    elif people:  # a named person still has to be an active member (never an ex-member)
        people &= set(audience_members(db, workspace_id, n.AUDIENCE[kind]))
    people.discard(exclude_user_id)
    endpoints = db.scalars(select(WebhookEndpoint.id).where(
        WebhookEndpoint.workspace_id == workspace_id, WebhookEndpoint.enabled.is_(True),
        WebhookEndpoint.events.contains([kind]))).all()
    payload = _payload(db, kind=kind, workspace_id=workspace_id, project_id=project_id, subject_kind=subject_kind,
                       subject_id=subject_id, code=code, link=link)
    return insert_outbox(db, kind=kind, workspace_id=workspace_id, dedupe_key=dedupe_key, payload=payload,
                         project_id=project_id, recipients=people, endpoint_ids=endpoints,
                         email_dedupe_key=email_dedupe_key)


# --- producers ----------------------------------------------------------------------------------


def notify_decision_needed(db: Session, *, workspace_id: UUID, project_id: UUID | None, subject_kind: str,
                           subject_id: UUID | None, dedupe_key: str, exclude_user_id: UUID | None = None) -> int:
    return enqueue_notification(
        db, kind=n.KIND_DECISION_NEEDED, workspace_id=workspace_id, dedupe_key=dedupe_key,
        audience=n.AUDIENCE_ML_WRITE, exclude_user_id=exclude_user_id, project_id=project_id,
        subject_kind=subject_kind, subject_id=subject_id, code="waiting_for_a_person", link="/inbox",
        email_dedupe_key=f"decision_needed:hour:{_now():%Y%m%d%H}")  # one email per person and hour


def _training_requester(db: Session, job: MlJob, upload: ClientLabUpload) -> UUID | None:
    """The person who started a training run; ``None`` when a service token started it."""

    if job.execution_request_id is not None:
        request = db.scalar(select(ExecutionRequest).where(
            ExecutionRequest.workspace_id == job.workspace_id, ExecutionRequest.id == job.execution_request_id))
        if request is None or request.initiated_by_service_token_id is not None:
            return None
        return request.requested_by_user_id
    return upload.requested_by  # the legacy Labs upload path: always a signed-in person


@_guarded
def notify_job_terminal(db: Session, job: MlJob) -> int:
    """A training or scoring job reached ``completed`` / ``failed`` (once per transition)."""

    from app.domain.lab_run_stages import COMPLETED
    from app.domain.ml_jobs import (
        HANDLER_LABS_AUTO_TRAIN,
        HANDLER_MODELS_BATCH_PREDICT,
        JOB_COMPLETED,
        JOB_FAILED,
        JOB_TYPE_AUTO_TRAIN,
    )

    if job.status not in (JOB_COMPLETED, JOB_FAILED) or job.completed_at is None:
        return 0
    failed = job.status == JOB_FAILED
    key = f"job:{job.id}:{job.status}:{int(_aware(job.completed_at).timestamp())}"
    if job.handler_key == HANDLER_MODELS_BATCH_PREDICT:
        row = db.scalar(select(BatchPrediction).where(
            BatchPrediction.workspace_id == job.workspace_id, BatchPrediction.id == job.target_id))
        if row is None:
            return 0
        code = "scoring_failed" if failed else "scoring_completed"
        if failed and row.error_code and n.CODE_RE.fullmatch(row.error_code):
            code = row.error_code
        common = {"workspace_id": job.workspace_id, "project_id": row.project_id, "subject_kind": "batch_prediction",
                  "subject_id": row.id, "code": code,
                  "link": f"/projects/{row.project_id}/predictions" if row.project_id else None}
        scheduled = db.scalar(select(ScoringScheduleRun.id).where(
            ScoringScheduleRun.workspace_id == job.workspace_id, ScoringScheduleRun.batch_prediction_id == row.id))
        if scheduled is not None:
            return enqueue_notification(db, kind=n.KIND_SCHEDULED_SCORING, dedupe_key=key,
                                        audience=n.AUDIENCE_ML_WRITE, **common)
        person = row.requested_by_user_id if row.initiated_by_service_token_id is None else None
        return enqueue_notification(db, kind=n.KIND_RUN_FAILED if failed else n.KIND_RUN_FINISHED, dedupe_key=key,
                                    recipients=[person], **common)
    if job.handler_key == HANDLER_LABS_AUTO_TRAIN or job.job_type == JOB_TYPE_AUTO_TRAIN:
        upload = db.scalar(select(ClientLabUpload).where(
            ClientLabUpload.workspace_id == job.workspace_id, ClientLabUpload.id == job.target_id))
        if upload is None or (not failed and upload.pipeline_status != COMPLETED):
            return 0  # still waiting for an answer, skipped or cancelled: nothing finished
        project_id = job.project_id
        return enqueue_notification(
            db, kind=n.KIND_RUN_FAILED if failed else n.KIND_RUN_FINISHED, workspace_id=job.workspace_id,
            dedupe_key=key, recipients=[_training_requester(db, job, upload)], project_id=project_id,
            subject_kind="experiment" if upload.experiment_id else None, subject_id=upload.experiment_id,
            code="training_failed" if failed else "training_completed",
            link=f"/projects/{project_id}/experiments" if project_id else None)
    return 0


def notify_schedule_outcome(db: Session, *, workspace_id: UUID, project_id: UUID, schedule_id: UUID,
                            planned_for: datetime, outcome: str) -> int:
    """A weekly slot that scored nothing for a reason someone should look at."""

    if outcome in ("queued", "skipped_no_new_file"):
        return 0
    return enqueue_notification(
        db, kind=n.KIND_SCHEDULED_SCORING, workspace_id=workspace_id,
        dedupe_key=f"schedule:{schedule_id}:{int(_aware(planned_for).timestamp())}", audience=n.AUDIENCE_ML_WRITE,
        project_id=project_id, subject_kind="scoring_schedule", subject_id=schedule_id, code=outcome,
        link=f"/projects/{project_id}/predictions")


def notify_drift_alert(db: Session, *, workspace_id: UUID, project_id: UUID, model_version_id: UUID,
                       window_start: datetime, code: str | None) -> int:
    return enqueue_notification(
        db, kind=n.KIND_DRIFT_ALERT, workspace_id=workspace_id,
        dedupe_key=f"drift:{model_version_id}:{_aware(window_start):%Y-%m-%d}", audience=n.AUDIENCE_ML_WRITE,
        project_id=project_id, subject_kind="model_version", subject_id=model_version_id,
        code=code if code and n.CODE_RE.fullmatch(code) else "drift_alert", link=f"/projects/{project_id}/monitoring")


@_guarded
def notify_quota_levels(db: Session, workspace_id: UUID, moment: datetime) -> int:
    """80% / 100% of each enforced plan limit, once per level and period (month; day for runs)."""

    from app.domain.usage import ENFORCED_QUOTAS, QUOTA_PERIODS, WARNING_FRACTION
    from app.services.usage_service import measure_quota, quota_limit

    count = 0
    for key in ENFORCED_QUOTAS:
        limit = quota_limit(db, workspace_id, key).value
        if not limit:
            continue
        used = measure_quota(db, workspace_id, key, moment)
        if used < WARNING_FRACTION * limit:
            continue
        level = 100 if used >= limit else 80
        period = f"{moment:%Y-%m-%d}" if QUOTA_PERIODS[key] == "day" else f"{moment:%Y-%m}"
        count += enqueue_notification(
            db, kind=n.KIND_QUOTA_WARNING, workspace_id=workspace_id, dedupe_key=f"quota:{key}:{level}:{period}",
            audience=n.AUDIENCE_ADMINS, subject_kind="workspace", subject_id=workspace_id, code=f"{key}_{level}",
            link="/settings")
    return count


# --- preferences and unsubscribe ------------------------------------------------------------------


def _email_choices(db: Session, workspace_id: UUID, user_id: UUID) -> dict[str, bool]:
    rows = db.execute(select(NotificationPreference.kind, NotificationPreference.email_enabled).where(
        NotificationPreference.workspace_id == workspace_id, NotificationPreference.user_id == user_id)).all()
    return {**n.DEFAULT_EMAIL, **{kind: bool(enabled) for kind, enabled in rows if kind in n.DEFAULT_EMAIL}}


def set_preference(db: Session, *, workspace_id: UUID, user_id: UUID, kind: str, email_enabled: bool) -> None:
    """Store an override; a value equal to the default removes the row (rows only for overrides)."""

    where = (NotificationPreference.workspace_id == workspace_id, NotificationPreference.user_id == user_id,
             NotificationPreference.kind == kind)
    if email_enabled == n.DEFAULT_EMAIL[kind]:
        db.execute(delete(NotificationPreference).where(*where))
        return
    statement = pg_insert(NotificationPreference).values(
        id=uuid4(), workspace_id=workspace_id, user_id=user_id, kind=kind, email_enabled=email_enabled)
    db.execute(statement.on_conflict_do_update(
        constraint="uq_notification_preferences_user_kind",
        set_={"email_enabled": statement.excluded.email_enabled, "updated_at": func.now()}))


def delivery_state() -> tuple[str, str | None]:
    settings = get_settings()
    if not settings.notifications_enabled:
        return "paused", n.PAUSED_NOTE
    if delivery.email_adapter(settings) is None:
        return "off", n.EMAIL_OFF_NOTE
    return "on", None


def preferences_read(db: Session, *, actor: User, workspace_id: UUID) -> n.NotificationPreferencesRead:
    choices = _email_choices(db, workspace_id, actor.id)
    applies = _applies(db, actor, workspace_id)
    state, note = delivery_state()
    verified = actor.email_verified_at is not None
    return n.NotificationPreferencesRead(
        workspace_id=workspace_id, email_delivery=state, email_verified=verified,
        note=note or (None if verified else n.UNVERIFIED_NOTE),
        items=[n.NotificationPreferenceRead(kind=kind, label=n.LABELS[kind], email_enabled=choices[kind],
                                            default=n.DEFAULT_EMAIL[kind], applies_to_you=applies[kind])
               for kind in n.KINDS])


def save_preferences(db: Session, *, actor: User, workspace_id: UUID, request: n.NotificationPreferencesWrite) -> None:
    kinds = [item.kind for item in request.items]
    if len(set(kinds)) != len(kinds):
        raise NotificationError(422, "duplicate_kind", "each kind may appear once")
    for item in request.items:
        set_preference(db, workspace_id=workspace_id, user_id=actor.id, kind=item.kind,
                       email_enabled=item.email_enabled)


def _link_key() -> bytes:
    settings = get_settings()
    base = settings.auth_token_hash_secret.strip() or settings.jwt_secret
    return hmac.new(base.encode(), b"dclab-notification-unsubscribe-v1", hashlib.sha256).digest()


_TOKEN_BODY = 1 + 16 + 16 + 1 + 8
_TOKEN_CHARS = len(base64.urlsafe_b64encode(bytes(_TOKEN_BODY + 32)).rstrip(b"="))


def unsubscribe_token(user_id: UUID, workspace_id: UUID, kind: str, *, now: datetime | None = None) -> str:
    """Single purpose: turn off one kind's email for one person in one workspace, for 30 days."""

    expires = int((now or _now()).timestamp()) + n.UNSUBSCRIBE_TTL_DAYS * 86400
    body = bytes([1]) + user_id.bytes + workspace_id.bytes + bytes([n.KINDS.index(kind)]) + expires.to_bytes(8, "big")
    tag = hmac.new(_link_key(), body, hashlib.sha256).digest()
    return n.UNSUBSCRIBE_TOKEN_PREFIX + base64.urlsafe_b64encode(body + tag).rstrip(b"=").decode()


def read_unsubscribe_token(token: str, *, now: datetime | None = None) -> tuple[UUID, UUID, str] | None:
    if not isinstance(token, str) or not token.startswith(n.UNSUBSCRIBE_TOKEN_PREFIX):
        return None
    encoded = token[len(n.UNSUBSCRIBE_TOKEN_PREFIX):]
    if len(encoded) != _TOKEN_CHARS or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
        return None
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    if len(raw) != _TOKEN_BODY + 32:
        return None
    body, tag = raw[:_TOKEN_BODY], raw[_TOKEN_BODY:]
    if not hmac.compare_digest(tag, hmac.new(_link_key(), body, hashlib.sha256).digest()):
        return None
    kind_index, expires = body[33], int.from_bytes(body[34:42], "big")
    if body[0] != 1 or kind_index >= len(n.KINDS) or expires < int((now or _now()).timestamp()):
        return None
    return UUID(bytes=body[1:17]), UUID(bytes=body[17:33]), n.KINDS[kind_index]


def unsubscribe(db: Session, token: str) -> str:
    """``POST /v1/notifications/unsubscribe``: the signed token is the only authority (no sign-in);
    it can only turn one kind's email off. A person or workspace that no longer exists: a no-op."""

    parsed = read_unsubscribe_token(token)
    if parsed is None:
        raise NotificationError(400, "invalid_unsubscribe_link", "this unsubscribe link is invalid or has expired")
    user_id, workspace_id, kind = parsed
    try:
        with db.begin_nested():
            set_preference(db, workspace_id=workspace_id, user_id=user_id, kind=kind, email_enabled=False)
    except Exception as exc:  # noqa: BLE001 - e.g. the person was deleted: nothing to turn off
        logger.info("unsubscribe: nothing stored (%s)", type(exc).__name__)
    return kind


# --- rendering ----------------------------------------------------------------------------------

_SUBJECTS = {
    n.KIND_DECISION_NEEDED: "Decision waiting", n.KIND_RUN_FINISHED: "Run finished", n.KIND_RUN_FAILED: "Run failed",
    n.KIND_SCHEDULED_SCORING: "Weekly scoring result", n.KIND_DRIFT_ALERT: "Drift alert",
    n.KIND_QUOTA_WARNING: "Usage warning",
}
_HEADLINES = {
    "waiting_for_a_person": "Something in this workspace is waiting for a person to decide (an agent proposal "
                            "or a proposed decision). Nothing has been applied; open the Inbox to decide.",
    "training_completed": "A training run you started finished.",
    "training_failed": "A training run you started stopped with an error.",
    "scoring_completed": "A scoring you started finished; its predictions are ready.",
    "drift_alert": "Weekly monitoring found an alert-level change in the inputs or predictions of the model in "
                   "use. DCLab did not change or retrain anything.",
}
_QUOTA_LABELS = {"compute_seconds_month": "monthly compute time", "storage_bytes": "storage",
                 "runs_per_day": "daily runs and scorings"}


def _headline(kind: str, code: str) -> str:
    from app.domain.scoring_schedules import OUTCOME_MESSAGES

    if kind == n.KIND_QUOTA_WARNING:
        key, _, level = code.rpartition("_")
        amount = "all of" if level == "100" else "80% or more of"
        return f"This workspace has used {amount} its {_QUOTA_LABELS.get(key, 'plan')} limit. See Settings, Usage."
    if kind == n.KIND_SCHEDULED_SCORING:
        if code == "scoring_completed":
            return "This week's scheduled scoring finished; the weekly list is ready."
        return OUTCOME_MESSAGES.get(code) or "This week's scheduled scoring stopped with an error."
    if kind == n.KIND_DRIFT_ALERT:
        return _HEADLINES["drift_alert"]
    if kind == n.KIND_RUN_FAILED and code not in _HEADLINES:
        return "A scoring you started stopped with an error."
    return _HEADLINES.get(code, n.LABELS[kind] + ".")


def _base_url() -> str:
    base = str(get_settings().public_base_url or "").strip().rstrip("/")
    return base if re.fullmatch(r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~/-]*)?", base) else ""


def render_email(row: NotificationOutbox, to: str) -> delivery.OutgoingEmail:
    """Plain text from fixed templates only: no project or workspace name (both untrusted text a
    workspace member chose) appears in an email, only "a project in your DCLab workspace" and the
    link. No unsubscribe line until its page exists."""

    payload = dict(row.payload or {})
    base = _base_url()
    lines = [_headline(row.kind, str(payload.get("code") or "")), ""]
    if payload.get("project_id"):
        lines.append("This is about a project in your DCLab workspace.")
    if base and payload.get("link"):
        lines.append(f"Open in DCLab: {base}{payload['link']}")
    settings_at = f": {base}/settings" if base else " in DCLab"
    lines += ["", f"You get this because {n.REASONS[row.kind]}. Change this in Settings{settings_at}."]
    return delivery.OutgoingEmail(to=to, subject=f"DCLab: {_SUBJECTS[row.kind]}", text="\n".join(lines) + "\n")


def webhook_request(row: NotificationOutbox, secret: str, *, timestamp: int | None = None) -> tuple[bytes, dict[str, str]]:
    payload = dict(row.payload or {})
    link = payload.pop("link", None)
    data = {key: value for key, value in payload.items() if key not in ("kind", "workspace_id")}
    body_obj: dict[str, Any] = {"id": str(row.id), "event": row.kind, "workspace_id": str(row.workspace_id),
                                "created_at": _aware(row.created_at).isoformat(), "data": data}
    base = _base_url()
    if base and link:
        body_obj["link"] = f"{base}{link}"
    body = json.dumps(body_obj, separators=(",", ":"), sort_keys=True).encode()
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    headers = {"Content-Type": "application/json", "User-Agent": n.WEBHOOK_USER_AGENT, "Connection": "close",
               n.HEADER_EVENT: row.kind, n.HEADER_DELIVERY: str(row.id), n.HEADER_TIMESTAMP: stamp,
               n.HEADER_SIGNATURE: delivery.sign(secret, stamp, body)}
    return body, headers


# --- the worker tick ----------------------------------------------------------------------------


@dataclass
class TickResult:
    claimed: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    dead: int = 0
    deferred: int = 0
    lost: int = 0  # another worker re-claimed the row (expired lease): its result was not written
    stopped_by_budget: bool = False


@dataclass(frozen=True)
class Claim:
    row_id: UUID
    token: UUID
    workspace_id: UUID
    endpoint_id: UUID | None


_DEFAULT = object()
_LIMIT_LOGGED: dict[str, str] = {}  # recipient -> UTC day already logged (one warning per person per day)


def _day_start(moment: datetime) -> datetime:
    moment = moment.astimezone(UTC)
    return datetime(moment.year, moment.month, moment.day, tzinfo=UTC)


def claim_next(db: Session, moment: datetime, *, workspace_id: UUID | None = None,
               skip_endpoints: Iterable[UUID] = (), skip_rows: Iterable[UUID] = (),
               emails_only: bool = False) -> Claim | None:
    """Claim ONE due row (of ``workspace_id`` when given; emails first): its own lease from now and
    a fresh token; commits. Rows of endpoints whose breaker is open are left alone (no attempt)."""

    paused = select(WebhookEndpoint.id).where(
        WebhookEndpoint.workspace_id == NotificationOutbox.workspace_id,
        WebhookEndpoint.id == NotificationOutbox.webhook_endpoint_id,
        WebhookEndpoint.paused_until > moment).exists()
    query = (select(NotificationOutbox)
             .where(NotificationOutbox.status.in_(n.DUE_STATUSES), NotificationOutbox.next_attempt_at <= moment, ~paused)
             .order_by(NotificationOutbox.channel == n.CHANNEL_WEBHOOK, NotificationOutbox.next_attempt_at,
                       NotificationOutbox.id)
             .limit(1).with_for_update(skip_locked=True))
    endpoints, rows = list(skip_endpoints), list(skip_rows)
    if workspace_id is not None:
        query = query.where(NotificationOutbox.workspace_id == workspace_id)
    if emails_only:
        query = query.where(NotificationOutbox.channel == n.CHANNEL_EMAIL)
    if rows:
        query = query.where(NotificationOutbox.id.not_in(rows))
    if endpoints:
        query = query.where(or_(NotificationOutbox.webhook_endpoint_id.is_(None),
                                NotificationOutbox.webhook_endpoint_id.not_in(endpoints)))
    while True:
        row = db.scalar(query)
        if row is None:
            db.commit()
            return None
        if _aware(row.created_at) < moment - timedelta(hours=n.MAX_AGE_HOURS):
            row.status, row.last_error_code, row.claim_token = n.STATUS_DEAD, "expired", None
            db.flush()
            continue
        token = uuid4()
        row.status, row.claim_token = n.STATUS_SENDING, token
        row.next_attempt_at = moment + timedelta(seconds=n.SEND_LEASE_SECONDS)
        claim = Claim(row.id, token, row.workspace_id, row.webhook_endpoint_id)
        db.commit()
        return claim


def _held(db: Session, claim: Claim) -> NotificationOutbox | None:
    """The row, locked, only while this claim still holds it."""

    row = db.scalar(select(NotificationOutbox).where(NotificationOutbox.id == claim.row_id).with_for_update()
                    .execution_options(populate_existing=True))
    if row is None or row.status != n.STATUS_SENDING or row.claim_token != claim.token:
        return None
    return row


def _finish(db: Session, claim: Claim, moment: datetime, *, error: str | None, attempted: bool = True,
            status: str | None = None, defer: bool = False, defer_seconds: int = 60) -> str:
    """Write one row's result in its own transaction, only while holding the claim."""

    row = _held(db, claim)
    if row is None:
        db.commit()  # a delivery log row the caller added stays: the attempt happened
        return "lost"
    row.claim_token = None
    if defer:  # handed back: no attempt used
        row.status, row.next_attempt_at = n.STATUS_PENDING, moment + timedelta(seconds=defer_seconds)
        db.commit()
        return "deferred"
    if status is not None:  # skipped: no attempt was made
        row.status, row.last_error_code = status, error
    else:
        row.attempts = min(n.MAX_ATTEMPTS, row.attempts + (1 if attempted else 0))
        if error is None:
            row.status, row.sent_at, row.last_error_code = n.STATUS_SENT, moment, None
        elif error in n.FINAL_ERROR_CODES or row.attempts >= n.MAX_ATTEMPTS or row.kind == n.EVENT_PING:
            row.status, row.last_error_code = n.STATUS_DEAD, error  # a ping is tried once
        else:
            row.status, row.last_error_code = n.STATUS_FAILED, error
            row.next_attempt_at = moment + timedelta(seconds=n.BACKOFF_SECONDS[max(0, row.attempts - 1)])
    result = row.status
    db.commit()
    return result


def _sent_today(db: Session, user_id: UUID, moment: datetime, *, exclude: UUID,
                workspace_id: UUID | None = None) -> int:
    """Emails to the person today: sent since 00:00 UTC, plus rows other workers are sending now
    (so concurrent workers cannot pass the cap together)."""

    query = select(func.count()).select_from(NotificationOutbox).where(
        NotificationOutbox.channel == n.CHANNEL_EMAIL, NotificationOutbox.recipient_user_id == user_id,
        NotificationOutbox.id != exclude,
        or_(and_(NotificationOutbox.status == n.STATUS_SENT, NotificationOutbox.sent_at >= _day_start(moment)),
            NotificationOutbox.status == n.STATUS_SENDING))
    if workspace_id is not None:
        query = query.where(NotificationOutbox.workspace_id == workspace_id)
    return int(db.scalar(query) or 0)


def _email(db: Session, claim: Claim, adapter: delivery.EmailPort | None, moment: datetime, budget: float) -> str:
    row = db.get(NotificationOutbox, claim.row_id)
    if adapter is None:
        return _finish(db, claim, moment, error=None, status=n.STATUS_SKIPPED_DISABLED)
    if not _email_choices(db, row.workspace_id, row.recipient_user_id).get(row.kind, False):
        return _finish(db, claim, moment, error=None, status=n.STATUS_SKIPPED_PREFERENCE)
    user = db.get(User, row.recipient_user_id)
    if user is not None and user.email_verified_at is None:
        return _finish(db, claim, moment, error="email_unverified")
    member = row.recipient_user_id in audience_members(db, row.workspace_id, n.AUDIENCE[row.kind])
    if user is None or not member or not delivery.valid_address(user.email):
        return _finish(db, claim, moment, error="recipient_unavailable")
    if (_sent_today(db, user.id, moment, exclude=row.id, workspace_id=row.workspace_id)
            >= n.EMAILS_PER_RECIPIENT_PER_WORKSPACE_PER_DAY
            or _sent_today(db, user.id, moment, exclude=row.id) >= n.EMAILS_PER_RECIPIENT_PER_DAY):
        day = f"{moment:%Y-%m-%d}"
        if _LIMIT_LOGGED.get(str(user.id)) != day:  # counted once per person and day
            for key in [key for key, logged in _LIMIT_LOGGED.items() if logged != day]:
                del _LIMIT_LOGGED[key]  # earlier days
            _LIMIT_LOGGED[str(user.id)] = day
            logger.warning("notification email daily limit reached for a recipient (%s)", day)
        return _finish(db, claim, moment, error="daily_limit", status=n.STATUS_SKIPPED_LIMIT)
    message = render_email(row, user.email)
    db.commit()  # no transaction is held during the network call (the row stays ``sending``)
    timeout = min(delivery.EMAIL_TIMEOUT_S, budget)
    try:
        error = adapter.send(message, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - adapters return codes; anything else is internal
        logger.warning("email adapter error: %s", type(exc).__name__)
        error = "internal_error"
    if error == "timeout" and timeout < delivery.EMAIL_TIMEOUT_S:  # cut by the tick budget: not an attempt
        return _finish(db, claim, moment, error=None, defer=True, defer_seconds=0)
    return _finish(db, claim, moment, error=error)


def _breaker(db: Session, workspace_id: UUID, endpoint_id: UUID, moment: datetime, failed: bool) -> None:
    # FOR NO KEY UPDATE: the breaker never changes a key column, so it must not block the FOR KEY
    # SHARE locks business transactions take when they insert rows referencing this endpoint.
    endpoint = db.scalar(select(WebhookEndpoint).where(
        WebhookEndpoint.workspace_id == workspace_id, WebhookEndpoint.id == endpoint_id)
        .with_for_update(key_share=True))
    if endpoint is None:
        return
    if not failed:
        endpoint.consecutive_failures, endpoint.paused_until = 0, None
        return
    endpoint.consecutive_failures += 1
    if endpoint.consecutive_failures >= n.BREAKER_FAILURES:  # open (again, after a failed half-open try)
        endpoint.paused_until = moment + timedelta(seconds=n.BREAKER_PAUSE_SECONDS)


def _webhook(db: Session, claim: Claim, deliver: Callable[..., delivery.WebhookResult], moment: datetime,
             budget: float) -> str:
    from app.services.webhook_service import webhook_secret

    settings = get_settings()
    row = db.get(NotificationOutbox, claim.row_id)
    workspace_id, endpoint_id, kind = row.workspace_id, row.webhook_endpoint_id, row.kind
    endpoint = db.scalar(select(WebhookEndpoint).where(
        WebhookEndpoint.workspace_id == workspace_id, WebhookEndpoint.id == endpoint_id))
    if endpoint is None or not endpoint.enabled:
        return _finish(db, claim, moment, error="endpoint_disabled", attempted=False)
    secret = webhook_secret(endpoint)
    if secret is None:
        return _finish(db, claim, moment, error="not_configured")
    if budget < delivery.WEBHOOK_TOTAL_TIMEOUT_S:  # never a budget-cut deadline: next tick, no attempt
        return _finish(db, claim, moment, error=None, defer=True, defer_seconds=0)
    recent = db.scalar(select(func.count()).select_from(WebhookDelivery).where(
        WebhookDelivery.workspace_id == workspace_id,
        WebhookDelivery.created_at >= moment - timedelta(seconds=60))) or 0
    if recent >= max(1, int(settings.webhook_max_deliveries_per_minute)):
        return _finish(db, claim, moment, error=None, defer=True)
    body, headers = webhook_request(row, secret)
    url, attempt = endpoint.url, row.attempts + 1
    db.commit()
    try:
        outcome = deliver(url, body, headers, ports=delivery.allowed_ports(settings.webhook_allowed_ports),
                          deadline=delivery.WEBHOOK_TOTAL_TIMEOUT_S, dns_key=str(workspace_id))
    except Exception as exc:  # noqa: BLE001 - the transport returns codes; anything else is internal
        logger.warning("webhook transport error: %s", type(exc).__name__)
        outcome = delivery.WebhookResult(None, "internal_error", 0)
    if outcome.error_code == "dns_busy":  # the lookup never ran: not an attempt, no breaker change
        return _finish(db, claim, moment, error=None, defer=True, defer_seconds=0)
    db.add(WebhookDelivery(workspace_id=workspace_id, webhook_endpoint_id=endpoint_id, outbox_id=claim.row_id,
                           event=kind, attempt=min(attempt, 10), outcome="failed" if outcome.error_code else "delivered",
                           status_code=outcome.status_code, error_code=outcome.error_code,
                           duration_ms=min(outcome.duration_ms, 600_000)))
    _breaker(db, workspace_id, endpoint_id, moment, failed=outcome.error_code is not None)
    return _finish(db, claim, moment, error=outcome.error_code)


def _due_workspaces(db: Session, moment: datetime) -> list[UUID]:
    """Workspaces with due rows, the one waiting longest first (the round-robin order)."""

    oldest = func.min(NotificationOutbox.next_attempt_at)
    rows = db.execute(select(NotificationOutbox.workspace_id, oldest)
                      .where(NotificationOutbox.status.in_(n.DUE_STATUSES), NotificationOutbox.next_attempt_at <= moment)
                      .group_by(NotificationOutbox.workspace_id).order_by(oldest).limit(n.TICK_BATCH)).all()
    db.rollback()
    return [row[0] for row in rows]


def run_notification_tick(db: Session, *, now: datetime | None = None, email: Any = _DEFAULT,
                          deliver: Callable[..., delivery.WebhookResult] | None = None,
                          clock: Callable[[], float] = time.monotonic) -> TickResult:
    """One bounded pass (worker only): at most ``TICK_BATCH`` rows and about ``TICK_BUDGET_SECONDS``,
    workspaces served round-robin with their own time budget. ``email`` / ``deliver`` / ``clock``
    are injectable fakes for tests."""

    result = TickResult()
    if not _enabled():
        return result
    settings = get_settings()
    adapter = delivery.email_adapter(settings) if email is _DEFAULT else email
    send_webhook = deliver or delivery.deliver_webhook
    start, began = (now or _now()), clock()
    active = _due_workspaces(db, start)
    rows_of: dict[UUID, int] = {}
    time_of: dict[UUID, float] = {}
    per_endpoint: dict[UUID, int] = {}
    handed_back: set[UUID] = set()
    while active and result.claimed < n.TICK_BATCH:
        for workspace_id in list(active):
            left = n.TICK_BUDGET_SECONDS - (clock() - began)
            if left < n.MIN_SEND_SECONDS or result.claimed >= n.TICK_BATCH:
                result.stopped_by_budget = left < n.MIN_SEND_SECONDS
                return result
            moment = start + timedelta(seconds=clock() - began)  # each row's lease starts when it is claimed
            claim = claim_next(
                db, moment, workspace_id=workspace_id, skip_rows=handed_back,
                skip_endpoints=[e for e, c in per_endpoint.items() if c >= n.TICK_MAX_PER_ENDPOINT],
                emails_only=left < delivery.WEBHOOK_TOTAL_TIMEOUT_S)
            if claim is None:
                active.remove(workspace_id)
                continue
            result.claimed += 1
            if claim.endpoint_id is not None:
                per_endpoint[claim.endpoint_id] = per_endpoint.get(claim.endpoint_id, 0) + 1
            began_row = clock()
            try:
                status = (_webhook(db, claim, send_webhook, moment, left) if claim.endpoint_id is not None
                          else _email(db, claim, adapter, moment, left))
            except Exception as exc:  # noqa: BLE001 - one row never blocks the others (its lease expires)
                logger.warning("notification %s: send failed (%s)", claim.row_id, type(exc).__name__)
                db.rollback()
                status = "error"
            rows_of[workspace_id] = rows_of.get(workspace_id, 0) + 1
            time_of[workspace_id] = time_of.get(workspace_id, 0.0) + (clock() - began_row)
            if (rows_of[workspace_id] >= n.TICK_MAX_PER_WORKSPACE
                    or time_of[workspace_id] >= n.TICK_BUDGET_PER_WORKSPACE_SECONDS):
                active.remove(workspace_id)  # this workspace had its share of this tick
            if status == n.STATUS_SENT:
                result.sent += 1
            elif status == n.STATUS_FAILED:
                result.failed += 1
            elif status == n.STATUS_DEAD:
                result.dead += 1
            elif status in ("deferred", "lost"):
                setattr(result, status, getattr(result, status) + 1)
                handed_back.add(claim.row_id)
            elif status != "error":
                result.skipped += 1
    return result
