"""P8.4-A: usage records from the existing ledgers, plan limits at command admission, the usage read."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    Artifact,
    ClientLabUpload,
    MlJob,
    UsageRecord,
    UserRole,
)
from app.domain.errors import QuotaExceededError
from app.domain.service_tokens import HUMAN_ONLY_ROUTES, TOKEN_ROUTE_SCOPES
from app.domain.usage import (
    BETA_LIMITS,
    QUOTA_COMPUTE_SECONDS_MONTH,
    QUOTA_RUNS_PER_DAY,
    QUOTA_STORAGE_BYTES,
)
from app.main import app
from app.services import usage_service as usage
from app.services.auth_service import create_user
from app.services.ml_job_service import ensure_run_capacity
from app.services.workspace_entitlement_service import get_entitlement, set_entitlement
from conftest import browser_login
from test_data_model_lineage import make_lineage_setup
from test_v1_contract_conventions import _assert_envelope, _headers

PASSWORD = "test-password-123"
ROUTE = "/v1/workspaces/{}/usage"
DAY = date(2026, 3, 10)  # a fixed past day for the fake-clock tests (ml_jobs / artifacts take explicit times)
REPO = Path(__file__).resolve().parents[3]


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


@pytest.fixture()
def ws(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    alpha, beta = setup["alpha"], setup["beta"]

    def user(tag, role, workspace):
        return create_user(db_session, email=f"usage-{tag}-{uuid4().hex[:6]}@test.invalid", password=PASSWORD,
                           role=role, workspace_id=workspace.id if workspace is not None else None)

    ns = SimpleNamespace(
        setup=setup, alpha=alpha, beta=beta, project=setup["alpha_project"],
        owner=user("owner", UserRole.WORKSPACE_OWNER, alpha), admin=setup["alpha_admin"],
        engineer=user("eng", UserRole.ML_ENGINEER, alpha), viewer=user("viewer", UserRole.VIEWER, alpha),
        client_user=user("client", UserRole.CLIENT_USER, alpha), beta_admin=setup["beta_admin"],
        platform=user("platform", UserRole.DCLAB_ADMIN, None),
    )
    db_session.commit()
    return ns


def _job(db, workspace_id, *, created_at=None, started_at=None, completed_at=None, status="completed",
         job_type="batch_predict") -> MlJob:
    row = MlJob(workspace_id=workspace_id, job_type=job_type, handler_key="models.batch_predict",
                target_id=uuid4(), status=status, payload={}, attempts=1, max_attempts=3,
                started_at=started_at, completed_at=completed_at)
    if created_at is not None:
        row.created_at = created_at
        row.queued_at = created_at
    db.add(row)
    db.flush()
    return row


def _artifact(db, workspace_id, size: int, *, created_at=None) -> Artifact:
    row = Artifact(workspace_id=workspace_id, artifact_type="report", provider="local",
                   object_key=f"usage-test/{uuid4().hex}", content_digest="0" * 64, size_bytes=size,
                   extra_metadata={})
    if created_at is not None:
        row.created_at = created_at
    db.add(row)
    db.flush()
    return row


def _insert(db, table: str, values: dict) -> UUID:
    values = {"id": uuid4(), **values}
    json_columns = {"limits", "redaction_summary"}
    params = ", ".join(f"CAST(:{k} AS jsonb)" if k in json_columns else f":{k}" for k in values)
    db.execute(text(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({params})"),
               {k: json.dumps(v) if k in json_columns else v for k, v in values.items()})
    return values["id"]


def _agent_run(db, workspace_id, project_id, **overrides) -> UUID:
    values = {
        "workspace_id": workspace_id, "project_id": project_id, "kind": "specialist",
        "agent_key": "experiment_critic", "agent_version": "1", "runtime": "fake", "runtime_version": "fake==1",
        "purpose": "experiment.review", "policy_digest": "c" * 64, "tool_catalog_digest": "d" * 64,
        "data_class": "aggregates", "outcome_scope": "cv", "status": "completed", "limits": {"steps": 4},
    }
    return _insert(db, "agent_runs", {**values, **overrides})


def _invocation(db, workspace_id, **overrides) -> UUID:
    values = {
        "workspace_id": workspace_id, "purpose": "pipeline_audit_routine", "mode": "routine",
        "prompt_version": "v1", "schema_version": "1", "input_evidence_digest": "a" * 64,
        "redaction_summary": {}, "llm_used": True, "reason": "r", "status": "completed",
        "validator_verdict": "accepted", "started_at": datetime.now(UTC),
    }
    return _insert(db, "llm_invocations", {**values, **overrides})


def _records(db, workspace_id) -> list[UsageRecord]:
    db.expire_all()
    return list(db.scalars(select(UsageRecord).where(UsageRecord.workspace_id == workspace_id)
                           .order_by(UsageRecord.kind, UsageRecord.period_day, UsageRecord.source_ref)))


def _latest(db, workspace_id, kind, day) -> int:
    return usage._latest_records(db, workspace_id, day, day).get((kind, day), 0)


# --- the table --------------------------------------------------------------------------------


def test_usage_records_are_append_only_and_a_source_counts_once(db_session, ws):
    db = db_session
    row = UsageRecord(workspace_id=ws.alpha.id, kind="compute_seconds", period_day=DAY, quantity=5,
                      source_ref="ledgers.v1:asof:2026-03-10t10")
    db.add(row)
    db.commit()
    for statement in ("UPDATE usage_records SET quantity = 6", "DELETE FROM usage_records"):
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(text(statement))
            db.commit()
        db.rollback()
    db.add(UsageRecord(workspace_id=ws.alpha.id, kind="compute_seconds", period_day=DAY, quantity=7,
                       source_ref="ledgers.v1:asof:2026-03-10t10"))  # project_id NULL: nulls not distinct
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    for bad in ({"kind": "invoices"}, {"quantity": -1}, {"source_ref": "Has Spaces"}):
        db.add(UsageRecord(**{"workspace_id": ws.alpha.id, "kind": "agent_runs", "period_day": DAY,
                              "quantity": 1, "source_ref": "ledgers.v1:x", **bad}))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    # Composite tenant FK: a record cannot name another workspace's project.
    db.add(UsageRecord(workspace_id=ws.alpha.id, project_id=ws.setup["beta_project"].id, kind="agent_runs",
                       period_day=DAY, quantity=1, source_ref="ledgers.v1:x"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# --- aggregation ------------------------------------------------------------------------------


def test_aggregation_is_idempotent_and_reads_the_latest_watermark(db_session, ws):
    db = db_session
    _job(db, ws.alpha.id, created_at=_at(DAY, 8), started_at=_at(DAY, 9), completed_at=_at(DAY, 9, 10))  # 600 s
    _job(db, ws.alpha.id, created_at=_at(DAY, 8), started_at=_at(DAY, 9), completed_at=None, status="running")
    _job(db, ws.beta.id, created_at=_at(DAY, 8), started_at=_at(DAY, 9), completed_at=_at(DAY, 10))  # other tenant
    _artifact(db, ws.alpha.id, 1000, created_at=_at(DAY, 7))
    db.commit()

    first = usage.run_usage_metering_tick(db, now=_at(DAY, 13, 5))
    assert ws.alpha.id in first and ws.beta.id in first
    rows = _records(db, ws.alpha.id)
    # Zeros are implied (no row); the day before had no compute and no stored file yet.
    assert {(r.kind, r.period_day, r.quantity) for r in rows} == {("compute_seconds", DAY, 600),
                                                                  ("storage_bytes", DAY, 1000)}
    assert {r.source_ref for r in rows} == {"ledgers.v1:asof:2026-03-10t13"}
    # A second tick (same hour, nothing new): no new rows.
    usage.run_usage_metering_tick(db, now=_at(DAY, 13, 6))
    assert len(_records(db, ws.alpha.id)) == len(rows)

    # A job finishes later in the same hour: the hour's record already exists, nothing is double counted.
    _job(db, ws.alpha.id, created_at=_at(DAY, 13), started_at=_at(DAY, 13, 10), completed_at=_at(DAY, 13, 40))
    db.commit()
    usage.run_usage_metering_tick(db, now=_at(DAY, 13, 50))
    assert _latest(db, ws.alpha.id, "compute_seconds", DAY) == 600
    # The next hour's watermark records the new total once; the read takes the latest, never a sum.
    usage.run_usage_metering_tick(db, now=_at(DAY, 14, 5))
    usage.run_usage_metering_tick(db, now=_at(DAY, 14, 30))
    compute = [r for r in _records(db, ws.alpha.id) if r.kind == "compute_seconds"]
    assert [(r.source_ref, r.quantity) for r in compute] == [
        ("ledgers.v1:asof:2026-03-10t13", 600), ("ledgers.v1:asof:2026-03-10t14", 2400)]
    assert _latest(db, ws.alpha.id, "compute_seconds", DAY) == 2400
    read = usage.usage_read(db, actor=ws.admin, workspace_id=ws.alpha.id, now=_at(DAY, 15))
    totals = {t.kind: t for t in read.totals}
    assert totals["compute_seconds"].this_month == 2400 and totals["storage_bytes"].this_month == 1000
    assert totals["storage_bytes"].aggregate == "latest"
    assert [d.compute_seconds for d in read.daily if d.day == DAY] == [2400]
    # The day after: yesterday is completed from the ledgers (a job that finished just before midnight).
    _job(db, ws.alpha.id, created_at=_at(DAY, 23), started_at=_at(DAY, 23, 50), completed_at=_at(DAY, 23, 59))
    db.commit()
    usage.run_usage_metering_tick(db, now=_at(DAY + timedelta(days=1), 0, 10))
    assert _latest(db, ws.alpha.id, "compute_seconds", DAY) == 2940
    # The beta tenant's job never lands in alpha's records.
    assert all(r.workspace_id == ws.alpha.id for r in _records(db, ws.alpha.id))
    assert _latest(db, ws.beta.id, "compute_seconds", DAY) == 3600


def test_metering_kill_switch_and_one_failure_never_blocks_the_others(db_session, ws, monkeypatch):
    db = db_session
    _job(db, ws.alpha.id, created_at=_at(DAY, 8), started_at=_at(DAY, 9), completed_at=_at(DAY, 9, 1))
    _job(db, ws.beta.id, created_at=_at(DAY, 8), started_at=_at(DAY, 9), completed_at=_at(DAY, 9, 2))
    db.commit()
    monkeypatch.setattr(get_settings(), "usage_metering_enabled", False)
    assert usage.run_usage_metering_tick(db, now=_at(DAY, 12)) == []
    monkeypatch.setattr(get_settings(), "usage_metering_enabled", True)
    real = usage.aggregate_workspace

    def flaky(session, workspace_id, moment):
        if workspace_id == ws.alpha.id:
            raise RuntimeError("boom")
        return real(session, workspace_id, moment)

    monkeypatch.setattr(usage, "aggregate_workspace", flaky)
    done = usage.run_usage_metering_tick(db, now=_at(DAY, 12))
    assert ws.beta.id in done and ws.alpha.id not in done
    assert _latest(db, ws.beta.id, "compute_seconds", DAY) == 120 and _records(db, ws.alpha.id) == []


def test_each_kind_comes_from_its_ledger(db_session, ws):
    db = db_session
    now = datetime.now(UTC)
    _job(db, ws.alpha.id, started_at=now - timedelta(seconds=100), completed_at=now - timedelta(seconds=1))
    _artifact(db, ws.alpha.id, 4096)
    _invocation(db, ws.alpha.id, project_id=ws.project.id, total_tokens=500, cost_micros=7000)
    _invocation(db, ws.alpha.id, project_id=ws.project.id, input_tokens=30, output_tokens=12, cost_micros=None)
    _agent_run(db, ws.alpha.id, ws.project.id)  # an agent task
    _agent_run(db, ws.alpha.id, ws.project.id, kind="lead", agent_key="lead", purpose="assistant.turn")  # a turn
    _agent_run(db, ws.beta.id, ws.setup["beta_project"].id)  # another tenant
    db.commit()
    moment = datetime.now(UTC) + timedelta(seconds=1)
    usage.run_usage_metering_tick(db, now=moment)
    today = moment.date()
    got = {kind: _latest(db, ws.alpha.id, kind, today) for kind in
           ("compute_seconds", "storage_bytes", "llm_tokens", "llm_cost_micros", "agent_runs", "assistant_runs")}
    assert got == {"compute_seconds": 99, "storage_bytes": 4096, "llm_tokens": 542, "llm_cost_micros": 7000,
                   "agent_runs": 1, "assistant_runs": 1}


# --- limits at admission ----------------------------------------------------------------------


def test_limit_check_under_at_over_unlimited_and_switched_off(db_session, ws, monkeypatch):
    db = db_session
    now = datetime.now(UTC)
    set_entitlement(db, ws.alpha.id, QUOTA_RUNS_PER_DAY, 2, source="test")
    _job(db, ws.alpha.id, created_at=now)
    usage.ensure_within_quota(db, ws.alpha.id, QUOTA_RUNS_PER_DAY, requested=1)  # 1 of 2: under
    _job(db, ws.alpha.id, created_at=now)
    with pytest.raises(QuotaExceededError) as at_limit:  # 2 of 2: at the limit
        usage.ensure_within_quota(db, ws.alpha.id, QUOTA_RUNS_PER_DAY, requested=1)
    detail = at_limit.value.public_detail()
    assert detail["code"] == "quota_exceeded" and detail["kind"] == "runs_per_day"
    assert (detail["used"], detail["limit"]) == (2, 2) and detail["resets_at"].endswith("00:00:00+00:00")
    assert "Work already running continues" in detail["message"]
    # Yesterday's runs do not count today.
    _job(db, ws.beta.id, created_at=now - timedelta(days=1, hours=1))
    set_entitlement(db, ws.beta.id, QUOTA_RUNS_PER_DAY, 1, source="test")
    usage.ensure_within_quota(db, ws.beta.id, QUOTA_RUNS_PER_DAY, requested=1)
    # Compute: 1 hour used of a 1 hour month is over; storage: requested bytes are counted.
    set_entitlement(db, ws.alpha.id, QUOTA_COMPUTE_SECONDS_MONTH, 3600, source="test")
    _job(db, ws.alpha.id, started_at=now - timedelta(hours=1, seconds=1), completed_at=now - timedelta(seconds=1))
    with pytest.raises(QuotaExceededError, match="compute time"):
        usage.ensure_within_quota(db, ws.alpha.id, QUOTA_COMPUTE_SECONDS_MONTH)
    set_entitlement(db, ws.alpha.id, QUOTA_STORAGE_BYTES, 1000, source="test")
    _artifact(db, ws.alpha.id, 900)
    usage.ensure_within_quota(db, ws.alpha.id, QUOTA_STORAGE_BYTES)
    with pytest.raises(QuotaExceededError):
        usage.ensure_within_quota(db, ws.alpha.id, QUOTA_STORAGE_BYTES, requested=200)
    # Unlimited (JSON null) and the platform switch never refuse.
    set_entitlement(db, ws.alpha.id, QUOTA_RUNS_PER_DAY, None, source="test")
    usage.ensure_within_quota(db, ws.alpha.id, QUOTA_RUNS_PER_DAY, requested=1)
    monkeypatch.setattr(get_settings(), "quotas_enforced", False)
    usage.ensure_within_quota(db, ws.alpha.id, QUOTA_COMPUTE_SECONDS_MONTH)
    # An invalid stored value never unlocks anything: the default plan applies.
    assert usage._as_limit("lots") is False
    db.rollback()


def test_runs_per_day_is_serialized_with_the_run_capacity_lock(test_engine, db_session, ws):
    db = db_session
    set_entitlement(db, ws.alpha.id, QUOTA_RUNS_PER_DAY, 1, source="test")
    db.commit()
    factory = sessionmaker(bind=test_engine)
    first, second = factory(), factory()
    outcome: dict[str, object] = {}
    try:
        ensure_run_capacity(first, ws.alpha.id)
        _job(first, ws.alpha.id)  # the first creator's job, not yet committed

        def other() -> None:
            try:
                ensure_run_capacity(second, ws.alpha.id)
                outcome["second"] = "admitted"
            except QuotaExceededError as exc:
                outcome["second"] = exc.public_detail()["code"]
            finally:
                second.rollback()

        thread = threading.Thread(target=other)
        thread.start()
        time.sleep(0.5)
        assert thread.is_alive()  # waiting on the same advisory lock
        first.commit()
        thread.join(10)
        assert outcome["second"] == "quota_exceeded"
    finally:
        first.close()
        second.close()


def test_new_runs_and_uploads_are_refused_with_a_plain_429_but_replays_and_reads_still_answer(
        client, db_session, ws):
    from test_v1_resources_experiments import _csv

    setup = ws.setup
    headers = lambda **extra: _headers(setup["alpha_admin"], setup["alpha"].id, **extra)  # noqa: E731
    upload = client.post("/v1/datasets", headers=headers(**{"Idempotency-Key": uuid4().hex}),
                         data={"project_id": str(setup["alpha_project"].id)},
                         files={"file": ("rows.csv", _csv(), "text/csv")})
    assert upload.status_code == 201, upload.text
    body = {"project_id": str(setup["alpha_project"].id), "dataset_id": upload.json()["id"], "target_column": "label"}
    set_entitlement(db_session, setup["alpha"].id, QUOTA_RUNS_PER_DAY, 1, source="test")
    db_session.commit()
    key = uuid4().hex
    first = client.post("/v1/experiments", json=body, headers=headers(**{"Idempotency-Key": key}))
    assert first.status_code == 202, first.text
    over = client.post("/v1/experiments", json=body, headers=headers(**{"Idempotency-Key": uuid4().hex}))
    error = _assert_envelope(over, 429, "quota_exceeded")
    assert error["retryable"] is False and error["details"]["kind"] == "runs_per_day"
    assert error["details"]["used"] == 1 and error["details"]["limit"] == 1 and error["details"]["resets_at"]
    replay = client.post("/v1/experiments", json=body, headers=headers(**{"Idempotency-Key": key}))
    assert replay.status_code == 202 and replay.headers["Idempotent-Replayed"] == "true"
    # Reads and cancelling the running work are never refused.
    assert client.get("/v1/experiments", headers=headers()).status_code == 200
    assert client.get(f"/v1/experiments/{first.json()['id']}", headers=headers()).status_code == 200
    assert client.post(f"/v1/experiments/{first.json()['id']}/cancel", headers=headers()).status_code == 200
    # Storage at its limit: the upload is refused before anything is stored.
    used = usage.measure_quota(db_session, setup["alpha"].id, QUOTA_STORAGE_BYTES, datetime.now(UTC))
    set_entitlement(db_session, setup["alpha"].id, QUOTA_STORAGE_BYTES, used, source="test")
    db_session.commit()
    artifacts = db_session.scalar(select(func.count()).select_from(Artifact))
    refused = client.post("/v1/datasets", headers=headers(**{"Idempotency-Key": uuid4().hex}),
                          data={"project_id": str(setup["alpha_project"].id)},
                          files={"file": ("rows.csv", _csv(seed=3), "text/csv")})
    assert _assert_envelope(refused, 429, "quota_exceeded")["details"]["kind"] == "storage_bytes"
    assert db_session.scalar(select(func.count()).select_from(Artifact)) == artifacts


def test_the_legacy_labs_upload_is_admitted_by_the_same_check(auth_client, db_session, monkeypatch):
    from test_v1_resources_experiments import _csv

    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)
    set_entitlement(db_session, DEFAULT_WORKSPACE_ID, QUOTA_RUNS_PER_DAY, 0, source="test")
    db_session.commit()
    before = (db_session.scalar(select(func.count()).select_from(ClientLabUpload)),
              db_session.scalar(select(func.count()).select_from(Artifact)))
    response = auth_client.post("/app/labs/uploads", data={"category": "Revenue"},
                                files={"file": ("data.csv", _csv(), "text/csv")})
    assert response.status_code == 429, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "quota_exceeded" and detail["kind"] == "runs_per_day"
    assert "used" not in detail and "limit" not in detail  # numbers only in the /v1 details
    assert detail["message"].startswith("This workspace's daily run limit is reached; it resets on")
    db_session.expire_all()
    assert (db_session.scalar(select(func.count()).select_from(ClientLabUpload)),
            db_session.scalar(select(func.count()).select_from(Artifact))) == before


def test_only_admission_points_call_the_limit_check():
    """Reads, downloads, approvals and rollbacks never call it: the callers are exactly these."""

    app_root = REPO / "apps" / "api" / "app"
    callers = {
        path.relative_to(app_root).as_posix()
        for path in app_root.rglob("*.py")
        if "ensure_within_quota(" in path.read_text(encoding="utf-8")
    }
    assert callers == {"services/usage_service.py", "services/ml_job_service.py",
                       "services/client_lab_upload_service.py"}
    capacity = {
        path.relative_to(app_root).as_posix()
        for path in app_root.rglob("*.py")
        if "ensure_run_capacity(" in path.read_text(encoding="utf-8")
    }
    assert capacity == {"services/ml_job_service.py", "services/experiment_service.py",
                        "services/experiment_branch_service.py", "services/batch_prediction_service.py",
                        "services/client_lab_upload_service.py", "services/execution_request_service.py"}


# --- plans ------------------------------------------------------------------------------------


def test_only_platform_staff_set_a_plan(db_session, ws):
    db = db_session
    with pytest.raises(Exception, match="platform administrator"):
        usage.set_plan(db, actor=ws.owner, workspace_id=ws.alpha.id, plan="team", limits={QUOTA_RUNS_PER_DAY: 9})
    result = usage.set_plan(db, actor=ws.platform, workspace_id=ws.alpha.id, plan="team",
                            limits={QUOTA_RUNS_PER_DAY: 9, QUOTA_STORAGE_BYTES: None})
    assert result["plan"] == "team" and result["limits"][QUOTA_RUNS_PER_DAY] == 9
    assert result["limits"][QUOTA_STORAGE_BYTES] is None
    assert get_entitlement(db, ws.alpha.id, QUOTA_RUNS_PER_DAY).source == f"platform_staff:{ws.platform.id}"
    usage.set_plan(db, actor=ws.platform, workspace_id=ws.alpha.id, limits={QUOTA_RUNS_PER_DAY: usage.UNSET})
    assert usage.quota_limit(db, ws.alpha.id, QUOTA_RUNS_PER_DAY).value == BETA_LIMITS[QUOTA_RUNS_PER_DAY]
    for bad in ({"invoices": 1}, {QUOTA_RUNS_PER_DAY: -1}, {QUOTA_RUNS_PER_DAY: "many"}):
        with pytest.raises(Exception, match="limit|whole number"):
            usage.set_plan(db, actor=ws.platform, workspace_id=ws.alpha.id, limits=bad)
    with pytest.raises(Exception, match="plan name"):
        usage.set_plan(db, actor=ws.platform, workspace_id=ws.alpha.id, plan="Not A Name")
    db.rollback()


# --- the read ---------------------------------------------------------------------------------


def _get(user, workspace, path_workspace=None, headers=None):
    with TestClient(app) as browser:
        assert browser_login(browser, user.email, PASSWORD if user.email.startswith("usage-") else
                             "test-password").status_code == 200
        browser.headers["X-Workspace-Id"] = str(workspace.id)
        return browser.get(ROUTE.format((path_workspace or workspace).id), headers=headers or {})


def test_usage_read_shape_quota_flags_and_no_people(client, db_session, ws):
    db = db_session
    now = datetime.now(UTC)
    set_entitlement(db, ws.alpha.id, QUOTA_RUNS_PER_DAY, 5, source="test")
    for _ in range(4):  # 80%: warning
        _job(db, ws.alpha.id, created_at=now)
    db.commit()
    res = _get(ws.engineer, ws.alpha)
    assert res.status_code == 200, res.text
    assert res.headers["Cache-Control"] == "private, no-store"
    body = res.json()
    assert set(body) == {"workspace_id", "plan", "quotas_enforced", "metering_enabled", "measured_at", "month_start",
                         "resets_at", "quotas", "totals", "daily", "ai_spend", "disclaimer", "notes"}
    assert body["plan"] == "beta" and body["disclaimer"].startswith("Measured from DCLab's own records")
    quotas = {q["key"]: q for q in body["quotas"]}
    assert set(quotas) == {"compute_seconds_month", "storage_bytes", "runs_per_day", "members"}
    runs = quotas["runs_per_day"]
    assert (runs["used"], runs["limit"], runs["percent"], runs["warning"], runs["over"]) == (4, 5, 80.0, True, False)
    assert runs["source"] == "workspace" and runs["enforced"] is True and runs["resets_at"]
    assert quotas["compute_seconds_month"]["source"] == "plan_default"
    assert quotas["members"]["used"] >= 5 and quotas["storage_bytes"]["resets_at"] is None
    assert len(body["daily"]) == 31 and body["daily"][-1]["day"] == now.date().isoformat()
    assert {t["kind"] for t in body["totals"]} == {"compute_seconds", "storage_bytes", "llm_tokens",
                                                   "llm_cost_micros", "agent_runs", "assistant_runs"}
    for person in (ws.owner, ws.admin, ws.engineer, ws.viewer):
        assert str(person.id) not in res.text and person.email not in res.text
    _job(db, ws.alpha.id, created_at=now)
    db.commit()
    over = {q["key"]: q for q in _get(ws.engineer, ws.alpha).json()["quotas"]}["runs_per_day"]
    assert over["over"] is True and over["warning"] is False


@pytest.mark.parametrize("who", ["owner", "admin", "engineer", "platform"])
def test_owners_admins_engineers_and_platform_admins_may_read(client, ws, who):
    assert _get(getattr(ws, who), ws.alpha).status_code == 200


@pytest.mark.parametrize("who", ["viewer", "client_user"])
def test_viewers_and_client_users_are_refused(client, ws, who):
    res = _get(getattr(ws, who), ws.alpha)
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "usage_not_permitted"


def test_other_workspaces_and_service_tokens_are_refused(client, db_session, ws):
    mismatched = _get(ws.admin, ws.alpha, path_workspace=ws.beta)
    assert mismatched.status_code == 404
    assert _get(ws.beta_admin, ws.alpha).status_code == 403  # selecting a workspace one is not in
    from app.services.service_token_service import create_service_token

    _row, raw = create_service_token(db_session, creator=ws.owner, workspace_id=ws.alpha.id, name="t",
                                     scopes=("read",), expires_in_days=30, current_password=PASSWORD)
    db_session.commit()
    res = client.get(ROUTE.format(ws.alpha.id), headers={"Authorization": f"Bearer {raw}"})
    assert res.status_code == 403
    route = ("GET", "/v1/workspaces/{workspace_id}/usage")
    assert route in HUMAN_ONLY_ROUTES and route not in TOKEN_ROUTE_SCOPES
    assert client.get(ROUTE.format(ws.alpha.id)).status_code in (401, 403)  # anonymous


# --- review fixes -----------------------------------------------------------------------------


def test_a_newer_record_wins_whatever_its_source_ref_says(db_session, ws):
    db = db_session
    for ref, quantity in (("ledgers.v10:asof:2026-03-10t09", 10), ("ledgers.v9:asof:2026-03-10t09", 9),
                          ("correction:2026-03-11", 7)):
        db.add(UsageRecord(workspace_id=ws.alpha.id, kind="agent_runs", period_day=DAY, quantity=quantity,
                           source_ref=ref))
        db.commit()  # separate transactions: created_at (clock_timestamp) orders them
    assert _latest(db, ws.alpha.id, "agent_runs", DAY) == 7
    read = usage.usage_read(db, actor=ws.admin, workspace_id=ws.alpha.id, now=_at(DAY, 12))
    assert [d.agent_runs for d in read.daily if d.day == DAY] == [7]
    # The period_day CHECK: no day before 2020 and none after tomorrow.
    for day in (date(2019, 12, 31), datetime.now(UTC).date() + timedelta(days=2)):
        db.add(UsageRecord(workspace_id=ws.alpha.id, kind="agent_runs", period_day=day, quantity=1,
                           source_ref="ledgers.v1:x"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_compute_counts_training_and_scoring_jobs_only(db_session, ws):
    db = db_session
    now = datetime.now(UTC)
    for job_type in ("agent_run", "monitoring", "auth_cleanup"):
        _job(db, ws.alpha.id, job_type=job_type, started_at=now - timedelta(hours=5), completed_at=now)
    _job(db, ws.alpha.id, job_type="batch_predict", started_at=now - timedelta(seconds=60), completed_at=now)
    assert usage.measure_quota(db, ws.alpha.id, QUOTA_COMPUTE_SECONDS_MONTH, now) == 60
    measured = usage.daily_quantities(db, ws.alpha.id, now - timedelta(days=1), now + timedelta(seconds=1))
    assert sum(measured["compute_seconds"].values()) == 60
    db.rollback()


def test_a_full_storage_refuses_new_runs_and_scorings(db_session, ws):
    db = db_session
    _artifact(db, ws.alpha.id, 500)
    set_entitlement(db, ws.alpha.id, QUOTA_STORAGE_BYTES, 500, source="test")
    with pytest.raises(QuotaExceededError) as refused:
        ensure_run_capacity(db, ws.alpha.id)
    assert refused.value.key == "storage_bytes" and "runs and scorings are refused" in str(refused.value)
    set_entitlement(db, ws.alpha.id, QUOTA_STORAGE_BYTES, 501, source="test")
    ensure_run_capacity(db, ws.alpha.id)
    db.rollback()


def test_resuming_a_parked_run_is_admitted_like_a_new_one(auth_client, engineer_client, db_session, monkeypatch):
    from test_execution_target_confirmation import _wait_for_target

    from app.db.models import ExecutionRequest

    run_id, body = _wait_for_target(auth_client, db_session, monkeypatch)
    request_id = body["target_confirmation"]["execution_request_id"]
    job = db_session.scalar(select(MlJob).where(MlJob.upload_id == run_id))
    parked = (job.status, job.queued_at)
    set_entitlement(db_session, DEFAULT_WORKSPACE_ID, QUOTA_COMPUTE_SECONDS_MONTH, 0, source="test")
    set_entitlement(db_session, DEFAULT_WORKSPACE_ID, QUOTA_RUNS_PER_DAY, 1, source="test")  # this run is today's 1
    db_session.commit()
    refused = engineer_client.post(f"/v1/execution-requests/{request_id}/target-confirmation",
                                   json={"target_column": "Churn"})
    error = _assert_envelope(refused, 429, "quota_exceeded")
    assert error["details"]["kind"] == "compute_seconds_month"
    db_session.expire_all()
    job = db_session.get(MlJob, job.id)
    assert (job.status, job.queued_at) == parked  # still parked
    assert db_session.get(ExecutionRequest, UUID(request_id)).status == "needs_input"
    # Under the limits it resumes; the parked run is not counted again against runs_per_day.
    set_entitlement(db_session, DEFAULT_WORKSPACE_ID, QUOTA_COMPUTE_SECONDS_MONTH, None, source="test")
    db_session.commit()
    confirmed = engineer_client.post(f"/v1/execution-requests/{request_id}/target-confirmation",
                                     json={"target_column": "Churn"})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "running"
