"""Simple-case auto-train: the automatic EDA -> ColumnTransformer -> train/test
+ K-fold -> RandomForest/XGBoost job that runs behind a Labs custom-box
upload. Persists as a real Lab `Experiment` (never a `ClientLabRun`/
`ClientLabRunAudit`). See docs/LABS_DATA_UNDERSTANDING.md and
apps/api/plan for the full design.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.db.models import ClientLabUpload, DEFAULT_WORKSPACE_ID, Experiment
from app.services.auto_train_service import is_simple_tabular, run_auto_train_job


def _make_upload(db_session, *, stored_path: str, kind: str = "spreadsheet", record_count: int = 200, has_named_fields: bool = True) -> ClientLabUpload:
    row = ClientLabUpload(
        workspace_id=DEFAULT_WORKSPACE_ID,
        category="Revenue",
        original_filename="upload.csv",
        stored_path=stored_path,
        kind=kind,
        record_count=record_count,
        fields_noticed=[],
        has_named_fields=has_named_fields,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _telco_like_frame(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 72, n)
    monthly = rng.uniform(20, 120, n)
    total = tenure * monthly + rng.normal(0, 50, n)
    total_str = [f"{value:.2f}" if rng.random() > 0.05 else " " for value in total]
    contract = rng.choice(["Month-to-month", "One year", "Two year"], n)
    gender = rng.choice(["Male", "Female"], n)
    churn_p = np.where(contract == "Month-to-month", 0.55, 0.15)
    churn = rng.binomial(1, churn_p)
    churn_labels = np.where(churn == 1, "Yes", "No")
    return pd.DataFrame(
        {
            "customer_id": [f"C{i}" for i in range(n)],
            "tenure": tenure,
            "MonthlyCharges": monthly,
            "TotalCharges": total_str,
            "gender": gender,
            "contract": contract,
            "churn": churn_labels,
        }
    )


class TestSimpleTabularGate:
    def test_raw_log_kind_is_not_simple_tabular(self, db_session):
        upload = _make_upload(db_session, stored_path="/tmp/x.log", kind="plain_text", record_count=200, has_named_fields=False)
        assert is_simple_tabular(upload) is False

    def test_too_few_rows_is_not_simple_tabular(self, db_session):
        upload = _make_upload(db_session, stored_path="/tmp/x.csv", record_count=5)
        assert is_simple_tabular(upload) is False

    def test_named_spreadsheet_with_enough_rows_is_simple_tabular(self, db_session):
        upload = _make_upload(db_session, stored_path="/tmp/x.csv", record_count=200)
        assert is_simple_tabular(upload) is True


class TestAutoTrainJob:
    def test_skips_raw_log_without_running_anything(self, db_session):
        upload = _make_upload(db_session, stored_path="/tmp/does-not-matter.log", kind="plain_text", has_named_fields=False)
        run_auto_train_job(db_session, upload.id)
        db_session.refresh(upload)
        assert upload.pipeline_status == "skipped"
        assert upload.experiment_id is None
        assert "reason" in (upload.pipeline_log or {})

    def test_skips_when_too_few_rows(self, db_session):
        upload = _make_upload(db_session, stored_path="/tmp/does-not-matter.csv", record_count=5)
        run_auto_train_job(db_session, upload.id)
        db_session.refresh(upload)
        assert upload.pipeline_status == "skipped"

    def test_fails_cleanly_when_no_target_column_exists(self, db_session, tmp_path):
        frame = pd.DataFrame(
            {
                "customer_id": [f"C{i}" for i in range(50)],
                "plan_name": (["Gold", "Silver"] * 25),
                "amount": list(range(50)),
            }
        )
        path = tmp_path / "no_target.csv"
        frame.to_csv(path, index=False)
        upload = _make_upload(db_session, stored_path=str(path), record_count=len(frame))

        run_auto_train_job(db_session, upload.id)
        db_session.refresh(upload)
        assert upload.pipeline_status == "failed"
        assert upload.experiment_id is None
        assert "no label column found" in upload.pipeline_log["reason"]

    def test_completes_and_persists_a_real_experiment_for_a_telco_like_csv(self, db_session, tmp_path):
        frame = _telco_like_frame(n=200)
        path = tmp_path / "telco.csv"
        frame.to_csv(path, index=False)
        upload = _make_upload(db_session, stored_path=str(path), record_count=len(frame))

        run_auto_train_job(db_session, upload.id)
        db_session.refresh(upload)

        assert upload.pipeline_status == "completed"
        assert upload.experiment_id is not None

        log = upload.pipeline_log
        assert log["target"]["column"] == "churn"
        assert "TotalCharges" in log["numerical_cols"]
        assert set(log["categorical_cols"]) >= {"gender", "contract"}
        assert log["missing_value_decisions"]["rows_with_missing"] >= 1
        assert log["boost_family_used"] in {"xgboost", "lightgbm", None}
        assert "column_names" in log["analysis"]
        assert "cleaning" in log
        assert "feature_engineering" in log
        assert log["preprocessing"]["numerical"][0] == "imputer:median"

        experiment = db_session.get(Experiment, upload.experiment_id)
        assert experiment is not None
        assert experiment.status == "COMPLETED"
        result = experiment.result
        assert result["best_single"]["model_family"] in {
            "logistic_regression",
            "random_forest",
            "xgboost",
            "lightgbm",
        }
        families = {row["model_family"] for row in result["candidates"]}
        assert "logistic_regression" in families
        assert "random_forest" in families
        assert all("missing_variant" not in (row.get("preprocessing") or {}) for row in result["candidates"])
        assert result["split"]["strategy"] == "train_test_split"
        assert result["split"]["n_val"] == 0
        trained = [row for row in result["candidates"] if row["status"] == "trained"]
        assert all(row["n_folds"] == 5 for row in trained)
        assert all(len(row["fold_metrics"]) == 5 for row in trained)
        assert "accuracy" in result["test_metrics"]
        assert "f1" in result["test_metrics"]
        assert "mae" not in result["test_metrics"]
        assert result["train_metrics"]
        assert len(result["test_predictions"]) == result["split"]["n_test"]
        assert result["test_predictions"][0]["y_true"] in {0, 1}
        assert result["analysis"]["row_count"] == 200
        winner_id = result["best_single"]["candidate_id"]
        winner_cv = result["best_single"]["score"]
        for row in trained:
            assert "roc_auc" in row["test_metrics"]
            if row["candidate_id"] != winner_id:
                assert row["score"] <= winner_cv + 1e-12

    def test_persists_real_processing_stages_in_order(self, db_session, tmp_path, monkeypatch):
        from app.services import auto_train_service

        seen: list[str] = []
        original = auto_train_service._mark

        def tracking(db, upload, *, status, log=None, experiment_id=None):
            seen.append(status)
            return original(db, upload, status=status, log=log, experiment_id=experiment_id)

        monkeypatch.setattr(auto_train_service, "_mark", tracking)

        frame = _telco_like_frame(n=200)
        path = tmp_path / "telco_stages.csv"
        frame.to_csv(path, index=False)
        upload = _make_upload(db_session, stored_path=str(path), record_count=len(frame))
        auto_train_service.run_auto_train_job(db_session, upload.id)

        required = [
            "ingesting",
            "analyzing",
            "cleaning",
            "feature_engineering",
            "preprocessing",
            "splitting",
            "cross_validation",
            "training",
            "evaluating",
            "predicting",
            "completed",
        ]
        cursor = 0
        for status in seen:
            if cursor < len(required) and status == required[cursor]:
                cursor += 1
        assert cursor == len(required), seen
        db_session.refresh(upload)
        assert upload.pipeline_status == "completed"

    def test_drops_column_over_50_percent_missing_and_still_completes(self, db_session, tmp_path):
        frame = _telco_like_frame(n=200)
        # A mostly-empty column should be dropped, not imputed.
        frame["notes"] = [None] * 180 + ["free text"] * 20
        path = tmp_path / "telco_sparse_col.csv"
        frame.to_csv(path, index=False)
        upload = _make_upload(db_session, stored_path=str(path), record_count=len(frame))

        run_auto_train_job(db_session, upload.id)
        db_session.refresh(upload)

        assert upload.pipeline_status == "completed"
        assert "notes" in upload.pipeline_log["missing_value_decisions"]["dropped_columns"]
        assert "notes" not in upload.pipeline_log["numerical_cols"]
        assert "notes" not in upload.pipeline_log["categorical_cols"]

    def test_missing_upload_id_is_a_no_op(self, db_session):
        import uuid

        run_auto_train_job(db_session, uuid.uuid4())  # must not raise
