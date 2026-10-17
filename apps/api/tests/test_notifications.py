"""P8.8-A: notifications (email + signed webhooks): outbox, producers, preferences, SSRF, signatures."""

from __future__ import annotations

import importlib.util
import ipaddress
import json
import re
import smtplib
import socket
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.db.models import (
    ClientLabUpload,
    MlJob,
    NotificationOutbox,
    NotificationPreference,
    UserRole,
    WebhookChange,
    WebhookDelivery,
    WebhookEndpoint,
    WorkspaceMembership,
)
from app.domain import notifications as n
from app.domain.decision_records import DecisionActor
from app.domain.service_tokens import HUMAN_ONLY_ROUTES, TOKEN_ROUTE_SCOPES
from app.main import app
from app.services import decision_record_service as drs
from app.services import notification_delivery as nd
from app.services import notification_service as ns_
from app.services import webhook_service
from app.services.auth_service import create_access_token, create_user
from app.services.ml_job_service import claim_next_queued_job, create_auto_train_job, execute_job
from app.services.workspace_entitlement_service import set_entitlement
from conftest import browser_login, csrf_headers
from test_data_model_lineage import make_lineage_setup

PASSWORD = "test-password-123"
REPO = Path(__file__).resolve().parents[3]
PUBLIC = [("93.184.216.34", 443, 0, 0, ("93.184.216.34", 443))]
NEW_TABLES = {"notification_preferences", "webhook_endpoints", "notification_outbox", "webhook_deliveries",
              "webhook_changes"}


@pytest.fixture()
def ws(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    alpha = setup["alpha"]

    def user(tag, role, workspace=alpha):
        return create_user(db_session, email=f"notif-{tag}-{uuid4().hex[:6]}@test.invalid", password=PASSWORD,
                           role=role, workspace_id=workspace.id if workspace is not None else None)

    out = SimpleNamespace(
        alpha=alpha, beta=setup["beta"], project=setup["alpha_project"], admin=setup["alpha_admin"],
        beta_admin=setup["beta_admin"], owner=user("owner", UserRole.WORKSPACE_OWNER),
        engineer=user("eng", UserRole.ML_ENGINEER), viewer=user("viewer", UserRole.VIEWER),
        client_user=user("client", UserRole.CLIENT_USER), platform=user("platform", UserRole.DCLAB_ADMIN, None),
        unverified=user("unverified", UserRole.ML_ENGINEER),
    )
    verified = datetime.now(UTC)
    for person in (out.admin, out.beta_admin, out.owner, out.engineer, out.viewer, out.client_user, out.platform):
        person.email_verified_at = verified  # email goes only to verified addresses
    db_session.commit()
    return out


@pytest.fixture()
def secret_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "webhook_secret_key", SecretStr("k" * 40))


@contextmanager
def _browser(user, workspace):
    with TestClient(app) as browser:
        password = PASSWORD if user.email.startswith("notif-") else "test-password"
        assert browser_login(browser, user.email, password).status_code == 200
        browser.headers["X-Workspace-Id"] = str(workspace.id)
        browser.headers.update(csrf_headers(browser))
        yield browser


def _rows(db, **filters) -> list[NotificationOutbox]:
    db.expire_all()
    query = select(NotificationOutbox).order_by(NotificationOutbox.created_at, NotificationOutbox.id)
    for key, value in filters.items():
        query = query.where(getattr(NotificationOutbox, key) == value)
    return list(db.scalars(query))


def _endpoint(db, ws, *, events=("drift_alert",), enabled=True, url="https://hooks.example.com/dclab",
              workspace=None):
    row = WebhookEndpoint(workspace_id=(workspace or ws.alpha).id, name="Ops", url=url, events=list(events),
                          enabled=enabled, secret_version=1, created_by_user_id=ws.owner.id)
    db.add(row)
    db.commit()
    return row


def _webhook_rows(db, ws, endpoint, count, *, kind="drift_alert", prefix="w"):
    for index in range(count):
        ns_.insert_outbox(db, kind=kind, workspace_id=endpoint.workspace_id, dedupe_key=f"{prefix}:{index}",
                          payload={}, project_id=None, endpoint_ids=[endpoint.id])
    db.commit()


class FakeEmail:
    def __init__(self, failing: set[str] = frozenset(), clock=None, cost=0.0):
        self.sent, self.failing, self.clock, self.cost = [], failing, clock, cost

    def send(self, email, *, timeout=10.0):
        if self.clock is not None:
            self.clock.now += self.cost
        self.sent.append(email)
        return "provider_unavailable" if email.to in self.failing else None


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _blackhole(clock, calls):
    """A delivery that hangs until its deadline (simulated time) and times out."""

    def deliver(url, body, headers, *, ports, deadline, dns_key=None):
        calls.append(deadline)
        clock.now += deadline
        return nd.WebhookResult(None, "timeout", int(deadline * 1000))

    return deliver


# --- schema -------------------------------------------------------------------------------------


