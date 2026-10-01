"""S0-P04B: migration, deterministic selection, concurrency, and admin audit."""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.db.models import DEFAULT_WORKSPACE_ID, SimulationRun, Workspace
from app.services.insight_query import latest_runs_by_use_case
from app.services.simulation_run_service import persist_simulation_run


def _payload(subject: str) -> dict:
    return {
        "use_case": "churn",
        "model_version": "churn_sim_v1",
        "policy_version": "churn_sim_v1",
        "fusion": "single:gb_all",
        "heroes": [{"external_id": subject}],
    }


def test_equal_timestamp_latest_run_has_stable_tie_breaker(db_session):
    created_at = datetime(2026, 9, 1, tzinfo=UTC)
    for run_id, subject in ((UUID(int=1), "FIRST"), (UUID(int=2), "SECOND")):
        payload = _payload(subject)
        db_session.add(
            SimulationRun(
                id=run_id,
                workspace_id=DEFAULT_WORKSPACE_ID,
                use_case="churn",
                model_version=payload["model_version"],
                policy_version=payload["policy_version"],
                fusion=payload["fusion"],
                payload=payload,
                created_at=created_at,
            )
        )
    db_session.commit()

    selected = latest_runs_by_use_case(db_session, DEFAULT_WORKSPACE_ID)
    assert list(selected) == ["churn"]
    assert selected["churn"].id == UUID(int=2)


def test_empty_history_is_successful_and_contains_no_insights(auth_client):
    response = auth_client.get("/app/insights")
    assert response.status_code == 200
    assert response.json()["categories"]
    assert all(not group["insights"] for group in response.json()["categories"])


def test_concurrent_creation_keeps_workspace_lineage(test_engine, db_session):
    other = Workspace(slug=f"concurrent-{uuid4().hex[:12]}", name="Concurrent")
    db_session.add(other)
    db_session.commit()
    workspace_ids = (DEFAULT_WORKSPACE_ID, DEFAULT_WORKSPACE_ID, other.id)
    barrier = Barrier(len(workspace_ids))

    def create(workspace_id: UUID) -> tuple[UUID, UUID]:
        with Session(test_engine) as session:
            barrier.wait(timeout=10)
            row = persist_simulation_run(
                session, workspace_id=workspace_id, payload=_payload(str(workspace_id))
            )
            return row.id, row.workspace_id

    with ThreadPoolExecutor(max_workers=len(workspace_ids)) as executor:
        results = list(executor.map(create, workspace_ids))

    assert {workspace_id for _, workspace_id in results} == set(workspace_ids)
    own_ids = {run_id for run_id, workspace_id in results if workspace_id == DEFAULT_WORKSPACE_ID}
    assert len(own_ids) == 2
    for run_id, workspace_id in results:
        row = db_session.scalar(select(SimulationRun).where(SimulationRun.id == run_id))
        assert row is not None and row.workspace_id == workspace_id
    assert latest_runs_by_use_case(db_session, DEFAULT_WORKSPACE_ID)["churn"].id in own_ids
    assert latest_runs_by_use_case(db_session, other.id)["churn"].id not in own_ids


def test_admin_simulation_access_is_capability_guarded_and_audited(
    admin_client, auth_client, admin_user, caplog
):
    with caplog.at_level(logging.INFO, logger="dclab.legacy_admin_access"):
        allowed = admin_client.get("/admin/simulations/runs")
        denied = auth_client.get("/admin/simulations/runs")

    assert allowed.status_code == 200
    assert denied.status_code == 403
    events = [
        json.loads(record.message.split("legacy_admin_audit ", 1)[1])
        for record in caplog.records
        if record.name == "dclab.legacy_admin_access"
    ]
    assert events == [
        {
            "event": "legacy_admin_access",
            "action": "simulation_list",
            "capabilities": ["platform_read", "workspace_read"],
            "outcome": "authorized_attempt",
            "request_id": events[0]["request_id"],
            "actor_id": str(admin_user.id),
            "workspace_id": str(DEFAULT_WORKSPACE_ID),
        }
    ]
    assert events[0]["request_id"]


