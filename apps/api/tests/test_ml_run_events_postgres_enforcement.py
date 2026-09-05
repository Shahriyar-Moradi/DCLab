from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import MlRunEvent, UserRole, Workspace
from app.engine.types import SearchConfig, TaskSpec
from app.services.auth_service import create_user
from app.services.lab_service import ingest_dataset, seed_dogfood, upsert_task
from app.services.lineage_service import (
    create_dataset_asset,
    create_pipeline_run,
    create_workflow,
    create_workflow_run,
    enable_workspace_domain,
    seed_business_domains,
)
from app.services.observability_service import append_ml_run_event
from app.services.project_service import create_project

POSTGRES_URL = os.environ.get(
    "MIGRATION_TEST_DATABASE_URL",
    "postgresql://localhost:55432/postgres",
)


def test_postgres_append_only_trigger_and_sequence_uniqueness(monkeypatch, tmp_path):
    admin_url = make_url(POSTGRES_URL)
    assert admin_url.host in {"localhost", "127.0.0.1"}
    assert admin_url.port == 55432

    database_name = f"decisionai_events_{uuid4().hex}"
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

    try:
        monkeypatch.setenv(
            "DATABASE_URL", database_url.render_as_string(hide_password=False)
        )
        from app.config import get_settings

        get_settings.cache_clear()
        command.upgrade(Config("alembic.ini"), "head")

        engine = create_engine(database_url)
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        try:
            with engine.connect() as connection:
                trigger = connection.execute(
                    text(
                        """
                        SELECT tgname FROM pg_trigger
                        WHERE tgname = 'ml_run_events_append_only'
                        """
                    )
                ).scalar()
                assert trigger == "ml_run_events_append_only"

            workspace = Workspace(
                slug=f"events-{uuid4().hex[:12]}", name="Events Tenant"
            )
            db.add(workspace)
            db.flush()
            actor = create_user(
                db,
                email=f"events-{uuid4().hex}@test.invalid",
                password="password",
                role=UserRole.BUSINESS_ADMIN,
                workspace_id=workspace.id,
            )
            seed_business_domains(db)
            domain = enable_workspace_domain(
                db,
                workspace_id=workspace.id,
                domain_slug="labs",
                actor=actor,
            )
            project = create_project(
                db,
                actor=actor,
                workspace_id=workspace.id,
                name="Events project",
                slug="events-project",
            )
            workflow = create_workflow(
                db,
                workspace_id=workspace.id,
                workspace_domain=domain,
                project_id=project.id,
                name="Events",
                slug="events",
                actor=actor,
            )
            env = seed_dogfood(db)
            csv_path = tmp_path / "events.csv"
            csv_path.write_text("feature,outcome\n1,0\n2,1\n")
            asset = create_dataset_asset(
                db,
                workspace_id=workspace.id,
                name="Events",
                actor=actor,
            )
            dataset = ingest_dataset(
                db,
                environment=env,
                name="Events",
                location=str(csv_path),
                workspace_id=workspace.id,
                dataset_asset=asset,
                created_by=actor.id,
            )
            task = upsert_task(
                db,
                env,
                TaskSpec(
                    id="events-task",
                    name="Events",
                    task_type="binary",
                    target="outcome",
                    entity_id=None,
                    evaluation_metric="pr_auc",
                    feature_groups={"features": ["feature"]},
                    validation_strategy="stratified",
                ),
            )
            run = create_workflow_run(
                db,
                workspace_id=workspace.id,
                workflow=workflow,
                requester=actor,
                trigger_type="manual",
                source_type="dataset",
            )
            pipeline = create_pipeline_run(
                db,
                workflow_run=run,
                environment=env,
                dataset=dataset,
                task=task,
                config=SearchConfig(seed=1),
            )
            db.commit()

            first = append_ml_run_event(
                db,
                workspace_id=workspace.id,
                workflow_run_id=run.id,
                experiment_id=pipeline.id,
                stage="ingestion",
                event_type="file_accepted",
                status="completed",
                payload={"ok": True},
            )
            second = append_ml_run_event(
                db,
                workspace_id=workspace.id,
                workflow_run_id=run.id,
                experiment_id=pipeline.id,
                stage="profiling_eda",
                event_type="profile_completed",
                status="completed",
                payload={"ok": True},
            )
            assert first.sequence == 1
            assert second.sequence == 2

            plan = db.execute(
                text(
                    "EXPLAIN (ANALYZE, BUFFERS) "
                    "SELECT * FROM ml_run_events "
                    "WHERE experiment_id = :experiment_id "
                    "ORDER BY sequence"
                ),
                {"experiment_id": pipeline.id},
            ).all()
            assert plan

            with pytest.raises(Exception, match="append-only"):
                db.execute(
                    text(
                        "UPDATE ml_run_events SET status = 'tampered' WHERE id = :id"
                    ),
                    {"id": first.id},
                )
                db.commit()
            db.rollback()

            with pytest.raises(Exception, match="append-only"):
                db.execute(
                    text("DELETE FROM ml_run_events WHERE id = :id"),
                    {"id": first.id},
                )
                db.commit()
            db.rollback()

            with pytest.raises(IntegrityError):
                db.execute(
                    text(
                        """
                        INSERT INTO ml_run_events (
                            id, workspace_id, workflow_run_id, experiment_id,
                            sequence, stage, event_type, status, timestamp, payload
                        ) VALUES (
                            :id, :workspace_id, :workflow_run_id, :experiment_id,
                            1, 'duplicate', 'duplicate', 'completed', :timestamp, '{}'::jsonb
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "workspace_id": workspace.id,
                        "workflow_run_id": run.id,
                        "experiment_id": pipeline.id,
                        "timestamp": datetime.now(UTC),
                    },
                )
                db.commit()
            db.rollback()

            def _append(index: int) -> int:
                session = SessionLocal()
                try:
                    event = append_ml_run_event(
                        session,
                        workspace_id=workspace.id,
                        workflow_run_id=run.id,
                        experiment_id=pipeline.id,
                        stage="concurrency",
                        event_type=f"concurrent_{index}",
                        status="completed",
                        payload={"index": index},
                    )
                    return event.sequence
                finally:
                    session.close()

            with ThreadPoolExecutor(max_workers=4) as pool:
                sequences = list(pool.map(_append, range(4)))
            assert len(sequences) == len(set(sequences))
            persisted = list(
                db.scalars(
                    select(MlRunEvent.sequence).where(
                        MlRunEvent.experiment_id == pipeline.id
                    )
                )
            )
            assert sorted(persisted) == list(range(1, len(persisted) + 1))
        finally:
            db.close()
            engine.dispose()
    finally:
        from app.config import get_settings

        get_settings.cache_clear()
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