def test_migration_literals_match_the_domain_and_the_checks_hold(db_session, ws):
    spec = importlib.util.spec_from_file_location(
        "m0083", REPO / "apps/api/alembic/versions/0083_notifications.py")
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    # D7a: 0090 widened the two kind CHECKs (question_asked / question_answered; webhooks unchanged).
    spec90 = importlib.util.spec_from_file_location(
        "m0090", REPO / "apps/api/alembic/versions/0090_business_questions.py")
    mig90 = importlib.util.module_from_spec(spec90)
    spec90.loader.exec_module(mig90)
    assert {name: new for _table, name, new, _old in mig90._KIND_CHECKS} == {
        "ck_notification_preferences_kind": n.CK_NOTIFICATION_PREFERENCES_KIND,
        "ck_notification_outbox_kind": n.CK_NOTIFICATION_OUTBOX_KIND}
    assert [old for *_rest, old in mig90._KIND_CHECKS] == [mig._CK_PREFERENCES_KIND, mig._CK_OUTBOX_KIND]
    pairs = {
        "_CK_ENDPOINTS_NAME": n.CK_WEBHOOK_ENDPOINTS_NAME,
        "_CK_ENDPOINTS_URL": n.CK_WEBHOOK_ENDPOINTS_URL, "_CK_ENDPOINTS_EVENTS": n.CK_WEBHOOK_ENDPOINTS_EVENTS,
        "_CK_ENDPOINTS_SECRET_VERSION": n.CK_WEBHOOK_ENDPOINTS_SECRET_VERSION,
        "_CK_ENDPOINTS_SECRET_KEY_ID": n.CK_WEBHOOK_ENDPOINTS_SECRET_KEY_ID,
        "_CK_ENDPOINTS_FAILURES": n.CK_WEBHOOK_ENDPOINTS_FAILURES,
        "_CK_OUTBOX_CHANNEL": n.CK_NOTIFICATION_OUTBOX_CHANNEL,
        "_CK_OUTBOX_STATUS": n.CK_NOTIFICATION_OUTBOX_STATUS, "_CK_OUTBOX_ERROR": n.CK_NOTIFICATION_OUTBOX_ERROR,
        "_CK_OUTBOX_RECIPIENT": n.CK_NOTIFICATION_OUTBOX_RECIPIENT, "_CK_OUTBOX_PING": n.CK_NOTIFICATION_OUTBOX_PING,
        "_CK_OUTBOX_DEDUPE": n.CK_NOTIFICATION_OUTBOX_DEDUPE, "_CK_OUTBOX_PAYLOAD": n.CK_NOTIFICATION_OUTBOX_PAYLOAD,
        "_CK_OUTBOX_ATTEMPTS": n.CK_NOTIFICATION_OUTBOX_ATTEMPTS, "_CK_OUTBOX_SENT": n.CK_NOTIFICATION_OUTBOX_SENT,
        "_CK_DELIVERIES_ERROR": n.CK_WEBHOOK_DELIVERIES_ERROR, "_CK_DELIVERIES_OUTCOME": n.CK_WEBHOOK_DELIVERIES_OUTCOME,
        "_CK_DELIVERIES_STATUS_CODE": n.CK_WEBHOOK_DELIVERIES_STATUS_CODE,
        "_CK_DELIVERIES_NUMBERS": n.CK_WEBHOOK_DELIVERIES_NUMBERS, "_CK_DELIVERIES_EVENT": n.CK_WEBHOOK_DELIVERIES_EVENT,
        "_CK_CHANGES_ACTION": n.CK_WEBHOOK_CHANGES_ACTION, "_CK_CHANGES_URL_HOST": n.CK_WEBHOOK_CHANGES_URL_HOST,
    }
    assert {name: getattr(mig, name) for name in pairs} == pairs
    for url in ("https://user:pw@hooks.example.com/x", "https://hooks.example.com/a b", "https://hooks.example.com\t/",
                "http://hooks.example.com/", "https://hooks.example.com/x\n"):
        with pytest.raises(IntegrityError, match="ck_webhook_endpoints_url"):
            _endpoint(db_session, ws, url=url)
        db_session.rollback()
    endpoint = _endpoint(db_session, ws, url="https://hooks.example.com/in?team=ml#x")
    assert endpoint.secret_key_id == "k1" and endpoint.consecutive_failures == 0
    ns_.insert_outbox(db_session, kind="ping", workspace_id=ws.alpha.id, dedupe_key="ping:x", payload={},
                      project_id=None, endpoint_ids=[endpoint.id])
    outbox = _rows(db_session)[0]
    db_session.add(WebhookDelivery(workspace_id=ws.alpha.id, webhook_endpoint_id=endpoint.id, outbox_id=outbox.id,
                                   event="ping", attempt=1, outcome="delivered", status_code=204, duration_ms=3))
    db_session.add(WebhookChange(workspace_id=ws.alpha.id, webhook_endpoint_id=endpoint.id, actor_user_id=ws.owner.id,
                                 action="create", url_host="hooks.example.com"))
    db_session.commit()
    for statement in ("UPDATE webhook_deliveries SET status_code = 500", "DELETE FROM webhook_deliveries",
                      "UPDATE webhook_changes SET action = 'delete'", "DELETE FROM webhook_changes"):
        with pytest.raises(DBAPIError):
            db_session.execute(text(statement))
        db_session.rollback()
    for statement, params in (
        ("UPDATE notification_outbox SET recipient_user_id = :u", {"u": ws.owner.id}),  # webhook rows name no person
        ("UPDATE notification_outbox SET status = 'sent'", {}),  # sent needs sent_at
        ("INSERT INTO webhook_deliveries (id, workspace_id, webhook_endpoint_id, outbox_id, event, attempt, outcome, "
         "duration_ms) VALUES (gen_random_uuid(), :w, :e, :o, 'bogus', 1, 'failed', 1)",
         {"w": ws.alpha.id, "e": endpoint.id, "o": outbox.id}),
        ("INSERT INTO webhook_changes (id, workspace_id, webhook_endpoint_id, actor_user_id, action) "
         "VALUES (gen_random_uuid(), :w, :e, :u, 'explode')", {"w": ws.alpha.id, "e": endpoint.id, "u": ws.owner.id}),
    ):
        with pytest.raises(IntegrityError):
            db_session.execute(text(statement), params)
        db_session.rollback()


def test_create_all_matches_the_alembic_catalog_for_the_new_tables(test_engine):
    from _alembic_catalog import dump_constraints_triggers

    def subset(dump):
        return {key: sorted((item for item in dump[key] if item["table"] in NEW_TABLES),
                            key=lambda item: (item["table"], item["name"])) for key in ("check_constraints", "triggers")}

    expected = subset(json.loads((REPO / "apps/api/tests/fixtures/alembic_head_constraints_triggers.json").read_text()))
    assert len(expected["check_constraints"]) == 24 and len(expected["triggers"]) == 2
    assert subset(dump_constraints_triggers(test_engine)) == expected


# --- enqueue, recipients, payload ---------------------------------------------------------------


def test_enqueue_dedupes_and_picks_verified_people_by_role(db_session, ws):
    common = {"workspace_id": ws.alpha.id, "project_id": ws.project.id, "subject_kind": "experiment",
              "subject_id": uuid4(), "code": "waiting_for_a_person", "link": "/inbox"}
    assert ns_.enqueue_notification(db_session, kind="decision_needed", dedupe_key="t:1",
                                    audience=n.AUDIENCE_ML_WRITE, **common) == 3
    assert ns_.enqueue_notification(db_session, kind="decision_needed", dedupe_key="t:1",
                                    audience=n.AUDIENCE_ML_WRITE, **common) == 0  # a re-run: no-op
    db_session.commit()
    people = {row.recipient_user_id for row in _rows(db_session, dedupe_key="t:1")}
    # never a viewer, client, platform staff, another tenant or an unverified address
    assert people == {ws.owner.id, ws.admin.id, ws.engineer.id}
    assert ns_.enqueue_notification(db_session, kind="quota_warning", workspace_id=ws.alpha.id, dedupe_key="t:2",
                                    audience=n.AUDIENCE_ADMINS) == 2
    assert ns_.enqueue_notification(db_session, kind="run_failed", workspace_id=ws.alpha.id, dedupe_key="t:3",
                                    recipients=[ws.viewer.id, ws.client_user.id, ws.beta_admin.id,
                                                ws.unverified.id]) == 0
    assert ns_.enqueue_notification(db_session, kind="run_failed", workspace_id=ws.alpha.id, dedupe_key="t:4",
                                    recipients=[ws.engineer.id]) == 1
    db_session.commit()
    row = _rows(db_session, dedupe_key="t:1")[0]
    assert set(row.payload) == {"kind", "workspace_id", "project_id", "project_name", "subject", "code", "link",
                                "occurred_at"}
    assert row.status == "pending" and row.channel == "email" and row.attempts == 0
    assert ns_.enqueue_notification(db_session, kind="decision_needed", workspace_id=ws.alpha.id,
                                    dedupe_key="Bad Key!", audience=n.AUDIENCE_ML_WRITE) == 0
    assert ns_.enqueue_notification(db_session, kind="decision_needed", workspace_id=ws.alpha.id,
                                    dedupe_key="t:5", audience=n.AUDIENCE_ML_WRITE, code="free text") == 0


def test_hostile_project_names_stay_out_of_the_subject_and_headers():
    evil = "Q3\r\nBcc: victim@example.com‮​\x00 churn\n"
    assert n.clean_untrusted_text(evil) == "Q3 Bcc: victim@example.com churn"
    assert len(n.clean_untrusted_text("x" * 500)) == n.PROJECT_NAME_MAX
    row = NotificationOutbox(id=uuid4(), workspace_id=uuid4(), kind="drift_alert", channel="email",
                             recipient_user_id=uuid4(),
                             payload={"project_id": str(uuid4()), "project_name": evil, "code": "drift_alert"})
    email = ns_.render_email(row, "someone@example.com")
    assert email.subject == "DCLab: Drift alert"  # fixed templates: no untrusted text anywhere in an email
    assert "Q3" not in email.text and "victim" not in email.text and "churn" not in email.text
    assert "This is about a project in your DCLab workspace." in email.text
    assert "\r" not in email.text and "unsubscribe" not in email.text.lower()
    message = nd.build_message("dclab@example.com", email)
    assert message["Subject"] == "DCLab: Drift alert" and message["Bcc"] is None
    assert message["Message-ID"].endswith("@example.com>") and message["Date"]
    assert "You get this because" in email.text and "did not change or retrain" in email.text
    with pytest.raises(ValueError):
        nd.build_message("dclab@example.com", nd.OutgoingEmail("a@b.co\r\nBcc: x@y.z", "s", "t"))


# --- the tick: email ----------------------------------------------------------------------------


