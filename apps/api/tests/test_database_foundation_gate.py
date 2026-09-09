"""Physical database foundation gate for Alembic head 0047.

Cross-tenant and delete assertions go through raw SQL. Alembic current/check
and compare_metadata live in test_historical_alembic_revisions.py against
isolated databases (fresh head and 0028 → head).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models import (
    Artifact,
    ClientLabUpload,
    ExperimentCandidate,
    MlJob,
    PipelineScientificPlan,
)
from app.domain.ml_jobs import JOB_QUEUED, JOB_TYPE_AUTO_TRAIN
from app.services.artifact_service import store_artifact
from app.services.data_source_service import create_data_source
from app.services.ingestion_run_service import start_ingestion_run
from app.services.lineage_service import (
    create_model_asset,
    create_pipeline_run,
    create_workflow_run,
)
from app.storage.local import LocalStorage
from test_data_model_lineage import make_lineage_setup

CURRENT_HEAD = "0047_personal_dev_identity"

IMPORTANT_DELETE_ACTIONS = {
    "fk_ingestion_runs_workspace_data_source": "c",
    "fk_datasets_workspace_ingestion_run": "a",
    "fk_client_lab_uploads_workspace_data_source": "n",
    "fk_client_lab_uploads_workspace_ingestion_run": "n",
    "fk_ml_jobs_workspace_project": "n",
    "fk_ml_jobs_workspace_upload": "c",
    "fk_workflow_runs_workspace_source_upload": "n",
    "fk_artifacts_workspace_pipeline_run": "n",
    "fk_pipeline_scientific_plans_workspace_pipeline_run": "c",
    "fk_experiment_candidates_workspace_pipeline_run": "c",
    "fk_code_snapshots_workspace_pipeline_run": "c",
}


@pytest.fixture
def foundation(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    alpha_run = create_workflow_run(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    beta_run = create_workflow_run(
        db_session,
        workspace_id=setup["beta"].id,
        workflow=setup["beta_workflow"],
        requester=setup["beta_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    alpha_pipeline = create_pipeline_run(
        db_session,
        workflow_run=alpha_run,
        environment=setup["env"],
        dataset=setup["alpha_dataset"],
        task=setup["task"],
    )
    beta_pipeline = create_pipeline_run(
        db_session,
        workflow_run=beta_run,
        environment=setup["env"],
        dataset=setup["beta_dataset"],
        task=setup["task"],
    )
    alpha_candidate = ExperimentCandidate(
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        experiment_id=alpha_pipeline.id,
        candidate_key="alpha",
        fingerprint="a" * 40,
        status="generated",
        payload={},
    )
    beta_candidate = ExperimentCandidate(
        workspace_id=setup["beta"].id,
        project_id=setup["beta_project"].id,
        experiment_id=beta_pipeline.id,
        candidate_key="beta",
        fingerprint="b" * 40,
        status="generated",
        payload={},
    )
    alpha_source = create_data_source(
        db_session,
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        name="alpha source",
        source_type="upload",
        provider="local",
        created_by=setup["alpha_admin"].id,
    )
    beta_source = create_data_source(
        db_session,
        workspace_id=setup["beta"].id,
        project_id=setup["beta_project"].id,
        name="beta source",
        source_type="upload",
        provider="local",
        created_by=setup["beta_admin"].id,
    )
    alpha_ingest = start_ingestion_run(
        db_session,
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        data_source_id=alpha_source.id,
    )
    beta_ingest = start_ingestion_run(
        db_session,
        workspace_id=setup["beta"].id,
        project_id=setup["beta_project"].id,
        data_source_id=beta_source.id,
    )
    storage = LocalStorage(root=tmp_path / "objects")
    alpha_artifact = store_artifact(
        db_session,
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        pipeline_run_id=alpha_pipeline.id,
        artifact_type="dataset",
        filename="alpha.csv",
        data=b"a,b\n1,0\n",
        storage=storage,
    )
    beta_artifact = store_artifact(
        db_session,
        workspace_id=setup["beta"].id,
        project_id=setup["beta_project"].id,
        pipeline_run_id=beta_pipeline.id,
        artifact_type="dataset",
        filename="beta.csv",
        data=b"a,b\n1,0\n",
        storage=storage,
    )
    alpha_plan = PipelineScientificPlan(
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        pipeline_run_id=alpha_pipeline.id,
        task_type="binary",
        holdout_strategy="random",
        holdout_test_size=0.2,
        validation_strategy="stratified_kfold",
        requested_folds=5,
        primary_metric="pr_auc",
        holdout_plan_digest="c" * 64,
        model_development_plan_digest="d" * 64,
        full_plan={},
        locked_at=datetime.now(UTC),
    )
    alpha_upload = ClientLabUpload(
        workspace_id=setup["alpha"].id,
        category="Revenue",
        original_filename="alpha.csv",
        stored_path="/tmp/alpha.csv",
        kind="spreadsheet",
        record_count=2,
        fields_noticed=["a", "b"],
        has_named_fields=True,
        data_source_id=alpha_source.id,
        ingestion_run_id=alpha_ingest.id,
    )
    beta_upload = ClientLabUpload(
        workspace_id=setup["beta"].id,
        category="Revenue",
        original_filename="beta.csv",
        stored_path="/tmp/beta.csv",
        kind="spreadsheet",
        record_count=2,
        fields_noticed=["a", "b"],
        has_named_fields=True,
        data_source_id=beta_source.id,
        ingestion_run_id=beta_ingest.id,
    )
    db_session.add_all(
        [
            alpha_candidate,
            beta_candidate,
            alpha_plan,
            alpha_upload,
            beta_upload,
        ]
    )
    db_session.flush()
    alpha_job = MlJob(
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        job_type=JOB_TYPE_AUTO_TRAIN,
        target_id=alpha_upload.id,
        upload_id=alpha_upload.id,
        status=JOB_QUEUED,
        attempts=0,
        max_attempts=3,
    )
    db_session.add(alpha_job)
    db_session.commit()
    return SimpleNamespace(
        setup=setup,
        alpha_run=alpha_run,
        beta_run=beta_run,
        alpha_pipeline=alpha_pipeline,
        beta_pipeline=beta_pipeline,
        alpha_candidate=alpha_candidate,
        beta_candidate=beta_candidate,
        alpha_source=alpha_source,
        beta_source=beta_source,
        alpha_ingest=alpha_ingest,
        beta_ingest=beta_ingest,
        alpha_artifact=alpha_artifact,
        beta_artifact=beta_artifact,
        alpha_plan=alpha_plan,
        alpha_upload=alpha_upload,
        beta_upload=beta_upload,
        alpha_job=alpha_job,
    )


def _reject(db_session, sql: str, **params) -> None:
    with pytest.raises((DBAPIError, IntegrityError), match="foreign key constraint"):
        db_session.execute(text(sql), params)
        db_session.commit()
    db_session.rollback()


def test_alembic_script_head_is_current_freeze():
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert head == CURRENT_HEAD


def test_postgres_rejects_cross_tenant_canonical_corruption(db_session, foundation):
    a = foundation
    s = a.setup

    _reject(
        db_session,
        "UPDATE projects SET workspace_id = :workspace WHERE id = :id",
        workspace=s["beta"].id,
        id=s["alpha_project"].id,
    )
    _reject(
        db_session,
        "UPDATE ml_workflows SET project_id = :project WHERE id = :id",
        project=s["beta_project"].id,
        id=s["alpha_workflow"].id,
    )
    _reject(
        db_session,
        "UPDATE data_sources SET project_id = :project WHERE id = :id",
        project=s["beta_project"].id,
        id=a.alpha_source.id,
    )
    _reject(
        db_session,
        "UPDATE ingestion_runs SET data_source_id = :source WHERE id = :id",
        source=a.beta_source.id,
        id=a.alpha_ingest.id,
    )
    _reject(
        db_session,
        """
        INSERT INTO datasets (
            id, workspace_id, dataset_asset_id, environment_id, project_id,
            ingestion_run_id, name, source_type, location, version,
            row_count, column_count
        ) VALUES (
            gen_random_uuid(), :workspace, :asset, :environment, :project,
            :ingestion_run, 'cross', 'csv', '/tmp/cross.csv', :version, 0, 0
        )
        """,
        workspace=s["alpha"].id,
        asset=s["alpha_asset"].id,
        environment=s["env"].id,
        project=s["alpha_project"].id,
        ingestion_run=a.beta_ingest.id,
        version=f"v-{uuid4().hex[:8]}",
    )
    _reject(
        db_session,
        "UPDATE ml_workflows SET workspace_domain_id = :domain WHERE id = :id",
        domain=s["beta_domain"].id,
        id=s["alpha_workflow"].id,
    )
    _reject(
        db_session,
        "UPDATE workflow_runs SET workflow_id = :workflow WHERE id = :id",
        workflow=s["beta_workflow"].id,
        id=a.alpha_run.id,
    )
    _reject(
        db_session,
        "UPDATE experiments SET dataset_id = :dataset WHERE id = :id",
        dataset=s["beta_dataset"].id,
        id=a.alpha_pipeline.id,
    )
    _reject(
        db_session,
        "UPDATE experiments SET workflow_run_id = :run, pipeline_index = 99 WHERE id = :id",
        run=a.beta_run.id,
        id=a.alpha_pipeline.id,
    )
    _reject(
        db_session,
        """
        INSERT INTO experiment_candidates (
            id, workspace_id, project_id, experiment_id, candidate_key,
            fingerprint, status, payload, model_family, algorithm
        ) VALUES (
            gen_random_uuid(), :workspace, :project, :experiment, 'cross',
            :fingerprint, 'generated', '{}'::jsonb, '', ''
        )
        """,
        workspace=s["alpha"].id,
        project=s["alpha_project"].id,
        experiment=a.beta_pipeline.id,
        fingerprint=uuid4().hex[:40],
    )
    _reject(
        db_session,
        "UPDATE artifacts SET pipeline_run_id = :run WHERE id = :id",
        run=a.beta_pipeline.id,
        id=a.alpha_artifact.id,
    )
    _reject(
        db_session,
        """
        INSERT INTO pipeline_scientific_plans (
            id, workspace_id, project_id, pipeline_run_id, task_type,
            holdout_strategy, holdout_test_size, validation_strategy,
            requested_folds, primary_metric, holdout_plan_digest,
            model_development_plan_digest, full_plan, locked_at,
            allowed_feature_count, excluded_feature_count
        ) VALUES (
            gen_random_uuid(), :workspace, :project, :pipeline_run, 'binary',
            'random', 0.2, 'stratified_kfold', 5, 'pr_auc', :digest, :digest,
            '{}'::jsonb, now(), 0, 0
        )
        """,
        workspace=s["alpha"].id,
        project=s["alpha_project"].id,
        pipeline_run=a.beta_pipeline.id,
        digest="9" * 64,
    )
    _reject(
        db_session,
        "UPDATE ml_jobs SET upload_id = :upload WHERE id = :id",
        upload=a.beta_upload.id,
        id=a.alpha_job.id,
    )
    _reject(
        db_session,
        "UPDATE ml_jobs SET project_id = :project WHERE id = :id",
        project=s["beta_project"].id,
        id=a.alpha_job.id,
    )
    asset = create_model_asset(
        db_session,
        workspace_id=s["alpha"].id,
        workflow=s["alpha_workflow"],
        name="Foundation model",
        slug=f"foundation-{uuid4().hex[:8]}",
        actor=s["alpha_admin"],
    )
    _reject(
        db_session,
        """
        INSERT INTO model_versions (
            id, model_asset_id, version, workspace_id, project_id, workflow_id,
            workflow_run_id, pipeline_run_id, selected_candidate_id, dataset_id,
            content_digest, metrics
        ) VALUES (
            gen_random_uuid(), :asset, 'v-cross', :workspace, :project, :workflow,
            :workflow_run, :pipeline_run, :candidate, :dataset, :digest, '{}'::jsonb
        )
        """,
        asset=asset.id,
        workspace=s["alpha"].id,
        project=s["alpha_project"].id,
        workflow=s["alpha_workflow"].id,
        workflow_run=a.alpha_run.id,
        pipeline_run=a.beta_pipeline.id,
        candidate=a.alpha_candidate.id,
        dataset=s["alpha_dataset"].id,
        digest="8" * 64,
    )


def test_same_workspace_canonical_links_remain_valid(db_session, foundation):
    a = foundation
    s = a.setup
    db_session.execute(
        text("UPDATE artifacts SET pipeline_run_id = :run WHERE id = :id"),
        {"run": a.alpha_pipeline.id, "id": a.alpha_artifact.id},
    )
    db_session.execute(
        text(
            """
            INSERT INTO experiment_candidates (
                id, workspace_id, project_id, experiment_id, candidate_key,
                fingerprint, status, payload, model_family, algorithm
            ) VALUES (
                gen_random_uuid(), :workspace, :project, :experiment, 'same',
                :fingerprint, 'generated', '{}'::jsonb, '', ''
            )
            """
        ),
        {
            "workspace": s["alpha"].id,
            "project": s["alpha_project"].id,
            "experiment": a.alpha_pipeline.id,
            "fingerprint": uuid4().hex[:40],
        },
    )
    db_session.commit()
    stored = db_session.get(Artifact, a.alpha_artifact.id)
    assert stored.pipeline_run_id == a.alpha_pipeline.id
    assert stored.workspace_id == s["alpha"].id


def test_important_delete_actions_match_catalog(test_engine):
    with test_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT conname, confdeltype FROM pg_constraint WHERE contype = 'f'")
        )
        found = {
            name: action
            for name, action in rows
            if name in IMPORTANT_DELETE_ACTIONS
        }
    assert found == IMPORTANT_DELETE_ACTIONS


def test_delete_semantics_cascade_and_column_specific_set_null(db_session, foundation):
    a = foundation
    s = a.setup
    workspace_id = s["alpha"].id
    job_id = a.alpha_job.id
    upload_id = a.alpha_upload.id

    db_session.execute(text("DELETE FROM ml_jobs WHERE id = :id"), {"id": job_id})
    db_session.execute(
        text("DELETE FROM client_lab_uploads WHERE id = :id"),
        {"id": upload_id},
    )
    db_session.commit()
    leftover = db_session.execute(
        text("SELECT COUNT(*) FROM client_lab_uploads WHERE id = :id"),
        {"id": upload_id},
    ).scalar()
    assert leftover == 0

    isolated_source = create_data_source(
        db_session,
        workspace_id=workspace_id,
        project_id=s["alpha_project"].id,
        name="delete-source",
        source_type="upload",
        provider="local",
        created_by=s["alpha_admin"].id,
    )
    isolated_run = start_ingestion_run(
        db_session,
        workspace_id=workspace_id,
        project_id=s["alpha_project"].id,
        data_source_id=isolated_source.id,
    )
    hanging_upload = ClientLabUpload(
        workspace_id=workspace_id,
        category="Revenue",
        original_filename="hang.csv",
        stored_path="/tmp/hang.csv",
        kind="spreadsheet",
        record_count=1,
        fields_noticed=["x"],
        has_named_fields=True,
        data_source_id=isolated_source.id,
        ingestion_run_id=isolated_run.id,
    )
    db_session.add(hanging_upload)
    db_session.commit()
    source_id = isolated_source.id
    run_id = isolated_run.id
    hanging_id = hanging_upload.id
    db_session.execute(
        text("DELETE FROM data_sources WHERE id = :id"), {"id": source_id}
    )
    db_session.commit()
    leftover_run = db_session.execute(
        text("SELECT COUNT(*) FROM ingestion_runs WHERE id = :id"),
        {"id": run_id},
    ).scalar()
    assert leftover_run == 0
    hanging = db_session.execute(
        text(
            "SELECT workspace_id, data_source_id, ingestion_run_id "
            "FROM client_lab_uploads WHERE id = :id"
        ),
        {"id": hanging_id},
    ).one()
    assert hanging == (workspace_id, None, None)


def test_dataset_ingestion_run_delete_is_restricted_when_dataset_points_at_it(
    db_session, foundation
):
    a = foundation
    s = a.setup
    db_session.execute(
        text(
            """
            INSERT INTO datasets (
                id, workspace_id, dataset_asset_id, environment_id, project_id,
                ingestion_run_id, name, source_type, location, version,
                row_count, column_count
            ) VALUES (
                gen_random_uuid(), :workspace, :asset, :environment, :project,
                :ingestion_run, 'held', 'csv', '/tmp/held.csv', :version, 0, 0
            )
            """
        ),
        {
            "workspace": s["alpha"].id,
            "asset": s["alpha_asset"].id,
            "environment": s["env"].id,
            "project": s["alpha_project"].id,
            "ingestion_run": a.alpha_ingest.id,
            "version": f"v-held-{uuid4().hex[:8]}",
        },
    )
    db_session.commit()
    with pytest.raises(DBAPIError):
        db_session.execute(
            text("DELETE FROM ingestion_runs WHERE id = :id"),
            {"id": a.alpha_ingest.id},
        )
        db_session.commit()
    db_session.rollback()
    leftover = db_session.execute(
        text("SELECT COUNT(*) FROM ingestion_runs WHERE id = :id"),
        {"id": a.alpha_ingest.id},
    ).scalar()
    assert leftover == 1


def _plan(db_session, sql: str, **params) -> str:
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    rows = db_session.execute(text(f"EXPLAIN {sql}"), params).all()
    return "\n".join(row[0] for row in rows)


def test_explain_uses_measured_hot_path_indexes(db_session, foundation):
    a = foundation
    s = a.setup
    workspace_id = s["alpha"].id

    projects = _plan(
        db_session,
        "SELECT id FROM projects WHERE workspace_id = :workspace_id "
        "ORDER BY created_at DESC",
        workspace_id=workspace_id,
    )
    assert "ix_projects_workspace_created_at" in projects

    runs = _plan(
        db_session,
        "SELECT id FROM experiments WHERE workspace_id = :workspace_id "
        "ORDER BY created_at DESC LIMIT 50",
        workspace_id=workspace_id,
    )
    assert "ix_experiments_workspace_created_at" in runs

    queued = _plan(
        db_session,
        "SELECT id FROM ml_jobs WHERE status = 'queued' ORDER BY queued_at",
    )
    assert "ix_ml_jobs_status_queued_at" in queued

    claiming = _plan(
        db_session,
        "SELECT id FROM ml_jobs WHERE status = 'queued' "
        "ORDER BY queued_at FOR UPDATE SKIP LOCKED LIMIT 1",
    )
    assert "ix_ml_jobs_status_queued_at" in claiming

    stages = _plan(
        db_session,
        "SELECT id FROM pipeline_stage_runs WHERE pipeline_run_id = :run "
        "ORDER BY sequence",
        run=a.alpha_pipeline.id,
    )
    assert "uq_pipeline_stage_runs_run_sequence" in stages

    events = _plan(
        db_session,
        "SELECT id FROM ml_run_events WHERE workflow_run_id = :run "
        "ORDER BY created_at",
        run=a.alpha_run.id,
    )
    assert "ix_ml_run_events_workflow_run_created_at" in events

    candidates = _plan(
        db_session,
        "SELECT id FROM experiment_candidates WHERE experiment_id = :run "
        "AND workspace_id = :workspace_id ORDER BY created_at",
        run=a.alpha_pipeline.id,
        workspace_id=workspace_id,
    )
    assert "ix_experiment_candidates_experiment_id" in candidates or "experiment_id" in candidates

    folds = _plan(
        db_session,
        "SELECT id FROM cv_fold_runs WHERE candidate_id = :candidate AND fold_number = 1",
        candidate=a.alpha_candidate.id,
    )
    assert "uq_cv_fold_runs_candidate_fold" in folds

    artifact_indexes = {
        row[0]
        for row in db_session.execute(
            text(
                """
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'artifacts'
                  AND indexname IN (
                    'ix_artifacts_pipeline_run_id',
                    'ix_artifacts_workspace_pipeline_run_id'
                  )
                """
            )
        )
    }
    assert artifact_indexes == {
        "ix_artifacts_pipeline_run_id",
        "ix_artifacts_workspace_pipeline_run_id",
    }
    artifacts = _plan(
        db_session,
        "SELECT id FROM artifacts WHERE pipeline_run_id = :run",
        run=a.alpha_pipeline.id,
    )
    assert (
        "ix_artifacts_pipeline_run_id" in artifacts
        or "ix_artifacts_workspace_pipeline_run_id" in artifacts
    )

    models = _plan(
        db_session,
        "SELECT id FROM model_versions WHERE workspace_id = :workspace_id "
        "ORDER BY created_at DESC LIMIT 50",
        workspace_id=workspace_id,
    )
    assert "ix_model_versions_workspace_id" in models or "Index Scan" in models

    ingest = _plan(
        db_session,
        "SELECT id FROM ingestion_runs WHERE data_source_id = :source",
        source=a.alpha_source.id,
    )
    assert "ix_ingestion_runs_data_source_id" in ingest
