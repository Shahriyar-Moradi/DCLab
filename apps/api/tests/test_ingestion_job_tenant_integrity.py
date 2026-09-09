"""PostgreSQL tenant FKs for DataSource, IngestionRun, Dataset, Labs upload, MlJob."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models import (
    ClientLabUpload,
    Dataset,
    MlJob,
    UserRole,
    Workspace,
)
from app.domain.ml_jobs import JOB_QUEUED, JOB_TYPE_AUTO_TRAIN
from app.services.auth_service import create_user
from app.services.data_source_service import create_data_source
from app.services.ingestion_run_service import start_ingestion_run
from app.services.lab_service import seed_dogfood
from app.services.lineage_service import create_dataset_asset
from app.services.project_service import create_project


def _user(db, *, workspace_id, prefix: str):
    return create_user(
        db,
        email=f"{prefix}-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name=prefix,
        workspace_id=workspace_id,
    )


def _workspace(db, name: str) -> Workspace:
    row = Workspace(slug=f"{name}-{uuid4().hex[:10]}", name=name)
    db.add(row)
    db.flush()
    return row


def _tenant(db, name: str):
    workspace = _workspace(db, name)
    actor = _user(db, workspace_id=workspace.id, prefix=name)
    project = create_project(
        db,
        actor=actor,
        workspace_id=workspace.id,
        name=f"{name} project",
        slug=f"{name}-project",
    )
    spare_project = create_project(
        db,
        actor=actor,
        workspace_id=workspace.id,
        name=f"{name} spare",
        slug=f"{name}-spare",
    )
    source = create_data_source(
        db,
        workspace_id=workspace.id,
        project_id=project.id,
        name=f"{name} files",
        source_type="upload",
        provider="local",
        created_by=actor.id,
    )
    run = start_ingestion_run(
        db,
        workspace_id=workspace.id,
        project_id=project.id,
        data_source_id=source.id,
    )
    asset = create_dataset_asset(
        db,
        workspace_id=workspace.id,
        name=f"{name} asset",
        slug=f"{name}-asset",
        actor=actor,
        project_id=project.id,
    )
    return SimpleNamespace(
        workspace=workspace,
        actor=actor,
        project=project,
        spare_project=spare_project,
        source=source,
        run=run,
        asset=asset,
    )


@pytest.fixture()
def ingest_tenants(db_session):
    env = seed_dogfood(db_session)
    alpha = _tenant(db_session, "ingest-alpha")
    beta = _tenant(db_session, "ingest-beta")
    db_session.commit()
    return SimpleNamespace(alpha=alpha, beta=beta, env=env)


def _commit(db_session, sql: str, **params):
    db_session.execute(text(sql), params)
    db_session.commit()


def _reject(db_session, sql: str, **params):
    with pytest.raises(DBAPIError, match="foreign key constraint"):
        _commit(db_session, sql, **params)
    db_session.rollback()


def _insert_ingestion_run(workspace_id, project_id, data_source_id):
    return (
        """
        INSERT INTO ingestion_runs (
            id, workspace_id, project_id, data_source_id, status, started_at,
            rows_read, rows_written, bytes_read
        ) VALUES (
            gen_random_uuid(), :workspace_id, :project_id, :data_source_id,
            'queued', :started_at, 0, 0, 0
        )
        """,
        {
            "workspace_id": workspace_id,
            "project_id": project_id,
            "data_source_id": data_source_id,
            "started_at": datetime.now(UTC),
        },
    )


def _insert_dataset(workspace_id, project_id, asset_id, environment_id, ingestion_run_id):
    return (
        """
        INSERT INTO datasets (
            id, workspace_id, dataset_asset_id, environment_id, project_id,
            ingestion_run_id, name, source_type, location, version,
            row_count, column_count
        ) VALUES (
            gen_random_uuid(), :workspace_id, :asset_id, :environment_id,
            :project_id, :ingestion_run_id, :name, 'csv', '/tmp/tenant.csv',
            :version, 0, 0
        )
        """,
        {
            "workspace_id": workspace_id,
            "project_id": project_id,
            "asset_id": asset_id,
            "environment_id": environment_id,
            "ingestion_run_id": ingestion_run_id,
            "name": "tenant-dataset",
            "version": f"v-{uuid4().hex[:12]}",
        },
    )


def _insert_upload(workspace_id, data_source_id, ingestion_run_id):
    upload_id = uuid4()
    return (
        """
        INSERT INTO client_lab_uploads (
            id, run_id, workspace_id, category, original_filename, stored_path,
            kind, record_count, fields_noticed, has_named_fields, pipeline_status,
            client_status, data_source_id, ingestion_run_id
        ) VALUES (
            :id, :id, :workspace_id, 'Revenue', 'upload.csv', '/tmp/upload.csv',
            'spreadsheet', 2, '[]'::jsonb, true, 'not_applicable', 'queued',
            :data_source_id, :ingestion_run_id
        )
        """,
        {
            "id": upload_id,
            "workspace_id": workspace_id,
            "data_source_id": data_source_id,
            "ingestion_run_id": ingestion_run_id,
        },
        upload_id,
    )


def _insert_ml_job(workspace_id, project_id, upload_id):
    return (
        """
        INSERT INTO ml_jobs (
            id, workspace_id, project_id, job_type, target_id, upload_id, status,
            attempts, max_attempts
        ) VALUES (
            gen_random_uuid(), :workspace_id, :project_id, :job_type, :target_id,
            :upload_id, :status, 0, 3
        )
        """,
        {
            "workspace_id": workspace_id,
            "project_id": project_id,
            "job_type": JOB_TYPE_AUTO_TRAIN,
            "target_id": uuid4(),
            "upload_id": upload_id,
            "status": JOB_QUEUED,
        },
    )


def test_postgres_rejects_cross_workspace_ingestion_run_data_source(db_session, ingest_tenants):
    alpha = ingest_tenants.alpha
    beta = ingest_tenants.beta
    sql, params = _insert_ingestion_run(
        alpha.workspace.id, alpha.project.id, beta.source.id
    )
    _reject(db_session, sql, **params)


def test_postgres_rejects_rebinding_ingestion_run_to_foreign_data_source(
    db_session, ingest_tenants
):
    alpha = ingest_tenants.alpha
    beta = ingest_tenants.beta
    _reject(
        db_session,
        "UPDATE ingestion_runs SET data_source_id = :data_source_id WHERE id = :id",
        data_source_id=beta.source.id,
        id=alpha.run.id,
    )


def test_same_workspace_ingestion_run_data_source_is_accepted(db_session, ingest_tenants):
    alpha = ingest_tenants.alpha
    sql, params = _insert_ingestion_run(
        alpha.workspace.id, alpha.project.id, alpha.source.id
    )
    _commit(db_session, sql, **params)
    count = db_session.execute(
        text(
            """
            SELECT COUNT(*) FROM ingestion_runs
            WHERE workspace_id = :workspace_id AND data_source_id = :data_source_id
            """
        ),
        {"workspace_id": alpha.workspace.id, "data_source_id": alpha.source.id},
    ).scalar()
    assert count >= 2


def test_postgres_rejects_cross_workspace_dataset_ingestion_run(db_session, ingest_tenants):
    alpha = ingest_tenants.alpha
    beta = ingest_tenants.beta
    sql, params = _insert_dataset(
        alpha.workspace.id,
        alpha.project.id,
        alpha.asset.id,
        ingest_tenants.env.id,
        beta.run.id,
    )
    _reject(db_session, sql, **params)


def test_same_workspace_dataset_ingestion_run_is_accepted(db_session, ingest_tenants):
    alpha = ingest_tenants.alpha
    sql, params = _insert_dataset(
        alpha.workspace.id,
        alpha.project.id,
        alpha.asset.id,
        ingest_tenants.env.id,
        alpha.run.id,
    )
    _commit(db_session, sql, **params)
    linked = db_session.execute(
        text(
            """
            SELECT workspace_id FROM datasets
            WHERE ingestion_run_id = :ingestion_run_id
            """
        ),
        {"ingestion_run_id": alpha.run.id},
    ).scalar()
    assert linked == alpha.workspace.id


def test_postgres_rejects_labs_upload_foreign_data_source_and_ingestion_run(
    db_session, ingest_tenants
):
    alpha = ingest_tenants.alpha
    beta = ingest_tenants.beta
    sql, params, _upload_id = _insert_upload(
        alpha.workspace.id, beta.source.id, alpha.run.id
    )
    _reject(db_session, sql, **params)
    sql, params, _upload_id = _insert_upload(
        alpha.workspace.id, alpha.source.id, beta.run.id
    )
    _reject(db_session, sql, **params)


def test_same_workspace_labs_upload_lineage_is_accepted(db_session, ingest_tenants):
    alpha = ingest_tenants.alpha
    sql, params, upload_id = _insert_upload(
        alpha.workspace.id, alpha.source.id, alpha.run.id
    )
    _commit(db_session, sql, **params)
    row = db_session.execute(
        text(
            """
            SELECT workspace_id, data_source_id, ingestion_run_id
            FROM client_lab_uploads WHERE id = :id
            """
        ),
        {"id": upload_id},
    ).one()
    assert row == (alpha.workspace.id, alpha.source.id, alpha.run.id)


def test_postgres_rejects_ml_job_foreign_project_and_upload(db_session, ingest_tenants):
    alpha = ingest_tenants.alpha
    beta = ingest_tenants.beta
    alpha_sql, alpha_params, alpha_upload_id = _insert_upload(
        alpha.workspace.id, alpha.source.id, alpha.run.id
    )
    beta_sql, beta_params, beta_upload_id = _insert_upload(
        beta.workspace.id, beta.source.id, beta.run.id
    )
    _commit(db_session, alpha_sql, **alpha_params)
    _commit(db_session, beta_sql, **beta_params)

    sql, params = _insert_ml_job(
        alpha.workspace.id, beta.project.id, alpha_upload_id
    )
    _reject(db_session, sql, **params)
    sql, params = _insert_ml_job(
        alpha.workspace.id, alpha.project.id, beta_upload_id
    )
    _reject(db_session, sql, **params)


def test_same_workspace_ml_job_project_and_upload_are_accepted(db_session, ingest_tenants):
    alpha = ingest_tenants.alpha
    sql, params, upload_id = _insert_upload(
        alpha.workspace.id, alpha.source.id, alpha.run.id
    )
    _commit(db_session, sql, **params)
    sql, params = _insert_ml_job(alpha.workspace.id, alpha.project.id, upload_id)
    _commit(db_session, sql, **params)
    row = db_session.execute(
        text(
            """
            SELECT workspace_id, project_id, upload_id FROM ml_jobs
            WHERE upload_id = :upload_id
            """
        ),
        {"upload_id": upload_id},
    ).one()
    assert row == (alpha.workspace.id, alpha.project.id, upload_id)


def test_data_source_delete_cascades_unreferenced_ingestion_run(db_session, ingest_tenants):
    alpha = ingest_tenants.alpha
    orphan_source = create_data_source(
        db_session,
        workspace_id=alpha.workspace.id,
        project_id=alpha.project.id,
        name="orphan source",
        source_type="upload",
        provider="local",
        created_by=alpha.actor.id,
    )
    orphan_run = start_ingestion_run(
        db_session,
        workspace_id=alpha.workspace.id,
        project_id=alpha.project.id,
        data_source_id=orphan_source.id,
    )
    db_session.commit()
    run_id = orphan_run.id
    source_id = orphan_source.id
    _commit(db_session, "DELETE FROM data_sources WHERE id = :id", id=source_id)
    leftover = db_session.execute(
        text("SELECT COUNT(*) FROM ingestion_runs WHERE id = :id"),
        {"id": run_id},
    ).scalar()
    assert leftover == 0


def test_labs_upload_set_null_clears_only_lineage_columns(db_session, ingest_tenants):
    alpha = ingest_tenants.alpha
    source = create_data_source(
        db_session,
        workspace_id=alpha.workspace.id,
        project_id=alpha.project.id,
        name="upload source",
        source_type="upload",
        provider="local",
        created_by=alpha.actor.id,
    )
    run = start_ingestion_run(
        db_session,
        workspace_id=alpha.workspace.id,
        project_id=alpha.project.id,
        data_source_id=source.id,
    )
    sql, params, upload_id = _insert_upload(alpha.workspace.id, source.id, run.id)
    _commit(db_session, sql, **params)

    _commit(db_session, "DELETE FROM data_sources WHERE id = :id", id=source.id)
    row = db_session.execute(
        text(
            """
            SELECT workspace_id, data_source_id, ingestion_run_id
            FROM client_lab_uploads WHERE id = :id
            """
        ),
        {"id": upload_id},
    ).one()
    assert row.workspace_id == alpha.workspace.id
    assert row.data_source_id is None
    assert row.ingestion_run_id is None


def test_dataset_blocks_delete_of_its_ingestion_run(db_session, ingest_tenants):
    alpha = ingest_tenants.alpha
    sql, params = _insert_dataset(
        alpha.workspace.id,
        alpha.project.id,
        alpha.asset.id,
        ingest_tenants.env.id,
        alpha.run.id,
    )
    _commit(db_session, sql, **params)
    with pytest.raises(DBAPIError):
        _commit(db_session, "DELETE FROM ingestion_runs WHERE id = :id", id=alpha.run.id)
    db_session.rollback()
    leftover = db_session.execute(
        text("SELECT COUNT(*) FROM ingestion_runs WHERE id = :id"),
        {"id": alpha.run.id},
    ).scalar()
    assert leftover == 1


def test_ml_job_upload_delete_cascades_and_project_delete_nulls_only_project_id(
    db_session, ingest_tenants
):
    alpha = ingest_tenants.alpha
    sql, params, upload_id = _insert_upload(
        alpha.workspace.id, alpha.source.id, alpha.run.id
    )
    _commit(db_session, sql, **params)
    job_sql, job_params = _insert_ml_job(
        alpha.workspace.id, alpha.spare_project.id, upload_id
    )
    _commit(db_session, job_sql, **job_params)
    job_id = db_session.execute(
        text("SELECT id FROM ml_jobs WHERE upload_id = :upload_id"),
        {"upload_id": upload_id},
    ).scalar()

    _commit(db_session, "DELETE FROM projects WHERE id = :id", id=alpha.spare_project.id)
    after_project = db_session.execute(
        text("SELECT workspace_id, project_id, upload_id FROM ml_jobs WHERE id = :id"),
        {"id": job_id},
    ).one()
    assert after_project == (alpha.workspace.id, None, upload_id)

    _commit(db_session, "DELETE FROM client_lab_uploads WHERE id = :id", id=upload_id)
    leftover = db_session.execute(
        text("SELECT COUNT(*) FROM ml_jobs WHERE id = :id"),
        {"id": job_id},
    ).scalar()
    assert leftover == 0


def test_orm_same_workspace_relationships_still_flush(db_session, ingest_tenants):
    alpha = ingest_tenants.alpha
    dataset = Dataset(
        workspace_id=alpha.workspace.id,
        dataset_asset_id=alpha.asset.id,
        environment_id=ingest_tenants.env.id,
        project_id=alpha.project.id,
        ingestion_run_id=alpha.run.id,
        name="orm-dataset",
        source_type="csv",
        location="/tmp/orm.csv",
        version=f"v-{uuid4().hex[:8]}",
        row_count=0,
        column_count=0,
    )
    upload = ClientLabUpload(
        workspace_id=alpha.workspace.id,
        category="Revenue",
        original_filename="orm.csv",
        stored_path="/tmp/orm.csv",
        kind="spreadsheet",
        record_count=1,
        fields_noticed=["x"],
        has_named_fields=True,
        data_source_id=alpha.source.id,
        ingestion_run_id=alpha.run.id,
    )
    db_session.add_all([dataset, upload])
    db_session.flush()
    job = MlJob(
        workspace_id=alpha.workspace.id,
        project_id=alpha.project.id,
        job_type=JOB_TYPE_AUTO_TRAIN,
        target_id=upload.id,
        upload_id=upload.id,
        status=JOB_QUEUED,
        attempts=0,
        max_attempts=3,
    )
    db_session.add(job)
    db_session.flush()
    db_session.expire_all()
    stored_upload = db_session.get(ClientLabUpload, upload.id)
    stored_job = db_session.get(MlJob, job.id)
    stored_dataset = db_session.get(Dataset, dataset.id)
    assert stored_dataset.ingestion_run_id == alpha.run.id
    assert stored_upload.data_source_id == alpha.source.id
    assert stored_upload.ingestion_run_id == alpha.run.id
    assert stored_job.project_id == alpha.project.id
    assert stored_job.upload_id == upload.id