def test_tick_sends_skips_backs_off_and_gives_up(db_session, ws, monkeypatch):
    monkeypatch.setattr(get_settings(), "public_base_url", "https://studio.example.com")
    ns_.notify_drift_alert(db_session, workspace_id=ws.alpha.id, project_id=ws.project.id,
                           model_version_id=uuid4(), window_start=datetime(2026, 10, 5, tzinfo=UTC), code="input_drift")
    ns_.set_preference(db_session, workspace_id=ws.alpha.id, user_id=ws.engineer.id, kind="drift_alert",
                       email_enabled=False)
    db_session.commit()
    fake = FakeEmail(failing={ws.admin.email})
    start = datetime.now(UTC)
    result = ns_.run_notification_tick(db_session, now=start, email=fake)
    assert (result.claimed, result.sent, result.failed, result.skipped) == (3, 1, 1, 1)
    by_user = {row.recipient_user_id: row for row in _rows(db_session)}
    assert by_user[ws.owner.id].status == "sent" and by_user[ws.owner.id].sent_at is not None
    assert by_user[ws.engineer.id].status == "skipped_preference" and by_user[ws.engineer.id].attempts == 0
    failed = by_user[ws.admin.id]
    assert (failed.status, failed.attempts, failed.last_error_code) == ("failed", 1, "provider_unavailable")
    assert failed.claim_token is None
    sent = next(email for email in fake.sent if email.to == ws.owner.email)
    assert "https://studio.example.com/projects/" in sent.text and "unsubscribe" not in sent.text.lower()
    gaps = []
    for _ in range(4):
        row = _rows(db_session, recipient_user_id=ws.admin.id)[0]
        due = row.next_attempt_at
        gaps.append(round((due - start).total_seconds()))
        start = due
        ns_.run_notification_tick(db_session, now=due, email=fake)
    final = _rows(db_session, recipient_user_id=ws.admin.id)[0]
    assert gaps[0] >= 59 and [round(g / 60) for g in gaps[1:]] == [5, 30, 360]
    assert (final.status, final.attempts, final.last_error_code) == ("dead", 5, "provider_unavailable")
    ns_.notify_drift_alert(db_session, workspace_id=ws.alpha.id, project_id=ws.project.id,
                           model_version_id=uuid4(), window_start=datetime(2026, 10, 12, tzinfo=UTC), code=None)
    db_session.commit()
    ns_.run_notification_tick(db_session, email=None)  # EMAIL_PROVIDER=none
    assert {row.status for row in _rows(db_session) if row.payload.get("code") == "drift_alert"} == {
        "skipped_disabled"}


def test_unverified_people_get_no_email_and_the_daily_caps_hold(db_session, ws, monkeypatch):
    ns_.insert_outbox(db_session, kind="run_failed", workspace_id=ws.alpha.id, dedupe_key="u:1", payload={},
                      project_id=None, recipients=[ws.unverified.id])  # e.g. verified at enqueue, not any more
    db_session.add(WorkspaceMembership(workspace_id=ws.beta.id, user_id=ws.owner.id, role="ml_engineer"))
    for index in range(n.EMAILS_PER_RECIPIENT_PER_WORKSPACE_PER_DAY):
        ns_.insert_outbox(db_session, kind="run_failed", workspace_id=ws.alpha.id, dedupe_key=f"cap:{index}",
                          payload={}, project_id=None, recipients=[ws.owner.id])
    db_session.execute(update(NotificationOutbox).where(NotificationOutbox.dedupe_key.like("cap:%"))
                       .values(status="sent", sent_at=datetime.now(UTC), attempts=1))
    for workspace, key in ((ws.alpha, "cap:over"), (ws.beta, "cap:beta")):
        ns_.insert_outbox(db_session, kind="run_failed", workspace_id=workspace.id, dedupe_key=key, payload={},
                          project_id=None, recipients=[ws.owner.id])
    db_session.commit()
    fake = FakeEmail()
    ns_.run_notification_tick(db_session, email=fake)
    assert [email.to for email in fake.sent] == [ws.owner.email]  # the other workspace still reaches them
    unverified = _rows(db_session, dedupe_key="u:1")[0]
    assert (unverified.status, unverified.last_error_code) == ("dead", "email_unverified")
    over = _rows(db_session, dedupe_key="cap:over")[0]
    assert (over.status, over.last_error_code, over.attempts) == ("skipped_limit", "daily_limit", 0)
    assert _rows(db_session, dedupe_key="cap:beta")[0].status == "sent"
    # The overall cap counts rows another worker is sending right now (12 = 11 sent + 1 in flight).
    monkeypatch.setattr(n, "EMAILS_PER_RECIPIENT_PER_WORKSPACE_PER_DAY", 100)
    monkeypatch.setattr(n, "EMAILS_PER_RECIPIENT_PER_DAY", 12)
    for key in ("cap:inflight", "cap:last"):
        ns_.insert_outbox(db_session, kind="run_failed", workspace_id=ws.alpha.id, dedupe_key=key, payload={},
                          project_id=None, recipients=[ws.owner.id])
    db_session.execute(update(NotificationOutbox).where(NotificationOutbox.dedupe_key == "cap:inflight").values(
        status="sending", claim_token=uuid4(), next_attempt_at=datetime.now(UTC) + timedelta(minutes=2)))
    db_session.commit()
    ns_.run_notification_tick(db_session, email=fake)
    assert _rows(db_session, dedupe_key="cap:last")[0].status == "skipped_limit" and len(fake.sent) == 1


def test_kill_switch_and_old_rows_are_never_sent(db_session, ws, monkeypatch):
    monkeypatch.setattr(get_settings(), "notifications_enabled", False)
    assert ns_.notify_decision_needed(db_session, workspace_id=ws.alpha.id, project_id=None, subject_kind="decision",
                                      subject_id=uuid4(), dedupe_key="k:1") == 0
    assert ns_.run_notification_tick(db_session, email=FakeEmail()).claimed == 0
    monkeypatch.setattr(get_settings(), "notifications_enabled", True)
    assert ns_.notify_decision_needed(db_session, workspace_id=ws.alpha.id, project_id=None, subject_kind="decision",
                                      subject_id=uuid4(), dedupe_key="k:2") == 3
    db_session.execute(update(NotificationOutbox).values(created_at=datetime.now(UTC) - timedelta(hours=25)))
    db_session.commit()
    fake = FakeEmail()
    assert ns_.run_notification_tick(db_session, email=fake).claimed == 0 and fake.sent == []
    assert {(row.status, row.last_error_code) for row in _rows(db_session)} == {("dead", "expired")}


# --- the tick: fairness, breaker, two workers ---------------------------------------------------


def test_a_blackholed_endpoint_cannot_take_more_than_its_workspace_budget(db_session, ws, secret_key):
    endpoint = _endpoint(db_session, ws)
    _webhook_rows(db_session, ws, endpoint, 5)
    clock, calls = FakeClock(), []
    began = clock.now
    ns_.run_notification_tick(db_session, email=None, deliver=_blackhole(clock, calls), clock=clock)
    assert clock.now - began <= n.TICK_BUDGET_SECONDS
    assert calls == [n.WEBHOOK_TOTAL_TIMEOUT_S]  # one full-deadline attempt, then the workspace's share is used
    rows = _rows(db_session, channel="webhook")
    assert sorted(row.status for row in rows) == ["failed", "pending", "pending", "pending", "pending"]
    assert sum(row.attempts for row in rows) == 1  # untouched rows used no attempt


