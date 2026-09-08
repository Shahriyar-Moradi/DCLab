"""PostgreSQL locks on canonical reproducibility records (Alembic 0042).

Every tamper case below goes through raw SQL, not the ORM: the point is that a
psql session or a stray migration cannot rewrite provenance either.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from adaptive_modeling.fixtures import ordinary_binary
from adaptive_modeling.production import ACCEPTABLE_VERIFICATION, labs_upload_and_train
from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    Artifact,
    CodeSnapshot,
    Dataset,
    DatasetAsset,
    Experiment,
    PipelineScientificPlan,
    RuntimeEnvironment,
)
from app.engine.modeling.holdout_planner import plan_holdout, require_supported_holdout
from app.engine.modeling.leakage_auditor import plan_model_development
from app.engine.validation.splits import SOURCE_ROW_COLUMN, split_train_test_holdout
from app.services.lab_service import seed_dogfood
from app.services.pipeline_verifier import PipelineVerifier
from app.services.reproducibility_service import persist_reproducibility
from app.services.scientific_lineage_service import persist_scientific_plan
from conftest import ADMIN_URL

PREVIOUS_REVISION = "0041_artifact_pipeline_run"
PROVENANCE_TRIGGERS = (
    "runtime_environments_immutable",
    "pipeline_scientific_plans_locked_immutable",
    "code_snapshots_immutable",
    "artifacts_columns_immutable",
)


@pytest.fixture
def _rule_engine_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )


def _raw(db_session, sql: str, **params) -> None:
    """Tamper the way an operator with a psql prompt would."""

    db_session.execute(text(sql), params)
    db_session.commit()


def _pipeline_run(db_session) -> Experiment:
    environment = seed_dogfood(db_session)
    slug = f"frozen-{uuid4().hex[:12]}"
    asset = DatasetAsset(workspace_id=DEFAULT_WORKSPACE_ID, name=slug, slug=slug)
    db_session.add(asset)
    db_session.flush()
    dataset = Dataset(
        workspace_id=DEFAULT_WORKSPACE_ID,
        dataset_asset_id=asset.id,
        environment_id=environment.id,
        name=slug,
        source_type="csv",
        location=f"/tmp/{slug}.csv",
        version="v1",
        row_count=1,
        column_count=1,
    )
    db_session.add(dataset)
    db_session.flush()
    experiment = Experiment(
        workspace_id=DEFAULT_WORKSPACE_ID,
        environment_id=environment.id,
        dataset_id=dataset.id,
        status="CREATED",
        config={},
        result={},
    )
    db_session.add(experiment)
    db_session.flush()
    return experiment


@pytest.fixture
def provenance(db_session):
    """A real PipelineRun with a locked plan, runtime, code snapshot, artifacts."""

    experiment = _pipeline_run(db_session)
    frame = ordinary_binary()
    frame.insert(0, SOURCE_ROW_COLUMN, list(range(len(frame))))
    holdout = plan_holdout(
        frame, target="outcome", task_type="binary", test_size=0.2, random_state=42
    )
    require_supported_holdout(holdout)
    train, _val, _test, split = split_train_test_holdout(
        frame, target="outcome", test_size=holdout.test_size, seed=42, plan=holdout
    )
    _profile, _validation, _metric, _audit, development = plan_model_development(
        train, target="outcome", task_type="binary", requested_folds=5, random_state=42
    )
    plan = persist_scientific_plan(
        db_session,
        experiment,
        holdout_plan=holdout,
        development_plan=development,
        split=split,
    )
    repro = persist_reproducibility(db_session, experiment, {})
    db_session.commit()
    assert plan is not None
    assert repro.code_snapshot is not None
    return SimpleNamespace(
        experiment=experiment,
        plan=plan,
        runtime=repro.runtime_environment,
        snapshot=repro.code_snapshot,
        lock_artifact=repro.dependency_lock_artifact,
        source_artifact=db_session.get(Artifact, repro.code_snapshot.artifact_id),
    )


def test_locked_scientific_plan_rejects_raw_sql_update_and_delete(db_session, provenance):
    plan = provenance.plan
    assert plan.locked_at is not None
    for column, value in (
        ("primary_metric", "'accuracy'"),
        ("validation_strategy", "'KFold'"),
        ("holdout_plan_digest", "'" + "f" * 64 + "'"),
        ("full_plan", "'{}'::jsonb"),
        ("locked_at", "NULL"),
    ):
        with pytest.raises(DBAPIError, match="locked and immutable"):
            _raw(
                db_session,
                f"UPDATE pipeline_scientific_plans SET {column} = {value} WHERE id = :id",
                id=plan.id,
            )
        db_session.rollback()

    with pytest.raises(DBAPIError, match="locked and immutable"):
        _raw(
            db_session,
            "DELETE FROM pipeline_scientific_plans WHERE id = :id",
            id=plan.id,
        )
    db_session.rollback()
    assert db_session.get(PipelineScientificPlan, plan.id) is not None


def test_runtime_environment_rejects_raw_sql_update_and_delete(db_session, provenance):
    runtime = provenance.runtime
    for column, value in (
        ("python_version", "'0.0.0'"),
        ("environment_digest", "'" + "0" * 64 + "'"),
        ("hardware", "'{}'::jsonb"),
        ("container_digest", "'sha256:tampered'"),
    ):
        with pytest.raises(DBAPIError, match="rows are immutable"):
            _raw(
                db_session,
                f"UPDATE runtime_environments SET {column} = {value} WHERE id = :id",
                id=runtime.id,
            )
        db_session.rollback()

    with pytest.raises(DBAPIError, match="rows are immutable"):
        _raw(db_session, "DELETE FROM runtime_environments WHERE id = :id", id=runtime.id)
    db_session.rollback()
    assert db_session.get(RuntimeEnvironment, runtime.id) is not None


def test_code_snapshot_rejects_raw_sql_update_and_delete(db_session, provenance):
    snapshot = provenance.snapshot
    for column, value in (
        ("code_digest", "'" + "e" * 64 + "'"),
        ("dependency_lock_digest", "'" + "d" * 64 + "'"),
        ("entrypoint", "'app.other:main'"),
        ("language", "'ruby'"),
        ("git_commit", "'0123456789abcdef'"),
        ("artifact_id", "gen_random_uuid()"),
        ("runtime_environment_id", "gen_random_uuid()"),
        ("workspace_id", "gen_random_uuid()"),
        ("pipeline_run_id", "gen_random_uuid()"),
        ("created_at", "now() - interval '1 year'"),
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            _raw(
                db_session,
                f"UPDATE code_snapshots SET {column} = {value} WHERE id = :id",
                id=snapshot.id,
            )
        db_session.rollback()
    with pytest.raises(DBAPIError, match="rows are immutable"):
        _raw(db_session, "DELETE FROM code_snapshots WHERE id = :id", id=snapshot.id)
    db_session.rollback()
    stored = db_session.get(CodeSnapshot, snapshot.id)
    assert stored is not None
    assert stored.code_digest == snapshot.code_digest
    assert stored.artifact_id == snapshot.artifact_id


def test_code_snapshot_optional_associations_are_not_directly_writable(
    db_session, provenance
):
    snapshot = provenance.snapshot
    assert snapshot.dependency_lock_artifact_id is not None

    with pytest.raises(DBAPIError, match="rows are immutable"):
        _raw(
            db_session,
            "UPDATE code_snapshots SET dependency_lock_artifact_id = :other WHERE id = :id",
            other=provenance.source_artifact.id,
            id=snapshot.id,
        )
    db_session.rollback()
    with pytest.raises(DBAPIError, match="rows are immutable"):
        _raw(
            db_session,
            "UPDATE code_snapshots SET candidate_id = gen_random_uuid() WHERE id = :id",
            id=snapshot.id,
        )
    db_session.rollback()
    with pytest.raises(DBAPIError, match="rows are immutable"):
        _raw(
            db_session,
            "UPDATE code_snapshots SET dependency_lock_artifact_id = NULL WHERE id = :id",
            id=snapshot.id,
        )
    db_session.rollback()


def test_artifact_identity_frozen_while_association_and_delete_stay_open(
    db_session, provenance
):
    artifact = provenance.lock_artifact
    for column, value in (
        ("provider", "'s3'"),
        ("bucket", "'other-bucket'"),
        ("object_key", "'workspaces/tampered/blob'"),
        ("content_digest", "'" + "c" * 64 + "'"),
        ("size_bytes", "size_bytes + 1"),
    ):
        with pytest.raises(DBAPIError, match="is immutable"):
            _raw(
                db_session,
                f"UPDATE artifacts SET {column} = {value} WHERE id = :id",
                id=artifact.id,
            )
        db_session.rollback()

    # Retention and association backfills (0041) must still work.
    _raw(
        db_session,
        "UPDATE artifacts SET \"metadata\" = jsonb_set(\"metadata\", '{retained}', 'true'::jsonb), "
        "mime_type = 'text/plain; charset=utf-8', pipeline_run_id = :run WHERE id = :id",
        run=provenance.experiment.id,
        id=artifact.id,
    )
    db_session.expire_all()
    reloaded = db_session.get(Artifact, artifact.id)
    assert reloaded.extra_metadata.get("retained") is True
    assert reloaded.pipeline_run_id == provenance.experiment.id

    # Offboarding: an Artifact no immutable record references can still be deleted.
    orphan_id = db_session.scalar(
        text(
            """
            INSERT INTO artifacts (
                id, workspace_id, artifact_type, provider, bucket, object_key,
                content_digest, size_bytes, "metadata"
            ) VALUES (
                gen_random_uuid(), :workspace_id, 'report', 'local', NULL,
                :object_key, :digest, 3, '{}'::jsonb
            ) RETURNING id
            """
        ),
        {
            "workspace_id": DEFAULT_WORKSPACE_ID,
            "object_key": f"workspaces/{DEFAULT_WORKSPACE_ID}/artifacts/{uuid4()}/report.md",
            "digest": "b" * 64,
        },
    )
    _raw(db_session, "DELETE FROM artifacts WHERE id = :id", id=orphan_id)
    assert db_session.get(Artifact, orphan_id) is None


def test_locked_provenance_still_allows_model_version_and_reverification(
    auth_client, db_session, monkeypatch, _rule_engine_only
):
    frame = ordinary_binary()
    _upload, _workflow_run, experiment, model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="frozen_provenance.csv",
        target="outcome",
    )
    db_session.refresh(model_version)
    assert model_version.runtime_environment_id is not None
    assert model_version.code_snapshot_id is not None
    assert model_version.model_artifact_id is not None
    snapshot = db_session.scalar(
        select(CodeSnapshot).where(CodeSnapshot.pipeline_run_id == experiment.id)
    )
    plan = db_session.scalar(
        select(PipelineScientificPlan).where(
            PipelineScientificPlan.pipeline_run_id == experiment.id
        )
    )
    assert snapshot is not None and plan is not None

    report = (experiment.result or {}).get("technical_report") or {}
    before = PipelineVerifier().verify(report, db=db_session)
    assert before["overall_status"] in ACCEPTABLE_VERIFICATION, before

    with pytest.raises(DBAPIError, match="immutable"):
        _raw(
            db_session,
            "UPDATE code_snapshots SET code_digest = :digest WHERE id = :id",
            digest="a" * 64,
            id=snapshot.id,
        )
    db_session.rollback()
    with pytest.raises(DBAPIError, match="locked and immutable"):
        _raw(
            db_session,
            "UPDATE pipeline_scientific_plans SET primary_metric = 'accuracy' WHERE id = :id",
            id=plan.id,
        )
    db_session.rollback()
    with pytest.raises(DBAPIError, match="is immutable"):
        _raw(
            db_session,
            "UPDATE artifacts SET content_digest = :digest WHERE id = :id",
            digest="a" * 64,
            id=model_version.model_artifact_id,
        )
    db_session.rollback()

    after = PipelineVerifier().verify(report, db=db_session)
    assert after["overall_status"] == before["overall_status"]
    assert after["overall_status"] in ACCEPTABLE_VERIFICATION, after


def _isolated_database(monkeypatch):
    admin_url = make_url(ADMIN_URL)
    database_name = f"decisionai_provenance_{uuid4().hex[:12]}"
    database_url = admin_url.set(database=database_name)
    admin_engine = create_engine(
        admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except Exception as exc:  # pragma: no cover - environment availability
        admin_engine.dispose()
        pytest.skip(f"cannot create isolated provenance database: {exc}")
    monkeypatch.setenv("DATABASE_URL", database_url.render_as_string(hide_password=False))
    from app.config import get_settings

    get_settings.cache_clear()
    return admin_engine, database_name, database_url


def _installed_triggers(engine) -> set[str]:
    with engine.connect() as connection:
        present = set(
            connection.scalars(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            )
        )
    return present & set(PROVENANCE_TRIGGERS)


def test_alembic_0042_fresh_and_existing_upgrade_install_provenance_triggers(monkeypatch):
    admin_engine, database_name, database_url = _isolated_database(monkeypatch)
    from app.config import get_settings

    try:
        alembic_config = Config("alembic.ini")
        engine = create_engine(database_url)

        # Existing deployment: stop at the previous head, then upgrade forward.
        command.upgrade(alembic_config, PREVIOUS_REVISION)
        assert _installed_triggers(engine) == set()
        get_settings.cache_clear()
        command.upgrade(alembic_config, "head")
        assert _installed_triggers(engine) == set(PROVENANCE_TRIGGERS)
        command.check(alembic_config)

        get_settings.cache_clear()
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        assert _installed_triggers(engine) == set()
        with engine.connect() as connection:
            # 0035 owns the shared functions; 0042's downgrade must not drop them.
            assert connection.scalar(
                text("SELECT proname FROM pg_proc WHERE proname = 'prevent_locked_row_mutation'")
            ) == "prevent_locked_row_mutation"
            assert connection.scalar(
                text(
                    "SELECT proname FROM pg_proc "
                    "WHERE proname = 'prevent_canonical_column_mutation'"
                )
            ) is None

        get_settings.cache_clear()
        command.upgrade(alembic_config, "head")
        assert _installed_triggers(engine) == set(PROVENANCE_TRIGGERS)
        engine.dispose()
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


def test_alembic_fresh_upgrade_to_head_installs_provenance_triggers(monkeypatch):
    admin_engine, database_name, database_url = _isolated_database(monkeypatch)
    from app.config import get_settings

    try:
        command.upgrade(Config("alembic.ini"), "head")
        engine = create_engine(database_url)
        assert _installed_triggers(engine) == set(PROVENANCE_TRIGGERS)
        engine.dispose()
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
