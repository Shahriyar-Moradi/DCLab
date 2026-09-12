"""PostgreSQL tenant-safe composite ON DELETE SET NULL behavior."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.db.models import (
    Artifact,
    ClientLabUpload,
    CodeSnapshot,
    PipelineStageRun,
    RuntimeEnvironment,
)
from app.services.lineage_service import create_pipeline_run, create_workflow_run
from conftest import ADMIN_URL
from test_execution_hierarchy import make_hierarchy


PRE_0044_ACTION_COLUMNS = {
    "fk_artifacts_workspace_pipeline_run": ["pipeline_run_id"],
    "fk_code_snapshots_workspace_pipeline_stage_run": ["pipeline_stage_run_id"],
    "fk_workflow_runs_workspace_source_upload": ["source_upload_id"],
}

EXPECTED_ACTION_COLUMNS = {
    **PRE_0044_ACTION_COLUMNS,
    "fk_client_lab_uploads_workspace_data_source": ["data_source_id"],
    "fk_client_lab_uploads_workspace_ingestion_run": ["ingestion_run_id"],
    "fk_ml_jobs_workspace_project": ["project_id"],
    "fk_ml_jobs_workspace_execution_request": ["execution_request_id"],
    "fk_ml_jobs_workspace_workflow_run": ["workflow_run_id"],
    "fk_ml_jobs_workspace_pipeline_run": ["pipeline_run_id"],
    "fk_execution_requests_workspace_parent": ["parent_request_id"],
    "fk_execution_requests_workspace_pipeline_run": ["pipeline_run_id"],
    "fk_execution_requests_workspace_project": ["project_id"],
    "fk_execution_requests_workspace_workflow_run": ["workflow_run_id"],
    "fk_data_accesses_workspace_project": ["project_id"],
    "fk_ingestion_runs_workspace_data_access": ["data_access_id"],
    "fk_ingestion_runs_data_source_data_access": ["data_access_id"],
    "fk_ingestion_runs_workspace_execution_request": ["execution_request_id"],
    "fk_data_access_events_workspace_execution_request": ["execution_request_id"],
    "fk_data_access_events_workspace_ingestion_run": ["ingestion_run_id"],
    "fk_visualizations_workspace_project": ["project_id"],
    "fk_visualizations_workspace_stage": ["pipeline_stage_run_id"],
    "fk_visualizations_workspace_candidate": ["candidate_id"],
    "fk_visualizations_workspace_evaluation": ["model_evaluation_id"],
    "fk_simulation_runs_workspace_project": ["project_id"],
    "fk_auth_sessions_rotated_from_user": ["rotated_from_id"],
}


def _artifact(*, workspace_id, project_id, object_key, pipeline_run_id=None):
    return Artifact(
        workspace_id=workspace_id,
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        artifact_type="source_code",
        provider="local",
        bucket=None,
        object_key=object_key,
        content_digest=uuid4().hex * 2,
        mime_type="application/octet-stream",
        size_bytes=1,
        extra_metadata={},
    )


@pytest.fixture()
def tenant_fk_rows(db_session, tmp_path):
    alpha_dir = tmp_path / "alpha"
    beta_dir = tmp_path / "beta"
    alpha_dir.mkdir()
    beta_dir.mkdir()
    alpha = make_hierarchy(db_session, alpha_dir)
    beta = make_hierarchy(db_session, beta_dir)

    upload = ClientLabUpload(
        run_id=uuid4(),
        workspace_id=alpha["workspace"].id,
        category="Revenue",
        original_filename="source.csv",
        stored_path="/tmp/source.csv",
        kind="spreadsheet",
        record_count=2,
        fields_noticed=["feature", "target"],
        has_named_fields=True,
    )
    cross_workspace_upload = ClientLabUpload(
        run_id=uuid4(),
        workspace_id=alpha["workspace"].id,
        category="Revenue",
        original_filename="cross.csv",
        stored_path="/tmp/cross.csv",
        kind="spreadsheet",
        record_count=2,
        fields_noticed=["feature", "target"],
        has_named_fields=True,
    )
    db_session.add_all([upload, cross_workspace_upload])
    db_session.flush()

    alpha_run = create_workflow_run(
        db_session,
        workspace_id=alpha["workspace"].id,
        workflow=alpha["workflow"],
        requester=alpha["actor"],
        trigger_type="manual",
        source_type="upload",
        source_upload=upload,
    )
    beta_run = create_workflow_run(
        db_session,
        workspace_id=beta["workspace"].id,
        workflow=beta["workflow"],
        requester=beta["actor"],
        trigger_type="manual",
        source_type="dataset",
    )
    artifact_pipeline = create_pipeline_run(
        db_session,
        workflow_run=alpha_run,
        environment=alpha["env"],
        dataset=alpha["dataset"],
        task=alpha["task"],
        pipeline_index=0,
    )
    snapshot_pipeline = create_pipeline_run(
        db_session,
        workflow_run=alpha_run,
        environment=alpha["env"],
        dataset=alpha["dataset"],
        task=alpha["task"],
        pipeline_index=1,
    )
    beta_pipeline = create_pipeline_run(
        db_session,
        workflow_run=beta_run,
        environment=beta["env"],
        dataset=beta["dataset"],
        task=beta["task"],
        pipeline_index=0,
    )
    stage = PipelineStageRun(
        workspace_id=alpha["workspace"].id,
        project_id=alpha["project"].id,
        pipeline_run_id=snapshot_pipeline.id,
        stage_key="source_capture",
        stage_type="execution",
        sequence=1,
        name="Source capture",
        status="completed",
        input_summary={},
        output_summary={},
    )
    linked_artifact = _artifact(
        workspace_id=alpha["workspace"].id,
        project_id=alpha["project"].id,
        object_key=f"linked/{uuid4().hex}",
        pipeline_run_id=artifact_pipeline.id,
    )
    source_artifact = _artifact(
        workspace_id=alpha["workspace"].id,
        project_id=alpha["project"].id,
        object_key=f"source/{uuid4().hex}",
        pipeline_run_id=snapshot_pipeline.id,
    )
    beta_artifact = _artifact(
        workspace_id=beta["workspace"].id,
        project_id=beta["project"].id,
        object_key=f"beta/{uuid4().hex}",
    )
    runtime = RuntimeEnvironment(
        python_version="3.12",
        os_name="test",
        os_version="1",
        architecture="test",
        hardware={},
        environment_digest=uuid4().hex * 2,
    )
    db_session.add_all([stage, linked_artifact, source_artifact, beta_artifact, runtime])
    db_session.flush()
    snapshot = CodeSnapshot(
        workspace_id=alpha["workspace"].id,
        project_id=alpha["project"].id,
        pipeline_run_id=snapshot_pipeline.id,
        pipeline_stage_run_id=stage.id,
        artifact_id=source_artifact.id,
        language="python",
        entrypoint="app.engine:run",
        code_digest=source_artifact.content_digest,
        runtime_environment_id=runtime.id,
    )
    db_session.add(snapshot)
    db_session.commit()
    return SimpleNamespace(
        alpha=alpha,
        beta=beta,
        upload=upload,
        cross_workspace_upload=cross_workspace_upload,
        alpha_run=alpha_run,
        beta_run=beta_run,
        artifact_pipeline=artifact_pipeline,
        snapshot_pipeline=snapshot_pipeline,
        beta_pipeline=beta_pipeline,
        stage=stage,
        linked_artifact=linked_artifact,
        beta_artifact=beta_artifact,
        runtime=runtime,
        snapshot=snapshot,
    )


def test_parent_deletes_clear_only_nullable_relationship_column(db_session, tenant_fk_rows):
    rows = tenant_fk_rows
    alpha_workspace_id = rows.alpha["workspace"].id

    db_session.execute(
        text("DELETE FROM client_lab_uploads WHERE id = :id"), {"id": rows.upload.id}
    )
    db_session.execute(
        text("DELETE FROM experiments WHERE id = :id"),
        {"id": rows.artifact_pipeline.id},
    )
    db_session.execute(
        text("DELETE FROM pipeline_stage_runs WHERE id = :id"), {"id": rows.stage.id}
    )
    db_session.commit()

    workflow_values = db_session.execute(
        text("SELECT workspace_id, source_upload_id FROM workflow_runs WHERE id = :id"),
        {"id": rows.alpha_run.id},
    ).one()
    artifact_values = db_session.execute(
        text("SELECT workspace_id, pipeline_run_id FROM artifacts WHERE id = :id"),
        {"id": rows.linked_artifact.id},
    ).one()
    snapshot_values = db_session.execute(
        text(
            "SELECT workspace_id, pipeline_stage_run_id "
            "FROM code_snapshots WHERE id = :id"
        ),
        {"id": rows.snapshot.id},
    ).one()

    assert workflow_values == (alpha_workspace_id, None)
    assert artifact_values == (alpha_workspace_id, None)
    assert snapshot_values == (alpha_workspace_id, None)


def test_cross_workspace_relationships_remain_rejected(db_session, tenant_fk_rows):
    rows = tenant_fk_rows

    with pytest.raises(DBAPIError, match="foreign key constraint"):
        db_session.execute(
            text("UPDATE workflow_runs SET source_upload_id = :parent WHERE id = :id"),
            {"parent": rows.cross_workspace_upload.id, "id": rows.beta_run.id},
        )
        db_session.commit()
    db_session.rollback()

    with pytest.raises(DBAPIError, match="foreign key constraint"):
        db_session.execute(
            text("UPDATE artifacts SET pipeline_run_id = :parent WHERE id = :id"),
            {"parent": rows.snapshot_pipeline.id, "id": rows.beta_artifact.id},
        )
        db_session.commit()
    db_session.rollback()

    with pytest.raises(DBAPIError, match="foreign key constraint"):
        db_session.execute(
            text(
                """
                INSERT INTO code_snapshots (
                    id, workspace_id, project_id, pipeline_run_id,
                    pipeline_stage_run_id, artifact_id, language, entrypoint,
                    code_digest, runtime_environment_id
                ) VALUES (
                    gen_random_uuid(), :workspace, :project, :pipeline_run,
                    :foreign_stage, :artifact, 'python', 'app.engine:run',
                    :digest, :runtime
                )
                """
            ),
            {
                "workspace": rows.beta["workspace"].id,
                "project": rows.beta["project"].id,
                "pipeline_run": rows.beta_pipeline.id,
                "foreign_stage": rows.stage.id,
                "artifact": rows.beta_artifact.id,
                "digest": uuid4().hex * 2,
                "runtime": rows.runtime.id,
            },
        )
        db_session.commit()
    db_session.rollback()


def _composite_set_null_actions(connection) -> dict[str, list[str]]:
    rows = connection.execute(
        text(
            """
            SELECT constraint_row.conname, array_agg(attribute_row.attname ORDER BY action_col.ordinality)
            FROM pg_constraint AS constraint_row
            CROSS JOIN LATERAL unnest(
                COALESCE(constraint_row.confdelsetcols, constraint_row.conkey)
            ) WITH ORDINALITY AS action_col(attnum, ordinality)
            JOIN pg_attribute AS attribute_row
              ON attribute_row.attrelid = constraint_row.conrelid
             AND attribute_row.attnum = action_col.attnum
            WHERE constraint_row.contype = 'f'
              AND constraint_row.confdeltype = 'n'
              AND cardinality(constraint_row.conkey) > 1
            GROUP BY constraint_row.conname
            ORDER BY constraint_row.conname
            """
        )
    )
    return {name: list(columns) for name, columns in rows}


def test_pg_constraint_has_no_unsafe_composite_set_null_tenant_fk(test_engine):
    with test_engine.connect() as connection:
        actions = _composite_set_null_actions(connection)
        unsafe = connection.execute(
            text(
                """
                SELECT DISTINCT constraint_row.conname
                FROM pg_constraint AS constraint_row
                CROSS JOIN LATERAL unnest(
                    COALESCE(constraint_row.confdelsetcols, constraint_row.conkey)
                ) AS action_col(attnum)
                JOIN pg_attribute AS attribute_row
                  ON attribute_row.attrelid = constraint_row.conrelid
                 AND attribute_row.attnum = action_col.attnum
                WHERE constraint_row.contype = 'f'
                  AND constraint_row.confdeltype = 'n'
                  AND cardinality(constraint_row.conkey) > 1
                  AND attribute_row.attnotnull
                """
            )
        ).scalars().all()

    assert actions == EXPECTED_ACTION_COLUMNS
    assert unsafe == []


def _isolated_database(monkeypatch):
    admin_url = make_url(ADMIN_URL)
    database_name = f"decisionai_tenant_fk_{uuid4().hex[:12]}"
    database_url = admin_url.set(database=database_name)
    admin_engine = create_engine(
        admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except Exception:
        admin_engine.dispose()
        raise
    # URL.__str__ masks the password. Render only for the temporary env.
    rendered = database_url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", rendered)
    from app.config import get_settings

    get_settings.cache_clear()
    return admin_engine, database_name, database_url


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


def test_alembic_existing_and_fresh_upgrade_remove_all_unsafe_constraints(monkeypatch):
    admin_engine, database_name, database_url = _isolated_database(monkeypatch)
    from app.config import get_settings

    engine = None
    try:
        engine = create_engine(database_url)
        config = Config("alembic.ini")
        command.upgrade(config, "0043_evidence_lock")
        with engine.connect() as connection:
            assert set(_composite_set_null_actions(connection)) == set(
                PRE_0044_ACTION_COLUMNS
            )
            assert all(
                columns == ["workspace_id", expected[0]]
                for name, columns in _composite_set_null_actions(connection).items()
                if (expected := PRE_0044_ACTION_COLUMNS.get(name))
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _composite_set_null_actions(connection) == EXPECTED_ACTION_COLUMNS

        command.downgrade(config, "base")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _composite_set_null_actions(connection) == EXPECTED_ACTION_COLUMNS
        command.check(config)
    finally:
        if engine is not None:
            engine.dispose()
        get_settings.cache_clear()
        _drop_isolated(admin_engine, database_name)