def test_pings_do_not_starve_email_rows_and_pings_are_tried_once(db_session, ws, secret_key):
    endpoint = _endpoint(db_session, ws)
    _webhook_rows(db_session, ws, endpoint, 5, kind="ping", prefix="ping")
    ns_.notify_drift_alert(db_session, workspace_id=ws.alpha.id, project_id=ws.project.id, model_version_id=uuid4(),
                           window_start=datetime(2026, 10, 5, tzinfo=UTC), code="input_drift")
    db_session.commit()
    clock, calls = FakeClock(), []
    fake = FakeEmail(clock=clock, cost=0.5)
    began = clock.now
    ns_.run_notification_tick(db_session, email=fake, deliver=_blackhole(clock, calls), clock=clock)
    assert len(fake.sent) == 3 and clock.now - began <= n.TICK_BUDGET_SECONDS  # emails first; the tick returns
    assert calls == [n.WEBHOOK_TOTAL_TIMEOUT_S]
    pings = _rows(db_session, kind="ping")
    assert sorted((row.status, row.attempts) for row in pings) == [("dead", 1)] + [("pending", 0)] * 4


def test_the_breaker_opens_after_five_failures_and_closes_on_success(db_session, ws, secret_key):
    endpoint = _endpoint(db_session, ws)
    _webhook_rows(db_session, ws, endpoint, 6)
    outcomes = []

    def deliver(url, body, headers, *, ports, deadline, dns_key=None):
        outcomes.append(url)
        return nd.WebhookResult(503, "http_error", 2) if len(outcomes) <= 5 else nd.WebhookResult(204, None, 2)

    now = datetime.now(UTC)
    first = ns_.run_notification_tick(db_session, now=now, email=None, deliver=deliver)
    db_session.refresh(endpoint)
    assert first.failed == 5 and endpoint.consecutive_failures == 5 and endpoint.paused_until is not None
    later = now + timedelta(minutes=5)  # retries are due, the breaker is open: nothing is claimed
    assert ns_.run_notification_tick(db_session, now=later, email=None, deliver=deliver).claimed == 0
    assert len(outcomes) == 5
    reopened = now + timedelta(seconds=n.BREAKER_PAUSE_SECONDS + 120)  # the pause counts from the 5th failure
    after = ns_.run_notification_tick(db_session, now=reopened, email=None, deliver=deliver)
    db_session.refresh(endpoint)
    assert after.sent >= 1 and endpoint.consecutive_failures == 0 and endpoint.paused_until is None


def test_slow_endpoints_in_one_workspace_cannot_delay_another_workspace(db_session, ws, secret_key):
    slow = [_endpoint(db_session, ws, url=f"https://slow{index}.example.com/") for index in range(5)]
    for index, endpoint in enumerate(slow):
        _webhook_rows(db_session, ws, endpoint, 1, prefix=f"slow{index}")
    fast = _endpoint(db_session, ws, url="https://fast.example.com/", workspace=ws.beta)
    _webhook_rows(db_session, ws, fast, 1, prefix="fast")
    clock, calls, served_at = FakeClock(), [], {}
    began = clock.now
    blackhole = _blackhole(clock, calls)

    def deliver(url, body, headers, *, ports, deadline, dns_key=None):
        served_at[url] = clock.now - began
        if "slow" in url:
            return blackhole(url, body, headers, ports=ports, deadline=deadline)
        return nd.WebhookResult(204, None, 1)

    ns_.run_notification_tick(db_session, email=None, deliver=deliver, clock=clock)
    assert served_at["https://fast.example.com/"] <= n.WEBHOOK_TOTAL_TIMEOUT_S  # after at most one slow send
    assert len(calls) == 1 and _rows(db_session, webhook_endpoint_id=fast.id)[0].status == "sent"


def test_no_send_starts_on_a_cut_deadline_and_busy_dns_is_not_an_attempt(db_session, ws, secret_key):
    hook = _endpoint(db_session, ws, workspace=ws.beta)
    ns_.insert_outbox(db_session, kind="run_failed", workspace_id=ws.alpha.id, dedupe_key="slow-mail", payload={},
                      project_id=None, recipients=[ws.owner.id])  # waiting longest: served first
    db_session.commit()
    _webhook_rows(db_session, ws, hook, 1)
    clock, calls = FakeClock(), []
    ns_.run_notification_tick(db_session, email=FakeEmail(clock=clock, cost=6.0), deliver=_blackhole(clock, calls),
                              clock=clock)
    db_session.refresh(hook)
    waiting = _rows(db_session, webhook_endpoint_id=hook.id)[0]
    assert calls == [] and (waiting.status, waiting.attempts) == ("pending", 0) and hook.consecutive_failures == 0

    class TimingOut:
        def send(self, email, *, timeout=10.0):
            return "timeout"

    ns_.insert_outbox(db_session, kind="run_failed", workspace_id=ws.alpha.id, dedupe_key="cut", payload={},
                      project_id=None, recipients=[ws.owner.id])
    db_session.commit()
    claim = ns_.claim_next(db_session, datetime.now(UTC), emails_only=True, skip_rows=[waiting.id])
    assert ns_._email(db_session, claim, TimingOut(), datetime.now(UTC), 3.0) == "deferred"
    cut = _rows(db_session, dedupe_key="cut")[0]
    assert (cut.status, cut.attempts, cut.last_error_code) == ("pending", 0, None)

    def busy(url, body, headers, *, ports, deadline, dns_key=None):
        return nd.WebhookResult(None, "dns_busy", 0)

    ns_.run_notification_tick(db_session, email=None, deliver=busy)
    db_session.refresh(hook)
    waiting = _rows(db_session, webhook_endpoint_id=hook.id)[0]
    assert (waiting.status, waiting.attempts) == ("pending", 0) and hook.consecutive_failures == 0
    assert db_session.scalar(select(WebhookDelivery.id)) is None


def test_a_saturated_dns_pool_and_a_workspace_lookup_in_flight_are_busy_not_attempts():
    url = "https://hooks.example.com/in"
    FakeConnection.made, FakeConnection.status = [], 200
    gate, second_gate = threading.Event(), threading.Event()
    blockers = [nd._DNS_POOL.submit(gate.wait, 5) for _ in range(4)]  # every lookup thread is taken
    try:
        busy = nd.deliver_webhook(url, b"{}", {}, ports=PORTS, deadline=0.2, resolver=_resolver(PUBLIC),
                                  connection_factory=FakeConnection, dns_key="w-saturated")
        assert busy.error_code == "dns_busy"  # the lookup never started: cancelled, no attempt
    finally:
        gate.set()
        for blocker in blockers:
            blocker.result(timeout=5)

    def blocking(*_args):
        second_gate.wait(5)
        return PUBLIC

    results = []
    first = threading.Thread(target=lambda: results.append(nd.deliver_webhook(
        url, b"{}", {}, ports=PORTS, deadline=0.5, resolver=blocking, connection_factory=FakeConnection,
        dns_key="w-1")))
    first.start()
    try:
        time.sleep(0.1)  # its lookup is running
        again = nd.deliver_webhook(url, b"{}", {}, ports=PORTS, deadline=0.5, resolver=_resolver(PUBLIC),
                                   connection_factory=FakeConnection, dns_key="w-1")
        other = nd.deliver_webhook(url, b"{}", {}, ports=PORTS, deadline=0.5, resolver=_resolver(PUBLIC),
                                   connection_factory=FakeConnection, dns_key="w-2")
        assert again.error_code == "dns_busy" and other.error_code is None  # one lookup per workspace
    finally:
        second_gate.set()
        first.join(timeout=5)
    assert results and results[0].error_code in ("timeout", None)


def test_pings_are_capped_per_endpoint_per_hour(db_session, ws, secret_key):
    endpoint = _endpoint(db_session, ws)
    _webhook_rows(db_session, ws, endpoint, n.PINGS_PER_ENDPOINT_PER_HOUR, kind="ping", prefix="ping:old")
    with pytest.raises(ns_.NotificationError) as refused:
        webhook_service.send_test(db_session, actor=ws.owner, workspace_id=ws.alpha.id, webhook_id=endpoint.id)
    assert refused.value.code == "ping_limit_reached"


