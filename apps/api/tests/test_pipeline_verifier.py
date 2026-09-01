from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.services.pipeline_verifier import PipelineVerifier


def _valid_report(tmp_path):
    input_path = tmp_path / "input.csv"
    model_path = tmp_path / "model.joblib"
    result_path = tmp_path / "result.json"
    predictions_path = tmp_path / "test_predictions.csv"
    input_path.write_text("measure,segment,outcome\n1,a,0\n2,b,1\n3,a,0\n4,b,1\n5,a,0\n6,b,1\n")
    for path in (model_path, result_path, predictions_path):
        path.write_text("evidence")

    now = datetime.now(UTC)
    locked = now.isoformat()
    fit_start = (now + timedelta(milliseconds=1)).isoformat()
    fit_end = (now + timedelta(milliseconds=2)).isoformat()
    test_start = (now + timedelta(milliseconds=3)).isoformat()
    test_end = (now + timedelta(milliseconds=4)).isoformat()
    folds = [
        {
            "fold_number": 1,
            "train_provenance": [2, 3],
            "validation_provenance": [0, 1],
            "train_row_count": 2,
            "validation_row_count": 2,
            "metrics": {"pr_auc": 0.8},
            "fit_duration_ms": 1.0,
        },
        {
            "fold_number": 2,
            "train_provenance": [0, 1],
            "validation_provenance": [2, 3],
            "train_row_count": 2,
            "validation_row_count": 2,
            "metrics": {"pr_auc": 0.8},
            "fit_duration_ms": 1.0,
        },
    ]
    candidate = {
        "candidate_id": "winner",
        "model_family": "logistic_regression",
        "hyperparameters": {},
        "feature_set": ["measure", "segment"],
        "preprocessing_config": {"type": "ColumnTransformer"},
        "status": "trained",
        "failure_reason": None,
        "score": 0.8,
        "test_metrics": {"pr_auc": 0.79},
        "cv_strategy": "StratifiedKFold",
        "requested_folds": 5,
        "actual_folds": 2,
        "adaptation_reason": "Test fixture has too few rows for five folds.",
        "fold_metrics": [{"pr_auc": 0.8}, {"pr_auc": 0.8}],
        "cv_mean": {"pr_auc": 0.8},
        "cv_std": {"pr_auc": 0.0},
        "fit_duration_ms": 2.0,
        "folds": folds,
    }
    return {
        "run": {"status": "completed"},
        "raw_profile": {
            "row_count": 6,
            "column_count": 3,
            "column_names": ["measure", "segment", "outcome"],
            "columns": [
                {
                    "name": "measure",
                    "dtype": "float64",
                    "missing_count": 0,
                    "missing_ratio": 0.0,
                    "unique_count": 6,
                    "unique_ratio": 1.0,
                    "constant": False,
                    "high_cardinality": False,
                    "identifier_like": False,
                    "mean": 2.5,
                    "skewness": 0.0,
                },
                {
                    "name": "segment",
                    "dtype": "object",
                    "missing_count": 0,
                    "missing_ratio": 0.0,
                    "unique_count": 2,
                    "unique_ratio": 1 / 3,
                    "constant": False,
                    "high_cardinality": False,
                    "identifier_like": False,
                },
                {
                    "name": "outcome",
                    "dtype": "int64",
                    "missing_count": 0,
                    "missing_ratio": 0.0,
                    "unique_count": 2,
                    "unique_ratio": 1 / 3,
                    "constant": False,
                    "high_cardinality": False,
                    "identifier_like": False,
                    "mean": 0.5,
                    "skewness": 0.0,
                },
            ],
        },
        "target_decision": {
            "target_column": "outcome",
            "task_type": "binary",
            "source": "explicit",
            "confidence": 1.0,
            "reason": "Explicit target.",
            "candidates": [{"column": "outcome"}],
            "locked_at": locked,
        },
        "task": {
            "task_type": "binary",
            "target": "outcome",
            "feature_groups": {"features": ["measure", "segment"]},
        },
        "split": {
            "split_at": locked,
            "strategy": "train_test_split",
            "random_state": 42,
            "stratify": True,
            "n_train": 4,
            "n_test": 2,
            "train_source_rows": [0, 1, 2, 3],
            "test_source_rows": [4, 5],
            "all_source_rows": [0, 1, 2, 3, 4, 5],
        },
        "cleaning": {
            "rows_in": 6,
            "rows_out": 6,
            "columns_in": ["measure", "segment", "outcome"],
            "columns_out": ["measure", "segment", "outcome"],
            "transformations": [],
            "missing_value_plan": {
                "decision_partition": "train",
                "evidence_source_rows": [0, 1, 2, 3],
            },
        },
        "column_role_evidence": {
            "decision_partition": "train",
            "evidence_source_rows": [0, 1, 2, 3],
            "columns": [
                {
                    "column": "measure",
                    "original_dtype": "float64",
                    "final_role": "numerical",
                    "source": "rule",
                    "reason": "numeric",
                    "confidence": 1.0,
                    "validator_verdict": "not_run",
                    "llm_used": False,
                },
                {
                    "column": "segment",
                    "original_dtype": "object",
                    "final_role": "categorical",
                    "source": "rule",
                    "reason": "category",
                    "confidence": 1.0,
                    "validator_verdict": "not_run",
                    "llm_used": False,
                },
                {
                    "column": "outcome",
                    "original_dtype": "int64",
                    "final_role": "target",
                    "source": "explicit",
                    "reason": "selected",
                    "confidence": 1.0,
                    "validator_verdict": "not_run",
                    "llm_used": False,
                },
            ],
        },
        "feature_engineering": {
            "original_features": ["measure", "segment"],
            "generated_features": [],
            "transformed_features": [],
            "removed_features": [],
            "feature_engineering_actions": [],
        },
        "preprocessing": {
            "numeric_columns": ["measure"],
            "categorical_columns": ["segment"],
            "numeric_imputer_strategy": "median",
            "numeric_scaler": "StandardScaler",
            "categorical_imputer_strategy": "most_frequent",
            "categorical_encoder": "OneHotEncoder",
            "handle_unknown": "ignore",
            "fit_partition": "fold_train_only_then_full_train_for_locked_winner",
        },
        "candidate_models": [candidate],
        "expected_candidate_ids": ["winner"],
        "selection": {
            "candidate_id": "winner",
            "selected_candidate_id": "winner",
            "selection_metric": "pr_auc",
            "cv_score": 0.8,
            "selection_source": "cross_validation",
            "eligible_candidate_ids": ["winner"],
            "locked": True,
            "locked_at": locked,
        },
        "final_fit": {
            "candidate_id": "winner",
            "started_at": fit_start,
            "ended_at": fit_end,
            "duration_ms": 1.0,
            "fit_row_count": 4,
            "fit_partition": "full_train",
        },
        "final_test_evaluation": {
            "candidate_id": "winner",
            "evaluation_count": 1,
            "test_row_count": 2,
            "started_at": test_start,
            "ended_at": test_end,
            "duration_ms": 1.0,
            "metrics": {"pr_auc": 0.79},
        },
        "prediction_evidence": [
            {"source_row_index": 4, "y_true": 0, "y_pred": 0},
            {"source_row_index": 5, "y_true": 1, "y_pred": 1},
        ],
        "artifacts": {
            "input": str(input_path),
            "model": str(model_path),
            "result": str(result_path),
            "predictions": str(predictions_path),
        },
        "stage_timings": [
            {
                "stage": stage,
                "started_at": locked,
                "ended_at": fit_start,
                "duration_ms": 1.0,
                "status": "completed",
            }
            for stage in (
                "file_ingestion",
                "profiling",
                "target_task_resolution",
                "structural_cleaning",
                "splitting",
                "train_only_decisions",
                "column_roles",
                "feature_engineering",
                "preprocessing_setup",
                "cross_validation",
                "candidate_training",
                "model_selection",
                "final_fit",
                "final_test_evaluation",
                "prediction_persistence",
                "artifact_persistence",
                "deterministic_verification",
                "report_generation",
                "total_run",
            )
        ],
    }


