"""D7a business-view questions: authority, tenancy, append-only, close + If-Match, Inbox, notifications."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models import BusinessQuestion, BusinessQuestionReply, NotificationOutbox, UserRole, Workspace
from app.services.auth_service import create_access_token, create_user
from app.services.csrf_service import csrf_token_for_session
from app.services.project_service import create_project
from app.services.service_token_service import create_service_token
from app.services.session_service import SESSION_HEADER, issue_session

from conftest import TRUSTED_ORIGIN  # noqa: E402

PASSWORD = "bq-password-123"
QUESTION_KEYS = {"id", "project_id", "project_name", "subject", "text", "status", "asked_by", "reply_count",
                 "last_reply_at", "created_at", "updated_at", "closed_at", "closed_by", "version", "can_reply",
                 "can_close", "you_reply_as"}
DETAIL_KEYS = QUESTION_KEYS | {"replies", "untrusted_fields"}
REPLY_KEYS = {"id", "text", "author", "created_at"}
PERSON_KEYS = {"kind", "is_you", "side"}


@pytest.fixture
def bq(db_session, client):
    db = db_session
    alpha = Workspace(slug=f"bq-a-{uuid4().hex[:8]}", name="BQ Alpha")
    beta = Workspace(slug=f"bq-b-{uuid4().hex[:8]}", name="BQ Beta")
    db.add_all([alpha, beta])
    db.flush()

    def user(tag, role, workspace=alpha):
        return create_user(db, email=f"bq-{tag}-{uuid4().hex[:6]}@test.invalid", password=PASSWORD, role=role,
                           workspace_id=workspace.id if workspace is not None else None)

    s = SimpleNamespace(
        alpha=alpha, beta=beta, admin=user("admin", UserRole.WORKSPACE_ADMIN), asker=user("eng", UserRole.ML_ENGINEER),
        engineer=user("eng2", UserRole.ML_ENGINEER), viewer=user("viewer", UserRole.VIEWER),
        business=user("client", UserRole.CLIENT_USER), platform=user("platform", UserRole.DCLAB_ADMIN, None),
        beta_engineer=user("beta-eng", UserRole.ML_ENGINEER, beta), beta_business=user("beta-client",
                                                                                       UserRole.CLIENT_USER, beta),
    )
    for person in (s.admin, s.asker, s.engineer, s.business, s.beta_business):
        person.email_verified_at = datetime.now(UTC)
    s.project = create_project(db, actor=s.admin, workspace_id=alpha.id, name="Churn", slug=f"c-{uuid4().hex[:6]}")
    s.beta_project = create_project(db, actor=s.beta_engineer, workspace_id=beta.id, name="B",
                                    slug=f"b-{uuid4().hex[:6]}")
    db.commit()
    s.client, s.db = client, db

    def as_(person, workspace=alpha):
        _row, raw = issue_session(db, person)
        db.commit()
        return {SESSION_HEADER: raw, "X-Workspace-Id": str(workspace.id), "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": csrf_token_for_session(raw)}

    s.h = {name: as_(getattr(s, name)) for name in ("admin", "asker", "engineer", "viewer", "business", "platform")}
    s.h["beta_engineer"], s.h["beta_business"] = as_(s.beta_engineer, beta), as_(s.beta_business, beta)
    return s


def _ask(s, who="asker", text="Do the churn scores match what you see?", key=None, project=None, **body):
    return s.client.post(f"/v1/projects/{(project or s.project).id}/questions", json={"text": text, **body},
                         headers={**s.h[who], "Idempotency-Key": key or f"k-{uuid4().hex}"})


def _reply(s, qid, who, text="Yes, mostly."):
    return s.client.post(f"/v1/questions/{qid}/replies", json={"text": text},
                         headers={**s.h[who], "Idempotency-Key": f"r-{uuid4().hex}"})


def _close(s, qid, who, etag=None):
    headers = dict(s.h[who])
    if etag is not None:
        headers["If-Match"] = etag
    return s.client.post(f"/v1/questions/{qid}/close", headers=headers)


def _code(response, status):
    assert response.status_code == status, response.text
    return response.json()["error"]["code"]


def test_authority_matrix_and_status_flow(bq):
    s = bq
    for who in ("viewer", "business", "platform"):
        assert _code(_ask(s, who), 403) == "question_ask_not_permitted"
    asked = _ask(s, key="same")
    assert asked.status_code == 201, asked.text
    q = asked.json()
    assert q["status"] == "open" and q["version"] == 1 and asked.headers["ETag"] == '"1"'
    assert q["asked_by"] == {"kind": "person", "is_you": True, "side": "data_team"} and q["can_close"]
    replay = _ask(s, key="same")
    assert replay.json()["id"] == q["id"] and replay.headers["Idempotent-Replayed"] == "true"
    assert _ask(s, "admin").status_code == 201  # owners and administrators have ML-write

    for who in ("viewer", "platform"):
        assert _code(_reply(s, q["id"], who), 403) == "question_reply_not_permitted"
    answered = _reply(s, q["id"], "business")
    assert answered.status_code == 201 and answered.json()["status"] == "answered", answered.text
    follow_up = _reply(s, q["id"], "engineer", "Which region?")
    assert follow_up.json()["status"] == "open" and follow_up.json()["reply_count"] == 2
    sides = [(r["author"]["side"], r["author"]["is_you"]) for r in follow_up.json()["replies"]]
    assert sides == [("business", False), ("data_team", True)]

    for who in ("engineer", "business", "viewer", "platform"):  # not the asker, not an administrator
        assert _code(_close(s, q["id"], who, "*"), 403) == "question_close_not_permitted"
    assert _code(_close(s, q["id"], "asker"), 428) == "precondition_required"
    assert _code(_close(s, q["id"], "asker", '"1"'), 412) == "precondition_failed"
    etag = s.client.get(f"/v1/questions/{q['id']}", headers=s.h["asker"]).headers["ETag"]
    closed = _close(s, q["id"], "asker", etag)
    assert closed.status_code == 200 and closed.json()["status"] == "closed", closed.text
    assert closed.json()["closed_by"]["is_you"] and not closed.json()["can_reply"]
    assert closed.json()["you_reply_as"] is None  # a closed question takes no reply from anyone
    assert _code(_reply(s, q["id"], "business"), 409) == "question_closed"
    assert _code(_close(s, q["id"], "admin", closed.headers["ETag"]), 409) == "question_closed"
    other = _ask(s).json()
    assert _close(s, other["id"], "admin", f'"{other["version"]}"').status_code == 200  # an administrator closes


def test_tokens_and_bearers_get_403_on_every_route_and_business_users_gain_no_other_write(bq):
    s = bq
    qid = _ask(s).json()["id"]
    _row, token = create_service_token(s.db, creator=s.asker, workspace_id=s.alpha.id, name="t",
                                       scopes=["read", "projects:write"], expires_in_days=1, current_password=PASSWORD)
    s.db.commit()
    routes = [("GET", "/v1/questions"), ("GET", f"/v1/projects/{s.project.id}/questions"),
              ("POST", f"/v1/projects/{s.project.id}/questions"), ("GET", f"/v1/questions/{qid}"),
              ("POST", f"/v1/questions/{qid}/replies"), ("POST", f"/v1/questions/{qid}/close")]
    for bearer in (token, create_access_token(s.asker)):
        for method, path in routes:
            response = s.client.request(method, path, json={"text": "x"}, headers={
                "Authorization": f"Bearer {bearer}", "X-Workspace-Id": str(s.alpha.id), "Idempotency-Key": "t1"})
            assert _code(response, 403) == "session_required", (method, path)
    # The reply is a business user's only new write: shared ML-core writes stay refused.
    for method, path, body in (("POST", "/v1/projects", {"name": "x", "slug": f"x-{uuid4().hex[:6]}"}),
                               ("PUT", f"/v1/projects/{s.project.id}/retrain-policy",
                                {"policy": "off", "rationale": "x"}),
                               ("POST", f"/v1/projects/{s.project.id}/retrain", {"dataset_id": str(uuid4())})):
        response = s.client.request(method, path, json=body, headers={
            **s.h["business"], "Idempotency-Key": f"w-{uuid4().hex}", "If-Match": "*"})
        assert response.status_code == 403, (path, response.text)


def test_tenancy_is_404(bq):
    s = bq
    qid = _ask(s).json()["id"]
    for who in ("beta_engineer", "beta_business"):
        assert s.client.get(f"/v1/questions/{qid}", headers=s.h[who]).status_code == 404
        assert _reply(s, qid, who).status_code == 404
        assert _close(s, qid, who, "*").status_code == 404
        assert s.client.get("/v1/questions", headers=s.h[who]).json()["items"] == []
        assert s.client.get(f"/v1/projects/{s.project.id}/questions", headers=s.h[who]).status_code == 404
    assert _ask(s, "beta_engineer", project=s.project).status_code == 404
    assert _ask(s, project=s.beta_project).status_code == 404
    foreign = s.client.get("/v1/questions", headers={**s.h["business"], "X-Workspace-Id": str(s.beta.id)})
    assert foreign.status_code == 403


def test_exact_keys_and_no_person_ids_or_emails(bq):
    s = bq
    qid = _ask(s).json()["id"]
    _reply(s, qid, "business")
    listing = s.client.get("/v1/questions", headers=s.h["business"])
    assert set(listing.json()) == {"items", "has_more", "limit", "untrusted_fields"}
    assert set(listing.json()["items"][0]) == QUESTION_KEYS
    detail = s.client.get(f"/v1/questions/{qid}", headers=s.h["business"])
    body = detail.json()
    assert set(body) == DETAIL_KEYS and set(body["replies"][0]) == REPLY_KEYS
    assert set(body["asked_by"]) == PERSON_KEYS and body["asked_by"]["is_you"] is False
    assert body["replies"][0]["author"] == {"kind": "person", "is_you": True, "side": "business"}
    assert body["can_reply"] and not body["can_close"]
    assert body["project_name"] == "Churn" and listing.json()["items"][0]["project_name"] == "Churn"
    assert "project_name" in body["untrusted_fields"] and "items[].project_name" in listing.json()["untrusted_fields"]
    # The side a reply would be written as: a business user answers, the data team follows up, a viewer cannot.
    sides = {who: s.client.get(f"/v1/questions/{qid}", headers=s.h[who]).json()["you_reply_as"]
             for who in ("business", "asker", "admin", "viewer", "platform")}
    assert sides == {"business": "business", "asker": "data_team", "admin": "data_team", "viewer": None,
                     "platform": None}
    for person in (s.asker, s.business, s.admin):
        assert str(person.id) not in detail.text + listing.text and person.email not in detail.text + listing.text
    assert detail.headers["Cache-Control"] == listing.headers["Cache-Control"] == "private, no-store"
    assert detail.headers["ETag"] == f'"{body["version"]}"'


def test_text_rules_and_links(bq):
    s = bq
    for bad in ("hi\x00there", "bell\x07", "rtl\u202eoverride", "zero\u200bwidth", "tag\U000e0041x", "   ",
                "\t\n", "x" * 2001):
        response = _ask(s, text=bad)
        assert response.status_code == 422, response.text
        assert bad.strip() == "" or bad[:5] not in response.text  # never echoed
    made = _ask(s, text="line one\r\nline two\tend").json()
    assert made["text"] == "line one\nline two\tend" and made["subject"] is None
    assert _code(_ask(s, subject={"kind": "model_version", "id": str(uuid4())}), 422) == "question_subject_not_found"
    assert _ask(s, subject={"kind": "notebook", "id": str(uuid4())}).status_code == 422
    # The project's name is cleaned to one plain line (no controls, no bidi overrides, capped).
    s.project.name = "Ch\x07urn \u202eforecast\n" + "x" * 100
    s.db.commit()
    name = s.client.get("/v1/questions", headers=s.h["business"]).json()["items"][0]["project_name"]
    assert name.startswith("Ch urn forecast") and len(name) <= 60
    assert not any(ch in name for ch in "\x07\u202e\n")


def test_db_backstops_append_only_guard_and_checks(bq):
    s = bq
    qid = _ask(s).json()["id"]
    _reply(s, qid, "business")
    for statement in ("UPDATE business_question_replies SET text = 'edited'",
                      "DELETE FROM business_question_replies",
                      "UPDATE business_questions SET text = 'edited', version = version + 1",
                      "UPDATE business_questions SET status = 'open'"):  # no version bump
        with pytest.raises(DBAPIError):
            s.db.execute(text(statement))
        s.db.rollback()
    question = s.db.get(BusinessQuestion, qid)
    question.status, question.closed_at, question.closed_by_user_id = "closed", datetime.now(UTC), s.asker.id
    question.version += 1
    s.db.commit()
    with pytest.raises(DBAPIError):  # closed is final
        s.db.execute(text("UPDATE business_questions SET status = 'open', closed_at = NULL, "
                          "closed_by_user_id = NULL, version = version + 1"))
    s.db.rollback()
    for bad in ("bad\x07", "\t\n "):
        with pytest.raises(IntegrityError):
            s.db.add(BusinessQuestionReply(workspace_id=s.alpha.id, question_id=qid, author_user_id=s.business.id,
                                           author_side="business", text=bad))
            s.db.flush()
        s.db.rollback()
    with pytest.raises(IntegrityError):  # the question of another workspace
        s.db.add(BusinessQuestionReply(workspace_id=s.beta.id, question_id=qid, author_user_id=s.business.id,
                                       author_side="business", text="ok"))
        s.db.flush()
    s.db.rollback()


def test_inbox_activity_and_notifications(bq):
    s = bq
    qid = _ask(s).json()["id"]

    def inbox(who):
        items = s.client.get("/v1/inbox", headers=s.h[who]).json()["items"]
        return {(i["kind"], i["source"]["id"]) for i in items if i["kind"].startswith("question")}

    assert inbox("business") == {("question_asked", qid)}
    assert inbox("asker") == set() and inbox("viewer") == set()
    asked = s.db.scalars(select(NotificationOutbox).where(NotificationOutbox.kind == "question_asked")).all()
    assert {row.recipient_user_id for row in asked} == {s.business.id}  # never the other tenant's business user
    assert all("Do the churn" not in str(row.payload) for row in asked)

    _reply(s, qid, "business")
    assert inbox("business") == set() and inbox("asker") == {("question_answered", qid)}
    assert inbox("engineer") == set()
    answered = s.db.scalars(select(NotificationOutbox).where(NotificationOutbox.kind == "question_answered")).all()
    assert {row.recipient_user_id for row in answered} == {s.asker.id}
    counts = s.client.get("/v1/inbox/counts", headers=s.h["asker"]).json()
    assert counts["needs_decision"] >= 1

    _row, token = create_service_token(s.db, creator=s.asker, workspace_id=s.alpha.id, name="t", scopes=["read"],
                                       expires_in_days=1, current_password=PASSWORD)
    s.db.commit()
    agent = {"Authorization": f"Bearer {token}", "X-Workspace-Id": str(s.alpha.id)}
    assert not [i for i in s.client.get("/v1/inbox", headers=agent).json()["items"] if "question" in i["kind"]]
    assert not [i for i in s.client.get("/v1/activity", headers=agent).json()["items"] if "question" in i["kind"]]
    activity = s.client.get("/v1/activity", headers=s.h["asker"]).json()["items"]
    kinds = {(i["kind"], i["actor"]["is_you"]) for i in activity if i["kind"].startswith("question")}
    assert kinds == {("question_asked", True), ("question_replied", False)}
    assert all(set(i["link"]) == {"kind", "id"} and i["link"]["id"] == qid for i in activity
               if i["kind"].startswith("question"))
    # D7b: the reply's side is the item's status (the web never reads the summary text).
    assert {i["status"] for i in activity if i["kind"] == "question_replied"} == {"business"}
    assert all(i["status"] is None for i in activity if i["kind"] == "question_asked")
    assert {row.payload["link"] for row in answered} == {f"/projects/{s.project.id}/questions?question={qid}"}
    prefs = s.client.get("/v1/me/notification-preferences", headers=s.h["business"]).json()["items"]
    applies = {item["kind"]: item["applies_to_you"] for item in prefs}
    assert applies["question_asked"] and not applies["question_answered"] and not applies["decision_needed"]


def test_migration_literals_match_the_domain_and_integrity():
    import importlib.util
    from pathlib import Path

    from app.db import integrity
    from app.domain import business_questions as d

    path = Path(__file__).resolve().parents[1] / "alembic/versions/0090_business_questions.py"
    spec = importlib.util.spec_from_file_location("m0090", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert (mig._CK_TEXT, mig._CK_STATUS, mig._CK_CLOSED, mig._CK_SUBJECT, mig._CK_SIDE) == (
        d.CK_BUSINESS_QUESTIONS_TEXT, d.CK_BUSINESS_QUESTIONS_STATUS, d.CK_BUSINESS_QUESTIONS_CLOSED,
        d.CK_BUSINESS_QUESTIONS_SUBJECT, d.CK_BUSINESS_QUESTION_REPLIES_SIDE)
    assert (mig._GUARD, *mig._TRIGGERS) == integrity.BUSINESS_QUESTIONS_TRIGGER_SQL