def test_two_workers_never_send_or_write_one_row_twice(db_session, ws, test_engine):
    ns_.insert_outbox(db_session, kind="run_failed", workspace_id=ws.alpha.id, dedupe_key="two:1", payload={},
                      project_id=None, recipients=[ws.owner.id])
    db_session.commit()
    other = sessionmaker(bind=test_engine)()
    try:
        now = datetime.now(UTC)
        first = ns_.claim_next(db_session, now)
        assert first is not None and ns_.claim_next(other, now) is None  # its lease is live: skipped
        expired = now + timedelta(seconds=n.SEND_LEASE_SECONDS + 1)  # worker A stalled past its lease
        second = ns_.claim_next(other, expired)
        assert second is not None and second.row_id == first.row_id and second.token != first.token
        fake = FakeEmail()
        assert ns_._email(other, second, fake, expired, 10.0) == "sent"
        assert ns_._finish(db_session, first, now, error=None) == "lost"  # A's late result is not written
        row = _rows(db_session, dedupe_key="two:1")[0]
        assert (row.status, row.attempts, len(fake.sent)) == ("sent", 1, 1)
    finally:
        other.close()


# --- producers ----------------------------------------------------------------------------------


def _upload(db, ws, requester):
    row = ClientLabUpload(workspace_id=ws.alpha.id, requested_by=requester.id, category="Revenue",
                          original_filename="upload.csv", stored_path="/tmp/p88a.csv", kind="table_file",
                          record_count=3, fields_noticed=[], has_named_fields=True, pipeline_status="queued",
                          client_status="queued")
    db.add(row)
    db.flush()
    return row


def _run(db, ws, requester, *, succeed=True, max_attempts=3) -> MlJob:
    upload = _upload(db, ws, requester)
    job = create_auto_train_job(db, workspace_id=ws.alpha.id, upload_id=upload.id, project_id=ws.project.id,
                                max_attempts=max_attempts)
    db.commit()
    claimed = claim_next_queued_job(db, job_id=job.id)
    db.commit()

    def runner(session, target_id):
        if not succeed:
            raise RuntimeError("engine said no")
        session.get(ClientLabUpload, target_id).pipeline_status = "completed"

    return execute_job(db, claimed, runner=runner)


def test_run_end_notifies_the_requester_once_and_never_fails_the_job(db_session, ws, monkeypatch):
    finished = _run(db_session, ws, ws.engineer)
    assert finished.status == "completed"
    rows = _rows(db_session, kind="run_finished")
    assert [(row.recipient_user_id, row.payload["code"]) for row in rows] == [(ws.engineer.id, "training_completed")]
    assert ns_.notify_job_terminal(db_session, finished) == 0  # the same transition: deduped
    failed = _run(db_session, ws, ws.owner, succeed=False, max_attempts=1)
    assert failed.status == "failed"
    assert [row.recipient_user_id for row in _rows(db_session, kind="run_failed")] == [ws.owner.id]
    assert "engine said no" not in json.dumps([row.payload for row in _rows(db_session)])  # no free text

    def broken(*_args, **_kwargs):
        raise RuntimeError("outbox down")

    monkeypatch.setattr(ns_, "insert_outbox", broken)
    again = _run(db_session, ws, ws.engineer)
    db_session.expire_all()
    assert again.status == "completed" and db_session.get(MlJob, again.id).status == "completed"
    assert len(_rows(db_session, kind="run_finished")) == 1