def _status_for(result, check_id):
    return next(row["status"] for row in result["checks"] if row["check_id"] == check_id)


def test_valid_evidence_is_verified(tmp_path):
    result = PipelineVerifier().verify(_valid_report(tmp_path))
    assert result["overall_status"] == "VERIFIED", result
    assert all(row["status"] == "PASS" for row in result["checks"])


@pytest.mark.parametrize(
    ("case", "check_id"),
    [
        ("split_overlap", "split_provenance_complete"),
        ("test_in_cv", "cross_validation_provenance"),
        ("missing_profile", "eda_profile_complete"),
        ("identifier_modeled", "column_roles_valid"),
        ("target_modeled", "column_roles_valid"),
        ("wrong_winner", "winner_selected_from_cv"),
        ("late_lock", "final_fit_after_lock"),
        ("rejected_test_metrics", "winner_only_final_test"),
        ("missing_model", "model_artifacts_persisted"),
        ("prediction_count", "prediction_provenance_complete"),
        ("train_prediction", "prediction_provenance_complete"),
    ],
)
def test_deliberate_corruption_is_never_verified(tmp_path, case, check_id):
    report = deepcopy(_valid_report(tmp_path))
    if case == "split_overlap":
        report["split"]["test_source_rows"] = [3, 5]
    elif case == "test_in_cv":
        report["candidate_models"][0]["folds"][0]["train_provenance"].append(4)
    elif case == "missing_profile":
        report["raw_profile"] = {}
    elif case == "identifier_modeled":
        report["column_role_evidence"]["columns"][0]["final_role"] = "identifier"
    elif case == "target_modeled":
        report["task"]["feature_groups"]["features"].append("outcome")
    elif case == "wrong_winner":
        challenger = deepcopy(report["candidate_models"][0])
        challenger.update({"candidate_id": "challenger", "score": 0.9, "test_metrics": None})
        report["candidate_models"].append(challenger)
        report["expected_candidate_ids"].append("challenger")
        report["selection"]["eligible_candidate_ids"].append("challenger")
    elif case == "late_lock":
        report["selection"]["locked_at"] = report["final_test_evaluation"]["ended_at"]
    elif case == "rejected_test_metrics":
        rejected = deepcopy(report["candidate_models"][0])
        rejected.update({"candidate_id": "rejected", "score": 0.7, "test_metrics": {"pr_auc": 0.1}})
        report["candidate_models"].append(rejected)
        report["expected_candidate_ids"].append("rejected")
        report["selection"]["eligible_candidate_ids"].append("rejected")
    elif case == "missing_model":
        report["artifacts"]["model"] = str(tmp_path / "missing.joblib")
    elif case == "prediction_count":
        report["prediction_evidence"].pop()
    elif case == "train_prediction":
        report["prediction_evidence"][0]["source_row_index"] = 0

    result = PipelineVerifier().verify(report)
    assert result["overall_status"] in {"FAILED", "NOT_VERIFIABLE"}
    assert _status_for(result, check_id) in {"FAIL", "NOT_VERIFIABLE"}
