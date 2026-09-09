"""Queryable candidate, hyperparameter, CV fold, evaluation, and selection rows."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from adaptive_modeling.fixtures import ordinary_binary
from adaptive_modeling.production import labs_upload_and_train
from app.db.models import (
    CVFoldRun,
    EvaluationMetric,
    ExperimentCandidate,
    ModelEvaluation,
    ModelHyperparameter,
    ModelSelectionDecision,
    ModelVersion,
)
from app.services.technical_run_report import build_technical_run_report


@pytest.fixture
def _rule_engine_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )


def _parse_time(value) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def test_candidate_modeling_persists_from_real_auto_train(
    auth_client, db_session, monkeypatch, _rule_engine_only
):
    frame = ordinary_binary()
    upload, _workflow_run, experiment, model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="candidate_modeling.csv",
        target="outcome",
    )
    assert upload.pipeline_status == "completed"
    assert experiment.status == "COMPLETED"

    candidates = list(
        db_session.scalars(
            select(ExperimentCandidate).where(
                ExperimentCandidate.experiment_id == experiment.id
            )
        )
    )
    by_family = {row.model_family: row for row in candidates}
    assert "logistic_regression" in by_family
    assert "xgboost" in by_family
    logistic = by_family["logistic_regression"]
    xgboost = by_family["xgboost"]
    assert logistic.workspace_id == experiment.workspace_id
    assert logistic.project_id == experiment.project_id
    assert logistic.algorithm == "logistic_regression"
    assert logistic.implementation_library == "sklearn"
    assert logistic.implementation_class == "sklearn.linear_model.LogisticRegression"
    assert logistic.library_version
    assert logistic.search_stage == "open_ingest"
    assert logistic.payload.get("candidate_id") == logistic.candidate_key
    assert logistic.fingerprint
    assert xgboost.implementation_library == "xgboost"
    assert xgboost.implementation_class == "xgboost.XGBClassifier"
    assert xgboost.library_version

    logistic_hp = {
        row.parameter_name: row
        for row in db_session.scalars(
            select(ModelHyperparameter).where(
                ModelHyperparameter.candidate_id == logistic.id
            )
        )
    }
    xgboost_hp = {
        row.parameter_name: row
        for row in db_session.scalars(
            select(ModelHyperparameter).where(ModelHyperparameter.candidate_id == xgboost.id)
        )
    }
    assert "max_iter" in logistic_hp
    assert logistic_hp["max_iter"].source == "default"
    assert int(logistic_hp["max_iter"].value_json) == 1000
    assert "n_estimators" in xgboost_hp
    assert "max_depth" in xgboost_hp
    assert xgboost_hp["max_depth"].source == "default"

    trained = [row for row in candidates if row.status == "trained"]
    for candidate in trained:
        folds = list(
            db_session.scalars(
                select(CVFoldRun)
                .where(CVFoldRun.candidate_id == candidate.id)
                .order_by(CVFoldRun.fold_number)
            )
        )
        assert folds
        assert [row.fold_number for row in folds] == list(range(1, len(folds) + 1))
        assert all(row.train_row_count > 0 and row.validation_row_count > 0 for row in folds)
        fold_evals = list(
            db_session.scalars(
                select(ModelEvaluation).where(
                    ModelEvaluation.candidate_id == candidate.id,
                    ModelEvaluation.evaluation_scope == "cv_fold",
                )
            )
        )
        assert len(fold_evals) == len(folds)
        for evaluation in fold_evals:
            metrics = list(
                db_session.scalars(
                    select(EvaluationMetric).where(
                        EvaluationMetric.model_evaluation_id == evaluation.id
                    )
                )
            )
            assert metrics
            summary = evaluation.summary or {}
            assert "test_metrics" not in summary
        aggregate = db_session.scalar(
            select(ModelEvaluation).where(
                ModelEvaluation.candidate_id == candidate.id,
                ModelEvaluation.evaluation_scope == "cv_aggregate",
            )
        )
        assert aggregate is not None
        aggregate_metrics = list(
            db_session.scalars(
                select(EvaluationMetric).where(
                    EvaluationMetric.model_evaluation_id == aggregate.id
                )
            )
        )
        assert aggregate_metrics

    selection = db_session.scalar(
        select(ModelSelectionDecision).where(
            ModelSelectionDecision.pipeline_run_id == experiment.id
        )
    )
    assert selection is not None
    winner = db_session.get(ExperimentCandidate, selection.selected_candidate_id)
    assert winner is not None
    candidate_ids = {item.id for item in candidates}
    holdouts = [
        row
        for row in db_session.scalars(
            select(ModelEvaluation).where(
                ModelEvaluation.evaluation_scope == "final_holdout"
            )
        )
        if row.candidate_id in candidate_ids
    ]
    assert len(holdouts) == 1
    holdout = holdouts[0]
    assert holdout.candidate_id == winner.id
    assert holdout.model_version_id == model_version.id
    holdout_started = _parse_time(holdout.summary["started_at"])
    assert selection.locked_at <= holdout_started
    for candidate in candidates:
        if candidate.id == winner.id:
            continue
        scopes = {
            row.evaluation_scope
            for row in db_session.scalars(
                select(ModelEvaluation).where(ModelEvaluation.candidate_id == candidate.id)
            )
        }
        assert "final_holdout" not in scopes
    published = db_session.get(ModelVersion, model_version.id)
    assert published is not None
    assert published.selected_candidate_id == winner.id
    assert winner.payload
    assert winner.payload.get("cv_mean") or winner.payload.get("metrics")
    assert (experiment.result or {}).get("candidates")
    report = build_technical_run_report(
        db_session,
        upload=upload,
        experiment=experiment,
        result=experiment.result or {},
        pipeline_log=upload.pipeline_log or {},
    )
    assert report["selection"]["selected_candidate_id"] == winner.candidate_key
    assert report["final_test_evaluation"]["candidate_id"] == winner.candidate_key
