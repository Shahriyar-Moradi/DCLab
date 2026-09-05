from __future__ import annotations

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

DEFAULT_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
POSTGRES_URL = os.environ.get(
    "MIGRATION_TEST_DATABASE_URL",
    "postgresql://localhost:55432/postgres",
)


def test_legacy_upload_lineage_is_repaired_without_moving_unlinked_rows(
    monkeypatch,
) -> None:
    admin_url = make_url(POSTGRES_URL)
    assert admin_url.host in {"localhost", "127.0.0.1"}
    assert admin_url.port == 55432

    database_name = f"decisionai_migration_{uuid4().hex}"
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
        monkeypatch.setenv("DATABASE_URL", database_url.render_as_string(hide_password=False))
        from app.config import get_settings

        get_settings.cache_clear()
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "0022_multi_tenant_identity")

        tenant_id = uuid4()
        environment_id = uuid4()
        task_id = uuid4()
        linked_dataset_id = uuid4()
        unlinked_dataset_id = uuid4()
        linked_experiment_id = uuid4()
        unlinked_experiment_id = uuid4()
        upload_id = uuid4()

        database_engine = create_engine(database_url)
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO workspaces (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant_id, "slug": f"tenant-{tenant_id.hex}", "name": "Tenant"},
            )
            connection.execute(
                text(
                    "INSERT INTO environments (id, org_id, name) "
                    "VALUES (:id, 'migration-test', 'Migration test')"
                ),
                {"id": environment_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO datasets
                        (id, environment_id, name, source_type, location, version)
                    VALUES
                        (:linked_id, :environment_id, 'Linked', 'file', '/linked', 'v1'),
                        (:unlinked_id, :environment_id, 'Unlinked', 'file', '/unlinked', 'v1')
                    """
                ),
                {
                    "linked_id": linked_dataset_id,
                    "unlinked_id": unlinked_dataset_id,
                    "environment_id": environment_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO prediction_tasks
                        (id, environment_id, slug, name, spec)
                    VALUES (:id, :environment_id, 'migration-task', 'Migration task', '{}')
                    """
                ),
                {"id": task_id, "environment_id": environment_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO experiments
                        (id, environment_id, task_id, dataset_id, config)
                    VALUES
                        (:linked_id, :environment_id, :task_id, :linked_dataset_id, '{}'),
                        (:unlinked_id, :environment_id, :task_id, :unlinked_dataset_id, '{}')
                    """
                ),
                {
                    "linked_id": linked_experiment_id,
                    "unlinked_id": unlinked_experiment_id,
                    "environment_id": environment_id,
                    "task_id": task_id,
                    "linked_dataset_id": linked_dataset_id,
                    "unlinked_dataset_id": unlinked_dataset_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO client_lab_uploads
                        (id, run_id, workspace_id, category, original_filename,
                         stored_path, kind, dataset_id, experiment_id)
                    VALUES
                        (:id, :id, :workspace_id, 'migration', 'linked.csv',
                         '/linked.csv', 'csv', :dataset_id, :experiment_id)
                    """
                ),
                {
                    "id": upload_id,
                    "workspace_id": tenant_id,
                    "dataset_id": linked_dataset_id,
                    "experiment_id": linked_experiment_id,
                },
            )

        command.upgrade(alembic_config, "0026_ml_run_events_append_only")
        with database_engine.connect() as connection:
            before = connection.execute(
                text(
                    "SELECT workspace_id FROM datasets WHERE id = :id"
                ),
                {"id": linked_dataset_id},
            ).scalar_one()
            assert str(before) == DEFAULT_WORKSPACE_ID

        command.upgrade(alembic_config, "head")
        with database_engine.connect() as connection:
            dataset_rows = {
                row.id: row.workspace_id
                for row in connection.execute(
                    text(
                        "SELECT id, workspace_id FROM datasets "
                        "WHERE id IN (:linked_id, :unlinked_id)"
                    ),
                    {
                        "linked_id": linked_dataset_id,
                        "unlinked_id": unlinked_dataset_id,
                    },
                )
            }
            experiment_rows = {
                row.id: row.workspace_id
                for row in connection.execute(
                    text(
                        "SELECT id, workspace_id FROM experiments "
                        "WHERE id IN (:linked_id, :unlinked_id)"
                    ),
                    {
                        "linked_id": linked_experiment_id,
                        "unlinked_id": unlinked_experiment_id,
                    },
                )
            }
            asset_rows = {
                row.id: row.workspace_id
                for row in connection.execute(
                    text(
                        "SELECT dataset.id AS id, asset.workspace_id AS workspace_id "
                        "FROM datasets AS dataset "
                        "JOIN dataset_assets AS asset "
                        "ON asset.id = dataset.dataset_asset_id "
                        "WHERE dataset.id IN (:linked_id, :unlinked_id)"
                    ),
                    {
                        "linked_id": linked_dataset_id,
                        "unlinked_id": unlinked_dataset_id,
                    },
                )
            }

        assert dataset_rows[linked_dataset_id] == tenant_id
        assert experiment_rows[linked_experiment_id] == tenant_id
        assert asset_rows[linked_dataset_id] == tenant_id
        assert str(dataset_rows[unlinked_dataset_id]) == DEFAULT_WORKSPACE_ID
        assert str(experiment_rows[unlinked_experiment_id]) == DEFAULT_WORKSPACE_ID
        assert str(asset_rows[unlinked_dataset_id]) == DEFAULT_WORKSPACE_ID
        database_engine.dispose()
    finally:
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
