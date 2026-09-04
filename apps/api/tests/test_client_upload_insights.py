"""Unit tests for the open-ingest → ClientFacingInsight translator.

A completed upload with a real Experiment row must produce non-empty, banned-term
clean insights. Queued/running/failed uploads must return the matching status
and no insights — and a failed job must not leak its admin-only reason.
"""

from __future__ import annotations

from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    ClientLabUpload,
    Dataset,
    DatasetAsset,
    Experiment,
    PredictionTask,
)
from app.services.client_upload_insights import (
    FAILED_STATUS,
    LOOKING_STATUS,
    insights_for_upload,
    outcome_for_upload,
)
from app.services.lab_service import seed_dogfood
from app.translation.banned_terms import find_banned_terms


def _make_upload(db_session, **overrides) -> ClientLabUpload:
    payload = {
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "category": "Revenue",
        "original_filename": "customers.csv",
        "stored_path": "/tmp/customers.csv",
        "kind": "spreadsheet",
        "record_count": 200,
        "fields_noticed": ["tenure", "churn"],
        "has_named_fields": True,
        "pipeline_status": "queued",
    }
    payload.update(overrides)
    row = ClientLabUpload(**payload)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _seed_experiment(db_session, *, roc_auc: float = 0.84, target: str = "churn") -> Experiment:
    env = seed_dogfood(db_session)
    asset = DatasetAsset(
        workspace_id=DEFAULT_WORKSPACE_ID,
        name="open-ingest-test",
        slug="open-ingest-test",
    )
    db_session.add(asset)
    db_session.flush()
    dataset = Dataset(
        workspace_id=DEFAULT_WORKSPACE_ID,
        dataset_asset_id=asset.id,
        environment_id=env.id,
        name="open-ingest-test",
        source_type="csv",
        location="/tmp/customers.csv",
        version="v1",
        row_count=200,
        column_count=4,
    )
    db_session.add(dataset)
    db_session.flush()
    task = PredictionTask(
        environment_id=env.id,
        slug="open_ingest_test",
        name="Auto-train test",
        spec={"target": target, "task_type": "binary"},
    )
    db_session.add(task)
    db_session.flush()
    experiment = Experiment(
        workspace_id=DEFAULT_WORKSPACE_ID,
        environment_id=env.id,
        task_id=task.id,
        dataset_id=dataset.id,
        status="COMPLETED",
        config={"strategy": "open_ingest"},
        result={
            "task": {"target": target, "task_type": "binary", "evaluation_metric": "roc_auc"},
            "best_single": {
                "candidate_id": "random_forest__impute_all",
                "model_family": "random_forest",
                "score": roc_auc,
                "metrics": {"roc_auc": roc_auc, "pr_auc": roc_auc - 0.05},
            },
            "test_metrics": {"roc_auc": roc_auc, "pr_auc": roc_auc - 0.05},
            "test_predictions": [
                {"row_index": 0, "record_id": "C-100", "y_true": 1, "y_pred": 1, "score": 0.91},
                {"row_index": 1, "record_id": "C-101", "y_true": 0, "y_pred": 0, "score": 0.12},
            ],
            "analysis": {"row_count": 200, "column_count": 4},
        },
    )
    db_session.add(experiment)
    db_session.commit()
    db_session.refresh(experiment)
    return experiment


def test_completed_upload_with_real_experiment_returns_insights(db_session):
    experiment = _seed_experiment(db_session, roc_auc=0.84, target="churn")
    upload = _make_upload(
        db_session,
        pipeline_status="completed",
        experiment_id=experiment.id,
        pipeline_log={"target": {"column": "churn", "reason": "column name matches known label 'churn'"}},
    )

    view = insights_for_upload(db_session, upload)

    assert view.insights, "a completed experiment must produce at least one insight"
    assert 1 <= len(view.insights) <= 2
    blob = "".join(item.model_dump_json() for item in view.insights) + view.status
    assert find_banned_terms(blob) == []
    # Column name, family, and metric jargon stay admin-only.
    lowered = blob.lower()
    assert "churn" not in lowered
    assert "random_forest" not in lowered
    assert "auc" not in lowered
    assert "84" in blob
    assert "who is likely to leave" in lowered or "who may leave" in lowered


def test_completed_upload_returns_plain_language_outcome_and_predictions(db_session):
    experiment = _seed_experiment(db_session, roc_auc=0.846, target="churn")
    upload = _make_upload(
        db_session,
        pipeline_status="completed",
        experiment_id=experiment.id,
        record_count=200,
        original_filename="customers.csv",
        pipeline_log={
            "target": {"column": "churn", "reason": "column name matches known label 'churn'"},
            "numerical_cols": ["tenure", "MonthlyCharges"],
            "categorical_cols": ["contract"],
        },
    )

    outcome = outcome_for_upload(db_session, upload, include_predictions=True)
    assert outcome is not None
    assert outcome.title == "Analysis complete"
    assert outcome.summary == "We analyzed your dataset to tell who is likely to leave."
    assert outcome.record_count == 200
    assert outcome.feature_count == 3
    assert outcome.task_kind == "classification"
    assert outcome.method_label == "Trees"
    assert outcome.performance_percent == 84.6
    assert "new records from your file" in outcome.performance_summary
    assert outcome.prediction_count == 2
    assert len(outcome.predictions) == 2
    assert outcome.predictions[0].prediction == "Likely to leave"
    assert outcome.predictions[0].probability == 0.91
    assert outcome.predictions[0].record_id == "C-100"
    assert outcome.predictions[1].record_id == "C-101"
    assert outcome.download_available is True
    assert find_banned_terms(outcome.model_dump_json()) == []
    assert "churn" not in outcome.model_dump_json().lower()
    assert "random_forest" not in outcome.model_dump_json().lower()
    assert "auc" not in outcome.model_dump_json().lower()

    listed = outcome_for_upload(db_session, upload, include_predictions=False)
    assert listed is not None
    assert listed.prediction_count == 2
    assert listed.predictions == []


def test_analyzing_upload_returns_in_progress_status_and_no_insights(db_session):
    upload = _make_upload(db_session, pipeline_status="analyzing")
    view = insights_for_upload(db_session, upload)
    assert view.insights == []
    assert view.status == LOOKING_STATUS
    assert find_banned_terms(view.status) == []


def test_running_upload_returns_in_progress_status_and_no_insights(db_session):
    upload = _make_upload(db_session, pipeline_status="running")
    view = insights_for_upload(db_session, upload)
    assert view.insights == []
    assert view.status == LOOKING_STATUS
    assert find_banned_terms(view.status) == []
    assert "training" not in view.status.lower()


def test_queued_upload_returns_in_progress_status_and_no_insights(db_session):
    upload = _make_upload(db_session, pipeline_status="queued")
    view = insights_for_upload(db_session, upload)
    assert view.insights == []
    assert view.status == LOOKING_STATUS


def test_failed_upload_returns_safe_status_and_does_not_leak_admin_reason(db_session):
    secret = "no label column found: dropped columns tenure, notes"
    upload = _make_upload(
        db_session,
        pipeline_status="failed",
        pipeline_log={"reason": secret, "target": {"column": "churn"}},
    )
    view = insights_for_upload(db_session, upload)
    assert view.insights == []
    assert view.status == FAILED_STATUS
    assert secret not in view.status
    assert "churn" not in view.status.lower()
    assert "tenure" not in view.status.lower()
    assert find_banned_terms(view.status) == []
