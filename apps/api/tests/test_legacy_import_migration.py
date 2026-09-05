"""Fresh-head and pre-redesign → head migration scenarios for Prompt 9."""

from __future__ import annotations

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from conftest import ADMIN_URL
from app.db.models import DEFAULT_WORKSPACE_ID, UserRole, WorkspaceKind
from app.domain.workspace_identity import LEGACY_IMPORT_PROJECT_SLUG
from app.services.auth_service import hash_password, register_customer
from app.services.problem_spec_service import create_problem_spec
from app.services.project_service import create_project
from app.services.workspace_service import create_personal_workspace


def _alembic_config() -> Config:
    return Config("alembic.ini")


def _isolated_database(monkeypatch):
    admin_url = make_url(ADMIN_URL)
    database_name = f"decisionai_prompt9_{uuid4().hex[:12]}"
    database_url = admin_url.set(database=database_name)
    admin_engine = create_engine(
        admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except Exception as exc:  # pragma: no cover - environment availability
        admin_engine.dispose()
        pytest.skip(f"cannot create isolated migration database: {exc}")

    rendered = database_url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", rendered)
    from app.config import get_settings

    get_settings.cache_clear()
    return admin_engine, database_name, database_url, _alembic_config()


def _drop_isolated(admin_engine, database_name: str) -> None:
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


def test_fresh_database_upgrade_matches_metadata_then_seed_and_identity_e2e(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch
    )
    try:
        command.upgrade(alembic_config, "head")
        command.check(alembic_config)

        engine = create_engine(database_url)
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        try:
            from app.services.auth_service import ensure_demo_users

            ensure_demo_users(db)
            db.commit()
            owner = register_customer(
                db,
                email=f"fresh-{uuid4().hex}@test.invalid",
                password="test-password",
                full_name="Fresh Owner",
            )
            workspace = create_personal_workspace(db, owner=owner, name="Fresh Personal")
            project = create_project(
                db, actor=owner, workspace_id=workspace.id, name="Fresh case"
            )
            spec = create_problem_spec(
                db,
                actor=owner,
                workspace_id=workspace.id,
                project_id=project.id,
                task_type="classification",
                business_objective="Fresh-database identity path",
            )
            db.commit()
            assert workspace.kind == WorkspaceKind.PERSONAL.value
            assert owner.role == UserRole.WORKSPACE_OWNER.value
            assert spec.project_id == project.id
            current = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert current == "0036_legacy_import_projects"
        finally:
            db.close()
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


def test_existing_pre_redesign_database_preserves_legacy_and_attaches_compatibility_project(
    monkeypatch,
):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch
    )
    user_id = uuid4()
    environment_id = uuid4()
    asset_a = uuid4()
    asset_b = uuid4()
    dataset_a = uuid4()
    dataset_b = uuid4()
    task_id = uuid4()
    experiment_a = uuid4()
    experiment_b = uuid4()
    workflow_a = uuid4()
    workflow_b = uuid4()
    run_a = uuid4()
    run_b = uuid4()
    upload_id = uuid4()
    candidate_id = uuid4()
    opportunity_id = uuid4()
    orphan_workspace = uuid4()
    orphan_asset = uuid4()
    orphan_dataset = uuid4()
    try:
        command.upgrade(alembic_config, "0028_semantic_leakage_purpose")
        engine = create_engine(database_url)
        password_hash = hash_password("test-password")
        with engine.begin() as connection:
            labs_domain = connection.execute(
                text(
                    """
                    SELECT wd.id
                    FROM workspace_domains AS wd
                    JOIN business_domains AS bd ON bd.id = wd.business_domain_id
                    WHERE wd.workspace_id = :workspace_id AND bd.slug = 'labs'
                    """
                ),
                {"workspace_id": DEFAULT_WORKSPACE_ID},
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO users (id, email, password_hash, role, full_name, workspace_id)
                    VALUES (:id, :email, :password_hash, 'business_admin', 'Legacy Owner', :workspace_id)
                    """
                ),
                {
                    "id": user_id,
                    "email": f"legacy-{user_id.hex}@test.invalid",
                    "password_hash": password_hash,
                    "workspace_id": DEFAULT_WORKSPACE_ID,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO workspace_memberships (id, workspace_id, user_id, role)
                    VALUES (:id, :workspace_id, :user_id, 'business_admin')
                    """
                ),
                {
                    "id": uuid4(),
                    "workspace_id": DEFAULT_WORKSPACE_ID,
                    "user_id": user_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO environments (id, org_id, name) VALUES (:id, 'legacy', 'Legacy env')"
                ),
                {"id": environment_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO dataset_assets (id, workspace_id, name, slug, description)
                    VALUES
                        (:asset_a, :workspace_id, 'Asset A', 'asset-a', 'Historical asset A'),
                        (:asset_b, :workspace_id, 'Asset B', 'asset-b', 'Historical asset B')
                    """
                ),
                {
                    "asset_a": asset_a,
                    "asset_b": asset_b,
                    "workspace_id": DEFAULT_WORKSPACE_ID,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO datasets
                        (id, environment_id, name, source_type, location, version,
                         workspace_id, dataset_asset_id)
                    VALUES
                        (:dataset_a, :environment_id, 'Dataset A', 'file', '/a.csv', 'v1',
                         :workspace_id, :asset_a),
                        (:dataset_b, :environment_id, 'Dataset B', 'file', '/b.csv', 'v1',
                         :workspace_id, :asset_b)
                    """
                ),
                {
                    "dataset_a": dataset_a,
                    "dataset_b": dataset_b,
                    "environment_id": environment_id,
                    "workspace_id": DEFAULT_WORKSPACE_ID,
                    "asset_a": asset_a,
                    "asset_b": asset_b,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO prediction_tasks (id, environment_id, slug, name, spec)
                    VALUES (:id, :environment_id, 'legacy-task', 'Legacy task', '{}'::jsonb)
                    """
                ),
                {"id": task_id, "environment_id": environment_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO ml_workflows
                        (id, workspace_id, workspace_domain_id, name, slug, description,
                         business_objective, status, config)
                    VALUES
                        (:workflow_a, :workspace_id, :domain_id, 'Workflow A', 'hist-a',
                         'First historical workflow', 'objective a', 'active', '{}'::jsonb),
                        (:workflow_b, :workspace_id, :domain_id, 'Workflow B', 'hist-b',
                         'Second historical workflow', 'objective b', 'active', '{}'::jsonb)
                    """
                ),
                {
                    "workflow_a": workflow_a,
                    "workflow_b": workflow_b,
                    "workspace_id": DEFAULT_WORKSPACE_ID,
                    "domain_id": labs_domain,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO workflow_runs
                        (id, workspace_id, workflow_id, trigger_type, source_type, status)
                    VALUES
                        (:run_a, :workspace_id, :workflow_a, 'manual', 'dataset', 'completed'),
                        (:run_b, :workspace_id, :workflow_b, 'manual', 'dataset', 'completed')
                    """
                ),
                {
                    "run_a": run_a,
                    "run_b": run_b,
                    "workspace_id": DEFAULT_WORKSPACE_ID,
                    "workflow_a": workflow_a,
                    "workflow_b": workflow_b,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO experiments
                        (id, environment_id, task_id, dataset_id, config, workspace_id,
                         workflow_run_id, pipeline_name, pipeline_index)
                    VALUES
                        (:experiment_a, :environment_id, :task_id, :dataset_a, '{}'::jsonb,
                         :workspace_id, :run_a, 'deterministic_ml', 0),
                        (:experiment_b, :environment_id, :task_id, :dataset_b, '{}'::jsonb,
                         :workspace_id, :run_b, 'deterministic_ml', 0)
                    """
                ),
                {
                    "experiment_a": experiment_a,
                    "experiment_b": experiment_b,
                    "environment_id": environment_id,
                    "task_id": task_id,
                    "dataset_a": dataset_a,
                    "dataset_b": dataset_b,
                    "workspace_id": DEFAULT_WORKSPACE_ID,
                    "run_a": run_a,
                    "run_b": run_b,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO experiment_candidates
                        (id, experiment_id, candidate_key, fingerprint, payload)
                    VALUES (:id, :experiment_id, 'hist-candidate', :fingerprint, '{}'::jsonb)
                    """
                ),
                {
                    "id": candidate_id,
                    "experiment_id": experiment_a,
                    "fingerprint": "a" * 40,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO client_lab_uploads
                        (id, run_id, workspace_id, category, original_filename, stored_path,
                         kind, dataset_id, experiment_id, client_status, pipeline_status)
                    VALUES
                        (:id, :id, :workspace_id, 'Revenue', 'legacy.csv', '/legacy.csv',
                         'spreadsheet', :dataset_id, :experiment_id, 'completed', 'completed')
                    """
                ),
                {
                    "id": upload_id,
                    "workspace_id": DEFAULT_WORKSPACE_ID,
                    "dataset_id": dataset_a,
                    "experiment_id": experiment_a,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO opportunities
                        (id, workspace_id, external_id, customer_id, amount, stage, source, owner_id)
                    VALUES
                        (:id, :workspace_id, 'opp-legacy', 'cust-1', 1000, 'proposal',
                         'inbound', 'rep-1')
                    """
                ),
                {"id": opportunity_id, "workspace_id": DEFAULT_WORKSPACE_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO workspaces (id, slug, name) VALUES (:id, :slug, 'Orphan tenant')"
                ),
                {"id": orphan_workspace, "slug": f"orphan-{orphan_workspace.hex[:8]}"},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO dataset_assets (id, workspace_id, name, slug, description)
                    VALUES (:id, :workspace_id, 'Orphan asset', 'orphan-asset', 'No actor')
                    """
                ),
                {"id": orphan_asset, "workspace_id": orphan_workspace},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO datasets
                        (id, environment_id, name, source_type, location, version,
                         workspace_id, dataset_asset_id)
                    VALUES
                        (:id, :environment_id, 'Orphan dataset', 'file', '/orphan.csv', 'v1',
                         :workspace_id, :asset_id)
                    """
                ),
                {
                    "id": orphan_dataset,
                    "environment_id": environment_id,
                    "workspace_id": orphan_workspace,
                    "asset_id": orphan_asset,
                },
            )

        command.upgrade(alembic_config, "head")
        with engine.connect() as connection:
            project_id = connection.execute(
                text(
                    """
                    SELECT id FROM projects
                    WHERE workspace_id = :workspace_id AND slug = :slug
                    """
                ),
                {
                    "workspace_id": DEFAULT_WORKSPACE_ID,
                    "slug": LEGACY_IMPORT_PROJECT_SLUG,
                },
            ).scalar_one()
            assert connection.execute(
                text("SELECT COUNT(*) FROM projects WHERE workspace_id = :id AND slug = :slug"),
                {"id": orphan_workspace, "slug": LEGACY_IMPORT_PROJECT_SLUG},
            ).scalar_one() == 0
            assert connection.execute(
                text("SELECT project_id FROM datasets WHERE id = :id"),
                {"id": orphan_dataset},
            ).scalar_one() is None
            for table, row_id in (
                ("datasets", dataset_a),
                ("datasets", dataset_b),
                ("dataset_assets", asset_a),
                ("experiments", experiment_a),
                ("experiments", experiment_b),
                ("ml_workflows", workflow_a),
                ("ml_workflows", workflow_b),
                ("workflow_runs", run_a),
                ("experiment_candidates", candidate_id),
            ):
                attached = connection.execute(
                    text(f"SELECT project_id FROM {table} WHERE id = :id"),
                    {"id": row_id},
                ).scalar_one()
                assert attached == project_id, table
            workflow_ids = list(
                connection.execute(
                    text(
                        "SELECT id FROM ml_workflows WHERE workspace_id = :workspace_id ORDER BY slug"
                    ),
                    {"workspace_id": DEFAULT_WORKSPACE_ID},
                ).scalars()
            )
            assert workflow_a in workflow_ids and workflow_b in workflow_ids
            assert workflow_a != workflow_b
            assert connection.execute(
                text("SELECT id FROM opportunities WHERE id = :id"),
                {"id": opportunity_id},
            ).scalar_one() == opportunity_id
            assert connection.execute(
                text("SELECT dataset_id, experiment_id FROM client_lab_uploads WHERE id = :id"),
                {"id": upload_id},
            ).one() == (dataset_a, experiment_a)
            assert connection.execute(
                text("SELECT COUNT(*) FROM experiments WHERE id IN (:a, :b)"),
                {"a": experiment_a, "b": experiment_b},
            ).scalar_one() == 2
            project_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM projects WHERE workspace_id = :workspace_id"
                ),
                {"workspace_id": DEFAULT_WORKSPACE_ID},
            ).scalar_one()
            assert project_count == 1
            assert connection.execute(
                text(
                    """
                    SELECT COUNT(*) FROM experiments
                    WHERE workspace_id = :workspace_id AND project_id = :project_id
                    """
                ),
                {"workspace_id": DEFAULT_WORKSPACE_ID, "project_id": project_id},
            ).scalar_one() == 2
            assert connection.execute(
                text(
                    """
                    SELECT COUNT(*) FROM ml_workflows
                    WHERE workspace_id = :workspace_id AND project_id = :project_id
                    """
                ),
                {"workspace_id": DEFAULT_WORKSPACE_ID, "project_id": project_id},
            ).scalar_one() == 2
        engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)
