"""P8.5-A operator read API and readiness: platform staff only, ids and counts only."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from app.db.models import UserRole
from app.domain.service_tokens import HUMAN_ONLY_ROUTES, TOKEN_ROUTE_SCOPES
from app.services import operator_service, system_health_service as health
from app.services.auth_service import create_access_token, create_user
from app.services.ml_job_service import create_ml_job
from app.services.workspace_service import create_business_workspace
from conftest import TEST_URL, browser_login

PASSWORD = "test-password"
ROUTES = ("/v1/operator/overview", "/v1/operator/workspaces", "/v1/operator/failed-jobs")
OVERVIEW_KEYS = {"generated_at", "workspaces", "jobs_last_24h", "queue", "workers", "failed_jobs_last_24h",
                 "failed_jobs_last_24h_truncated", "platform_ai_caps", "readiness"}
WORKSPACE_KEYS = {"id", "name", "kind", "created_at", "member_count", "project_count", "jobs_last_30d",
                  "failed_jobs_last_24h", "active_last_7d", "assistant_beta"}
FAILED_JOB_KEYS = {"id", "workspace_id", "job_type", "handler_key", "code", "attempts", "failed_at"}
SECRET_REASON = "KeyError: customer.email@acme.test salary=91000 /tmp/upload/clients.csv"


def _user(db, role, workspace_id=None):
    return create_user(db, email=f"op-{role.value}-{uuid4().hex[:8]}@test.invalid", password=PASSWORD,
                       role=role, workspace_id=workspace_id)


def _job(db, workspace_id, *, status, reason=None, job_type="auth_cleanup", handler="auth.session_cleanup"):
    job = create_ml_job(db, workspace_id=workspace_id, job_type=job_type, handler_key=handler, target_id=uuid4())
    job.status, job.failure_reason = status, reason
    if status == "failed":
        job.completed_at = datetime.now(UTC)
    db.flush()
    return job


@pytest.fixture()
def op(db_session):
    db = db_session
    owner = _user(db, UserRole.WORKSPACE_OWNER)
    alpha = create_business_workspace(db, owner=owner, name="Alpha <b>Telecom</b>")
    beta = create_business_workspace(db, owner=owner, name="Beta Retail")
    users = {
        "platform_admin": _user(db, UserRole.DCLAB_ADMIN),
        "platform_developer": _user(db, UserRole.DCLAB_DEVELOPER),
        "owner": owner,
        "workspace_admin": _user(db, UserRole.WORKSPACE_ADMIN, alpha.id),
        "ml_engineer": _user(db, UserRole.ML_ENGINEER, alpha.id),
        "viewer": _user(db, UserRole.VIEWER, alpha.id),
        "client": _user(db, UserRole.CLIENT_USER, alpha.id),
    }
    failed = _job(db, alpha.id, status="failed", reason=SECRET_REASON)
    coded = _job(db, beta.id, status="failed", reason="feature_contract_failed",
                 job_type="batch_predict", handler="models.batch_predict")
    queued = _job(db, alpha.id, status="queued")
    db.commit()
    return {"db": db, "alpha": alpha, "beta": beta, "users": users, "failed": failed, "coded": coded,
            "queued": queued}


def _login(client, user):
    client.cookies.clear()
    assert browser_login(client, user.email, PASSWORD).status_code == 200


def _session_get(client, user, path):
    _login(client, user)
    return client.get(path)


def test_operator_routes_are_human_only_and_never_token_routes():
    for path in ROUTES:
        assert ("GET", path) in HUMAN_ONLY_ROUTES and ("GET", path) not in TOKEN_ROUTE_SCOPES


def test_authority_matrix_platform_staff_only(client, op):
    _login(client, op["users"]["platform_admin"])  # founder decision 2026-10-16: platform ADMIN only
    for path in ROUTES:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert response.headers["Cache-Control"] == "private, no-store"
    for role in ("platform_developer", "owner", "workspace_admin", "ml_engineer", "viewer", "client"):
        _login(client, op["users"][role])
        for path in ROUTES:
            response = client.get(path)
            assert response.status_code == 403, (role, path, response.text)
            assert response.json()["error"]["code"] == "forbidden"
    client.cookies.clear()
    bearer = {"Authorization": f"Bearer {create_access_token(op['users']['platform_admin'])}"}
    for path in ROUTES:  # a person's API bearer (or a service token) is not a signed-in session
        response = client.get(path, headers=bearer)
        assert response.status_code == 403 and response.json()["error"]["code"] == "human_session_required"
        response = client.get(path, headers={"Authorization": "Bearer dclab_st_" + "0" * 32 + "_" + "a" * 43})
        assert response.status_code == 403
        assert client.get(path).status_code == 401


def test_overview_shape_counts_and_no_free_text(client, op):
    body = _session_get(client, op["users"]["platform_admin"], ROUTES[0]).json()
    assert set(body) == OVERVIEW_KEYS
    assert body["workspaces"]["total"] >= 2 and body["workspaces"]["active_last_7d"] >= 2
    assert body["queue"]["depth"] >= 1 and body["queue"]["oldest_age_seconds"] is not None
    assert set(body["workers"]) == {"dispatcher", "count", "heartbeat_age_seconds", "visible"}
    codes = {row["code"]: row["count"] for row in body["failed_jobs_last_24h"]}
    assert codes["see_logs"] >= 1 and codes["feature_contract_failed"] >= 1
    assert body["failed_jobs_last_24h_truncated"] is False
    assert {row["job_type"] for row in body["jobs_last_24h"]} >= {"auth_cleanup", "batch_predict"}
    caps = body["platform_ai_caps"]
    assert caps["max_data_class"] == "aggregates" and caps["auto_release"] is False
    assert {check["name"] for check in body["readiness"]["checks"]} == set(health.READINESS_CHECKS)
    raw = str(body)
    for leaked in ("customer.email", "acme.test", "salary", "clients.csv", "KeyError", "Alpha", "Beta"):
        assert leaked not in raw, leaked


def test_workspace_and_failed_job_pages_have_exact_keys_and_cursors(client, op):
    _session_get(client, op["users"]["platform_admin"], ROUTES[0])
    first = client.get("/v1/operator/workspaces", params={"limit": 1}).json()
    assert set(first) == {"items", "next_cursor"} and len(first["items"]) == 1
    assert set(first["items"][0]) == WORKSPACE_KEYS
    seen, cursor = [], None
    while True:
        page = client.get("/v1/operator/workspaces", params={"limit": 2, **({"cursor": cursor} if cursor else {})}).json()
        seen += page["items"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    by_id = {row["id"]: row for row in seen}
    alpha = by_id[str(op["alpha"].id)]
    assert alpha["name"] == "Alpha <b>Telecom</b>"  # untrusted text, returned verbatim (rendered as text)
    assert alpha["member_count"] >= 4 and alpha["failed_jobs_last_24h"] == 1 and alpha["active_last_7d"] is True
    jobs = client.get("/v1/operator/failed-jobs").json()
    assert all(set(item) == FAILED_JOB_KEYS for item in jobs["items"])
    by_job = {item["id"]: item for item in jobs["items"]}
    assert by_job[str(op["failed"].id)]["code"] == "see_logs"
    assert by_job[str(op["coded"].id)]["code"] == "feature_contract_failed"
    assert SECRET_REASON not in str(jobs) and str(op["queued"].id) not in by_job
    bad = client.get("/v1/operator/failed-jobs", params={"cursor": first["next_cursor"]})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_cursor"  # scope-bound cursors


def test_failure_codes_never_echo_free_text():
    assert operator_service.failure_code(None) == "see_logs"
    assert operator_service.failure_code("salary") == "see_logs"  # code-shaped text is not a code
    assert operator_service.failure_code("scoring_failed") == "scoring_failed"
    assert operator_service.failure_code("abandoned: heartbeat expired") == "heartbeat_expired"
    assert operator_service.failure_code("unsupported ml job handler 'x'") == "unknown_handler"


def test_readiness_endpoint_states(client, op, monkeypatch, caplog):
    clock = [1000.0]
    monkeypatch.setattr(health, "_clock", lambda: clock[0])
    health.reset_readiness_cache()
    response = client.get("/health/ready")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"status", "checks"}
    checks = {check["name"]: check for check in body["checks"]}
    assert set(checks) == set(health.READINESS_CHECKS)
    assert checks["database"]["status"] == "ok" and checks["object_storage"]["status"] == "ok"
    assert checks["migrations"] == {"name": "migrations", "status": "unknown", "required": True,
                                    "code": "not_versioned"}  # tests build the schema without Alembic
    assert checks["worker"]["status"] in ("stale", "ok") and body["status"] == "degraded"
    assert client.get("/health").json() == {"status": "ok", "db": "connected"}  # unchanged

    class Broken:
        def exists(self, key):
            raise OSError("bucket s3://secret-bucket denied")

    monkeypatch.setattr("app.storage.factory.get_object_storage", lambda: Broken())
    assert client.get("/health/ready").status_code == 200  # cached: probes never fan out per hit
    clock[0] += 4.9
    assert client.get("/health/ready").status_code == 200
    clock[0] += 0.2  # READINESS_CACHE_SECONDS (5) passed
    with caplog.at_level(logging.WARNING, logger="dclab.health"):
        failing = client.get("/health/ready")
    assert failing.status_code == 503 and failing.json()["status"] == "not_ready"
    assert "secret-bucket" not in failing.text
    assert [r.getMessage() for r in caplog.records if r.name == "dclab.health"] == [
        "readiness check object_storage failed: OSError"]  # the type only, never the message
    health.reset_readiness_cache()
    storage = {c["name"]: c for c in failing.json()["checks"]}["object_storage"]
    assert storage == {"name": "object_storage", "status": "failing", "required": True, "code": "unreachable"}


def test_migration_check_compares_with_script_heads(db_session, monkeypatch):
    real_heads = health._script_heads.__wrapped__()
    assert real_heads and len(real_heads) == 1  # the real script directory resolves to one head
    with db_session.get_bind().begin() as connection:
        connection.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(64) PRIMARY KEY)"))
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0001_old')"))
    try:
        monkeypatch.setattr(health, "_script_heads", lambda: frozenset({"0001_old"}))
        assert health.check_migrations(db_session).status == "ok"
        monkeypatch.setattr(health, "_script_heads", lambda: frozenset({"0099_new"}))
        assert (health.check_migrations(db_session).status, health.check_migrations(db_session).code) == (
            "failing", "not_at_head")
    finally:
        with db_session.get_bind().begin() as connection:
            connection.execute(text("DROP TABLE alembic_version"))


def test_worker_heartbeat_reads_named_worker_connections(db_session, monkeypatch):
    monkeypatch.setenv("PGAPPNAME", "something-else")
    name = health.mark_worker_connections()
    assert name.startswith("dclab-worker:") and len(name) <= 63
    assert health.mark_worker_connections() == name  # stable for the process
    monkeypatch.delenv("PGAPPNAME")  # this test process must not look like a worker
    baseline = health.worker_heartbeat_ages(db_session)
    assert baseline is not None
    if not baseline:  # nothing else in this database looks like a worker
        before = health.check_worker(db_session)
        assert (before.status, before.code) == ("stale", "no_worker_seen")
    engine = create_engine(TEST_URL, connect_args={"application_name": name})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            ages = health.worker_heartbeat_ages(db_session)
            assert ages is not None and len(ages) == len(baseline) + 1 and ages[0] < 60
            assert health.check_worker(db_session).status == "ok"
            later = datetime.now(UTC) + timedelta(seconds=600)
            assert (health.check_worker(db_session, now=later).code) == "heartbeat_old"
            workers = health.workers(db_session)
            assert workers.count == len(baseline) + 1 and workers.visible is True
    finally:
        engine.dispose()
    monkeypatch.setattr(health, "get_settings", lambda: type("S", (), {"ml_job_dispatcher": "thread"})())
    assert health.check_worker(db_session).status == "skipped"


def test_queue_stats_and_gauge_snapshot(db_session, op, monkeypatch):
    depth, age = health.queue_stats(db_session)
    assert depth >= 1 and age is not None and age >= 0
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: (lambda: _Unclosable(db_session)))
    values = health.gauge_snapshot()
    assert values["dclab_ml_job_queue_depth"][0][0] >= 1
    assert {labels["check"] for _value, labels in values["dclab_readiness_ok"]} == set(health.READINESS_CHECKS)
    assert all(not labels for name, rows in values.items() if name != "dclab_readiness_ok" for _v, labels in rows)


class _Unclosable:
    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        return None


def test_worker_rows_hidden_by_another_role_read_unknown_not_stale(db_session, monkeypatch):
    class Rows:
        def all(self):
            return [("dclab-worker:abc", None)]  # another role: application_name visible, state_change NULL

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *args, **kwargs):
            return Rows()

    class Bind:
        def connect(self):
            return Connection()

    class Db:
        def get_bind(self):
            return Bind()

    assert health.worker_heartbeat_ages(Db()) is None
    check = health.check_worker(Db())
    assert (check.status, check.code) == ("unknown", "activity_not_visible")
    assert health.workers(Db()).visible is False


def test_failure_code_counts_say_when_the_scan_is_truncated(db_session, op, monkeypatch):
    monkeypatch.setattr(operator_service, "FAILED_CODES_SCAN_MAX", 1)
    read = operator_service.overview(db_session)
    assert read.failed_jobs_last_24h_truncated is True
    assert sum(row.count for row in read.failed_jobs_last_24h) == 1  # newest failure only