def test_admin_derivative_routes_audit_authorized_attempts_only(
    admin_client, auth_client, caplog
):
    with caplog.at_level(logging.INFO, logger="dclab.legacy_admin_access"):
        models = admin_client.get("/admin/models")
        monitoring = admin_client.get("/admin/monitoring")
        missing_trial = admin_client.get(f"/admin/models/client-trials/{uuid4()}")
        denied = auth_client.get("/admin/models")

    assert models.status_code == monitoring.status_code == 200
    assert missing_trial.status_code == 404
    assert denied.status_code == 403
    events = [
        json.loads(record.message.split("legacy_admin_audit ", 1)[1])
        for record in caplog.records
        if record.name == "dclab.legacy_admin_access"
    ]
    assert [event["action"] for event in events] == [
        "model_registry_list",
        "monitoring_list",
        "trial_audit_detail",
    ]
    assert all(event["capabilities"] == ["platform_read", "workspace_read"] for event in events)
    assert all(event["outcome"] == "authorized_attempt" for event in events)


def test_admin_simulation_create_and_raw_reads_audit_without_payload(
    admin_client, monkeypatch, caplog
):
    monkeypatch.setattr(
        "app.api.simulations.run_use_case", lambda _name: _payload("PRIVATE-SUBJECT")
    )
    with caplog.at_level(logging.INFO, logger="dclab.legacy_admin_access"):
        created = admin_client.post("/admin/simulations/run", json={"use_case": "churn"})
        assert created.status_code == 200, created.text
        run_id = created.json()["id"]
        detail = admin_client.get(f"/admin/simulations/runs/{run_id}")
        decision = admin_client.get(
            f"/admin/simulations/runs/{run_id}/decisions/PRIVATE-SUBJECT"
        )

    assert detail.status_code == decision.status_code == 200
    events = [
        json.loads(record.message.split("legacy_admin_audit ", 1)[1])
        for record in caplog.records
        if record.name == "dclab.legacy_admin_access"
    ]
    assert [event["action"] for event in events] == [
        "simulation_create",
        "simulation_detail",
        "simulation_decision",
    ]
    assert events[0]["capabilities"] == ["platform_write", "workspace_read"]
    assert all("PRIVATE-SUBJECT" not in record.message for record in caplog.records)


def test_previous_head_upgrade_quarantines_unowned_simulation(monkeypatch):
    admin_url = make_url(
        os.environ.get("MIGRATION_TEST_DATABASE_URL", "postgresql://localhost:55432/postgres")
    )
    assert admin_url.host in {"localhost", "127.0.0.1"}
    assert admin_url.port == 55432
    database_name = f"decisionai_simulation_{uuid4().hex}"
    database_url = admin_url.set(database=database_name)
    admin_engine = create_engine(
        admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except Exception as exc:  # pragma: no cover - environment availability
        admin_engine.dispose()
        pytest.skip(f"isolated PostgreSQL on 55432 is unavailable: {exc}")

    database_engine = create_engine(database_url)
    try:
        from app.config import get_settings

        monkeypatch.setenv("DATABASE_URL", database_url.render_as_string(hide_password=False))
        get_settings.cache_clear()
        config = Config("alembic.ini")
        command.upgrade(config, "0057_session_workspace")
        legacy_id = uuid4()
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO simulation_runs "
                    "(id, use_case, model_version, policy_version, fusion, payload) "
                    "VALUES (:id, 'churn', 'old-model', 'old-policy', 'single', '{}'::jsonb)"
                ),
                {"id": legacy_id},
            )

        command.upgrade(config, "head")
        with database_engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT workspace_id, project_id, model_version, payload "
                    "FROM simulation_runs WHERE id = :id"
                ),
                {"id": legacy_id},
            ).one()
            constraints = set(
                connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'simulation_runs'::regclass"
                    )
                ).scalars()
            )
        assert row.workspace_id is None and row.project_id is None
        assert row.model_version == "old-model" and row.payload == {}
        assert {
            "fk_simulation_runs_workspace_id",
            "fk_simulation_runs_workspace_project",
            "ck_simulation_runs_project_requires_workspace",
            "uq_simulation_runs_workspace_id",
        } <= constraints
    finally:
        from app.config import get_settings

        get_settings.cache_clear()
        database_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()