def test_decision_emails_collapse_per_hour_webhooks_do_not_and_an_outbox_failure_is_harmless(
        db_session, ws, monkeypatch):
    endpoint = _endpoint(db_session, ws, events=("decision_needed",))
    actor = DecisionActor.human(ws.owner)
    monkeypatch.setattr(ns_, "_now", lambda: datetime(2026, 10, 16, 12, 30, tzinfo=UTC))  # one UTC hour
    original = ns_.insert_outbox

    def propose(rationale):
        record = drs.record(db_session, workspace_id=ws.alpha.id, project_id=ws.project.id, actor=actor,
                            decision_type="experiment_accepted", subject_kind="project", rationale=rationale)
        db_session.commit()
        return record

    first = propose("ship it?")
    emails = _rows(db_session, kind="decision_needed", channel="email")
    assert {row.recipient_user_id for row in emails} == {ws.admin.id, ws.engineer.id}  # not the proposer
    assert emails[0].payload["subject"] == {"kind": "decision", "id": str(first.id)}
    assert emails[0].dedupe_key.startswith("decision_needed:hour:") and "ship it" not in json.dumps(emails[0].payload)
    monkeypatch.setattr(ns_, "insert_outbox", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert propose("again").id is not None  # the business operation never fails
    monkeypatch.setattr(ns_, "insert_outbox", original)
    propose("third")
    assert len(_rows(db_session, kind="decision_needed", channel="email")) == 2  # one email per person and hour
    hooks = _rows(db_session, kind="decision_needed", channel="webhook")
    assert len(hooks) == 2 and {row.webhook_endpoint_id for row in hooks} == {endpoint.id}


def test_schedule_drift_and_quota_producers_enqueue_once(db_session, ws):
    schedule_id, slot = uuid4(), datetime(2026, 10, 5, 9, tzinfo=UTC)
    common = {"workspace_id": ws.alpha.id, "project_id": ws.project.id, "schedule_id": schedule_id, "planned_for": slot}
    assert ns_.notify_schedule_outcome(db_session, outcome="skipped_no_new_file", **common) == 0
    assert ns_.notify_schedule_outcome(db_session, outcome="skipped_no_approval", **common) == 3
    assert ns_.notify_schedule_outcome(db_session, outcome="skipped_no_approval", **common) == 0
    version, window = uuid4(), datetime(2026, 10, 5, tzinfo=UTC)
    drift = {"workspace_id": ws.alpha.id, "project_id": ws.project.id, "model_version_id": version,
             "window_start": window, "code": "prediction_drift"}
    assert ns_.notify_drift_alert(db_session, **drift) == 3 and ns_.notify_drift_alert(db_session, **drift) == 0
    set_entitlement(db_session, ws.alpha.id, "runs_per_day", 1, source="test")
    db_session.add(MlJob(workspace_id=ws.alpha.id, job_type="batch_predict", handler_key="models.batch_predict",
                         target_id=uuid4(), status="queued", payload={}, attempts=0, max_attempts=3))
    db_session.flush()
    now = datetime.now(UTC)
    assert ns_.notify_quota_levels(db_session, ws.alpha.id, now) == 2  # owner + administrator
    assert ns_.notify_quota_levels(db_session, ws.alpha.id, now) == 0
    db_session.commit()
    quota = _rows(db_session, kind="quota_warning")
    assert {row.recipient_user_id for row in quota} == {ws.owner.id, ws.admin.id}
    assert quota[0].payload["code"] == "runs_per_day_100"


# --- preferences and unsubscribe ----------------------------------------------------------------

PREFS = "/v1/me/notification-preferences"


def test_preferences_are_your_own_and_say_when_email_is_off(db_session, ws):
    with _browser(ws.engineer, ws.alpha) as browser:
        body = browser.get(PREFS).json()
        assert body["email_delivery"] == "off" and body["email_verified"] and "EMAIL_PROVIDER=none" in body["note"]
        items = {item["kind"]: item for item in body["items"]}
        assert items["drift_alert"]["email_enabled"] and not items["run_finished"]["email_enabled"]
        assert not items["quota_warning"]["applies_to_you"] and items["decision_needed"]["applies_to_you"]
        saved = browser.put(PREFS, json={"items": [{"kind": "run_finished", "email_enabled": True},
                                                   {"kind": "drift_alert", "email_enabled": False}]})
        assert saved.status_code == 200, saved.text
        assert {i["kind"]: i["email_enabled"] for i in saved.json()["items"]}["run_finished"] is True
        assert browser.put(PREFS, json={"items": [{"kind": "drift_alert", "email_enabled": True}]}).status_code == 200
        bad = browser.put(PREFS, json={"items": [{"kind": "everything", "email_enabled": False}]})
        assert bad.status_code == 422
        assert browser.get(PREFS, headers={"X-Workspace-Id": str(ws.beta.id)}).status_code == 403
    rows = db_session.scalars(select(NotificationPreference)).all()
    assert [(row.user_id, row.kind, row.email_enabled) for row in rows] == [(ws.engineer.id, "run_finished", True)]
    with _browser(ws.viewer, ws.alpha) as browser:
        assert not any(item["applies_to_you"] for item in browser.get(PREFS).json()["items"])
    with _browser(ws.unverified, ws.alpha) as browser:
        assert browser.get(PREFS).json()["email_verified"] is False
    bearer = {"Authorization": f"Bearer {create_access_token(ws.engineer)}", "X-Workspace-Id": str(ws.alpha.id)}
    with TestClient(app) as client:
        assert client.get(PREFS, headers=bearer).json()["error"]["code"] == "session_required"


def test_unsubscribe_link_turns_one_kind_off_and_rejects_anything_else(client, db_session, ws):
    token = ns_.unsubscribe_token(ws.engineer.id, ws.alpha.id, "drift_alert")
    response = client.post("/v1/notifications/unsubscribe", json={"token": token})
    assert response.status_code == 200 and response.json() == {"unsubscribed": True, "kind": "drift_alert"}
    assert client.post("/v1/notifications/unsubscribe", json={"token": token}).status_code == 200  # idempotent
    assert db_session.scalar(select(NotificationPreference.email_enabled)) is False
    expired = ns_.unsubscribe_token(ws.engineer.id, ws.alpha.id, "run_failed",
                                    now=datetime.now(UTC) - timedelta(days=31))
    flipped = token[:-3] + ("A" if token[-3] != "A" else "B") + token[-2:]
    for bad in (expired, flipped, "dclab_unsub_" + "A" * 99, "x" * 40):
        error = client.post("/v1/notifications/unsubscribe", json={"token": bad})
        assert error.status_code == 400 and error.json()["error"]["code"] == "invalid_unsubscribe_link", bad


# --- webhooks: API ------------------------------------------------------------------------------

HOOKS = "/v1/workspaces/{}/webhooks"


def _changes(db, endpoint_id):
    db.expire_all()
    return [(row.action, row.url_host) for row in db.scalars(
        select(WebhookChange).where(WebhookChange.webhook_endpoint_id == endpoint_id)
        .order_by(WebhookChange.created_at, WebhookChange.id))]


def test_webhook_secret_is_shown_once_rotates_is_recorded_and_owners_only(db_session, ws, secret_key, monkeypatch):
    monkeypatch.setattr(webhook_service, "_now", lambda: datetime(2026, 10, 16, 12, 0, 30, tzinfo=UTC))
    base = HOOKS.format(ws.alpha.id)
    body = {"name": "Ops", "url": "https://hooks.example.com/dclab?team=ml", "events": ["drift_alert", "run_failed"]}
    with _browser(ws.owner, ws.alpha) as browser:
        created = browser.post(base, json=body, headers={"Idempotency-Key": "hook-1"})
        assert created.status_code == 201, created.text
        hook = created.json()
        assert hook["secret"].startswith("dclab_whsec_") and hook["events"] == ["run_failed", "drift_alert"]
        replay = browser.post(base, json=body, headers={"Idempotency-Key": "hook-1"})
        assert replay.json()["secret"] is None and replay.headers["Idempotent-Replayed"] == "true"
        listed = browser.get(base).json()
        assert len(listed) == 1 and "secret" not in listed[0] and listed[0]["paused_until"] is None
        rotate = f"{base}/{hook['id']}/rotate-secret"
        rotated = browser.post(rotate, headers={"Idempotency-Key": "rot-1"}).json()
        assert rotated["secret_version"] == 2 and rotated["secret"] not in (None, hook["secret"])
        again = browser.post(rotate, headers={"Idempotency-Key": "rot-1"}).json()
        assert again["secret"] is None and again["secret_version"] == 2
        row = db_session.get(WebhookEndpoint, UUID(hook["id"]))
        db_session.refresh(row)
        assert webhook_service.webhook_secret(row) == rotated["secret"] and row.secret_key_id == "k1"
        assert browser.patch(f"{base}/{hook['id']}", json={"enabled": False}).json()["enabled"] is False
        assert browser.post(f"{base}/{hook['id']}/test").json()["error"]["code"] == "webhook_disabled"
        browser.patch(f"{base}/{hook['id']}", json={"enabled": True, "url": "https://ops.example.org/in?k=secret"})
        tested = browser.post(f"{base}/{hook['id']}/test")
        assert tested.status_code == 202 and browser.post(f"{base}/{hook['id']}/test").json()["outbox_id"] == \
            tested.json()["outbox_id"]
        assert _changes(db_session, UUID(hook["id"])) == [
            ("create", "hooks.example.com"), ("rotate", None), ("disable", None), ("update_url", "ops.example.org"),
            ("enable", None)]
        for url in ("http://hooks.example.com/x", "https://10.0.0.5/x", "https://user:pw@hooks.example.com/",
                    "https://hooks.example.com:8443/x", "https://169.254.169.254/latest", "https://[fe80::1%25eth0]/"):
            refused = browser.post(base, json={**body, "url": url}, headers={"Idempotency-Key": uuid4().hex})
            assert refused.status_code == 422, url
        for index in range(4):
            browser.post(base, json={**body, "name": f"n{index}"}, headers={"Idempotency-Key": f"more-{index}"})
        full = browser.post(base, json=body, headers={"Idempotency-Key": "sixth"})
        assert full.json()["error"]["code"] == "webhook_limit_reached"
        assert browser.get(f"{base}/{uuid4()}/deliveries").status_code == 404
    for user in (ws.engineer, ws.viewer, ws.client_user):
        with _browser(user, ws.alpha) as browser:
            assert browser.get(base).json()["error"]["code"] == "webhooks_not_permitted", user.email
            assert browser.post(base, json=body, headers={"Idempotency-Key": "x"}).status_code == 403
    foreign = f"{HOOKS.format(ws.beta.id)}/{hook['id']}"
    with _browser(ws.beta_admin, ws.beta) as browser:  # another tenant: path mismatch and foreign ids are 404
        assert browser.get(base).status_code == 404
        assert browser.get(f"{foreign}/deliveries").status_code == 404
        assert browser.patch(foreign, json={"enabled": False}).status_code == 404
        assert browser.post(f"{foreign}/rotate-secret", headers={"Idempotency-Key": "r-x"}).status_code == 404
        assert browser.post(f"{foreign}/test").status_code == 404
    bearer = {"Authorization": f"Bearer {create_access_token(ws.owner)}", "X-Workspace-Id": str(ws.alpha.id)}
    with TestClient(app) as client:
        assert client.get(base, headers=bearer).json()["error"]["code"] == "session_required"


def test_a_demoted_admin_cannot_replay_an_idempotency_key(db_session, ws, secret_key):
    base = HOOKS.format(ws.alpha.id)
    body = {"name": "Ops", "url": "https://hooks.example.com/", "events": ["drift_alert"]}
    with _browser(ws.admin, ws.alpha) as browser:
        hook = browser.post(base, json=body, headers={"Idempotency-Key": "adm-1"}).json()
        assert browser.post(f"{base}/{hook['id']}/rotate-secret", headers={"Idempotency-Key": "adm-r"}).status_code == 200
        db_session.execute(update(WorkspaceMembership).where(
            WorkspaceMembership.user_id == ws.admin.id, WorkspaceMembership.workspace_id == ws.alpha.id)
            .values(role="viewer"))
        db_session.commit()
        for response in (browser.post(base, json=body, headers={"Idempotency-Key": "adm-1"}),
                         browser.post(f"{base}/{hook['id']}/rotate-secret", headers={"Idempotency-Key": "adm-r"}),
                         browser.patch(f"{base}/{hook['id']}", json={"enabled": False}),
                         browser.post(f"{base}/{hook['id']}/test")):
            assert response.status_code == 403 and response.json()["error"]["code"] == "webhooks_not_permitted"


def test_webhooks_need_the_server_key(db_session, ws):
    with _browser(ws.owner, ws.alpha) as browser:
        response = browser.post(HOOKS.format(ws.alpha.id), headers={"Idempotency-Key": "k1"},
                                json={"name": "Ops", "url": "https://hooks.example.com/", "events": ["drift_alert"]})
    assert response.json()["error"]["code"] == "webhooks_not_configured"


# --- webhooks: delivery -------------------------------------------------------------------------


def _doc_verify():
    text_ = (REPO / "docs/mvp/WEBHOOKS.md").read_text()
    snippet = re.search(r"```python\n(.*?)```", text_, re.S).group(1)
    scope: dict = {}
    exec(compile(snippet, "WEBHOOKS.md", "exec"), scope)  # noqa: S102 - our own documentation
    return text_, scope["verify"]


def test_signature_known_answer_and_the_documented_recipe():
    expected = "v1=ff8257ec6725c712f7a6cfce3af3dc52345d479a73d4de56d073686dc0d3e4ee"
    assert nd.sign("dclab_whsec_test-vector", "1700000000", b'{"event":"ping"}') == expected
    doc, verify = _doc_verify()
    assert "300 seconds" in doc and n.REPLAY_WINDOW_SECONDS == 300 and "X-DCLab-Delivery" in doc
    headers = {"X-DCLab-Timestamp": "1700000000", "X-DCLab-Signature": expected}
    assert verify("dclab_whsec_test-vector", headers, b'{"event":"ping"}', now=1700000100)
    assert not verify("dclab_whsec_test-vector", headers, b'{"event":"pong"}', now=1700000100)
    assert not verify("dclab_whsec_test-vector", headers, b'{"event":"ping"}', now=1700000301)
    assert not verify("other", headers, b'{"event":"ping"}', now=1700000000)
    for stamp in ("١٧", "-5", "", "1700000000.0"):  # non-ASCII digits, signs, empty, decimals
        assert not verify("dclab_whsec_test-vector", {**headers, "X-DCLab-Timestamp": stamp}, b"", now=1700000000)


def test_tick_delivers_signed_webhooks_and_logs_attempts(db_session, ws, secret_key, monkeypatch):
    endpoint = _endpoint(db_session, ws)
    seen = []

    def deliver(url, body, headers, *, ports, deadline, dns_key=None):
        seen.append((url, body, headers, ports))
        return nd.WebhookResult(204 if len(seen) == 1 else 500, None if len(seen) == 1 else "http_error", 12)

    ns_.notify_drift_alert(db_session, workspace_id=ws.alpha.id, project_id=ws.project.id, model_version_id=uuid4(),
                           window_start=datetime(2026, 10, 5, tzinfo=UTC), code="input_drift")
    db_session.commit()
    ns_.run_notification_tick(db_session, email=None, deliver=deliver)
    url, body, headers, ports = seen[0]
    secret = webhook_service.webhook_secret(endpoint)
    _doc, verify = _doc_verify()
    assert url == endpoint.url and ports == frozenset({443}) and verify(secret, headers, body)
    assert headers["X-DCLab-Event"] == "drift_alert" and headers["User-Agent"] == "DCLab-Webhooks/1"
    sent = json.loads(body)
    assert set(sent) == {"id", "event", "workspace_id", "created_at", "data"} and sent["id"] == headers["X-DCLab-Delivery"]
    assert set(sent["data"]) == {"project_id", "project_name", "subject", "code", "occurred_at"}
    hook_row = _rows(db_session, channel="webhook")[0]
    assert hook_row.status == "sent"
    logged = db_session.scalars(select(WebhookDelivery)).all()
    assert [(d.outcome, d.status_code, d.attempt) for d in logged] == [("delivered", 204, 1)]
    ns_.notify_drift_alert(db_session, workspace_id=ws.alpha.id, project_id=ws.project.id, model_version_id=uuid4(),
                           window_start=datetime(2026, 10, 12, tzinfo=UTC), code="input_drift")
    db_session.commit()
    monkeypatch.setattr(get_settings(), "webhook_max_deliveries_per_minute", 1)
    assert ns_.run_notification_tick(db_session, email=None, deliver=deliver).deferred == 1  # per-minute cap
    monkeypatch.setattr(get_settings(), "webhook_max_deliveries_per_minute", 60)
    db_session.execute(update(NotificationOutbox).where(NotificationOutbox.status == "pending")
                       .values(next_attempt_at=datetime.now(UTC)))
    db_session.commit()
    ns_.run_notification_tick(db_session, email=None, deliver=deliver)
    second = [row for row in _rows(db_session, channel="webhook") if row.id != hook_row.id][0]
    assert (second.status, second.last_error_code, second.attempts) == ("failed", "http_error", 1)
    db_session.refresh(endpoint)
    assert endpoint.consecutive_failures == 1
    endpoint.enabled = False
    db_session.commit()
    db_session.execute(update(NotificationOutbox).where(NotificationOutbox.id == second.id)
                       .values(next_attempt_at=datetime.now(UTC)))
    db_session.commit()
    ns_.run_notification_tick(db_session, email=None, deliver=deliver)
    assert _rows(db_session, id=second.id)[0].last_error_code == "endpoint_disabled" and len(seen) == 2


# --- webhooks: SSRF -----------------------------------------------------------------------------

PORTS = frozenset({443})


@pytest.mark.parametrize("url,code", [
    ("http://hooks.example.com/", "invalid_webhook_url"),
    ("https://user:pw@hooks.example.com/", "invalid_webhook_url"),
    ("https://hooks.example.com@evil.example/", "invalid_webhook_url"),
    ("https://hooks.example.com:8443/", "invalid_webhook_url"),
    ("https://hooks.example.com:0/", "invalid_webhook_url"),
    ("https://hooks.example.com/#frag", "invalid_webhook_url"),
    ("https://127.0.0.1/", "destination_refused"),
    ("https://[::1]/", "destination_refused"),
    ("https://10.1.2.3/", "destination_refused"),
    ("https://172.16.0.1/", "destination_refused"),
    ("https://192.168.1.1/", "destination_refused"),
    ("https://169.254.169.254/latest/meta-data", "destination_refused"),
    ("https://100.64.0.1/", "destination_refused"),
    ("https://[fc00::1]/", "destination_refused"),
    ("https://[fe80::1]/", "destination_refused"),
    ("https://[fe80::1%25eth0]/", "invalid_webhook_url"),
    ("https://hooks%2eexample.com/", "invalid_webhook_url"),
    ("https://[::ffff:127.0.0.1]/", "destination_refused"),
    ("https://[64:ff9b::a9fe:a9fe]/", "destination_refused"),
    ("https://0.0.0.0/", "destination_refused"),
    ("https://0x7f.1/", "invalid_webhook_url"),
    ("https://2130706433/", "invalid_webhook_url"),
    ("https://017700000001/", "invalid_webhook_url"),
    ("https://127.1/", "invalid_webhook_url"),
    ("https://localhost/", "invalid_webhook_url"),
    ("https://metadata.internal/", "destination_refused"),
    ("https://printer.local/", "destination_refused"),
    ("https://hooks.example.com/a b", "invalid_webhook_url"),
    ("https://hooks.example.com\\@evil/", "invalid_webhook_url"),
])
def test_ssrf_url_rules(url, code):
    with pytest.raises(nd.WebhookUrlError) as refused:
        nd.validate_webhook_url(url, PORTS)
    assert refused.value.code == code


class FakeResponse:
    def __init__(self, status, size=1 << 20):
        self.status, self.size, self.reads = status, size, []

    def read(self, amount=None):
        self.reads.append(amount)
        return b"x" * min(self.size, amount or self.size)


class FakeConnection:
    made: list = []
    status = 200

    def __init__(self, host, port, ip, *, timeout, context):
        self.host, self.port, self.ip, self.timeout, self.sock = host, port, ip, timeout, None
        self.response = FakeResponse(FakeConnection.status)
        FakeConnection.made.append(self)

    def connect(self):
        pass

    def request(self, method, path, body, headers):
        self.sent = (method, path, body, headers)

    def getresponse(self):
        return self.response

    def close(self):
        pass


def _resolver(*answers):
    calls = []

    def resolve(host, port, *_args):
        calls.append(host)
        return answers[min(len(calls), len(answers)) - 1]

    resolve.calls = calls
    return resolve


def test_ssrf_resolution_pinning_redirects_timeouts_and_response_cap():
    FakeConnection.made, FakeConnection.status = [], 200
    url, body = "https://hooks.example.com/in?x=1", b"{}"
    private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))]
    mixed = PUBLIC + private
    for answers in (private, mixed, [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))]):
        result = nd.deliver_webhook(url, body, {}, ports=PORTS, resolver=_resolver(answers),
                                    connection_factory=FakeConnection)
        assert result.error_code == "destination_refused" and FakeConnection.made == []
    rebinding = _resolver(PUBLIC, private)  # a second lookup would answer a private address
    ok = nd.deliver_webhook(url, body, {"X": "1"}, ports=PORTS, resolver=rebinding, connection_factory=FakeConnection)
    conn = FakeConnection.made[-1]
    assert ok.error_code is None and ok.status_code == 200
    assert rebinding.calls == ["hooks.example.com."]  # looked up once, fully qualified (no search domains)
    assert (conn.host, conn.ip, conn.port, conn.sent[1]) == ("hooks.example.com", "93.184.216.34", 443, "/in?x=1")
    assert conn.response.reads == [n.WEBHOOK_RESPONSE_READ_MAX]  # never more than 4 KiB read
    FakeConnection.status = 302  # a redirect (to anywhere, private included) is never followed
    redirected = nd.deliver_webhook(url, body, {}, ports=PORTS, resolver=_resolver(PUBLIC),
                                    connection_factory=FakeConnection)
    assert redirected.error_code == "redirect_not_followed" and len(FakeConnection.made) == 2
    assert nd.deliver_webhook(url, b"x" * (16 * 1024 + 1), {}, ports=PORTS, resolver=_resolver(PUBLIC),
                              connection_factory=FakeConnection).error_code == "body_too_large"

    def no_dns(*_args):
        raise socket.gaierror("nope")

    assert nd.deliver_webhook(url, body, {}, ports=PORTS, resolver=no_dns,
                              connection_factory=FakeConnection).error_code == "dns_failed"

    def slow_dns(*_args):
        time.sleep(1.0)
        return PUBLIC

    started = time.monotonic()
    slow = nd.deliver_webhook(url, body, {}, ports=PORTS, deadline=0.2, resolver=slow_dns,
                              connection_factory=FakeConnection)
    assert slow.error_code == "timeout" and time.monotonic() - started < 0.8 and len(FakeConnection.made) == 2
    for ip in ("8.8.8.8", "2606:4700::1111"):
        assert nd.is_public_ip(ipaddress.ip_address(ip))


