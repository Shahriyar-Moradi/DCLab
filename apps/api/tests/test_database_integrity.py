"""PostgreSQL tenant FKs, canonical immutability, list indexes, Alembic 0035."""

from __future__ import annotations

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.db.models import ExperimentCandidate, FeatureSet, FeatureSetVersion
from app.engine.types import SearchConfig
from app.services.lineage_service import (
    create_model_asset,
    create_model_version,
    create_pipeline_run,
    create_workflow_run,
)
from app.services.problem_spec_service import create_problem_spec
from app.services.workflow_execution_service import create_workflow_version
from conftest import ADMIN_URL
from test_data_model_lineage import make_lineage_setup
from test_execution_hierarchy import make_hierarchy


@pytest.fixture
def lineage_setup(db_session, tmp_path):
    return make_lineage_setup(db_session, tmp_path)


@pytest.fixture
def hierarchy(db_session, tmp_path):
    return make_hierarchy(db_session, tmp_path)


def _plan(db_session, sql: str, **params) -> str:
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    rows = db_session.execute(text(f"EXPLAIN {sql}"), params).all()
    return "\n".join(row[0] for row in rows)


def _raw_update(db_session, sql: str, **params):
    db_session.execute(text(sql), params)
    db_session.commit()


def test_cross_tenant_pipeline_dataset_fk_rejected_by_postgres(db_session, lineage_setup):
    setup = lineage_setup
    run = create_workflow_run(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    pipeline = create_pipeline_run(
        db_session,
        workflow_run=run,
        environment=setup["env"],
        dataset=setup["alpha_dataset"],
        task=setup["task"],
        config=SearchConfig(seed=1),
    )
    db_session.commit()

    with pytest.raises(IntegrityError):
        _raw_update(
            db_session,
            "UPDATE experiments SET dataset_id = :dataset_id WHERE id = :id",
            dataset_id=setup["beta_dataset"].id,
            id=pipeline.id,
        )
    db_session.rollback()


def test_immutable_workflow_version_update_rejected_by_postgres(db_session, hierarchy):
    version = create_workflow_version(
        db_session,
        workflow=hierarchy["workflow"],
        actor=hierarchy["actor"],
        lock=True,
    )
    db_session.commit()

    with pytest.raises(Exception, match="locked and immutable"):
        _raw_update(
            db_session,
            "UPDATE workflow_versions SET definition = '{}'::jsonb WHERE id = :id",
            id=version.id,
        )
    db_session.rollback()


def test_unlocked_workflow_version_can_lock_then_becomes_immutable(db_session, hierarchy):
    version = create_workflow_version(
        db_session,
        workflow=hierarchy["workflow"],
        actor=hierarchy["actor"],
        lock=False,
    )
    db_session.commit()
    _raw_update(
        db_session,
        """
        UPDATE workflow_versions
        SET definition = jsonb_set(definition, '{tamper}', 'true'::jsonb)
        WHERE id = :id
        """,
        id=version.id,
    )
    _raw_update(
        db_session,
        "UPDATE workflow_versions SET locked_at = now() WHERE id = :id",
        id=version.id,
    )
    with pytest.raises(Exception, match="locked and immutable"):
        _raw_update(
            db_session,
            "UPDATE workflow_versions SET definition = '{}'::jsonb WHERE id = :id",
            id=version.id,
        )
    db_session.rollback()


def test_immutable_dataset_version_update_rejected_by_postgres(db_session, lineage_setup):
    with pytest.raises(Exception, match="immutable"):
        _raw_update(
            db_session,
            "UPDATE datasets SET version = 'tampered' WHERE id = :id",
            id=lineage_setup["alpha_dataset"].id,
        )
    db_session.rollback()


def test_published_model_version_update_rejected_by_postgres(db_session, lineage_setup):
    setup = lineage_setup
    run = create_workflow_run(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    pipeline = create_pipeline_run(
        db_session,
        workflow_run=run,
        environment=setup["env"],
        dataset=setup["alpha_dataset"],
        task=setup["task"],
    )
    winner = ExperimentCandidate(
        workspace_id=pipeline.workspace_id,
        project_id=pipeline.project_id,
        experiment_id=pipeline.id,
        candidate_key="winner",
        fingerprint="a" * 40,
        status="trained",
        payload={"test_metrics": {"pr_auc": 0.82}},
    )
    db_session.add(winner)
    pipeline.status = "COMPLETED"
    pipeline.result = {
        "selection": {"selected_candidate_id": "winner"},
        "test_metrics": {"pr_auc": 0.82},
    }
    db_session.commit()
    asset = create_model_asset(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        name="Integrity model",
        slug="integrity-model",
        actor=setup["alpha_admin"],
    )
    version = create_model_version(
        db_session,
        model_asset=asset,
        pipeline_run=pipeline,
        selected_candidate=winner,
        version="v1",
    )
    db_session.commit()

    with pytest.raises(Exception, match="immutable"):
        _raw_update(
            db_session,
            "UPDATE model_versions SET metrics = '{}'::jsonb WHERE id = :id",
            id=version.id,
        )
    db_session.rollback()


def test_mutable_pipeline_run_status_still_updates(db_session, lineage_setup):
    setup = lineage_setup
    run = create_workflow_run(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    pipeline = create_pipeline_run(
        db_session,
        workflow_run=run,
        environment=setup["env"],
        dataset=setup["alpha_dataset"],
        task=setup["task"],
    )
    db_session.commit()
    _raw_update(
        db_session,
        "UPDATE experiments SET status = 'RUNNING' WHERE id = :id",
        id=pipeline.id,
    )
    db_session.refresh(pipeline)
    assert pipeline.status == "RUNNING"
    pipeline.status = "COMPLETED"
    db_session.commit()
    assert pipeline.status == "COMPLETED"


def test_locked_problem_spec_rejected_draft_still_updates(db_session, hierarchy):
    draft = create_problem_spec(
        db_session,
        actor=hierarchy["actor"],
        workspace_id=hierarchy["workspace"].id,
        project_id=hierarchy["project"].id,
        task_type="binary",
        business_objective="Draft objective.",
        target_column="target",
        status="draft",
    )
    locked = create_problem_spec(
        db_session,
        actor=hierarchy["actor"],
        workspace_id=hierarchy["workspace"].id,
        project_id=hierarchy["project"].id,
        task_type="binary",
        business_objective="Locked objective.",
        target_column="target",
        status="locked",
    )
    db_session.commit()
    _raw_update(
        db_session,
        "UPDATE problem_specs SET business_objective = 'edited draft' WHERE id = :id",
        id=draft.id,
    )
    with pytest.raises(Exception, match="locked and immutable"):
        _raw_update(
            db_session,
            "UPDATE problem_specs SET business_objective = 'tampered' WHERE id = :id",
            id=locked.id,
        )
    db_session.rollback()


def test_locked_feature_set_version_update_rejected_by_postgres(db_session, hierarchy):
    feature_set = FeatureSet(
        workspace_id=hierarchy["workspace"].id,
        project_id=hierarchy["project"].id,
        name="integrity-features",
        description="Index/integrity fixture",
    )
    db_session.add(feature_set)
    db_session.flush()
    version = FeatureSetVersion(
        workspace_id=hierarchy["workspace"].id,
        project_id=hierarchy["project"].id,
        feature_set_id=feature_set.id,
        version=1,
        content_digest="a" * 64,
        locked_at=None,
    )
    db_session.add(version)
    db_session.commit()
    _raw_update(
        db_session,
        "UPDATE feature_set_versions SET locked_at = now() WHERE id = :id",
        id=version.id,
    )
    with pytest.raises(Exception, match="locked and immutable"):
        _raw_update(
            db_session,
            "UPDATE feature_set_versions SET content_digest = :digest WHERE id = :id",
            digest="b" * 64,
            id=version.id,
        )
    db_session.rollback()


def test_explain_uses_intended_list_indexes(db_session, lineage_setup):
    setup = lineage_setup
    run = create_workflow_run(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    pipeline = create_pipeline_run(
        db_session,
        workflow_run=run,
        environment=setup["env"],
        dataset=setup["alpha_dataset"],
        task=setup["task"],
    )
    db_session.commit()

    workspace_plan = _plan(
        db_session,
        "SELECT id FROM experiments WHERE workspace_id = :workspace_id "
        "ORDER BY created_at DESC",
        workspace_id=setup["alpha"].id,
    )
    assert "ix_experiments_workspace_created_at" in workspace_plan

    status_plan = _plan(
        db_session,
        "SELECT id FROM experiments WHERE workspace_id = :workspace_id "
        "AND status = :status ORDER BY created_at DESC",
        workspace_id=setup["alpha"].id,
        status="CREATED",
    )
    assert "Index Scan" in status_plan
    assert (
        "ix_experiments_workspace_status_created_at" in status_plan
        or "ix_experiments_workspace_created_at" in status_plan
    )
    present = db_session.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE indexname = 'ix_experiments_workspace_status_created_at'"
        )
    ).scalar()
    assert present == "ix_experiments_workspace_status_created_at"

    project_plan = _plan(
        db_session,
        "SELECT id FROM experiments WHERE project_id = :project_id "
        "ORDER BY created_at DESC",
        project_id=setup["alpha_project"].id,
    )
    assert "ix_experiments_project_created_at" in project_plan

    run_plan = _plan(
        db_session,
        "SELECT id FROM experiments WHERE workflow_run_id = :workflow_run_id "
        "ORDER BY created_at",
        workflow_run_id=run.id,
    )
    assert "ix_experiments_workflow_run_created_at" in run_plan

    stage_plan = _plan(
        db_session,
        "SELECT id FROM pipeline_stage_runs WHERE pipeline_run_id = :pipeline_run_id "
        "ORDER BY sequence",
        pipeline_run_id=pipeline.id,
    )
    assert "uq_pipeline_stage_runs_run_sequence" in stage_plan

    fold_plan = _plan(
        db_session,
        "SELECT id FROM cv_fold_runs WHERE candidate_id = :candidate_id "
        "AND fold_number = 1",
        candidate_id=uuid4(),
    )
    assert "uq_cv_fold_runs_candidate_fold" in fold_plan

    metric_plan = _plan(
        db_session,
        "SELECT id FROM evaluation_metrics WHERE model_evaluation_id = :evaluation_id "
        "AND metric_name = 'pr_auc'",
        evaluation_id=uuid4(),
    )
    assert "uq_evaluation_metrics_evaluation_name" in metric_plan

    asset_plan = _plan(
        db_session,
        "SELECT id FROM model_versions WHERE model_asset_id = :asset_id "
        "AND version = 'v1'",
        asset_id=uuid4(),
    )
    assert "uq_model_versions_asset_version" in asset_plan

    event_plan = _plan(
        db_session,
        "SELECT id FROM ml_run_events WHERE workflow_run_id = :workflow_run_id "
        "ORDER BY created_at",
        workflow_run_id=run.id,
    )
    assert "ix_ml_run_events_workflow_run_created_at" in event_plan


def test_alembic_0035_upgrade_downgrade_round_trip(monkeypatch):
    admin_url = make_url(ADMIN_URL)
    database_name = f"decisionai_integrity_{uuid4().hex[:12]}"
    database_url = admin_url.set(database=database_name)
    admin_engine = create_engine(
        admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except Exception as exc:  # pragma: no cover - environment availability
        admin_engine.dispose()
        pytest.skip(f"cannot create isolated integrity database: {exc}")

    try:
        rendered = database_url.render_as_string(hide_password=False)
        monkeypatch.setenv("DATABASE_URL", rendered)
        from app.config import get_settings

        get_settings.cache_clear()
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")

        engine = create_engine(database_url)
        with engine.connect() as connection:
            trigger = connection.execute(
                text(
                    """
                    SELECT tgname FROM pg_trigger
                    WHERE tgname = 'datasets_immutable'
                    """
                )
            ).scalar()
            assert trigger == "datasets_immutable"
            fk = connection.execute(
                text(
                    """
                    SELECT conname FROM pg_constraint
                    WHERE conname = 'fk_experiments_workspace_dataset'
                    """
                )
            ).scalar()
            assert fk == "fk_experiments_workspace_dataset"

        get_settings.cache_clear()
        command.downgrade(alembic_config, "0034_reproducible_code")
        with engine.connect() as connection:
            trigger = connection.execute(
                text(
                    """
                    SELECT tgname FROM pg_trigger
                    WHERE tgname = 'datasets_immutable'
                    """
                )
            ).scalar()
            assert trigger is None
            fk = connection.execute(
                text(
                    """
                    SELECT conname FROM pg_constraint
                    WHERE conname = 'fk_experiments_workspace_dataset'
                    """
                )
            ).scalar()
            assert fk is None

        get_settings.cache_clear()
        command.upgrade(alembic_config, "head")
        with engine.connect() as connection:
            trigger = connection.execute(
                text(
                    """
                    SELECT tgname FROM pg_trigger
                    WHERE tgname = 'model_versions_immutable'
                    """
                )
            ).scalar()
            assert trigger == "model_versions_immutable"
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
