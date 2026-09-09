"""PostgreSQL freeze on a finished PipelineRun's scientific evidence (Alembic 0043).

Every tamper case goes through raw SQL rather than the ORM: the claim under test
is that a psql session cannot rewrite CV scores, hyperparameters, the winner, or
the final holdout metrics once the run is locked.
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
    CVFoldRun,
    Dataset,
    DatasetAsset,
    EvaluationMetric,
    Experiment,
    ExperimentCandidate,
    ExperimentTestPrediction,
    Feature,
    FeatureTransformation,
    MlRunEvent,
    MlRunVerification,
    ModelEvaluation,
    ModelHyperparameter,
    ModelSelectionDecision,
    PipelineStageRun,
    PreprocessingStep,
)
from app.services.evidence_lock_service import (
    EVIDENCE_REQUIREMENTS,
    lock_scientific_evidence,
    missing_scientific_evidence,
    scientific_evidence_status,
)
from app.services.lab_service import seed_dogfood
from app.services.pipeline_audit_service import request_pipeline_verification
from app.services.pipeline_verifier import PipelineVerifier
from conftest import ADMIN_URL

PREVIOUS_REVISION = "0042_provenance_immutability"
FROZEN = "scientific evidence for this pipeline run is locked"

EVIDENCE_TRIGGERS = (
    "data_quality_findings_evidence_locked",
    "data_preparation_decisions_evidence_locked",
    "preprocessing_steps_evidence_locked",
    "model_selection_decisions_evidence_locked",
    "experiment_test_predictions_evidence_locked",
    "experiment_candidates_evidence_locked",
    "model_hyperparameters_evidence_locked",
    "cv_fold_runs_evidence_locked",
    "evaluation_metrics_evidence_locked",
    "features_evidence_locked",
    "feature_lineage_evidence_locked",
    "feature_transformations_evidence_locked",
    "model_evaluations_evidence_locked",
    "pipeline_stage_runs_evidence_locked",
    "experiments_evidence_lock_stamp",
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


def _rejected(db_session, sql: str, **params) -> None:
    with pytest.raises(DBAPIError, match=FROZEN):
        _raw(db_session, sql, **params)
    db_session.rollback()


@pytest.fixture
def locked_run(auth_client, db_session, monkeypatch, _rule_engine_only):
    """A real Labs run, its evidence, and the stamp that freezes it."""

    upload, workflow_run, experiment, model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        ordinary_binary(),
        filename="frozen_evidence.csv",
        target="outcome",
    )
    db_session.expire_all()
    selection = db_session.scalar(
        select(ModelSelectionDecision).where(
            ModelSelectionDecision.pipeline_run_id == experiment.id
        )
    )
    assert selection is not None
    winner = db_session.get(ExperimentCandidate, selection.selected_candidate_id)
    holdout = db_session.scalar(
        select(ModelEvaluation).where(
            ModelEvaluation.candidate_id == winner.id,
            ModelEvaluation.evaluation_scope == "final_holdout",
        )
    )
    cv_evaluation = db_session.scalar(
        select(ModelEvaluation).where(
            ModelEvaluation.candidate_id == winner.id,
            ModelEvaluation.evaluation_scope == "cv_fold",
        )
    )
    assert holdout is not None and cv_evaluation is not None
    runner_up = db_session.scalar(
        select(ExperimentCandidate).where(
            ExperimentCandidate.experiment_id == experiment.id,
            ExperimentCandidate.id != winner.id,
        )
    )
    return SimpleNamespace(
        upload=upload,
        workflow_run=workflow_run,
        experiment=experiment,
        model_version=model_version,
        selection=selection,
        winner=winner,
        runner_up=runner_up,
        holdout=holdout,
        cv_evaluation=cv_evaluation,
        cv_fold=db_session.scalar(
            select(CVFoldRun).where(CVFoldRun.candidate_id == winner.id)
        ),
        cv_metric=db_session.scalar(
            select(EvaluationMetric).where(
                EvaluationMetric.model_evaluation_id == cv_evaluation.id
            )
        ),
        holdout_metric=db_session.scalar(
            select(EvaluationMetric).where(
                EvaluationMetric.model_evaluation_id == holdout.id
            )
        ),
        hyperparameter=db_session.scalar(
            select(ModelHyperparameter).where(
                ModelHyperparameter.candidate_id == winner.id
            )
        ),
        feature=db_session.scalar(
            select(Feature).where(
                Feature.feature_set_version_id == winner.feature_set_version_id
            )
        ),
        preprocessing=db_session.scalar(
            select(PreprocessingStep).where(
                PreprocessingStep.pipeline_run_id == experiment.id
            )
        ),
        test_prediction=db_session.scalar(
            select(ExperimentTestPrediction).where(
                ExperimentTestPrediction.experiment_id == experiment.id
            )
        ),
    )


def test_completed_labs_run_locks_every_piece_of_evidence(db_session, locked_run):
    experiment = locked_run.experiment
    assert experiment.scientific_evidence_locked_at is not None
    assert missing_scientific_evidence(db_session, experiment) == []
    status = scientific_evidence_status(db_session, experiment)
    assert set(status) == set(EVIDENCE_REQUIREMENTS)
    assert all(status.values()), status
    assert db_session.scalar(
        text("SELECT pipeline_run_scientific_evidence_complete(:run_id)"),
        {"run_id": experiment.id},
    ) is True
    # The stamp must land after the evidence it certifies.
    assert experiment.scientific_evidence_locked_at >= locked_run.selection.locked_at


def test_locked_run_rejects_cv_score_tampering(db_session, locked_run):
    fold = locked_run.cv_fold
    metric = locked_run.cv_metric
    assert fold is not None and metric is not None
    original = metric.metric_value

    _rejected(
        db_session,
        "UPDATE evaluation_metrics SET metric_value = 0.999999 WHERE id = :id",
        id=metric.id,
    )
    _rejected(
        db_session,
        "DELETE FROM evaluation_metrics WHERE id = :id",
        id=metric.id,
    )
    _rejected(
        db_session,
        """
        INSERT INTO evaluation_metrics (id, model_evaluation_id, metric_name, metric_value)
        VALUES (gen_random_uuid(), :evaluation, 'roc_auc_tampered', 1.0)
        """,
        evaluation=locked_run.cv_evaluation.id,
    )
    _rejected(
        db_session,
        "UPDATE cv_fold_runs SET validation_row_count = 1, status = 'failed' WHERE id = :id",
        id=fold.id,
    )
    _rejected(db_session, "DELETE FROM cv_fold_runs WHERE id = :id", id=fold.id)
    _rejected(
        db_session,
        """
        INSERT INTO cv_fold_runs (
            id, workspace_id, candidate_id, fold_number,
            train_row_count, validation_row_count, status
        ) VALUES (gen_random_uuid(), :workspace, :candidate, 99, 10, 10, 'completed')
        """,
        workspace=DEFAULT_WORKSPACE_ID,
        candidate=locked_run.winner.id,
    )

    db_session.expire_all()
    assert db_session.get(EvaluationMetric, metric.id).metric_value == original
    assert db_session.get(CVFoldRun, fold.id).status == "completed"


def test_locked_run_rejects_hyperparameter_tampering(db_session, locked_run):
    hyperparameter = locked_run.hyperparameter
    assert hyperparameter is not None
    original = hyperparameter.value_json

    _rejected(
        db_session,
        "UPDATE model_hyperparameters SET value_json = '9999'::jsonb WHERE id = :id",
        id=hyperparameter.id,
    )
    _rejected(
        db_session,
        "UPDATE model_hyperparameters SET source = 'planner', parameter_name = 'forged' "
        "WHERE id = :id",
        id=hyperparameter.id,
    )
    _rejected(
        db_session,
        "DELETE FROM model_hyperparameters WHERE candidate_id = :candidate",
        candidate=locked_run.winner.id,
    )
    _rejected(
        db_session,
        """
        INSERT INTO model_hyperparameters (id, candidate_id, parameter_name, value_json, source)
        VALUES (gen_random_uuid(), :candidate, 'forged_depth', '99'::jsonb, 'planner')
        """,
        candidate=locked_run.winner.id,
    )

    db_session.expire_all()
    assert db_session.get(ModelHyperparameter, hyperparameter.id).value_json == original


def test_locked_run_rejects_winner_candidate_tampering(db_session, locked_run):
    selection = locked_run.selection
    winner = locked_run.winner
    runner_up = locked_run.runner_up
    assert runner_up is not None

    _rejected(
        db_session,
        "UPDATE model_selection_decisions SET selected_candidate_id = :other WHERE id = :id",
        other=runner_up.id,
        id=selection.id,
    )
    _rejected(
        db_session,
        "UPDATE model_selection_decisions SET selected_score = 1.0, "
        "selection_metric = 'accuracy' WHERE id = :id",
        id=selection.id,
    )
    _rejected(
        db_session,
        "DELETE FROM model_selection_decisions WHERE id = :id",
        id=selection.id,
    )
    # A second, competing winner decision is refused as well.
    _rejected(
        db_session,
        """
        INSERT INTO model_selection_decisions (
            id, workspace_id, pipeline_run_id, selected_candidate_id, selection_metric,
            selected_score, selection_policy, reason, evidence, locked_at
        ) VALUES (
            gen_random_uuid(), :workspace, :run, :other, 'score', 1.0,
            'forged', 'forged', '{}'::jsonb, now()
        )
        """,
        workspace=DEFAULT_WORKSPACE_ID,
        run=locked_run.experiment.id,
        other=runner_up.id,
    )
    # And so is repainting the losing candidate as the trained winner.
    _rejected(
        db_session,
        "UPDATE experiment_candidates SET status = 'trained', "
        "payload = jsonb_set(payload, '{score}', '1.0'::jsonb) WHERE id = :id",
        id=runner_up.id,
    )
    _rejected(
        db_session,
        "UPDATE experiment_candidates SET fingerprint = :fingerprint WHERE id = :id",
        fingerprint="f" * 40,
        id=winner.id,
    )
    _rejected(
        db_session,
        "DELETE FROM experiment_candidates WHERE id = :id",
        id=runner_up.id,
    )

    # Neither repointing nor clearing the candidate's canonical feature-set link
    # is permitted after the final evidence lock.
    _rejected(
        db_session,
        "UPDATE experiment_candidates SET feature_set_version_id = gen_random_uuid() "
        "WHERE id = :id",
        id=runner_up.id,
    )
    _rejected(
        db_session,
        "UPDATE experiment_candidates SET feature_set_version_id = NULL WHERE id = :id",
        id=runner_up.id,
    )
    assert locked_run.model_version.feature_set_version_id == winner.feature_set_version_id

    db_session.expire_all()
    stored = db_session.get(ModelSelectionDecision, selection.id)
    assert stored.selected_candidate_id == winner.id
    assert stored.selected_score == selection.selected_score
    assert (
        db_session.scalar(
            select(ModelSelectionDecision.id).where(
                ModelSelectionDecision.pipeline_run_id == locked_run.experiment.id
            )
        )
        == selection.id
    )


def test_locked_run_rejects_final_holdout_metric_tampering(db_session, locked_run):
    holdout = locked_run.holdout
    metric = locked_run.holdout_metric
    prediction = locked_run.test_prediction
    assert metric is not None and prediction is not None
    original = metric.metric_value

    _rejected(
        db_session,
        "UPDATE evaluation_metrics SET metric_value = 1.0 WHERE id = :id",
        id=metric.id,
    )
    _rejected(
        db_session,
        "UPDATE model_evaluations SET summary = jsonb_set(summary, '{test_row_count}', '1'::jsonb) "
        "WHERE id = :id",
        id=holdout.id,
    )
    _rejected(
        db_session,
        "UPDATE model_evaluations SET evaluation_scope = 'cv_fold' WHERE id = :id",
        id=holdout.id,
    )
    # Unlinking the holdout from its published ModelVersion is refused too.
    _rejected(
        db_session,
        "UPDATE model_evaluations SET model_version_id = NULL WHERE id = :id",
        id=holdout.id,
    )
    _rejected(db_session, "DELETE FROM model_evaluations WHERE id = :id", id=holdout.id)
    _rejected(
        db_session,
        "UPDATE experiment_test_predictions SET probability = 1.0, "
        "predicted_value = '1'::jsonb WHERE id = :id",
        id=prediction.id,
    )
    _rejected(
        db_session,
        "DELETE FROM experiment_test_predictions WHERE id = :id",
        id=prediction.id,
    )
    # A second holdout evaluation would let a tamperer claim a better score.
    _rejected(
        db_session,
        """
        INSERT INTO model_evaluations (
            id, workspace_id, candidate_id, evaluation_type, evaluation_scope,
            dataset_id, status, summary
        ) VALUES (
            gen_random_uuid(), :workspace, :candidate, 'final_holdout', 'final_holdout',
            :dataset, 'completed', '{}'::jsonb
        )
        """,
        workspace=DEFAULT_WORKSPACE_ID,
        candidate=locked_run.winner.id,
        dataset=locked_run.experiment.dataset_id,
    )

    db_session.expire_all()
    assert db_session.get(EvaluationMetric, metric.id).metric_value == original
    reloaded = db_session.get(ModelEvaluation, holdout.id)
    assert reloaded.evaluation_scope == "final_holdout"
    assert reloaded.model_version_id == locked_run.model_version.id


def test_locked_run_rejects_feature_and_preparation_tampering(db_session, locked_run):
    feature = locked_run.feature
    step = locked_run.preprocessing
    assert feature is not None and step is not None

    _rejected(
        db_session,
        "UPDATE features SET definition = 'forged', status = 'dropped' WHERE id = :id",
        id=feature.id,
    )
    _rejected(db_session, "DELETE FROM features WHERE id = :id", id=feature.id)
    _rejected(
        db_session,
        """
        INSERT INTO features (
            id, workspace_id, project_id, feature_set_version_id, name,
            feature_type, output_dtype, definition, status
        ) VALUES (
            gen_random_uuid(), :workspace, :project, :version, 'forged_feature',
            'numeric', 'float64', 'forged', 'modeled'
        )
        """,
        workspace=feature.workspace_id,
        project=feature.project_id,
        version=feature.feature_set_version_id,
    )
    # A feature's transformation recipe and lineage inherit the freeze.
    _rejected(
        db_session,
        """
        INSERT INTO feature_transformations (
            id, feature_id, sequence, transformation_type, parameters, fit_required
        ) VALUES (gen_random_uuid(), :id, 99, 'forged_scale', '{}'::jsonb, false)
        """,
        id=feature.id,
    )
    lineage_relationship = db_session.scalar(
        text("SELECT relationship FROM feature_lineage WHERE feature_id = :id"),
        {"id": feature.id},
    )
    assert lineage_relationship is not None
    _rejected(
        db_session,
        "UPDATE feature_lineage SET relationship = 'forged' WHERE feature_id = :id",
        id=feature.id,
    )
    _rejected(
        db_session,
        "DELETE FROM feature_lineage WHERE feature_id = :id",
        id=feature.id,
    )
    _rejected(
        db_session,
        "UPDATE preprocessing_steps SET parameters = '{}'::jsonb, "
        "transformer_class = 'forged.Transformer' WHERE id = :id",
        id=step.id,
    )
    _rejected(
        db_session,
        "DELETE FROM preprocessing_steps WHERE pipeline_run_id = :run",
        run=locked_run.experiment.id,
    )
    # A clean dataset produces no findings, so prove the guard by trying to
    # invent audit trail for the frozen run instead.
    _rejected(
        db_session,
        """
        INSERT INTO data_quality_findings (
            id, workspace_id, pipeline_run_id, dataset_id, finding_type, severity, evidence
        ) VALUES (
            gen_random_uuid(), :workspace, :run, :dataset, 'missing_values', 'low', '{}'::jsonb
        )
        """,
        workspace=locked_run.experiment.workspace_id,
        run=locked_run.experiment.id,
        dataset=locked_run.experiment.dataset_id,
    )
    _rejected(
        db_session,
        """
        INSERT INTO data_preparation_decisions (
            id, workspace_id, pipeline_run_id, dataset_id, decision_type, strategy,
            parameter_value, reason, evidence, decision_source
        ) VALUES (
            gen_random_uuid(), :workspace, :run, :dataset, 'missing_values', 'impute_median',
            '{}'::jsonb, 'forged', '{}'::jsonb, 'deterministic'
        )
        """,
        workspace=locked_run.experiment.workspace_id,
        run=locked_run.experiment.id,
        dataset=locked_run.experiment.dataset_id,
    )

    db_session.expire_all()
    assert db_session.get(Feature, feature.id).status == feature.status
    assert (
        db_session.scalar(
            select(FeatureTransformation.id).where(
                FeatureTransformation.feature_id == feature.id
            )
        )
        is None
    )
    assert db_session.scalar(
        text("SELECT relationship FROM feature_lineage WHERE feature_id = :id"),
        {"id": feature.id},
    ) == lineage_relationship


def test_locked_run_freezes_finalized_stages_but_not_post_run_stages(db_session, locked_run):
    experiment = locked_run.experiment
    finalized = list(
        db_session.scalars(
            select(PipelineStageRun).where(
                PipelineStageRun.pipeline_run_id == experiment.id,
                PipelineStageRun.status.in_(("completed", "failed", "skipped")),
                PipelineStageRun.created_at <= experiment.scientific_evidence_locked_at,
            )
        )
    )
    assert finalized

    for stage in finalized:
        _rejected(
            db_session,
            "UPDATE pipeline_stage_runs SET status = 'failed', "
            "failure_reason = 'forged' WHERE id = :id",
            id=stage.id,
        )
    _rejected(
        db_session,
        "UPDATE pipeline_stage_runs SET output_summary = '{}'::jsonb WHERE id = :id",
        id=finalized[0].id,
    )
    _rejected(db_session, "DELETE FROM pipeline_stage_runs WHERE id = :id", id=finalized[0].id)

    _rejected(
        db_session,
        """
        INSERT INTO pipeline_stage_runs (
            id, workspace_id, pipeline_run_id, stage_key, stage_type,
            sequence, name, status, input_summary, output_summary
        ) VALUES (
            gen_random_uuid(), :workspace, :run, 'forged_training', 'execution',
            998, 'Forged training', 'completed', '{}'::jsonb, '{}'::jsonb
        )
        """,
        workspace=DEFAULT_WORKSPACE_ID,
        run=experiment.id,
    )

    # A stage opened after the lock is post-run evidence and stays writable, so
    # repeated advisory audits keep working.
    stage_id = db_session.scalar(
        text(
            """
            INSERT INTO pipeline_stage_runs (
                id, workspace_id, pipeline_run_id, stage_key, stage_type,
                sequence, name, status, input_summary, output_summary
            ) VALUES (
                gen_random_uuid(), :workspace, :run, 'post_lock_audit', 'verification',
                999, 'Post-lock audit', 'running', '{}'::jsonb, '{}'::jsonb
            ) RETURNING id
            """
        ),
        {"workspace": DEFAULT_WORKSPACE_ID, "run": experiment.id},
    )
    db_session.commit()
    _raw(
        db_session,
        "UPDATE pipeline_stage_runs SET status = 'completed' WHERE id = :id",
        id=stage_id,
    )
    _rejected(
        db_session,
        "UPDATE pipeline_stage_runs SET stage_key = 'forged_training', "
        "stage_type = 'execution' WHERE id = :id",
        id=stage_id,
    )
    _raw(db_session, "DELETE FROM pipeline_stage_runs WHERE id = :id", id=stage_id)
    assert db_session.get(PipelineStageRun, stage_id) is None
    db_session.expire_all()
    assert db_session.get(PipelineStageRun, finalized[0].id).status == finalized[0].status


def test_evidence_lock_stamp_is_one_way(db_session, locked_run):
    experiment = locked_run.experiment
    locked_at = experiment.scientific_evidence_locked_at

    for value in ("NULL", "now() + interval '1 day'", "now() - interval '1 year'"):
        with pytest.raises(DBAPIError, match="cannot be changed once the run is locked"):
            _raw(
                db_session,
                f"UPDATE experiments SET scientific_evidence_locked_at = {value} WHERE id = :id",
                id=experiment.id,
            )
        db_session.rollback()

    # Execution state is not scientific evidence and stays writable.
    _raw(
        db_session,
        "UPDATE experiments SET status = 'ARCHIVED', failure_reason = NULL WHERE id = :id",
        id=experiment.id,
    )
    db_session.expire_all()
    reloaded = db_session.get(Experiment, experiment.id)
    assert reloaded.status == "ARCHIVED"
    assert reloaded.scientific_evidence_locked_at == locked_at


def test_post_run_evidence_and_reverification_survive_the_lock(db_session, locked_run):
    experiment = locked_run.experiment
    report = (experiment.result or {}).get("technical_report") or {}
    before = PipelineVerifier().verify(report, db=db_session)
    assert before["overall_status"] in ACCEPTABLE_VERIFICATION, before

    # MlRunEvent stays append-only rather than frozen.
    next_sequence = (
        db_session.scalar(
            select(MlRunEvent.sequence)
            .where(MlRunEvent.experiment_id == experiment.id)
            .order_by(MlRunEvent.sequence.desc())
            .limit(1)
        )
        or 0
    ) + 1
    _raw(
        db_session,
        """
        INSERT INTO ml_run_events (
            id, workspace_id, workflow_run_id, experiment_id, sequence,
            stage, event_type, status, timestamp, payload
        ) VALUES (
            gen_random_uuid(), :workspace, :workflow_run, :run, :sequence,
            'post_lock', 'post_lock_probe', 'completed', now(), '{}'::jsonb
        )
        """,
        workspace=experiment.workspace_id,
        workflow_run=locked_run.workflow_run.id,
        run=experiment.id,
        sequence=next_sequence,
    )

    # Two further advisory attempts, each writing its own verification and audit
    # invocation plus the post-lock audit stage.
    first = request_pipeline_verification(db_session, locked_run.upload.id)
    second = request_pipeline_verification(db_session, locked_run.upload.id)
    assert isinstance(first, MlRunVerification) and first.id != second.id
    attempts = db_session.scalars(
        select(MlRunVerification.id).where(
            MlRunVerification.run_id == locked_run.upload.id
        )
    ).all()
    assert len(attempts) >= 2

    after = PipelineVerifier().verify(report, db=db_session)
    assert after["overall_status"] == before["overall_status"]
    assert after["overall_status"] in ACCEPTABLE_VERIFICATION, after


def test_locked_run_cannot_be_rerun_through_the_admin_api(admin_client, db_session, locked_run):
    response = admin_client.post(f"/admin/experiments/{locked_run.experiment.id}/run")
    assert response.status_code == 409, response.text
    assert "locked" in response.json()["detail"]
    db_session.expire_all()
    # The rejected re-run must not have touched the run's status either.
    assert db_session.get(Experiment, locked_run.experiment.id).status == "COMPLETED"


def test_incomplete_run_stays_unlocked_and_writable_beside_a_locked_one(
    db_session, locked_run
):
    """A half-written run must stay open, and the guards must be per-run."""

    environment = seed_dogfood(db_session)
    slug = f"partial-{uuid4().hex[:12]}"
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
    db_session.commit()

    assert lock_scientific_evidence(db_session, experiment) is None
    assert experiment.scientific_evidence_locked_at is None
    assert set(missing_scientific_evidence(db_session, experiment)) == set(EVIDENCE_REQUIREMENTS)

    # The database validates the transition too; direct SQL cannot prematurely
    # freeze an incomplete run and strand it in a half-written state.
    with pytest.raises(DBAPIError, match="before scientific evidence is complete"):
        _raw(
            db_session,
            "UPDATE experiments SET scientific_evidence_locked_at = now() WHERE id = :id",
            id=experiment.id,
        )
    db_session.rollback()
    assert db_session.get(Experiment, experiment.id).scientific_evidence_locked_at is None

    # Its evidence is still writable even though a sibling run is frozen, so the
    # guards are scoped to the locked run rather than the whole table.
    assert locked_run.experiment.scientific_evidence_locked_at is not None
    for run_id in (experiment.id, locked_run.experiment.id):
        statement = """
            INSERT INTO preprocessing_steps (
                id, workspace_id, pipeline_run_id, sequence, column_scope,
                transformer_type, transformer_class, parameters, fit_scope
            ) VALUES (
                gen_random_uuid(), :workspace, :run, 99, 'numeric',
                'imputer', 'sklearn.impute.SimpleImputer', '{}'::jsonb, 'fold_train'
            )
        """
        if run_id == experiment.id:
            _raw(db_session, statement, workspace=DEFAULT_WORKSPACE_ID, run=run_id)
        else:
            _rejected(db_session, statement, workspace=DEFAULT_WORKSPACE_ID, run=run_id)

    assert (
        db_session.scalar(
            select(PreprocessingStep.id).where(
                PreprocessingStep.pipeline_run_id == experiment.id
            )
        )
        is not None
    )


def _isolated_database(monkeypatch):
    admin_url = make_url(ADMIN_URL)
    database_name = f"decisionai_evidence_{uuid4().hex[:12]}"
    database_url = admin_url.set(database=database_name)
    admin_engine = create_engine(
        admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except Exception as exc:  # pragma: no cover - environment availability
        admin_engine.dispose()
        pytest.skip(f"cannot create isolated evidence-lock database: {exc}")
    rendered = database_url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", rendered)
    from app.config import get_settings

    get_settings.cache_clear()
    return admin_engine, database_name, database_url


def _installed_triggers(engine) -> set[str]:
    with engine.connect() as connection:
        present = set(
            connection.scalars(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))
        )
    return present & set(EVIDENCE_TRIGGERS)


def _has_lock_column(engine) -> bool:
    with engine.connect() as connection:
        return (
            connection.scalar(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'experiments' "
                    "AND column_name = 'scientific_evidence_locked_at'"
                )
            )
            is not None
        )


def test_alembic_0043_installs_evidence_lock_on_fresh_and_existing_databases(monkeypatch):
    admin_engine, database_name, database_url = _isolated_database(monkeypatch)
    from app.config import get_settings

    try:
        alembic_config = Config("alembic.ini")
        engine = create_engine(database_url)

        # Existing deployment: stop at the previous head, then upgrade forward.
        command.upgrade(alembic_config, PREVIOUS_REVISION)
        assert _installed_triggers(engine) == set()
        assert not _has_lock_column(engine)

        get_settings.cache_clear()
        command.upgrade(alembic_config, "head")
        assert _installed_triggers(engine) == set(EVIDENCE_TRIGGERS)
        assert _has_lock_column(engine)
        command.check(alembic_config)

        get_settings.cache_clear()
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        assert _installed_triggers(engine) == set()
        assert not _has_lock_column(engine)
        with engine.connect() as connection:
            # 0035 and 0042 own their own functions; 0043's downgrade leaves them.
            assert (
                connection.scalar(
                    text(
                        "SELECT proname FROM pg_proc "
                        "WHERE proname = 'prevent_locked_row_mutation'"
                    )
                )
                == "prevent_locked_row_mutation"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT proname FROM pg_proc "
                        "WHERE proname = 'pipeline_run_evidence_locked'"
                    )
                )
                is None
            )

        get_settings.cache_clear()
        command.upgrade(alembic_config, "head")
        assert _installed_triggers(engine) == set(EVIDENCE_TRIGGERS)
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
