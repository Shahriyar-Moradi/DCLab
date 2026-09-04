"""Admin ML-run visualization: flatten persisted cleaning / comparison values."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.services.admin_ml_run import build_ml_run, cleaning_steps, predictions_csv_text


def test_cleaning_steps_include_median_imputation_for_total_charges():
    cleaning_log = {
        "transformations": [
            {"step": "coerce_numeric", "columns": ["TotalCharges"]},
        ],
        "missing_value_plan": {
            "column_decisions": [
                {
                    "column": "TotalCharges",
                    "missing_count": 11,
                    "missing_fraction": 0.0016,
                    "action": "impute_median",
                    "fill_value": None,
                },
                {
                    "column": "tenure",
                    "missing_count": 0,
                    "missing_fraction": 0.0,
                    "action": "keep",
                    "fill_value": None,
                },
            ]
        },
    }
    rows = cleaning_steps(cleaning_log)
    coerce = next(row for row in rows if row.action == "Coerce to numeric")
    assert coerce.column == "TotalCharges"
    assert coerce.problem == "Stored as text"
    impute = next(row for row in rows if row.action == "Median imputation")
    assert impute.column == "TotalCharges"
    assert impute.problem == "11 missing values"
    assert "median" in impute.result.lower()
    assert all(row.action != "keep" for row in rows)


def test_cleaning_steps_record_duplicate_and_constant_drops():
    rows = cleaning_steps(
        {
            "transformations": [
                {"step": "drop_duplicate_rows", "rows_removed": 4},
                {"step": "drop_constant_columns", "columns": ["country"]},
            ]
        }
    )
    assert rows[0].problem == "4 duplicate rows"
    assert rows[1].column == "country"
    assert rows[1].action == "Dropped column"


def test_predictions_csv_prefers_artifact_file(tmp_path):
    path = tmp_path / "test_predictions.csv"
    path.write_text("row_index,y_true,y_pred,score\n0,1,1,0.9\n", encoding="utf-8")
    experiment = SimpleNamespace(artifact_dir=str(tmp_path), result={"test_predictions": []})
    text = predictions_csv_text(experiment)  # type: ignore[arg-type]
    assert text is not None
    assert "y_pred" in text
    assert "0.9" in text


def test_build_ml_run_copies_persisted_comparison_and_validation():
    upload_id = uuid4()
    upload = SimpleNamespace(
        id=upload_id,
        original_filename="telco.csv",
        pipeline_status="completed",
        pipeline_log={
            "analysis": {
                "row_count": 100,
                "column_count": 5,
                "missing_count": 11,
                "duplicate_rows": 0,
                "numerical_statistics": {"tenure": {}, "TotalCharges": {}},
                "categorical_statistics": {"contract": {}},
                "constant_columns": [],
                "high_cardinality_columns": [],
                "column_names": ["tenure", "TotalCharges", "contract", "churn"],
            },
            "cleaning": {"transformations": [], "columns_in": ["tenure", "TotalCharges", "contract", "churn"]},
            "feature_engineering": {
                "transformations": [],
                "numerical_cols": ["tenure", "TotalCharges"],
                "categorical_cols": ["contract"],
            },
            "target": {
                "column": "churn",
                "source": "rule",
                "reason": "strong deterministic candidate",
                "confidence": 0.88,
            },
            "numerical_cols": ["tenure", "TotalCharges"],
            "categorical_cols": ["contract"],
        },
        dataset_id=None,
    )
    experiment = SimpleNamespace(
        started_at=None,
        ended_at=None,
        artifact_dir=None,
        dataset_id=None,
        result={
            "task": {"target": "churn", "task_type": "binary"},
            "duration_seconds": 12.5,
            "split": {"n_train": 80, "n_test": 20, "random_state": 42},
            "validation": {
                "train_rows": 80,
                "test_rows": 20,
                "cv_strategy": "StratifiedKFold",
                "n_folds": 5,
                "random_state": 42,
            },
            "candidates": [
                {
                    "candidate_id": "logistic_regression__features",
                    "model_family": "logistic_regression",
                    "status": "trained",
                    "cv_mean": {"roc_auc": 0.821},
                    "test_metrics": {"roc_auc": 0.814},
                    "score": 0.82,
                },
                {
                    "candidate_id": "lightgbm__features",
                    "model_family": "lightgbm",
                    "status": "trained",
                    "cv_mean": {"roc_auc": 0.864},
                    "test_metrics": {"roc_auc": 0.856},
                    "score": 0.86,
                    "locked": True,
                },
            ],
            "best_single": {
                "candidate_id": "lightgbm__features",
                "model_family": "lightgbm",
                "cv_mean": {"roc_auc": 0.864, "accuracy": 0.8},
                "test_metrics": {"roc_auc": 0.856, "accuracy": 0.79},
                "locked": True,
            },
            "test_predictions": [
                {"row_index": 0, "y_true": 1, "y_pred": 1, "score": 0.9},
                {"row_index": 1, "y_true": 0, "y_pred": 0, "score": 0.2},
            ],
        },
    )
    run = build_ml_run(upload, experiment, None)  # type: ignore[arg-type]
    assert run.run_id == upload_id
    assert run.dataset == "telco.csv"
    assert run.analysis.rows == 100
    assert run.analysis.missing_values == 11
    assert "TotalCharges" in run.analysis.numerical_columns
    assert run.validation.cv_strategy == "StratifiedKFold"
    assert run.validation.n_folds == 5
    assert run.validation.random_state == 42
    by_family = {row.model_family: row for row in run.model_comparison}
    assert by_family["logistic_regression"].cv_auc == 0.821
    assert by_family["logistic_regression"].test_auc == 0.814
    assert by_family["lightgbm"].selected is True
    assert run.final_model is not None
    assert run.final_model.selected_model == "LightGBM"
    assert run.final_model.test_metrics["roc_auc"] == 0.856
    assert run.predictions.count == 2
    assert run.predictions.distribution == {"1": 1, "0": 1}
    assert run.predictions.download_available is True
    assert run.duration_seconds == 12.5
    assert run.target == "churn"
    assert run.task_type == "binary"
    assert run.target_source == "rule"
    assert run.target_reason == "strong deterministic candidate"
    assert run.target_confidence == 0.88
    assert run.processing_summary.cleaning_completed is True
    assert run.processing_summary.train_test_split == "80 / 20"
    assert run.processing_summary.cross_validation == "StratifiedKFold · 5 folds"
    assert run.processing_summary.training_completed is True
    assert run.processing_summary.predictions_completed is True
    assert run.failure_reason is None


def test_build_ml_run_surfaces_holdout_failure_reason():
    upload = SimpleNamespace(
        id=uuid4(),
        original_filename="part1-dataset.csv",
        pipeline_status="failed",
        pipeline_log={
            "reason": "Repeated-entity grouping and strong temporal prediction structure are both present.",
            "failed_at": "cleaning",
            "target": {
                "column": "hyper_ack",
                "source": "explicit",
                "reason": "explicit target supplied by user/admin",
                "confidence": 1.0,
                "task_type": "binary",
            },
            "analysis": {"row_count": 11118, "column_count": 16},
        },
        dataset_id=None,
    )
    run = build_ml_run(upload, None, None)  # type: ignore[arg-type]
    assert run.failure_reason.startswith("Repeated-entity grouping")
    assert run.task_type == "binary"
    assert run.target == "hyper_ack"