def test_pinned_connection_connects_to_the_checked_address_only(monkeypatch):
    seen = {}

    class Ctx:
        def wrap_socket(self, sock, server_hostname):
            seen["sni"] = server_hostname
            return sock

    class Sock:
        def close(self):
            pass

    def create_connection(address, timeout=None):
        seen["address"], seen["timeout"] = address, timeout
        return Sock()

    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("the name must not be resolved again"))
    conn = nd.PinnedHTTPSConnection("hooks.example.com", 443, "93.184.216.34", timeout=5.0, context=Ctx())
    conn.connect()
    assert seen == {"address": ("93.184.216.34", 443), "timeout": 5.0, "sni": "hooks.example.com"}


# --- email adapters -----------------------------------------------------------------------------


class FakeSmtp:
    def __init__(self, *, starttls=True, fail=None, quit_fails=False):
        self.calls, self._starttls, self._fail, self._quit_fails = [], starttls, fail, quit_fails

    def __call__(self, host, port, timeout):
        self.calls.append(("connect", host, port, timeout))
        return self

    def ehlo(self):
        self.calls.append("ehlo")

    def has_extn(self, name):
        return self._starttls

    def starttls(self, context):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append("login")
        if self._fail is not None:
            raise self._fail

    def send_message(self, message):
        self.calls.append(("send", message["To"]))

    def quit(self):
        self.calls.append("quit")
        if self._quit_fails:
            raise smtplib.SMTPServerDisconnected("gone")

    def close(self):
        self.calls.append("close")


def test_email_adapters_require_tls_and_return_codes_only():
    email = nd.OutgoingEmail("person@example.com", "DCLab: Run failed", "body")
    kwargs = {"host": "smtp.example.com", "port": 587, "username": "u", "password": "p", "sender": "dclab@example.com"}
    ok = FakeSmtp()
    assert nd.SmtpEmailAdapter(**kwargs, smtp_factory=ok).send(email, timeout=3.0) is None
    assert ok.calls.index("starttls") < ok.calls.index("login") < ok.calls.index(("send", "person@example.com"))
    assert ok.calls[0][3] == 3.0 and ok.calls[-1] == "quit"
    quitless = FakeSmtp(quit_fails=True)  # accepted, then QUIT fails: still sent (no retry, no duplicate)
    assert nd.SmtpEmailAdapter(**kwargs, smtp_factory=quitless).send(email) is None and quitless.calls[-1] == "close"
    plain = FakeSmtp(starttls=False)
    assert nd.SmtpEmailAdapter(**kwargs, smtp_factory=plain).send(email) == "tls_failed" and "login" not in plain.calls
    denied = FakeSmtp(fail=smtplib.SMTPAuthenticationError(535, b"secret detail"))
    assert nd.SmtpEmailAdapter(**kwargs, smtp_factory=denied).send(email) == "auth_failed"

    class Client:
        def __init__(self, status):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json, headers):
            assert url == nd.POSTMARK_URL and headers["X-Postmark-Server-Token"] == "tok"
            return SimpleNamespace(status_code=self.status)

    for status, code in ((200, None), (401, "auth_failed"), (422, "provider_rejected"), (503, "provider_unavailable")):
        adapter = nd.PostmarkEmailAdapter(token="tok", sender="dclab@example.com", client_factory=lambda: Client(status))
        assert adapter.send(email) == code
    settings = Settings(email_provider="smtp", smtp_host="", smtp_password="hunter2-secret",
                        webhook_secret_key="w" * 40)
    assert "hunter2" not in repr(settings) and "w" * 40 not in repr(settings)
    assert nd.email_adapter(Settings(email_provider="none")) is None
    assert nd.email_adapter(settings).send(email) == "not_configured"


def test_secrets_and_links_are_redacted_from_logs():
    from app.observability.logging import redact_text

    line = redact_text(f"secret {WEBHOOK_SECRET_SAMPLE} link {ns_.unsubscribe_token(uuid4(), uuid4(), 'run_failed')}")
    assert "dclab_whsec_" not in line and "dclab_unsub_" not in line and line.count("[redacted]") == 2


WEBHOOK_SECRET_SAMPLE = "dclab_whsec_" + "A" * 43


def test_routes_are_people_only():
    for route in (("GET", PREFS), ("PUT", PREFS), ("POST", "/v1/notifications/unsubscribe"),
                  ("GET", "/v1/workspaces/{workspace_id}/webhooks"),
                  ("POST", "/v1/workspaces/{workspace_id}/webhooks/{webhook_id}/rotate-secret")):
        assert route in HUMAN_ONLY_ROUTES and route not in TOKEN_ROUTE_SCOPES
