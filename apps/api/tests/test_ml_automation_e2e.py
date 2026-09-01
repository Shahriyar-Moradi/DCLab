"""Step 6 — End-to-end ML automation verification.

Exercises the existing upload → dataset → ML run → analysis → cleaning →
feature engineering → split → CV → selection → test prediction → client
result path. No new product features; these tests prove the pipeline that
already exists (plus the catalog wiring that lets a regression alias use the
existing open-ingest regression families).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.config import REPO_ROOT
from app.db.models import ClientLabUpload, Experiment
from app.engine.lab.auto_prepare import build_preprocessor
from app.engine.search.generator import open_ingest_families
from app.services.auto_train_service import run_auto_train_job
from app.translation.banned_terms import find_banned_terms

TELCO_PATH = REPO_ROOT / "data" / "sample" / "telco_like.csv"
REQUIRED_STAGES = [
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


def _disable_background_job(monkeypatch) -> None:
    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)


def _post_csv(auth_client, filename: str, frame: pd.DataFrame | bytes, *, category: str = "Revenue"):
    payload = frame if isinstance(frame, bytes) else frame.to_csv(index=False).encode()
    return auth_client.post(
        "/app/labs/uploads",
        data={"category": category},
        files={"file": (filename, payload, "text/csv")},
    )


def _classification_frame_with_missing(n: int = 220, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 72, n).astype(float)
    monthly = rng.uniform(20, 120, n)
    contract = rng.choice(["Month-to-month", "One year", "Two year"], n).astype(object)
    gender = rng.choice(["Male", "Female"], n).astype(object)
    tenure[::17] = np.nan
    gender[::19] = None
    churn_p = np.where(contract == "Month-to-month", 0.55, 0.18)
    churn = np.where(rng.binomial(1, churn_p) == 1, "Yes", "No")
    contract[0] = "Week-to-week"
    contract[1] = "Week-to-week"
    return pd.DataFrame(
        {
            "tenure": tenure,
            "MonthlyCharges": monthly,
            "gender": gender,
            "contract": contract,
            "churn": churn,
        }
    )


def _regression_frame(n: int = 200, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 72, n)
    monthly = rng.uniform(20, 120, n)
    segment = rng.choice(["smb", "midmarket", "enterprise"], n)
    revenue = 30 + 1.8 * tenure + 0.35 * monthly + rng.normal(0, 6, n)
    return pd.DataFrame(
        {
            "tenure": tenure,
            "MonthlyCharges": monthly,
            "segment": segment,
            "revenue_60d": revenue,
        }
    )


def _track_preprocessor_fit_sizes(monkeypatch) -> list[int]:
    """Record how many rows imputers see at fit time (train folds / train pool)."""
    from sklearn.impute import SimpleImputer

    sizes: list[int] = []
    original = SimpleImputer.fit

    def fit(self, X, y=None):
        sizes.append(len(X))
        return original(self, X, y)

    monkeypatch.setattr(SimpleImputer, "fit", fit)
    return sizes


def _assert_stages_in_order(seen: list[str]) -> None:
    cursor = 0
    for status in seen:
        if cursor < len(REQUIRED_STAGES) and status == REQUIRED_STAGES[cursor]:
            cursor += 1
    assert cursor == len(REQUIRED_STAGES), seen


class Test1ClassificationCsv:
    def test_real_telco_csv_travels_upload_to_client_result(self, auth_client, db_session, monkeypatch):
        _disable_background_job(monkeypatch)
        seen: list[str] = []
        from app.services import auto_train_service

        original = auto_train_service._mark

        def tracking(db, upload, *, status, log=None, experiment_id=None):
            seen.append(status)
            return original(db, upload, status=status, log=log, experiment_id=experiment_id)

        monkeypatch.setattr(auto_train_service, "_mark", tracking)

        csv_bytes = TELCO_PATH.read_bytes()
        created = _post_csv(auth_client, "telco_like.csv", csv_bytes)
        assert created.status_code == 200, created.text
        body = created.json()
        run_id = body["run_id"]
        assert run_id == body["id"]
        assert body["dataset_id"]
        assert body["status"] in {"queued", "processing"}
        assert body["outcome"] is None
        assert find_banned_terms(created.text) == []

        upload = db_session.get(ClientLabUpload, run_id)
        assert upload is not None
        assert upload.dataset_id is not None
        run_auto_train_job(db_session, upload.id)
        db_session.expire_all()
        db_session.refresh(upload)
        _assert_stages_in_order(seen)
        assert upload.pipeline_status == "completed"
        assert upload.experiment_id is not None

        log = upload.pipeline_log or {}
        assert log["analysis"]["row_count"] >= 40
        assert log["cleaning"]
        assert log["feature_engineering"]
        assert log["preprocessing"]["numerical"][0] == "imputer:median"
        assert log["preprocessing"]["categorical"][1] == "onehot:drop_first"

        experiment = db_session.get(Experiment, upload.experiment_id)
        result = experiment.result
        assert result["split"]["strategy"] == "train_test_split"
        assert result["split"]["n_val"] == 0
        assert result["validation"]["n_folds"] == 5
        assert result["validation"]["cv_strategy"] == "StratifiedKFold"
        trained = [row for row in result["candidates"] if row["status"] == "trained"]
        families = {row["model_family"] for row in trained}
        assert "logistic_regression" in families
        assert "random_forest" in families
        assert len(trained) == len(open_ingest_families("binary"))
        winner = result["best_single"]
        assert winner["locked"] is True
        assert winner["model_family"] in families
        winner_cv = winner["score"]
        for row in trained:
            assert row["n_folds"] == 5
            if row["candidate_id"] != winner["candidate_id"]:
                assert row["score"] <= winner_cv + 1e-12
        assert "roc_auc" in result["test_metrics"]
        assert len(result["test_predictions"]) == result["split"]["n_test"]

        detail = auth_client.get(f"/app/labs/uploads/{run_id}")
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["status"] == "completed"
        assert payload["run_id"] == run_id
        assert payload["outcome"] is not None
        assert payload["outcome"]["prediction_count"] > 0
        assert payload["outcome"]["task_kind"] == "classification"
        assert find_banned_terms(detail.text) == []
        again = auth_client.get(f"/app/labs/uploads/{run_id}")
        assert again.json()["outcome"]["prediction_count"] == payload["outcome"]["prediction_count"]
        assert again.json()["outcome"]["performance_percent"] == payload["outcome"]["performance_percent"]


class Test2And3MissingAndCategorical:
    def test_missing_values_and_onehot_are_fit_on_train_only(self, db_session, tmp_path, monkeypatch):
        from app.db.models import DEFAULT_WORKSPACE_ID

        frame = _classification_frame_with_missing()
        path = tmp_path / "missing.csv"
        frame.to_csv(path, index=False)
        sizes = _track_preprocessor_fit_sizes(monkeypatch)
        upload = ClientLabUpload(
            workspace_id=DEFAULT_WORKSPACE_ID,
            category="Revenue",
            original_filename="missing.csv",
            stored_path=str(path),
            kind="spreadsheet",
            record_count=len(frame),
            fields_noticed=list(frame.columns),
            has_named_fields=True,
        )
        db_session.add(upload)
        db_session.commit()
        run_auto_train_job(db_session, upload.id)
        db_session.refresh(upload)
        assert upload.pipeline_status == "completed"
        log = upload.pipeline_log
        assert log["analysis"]["missing_count"] >= 1 or log["missing_value_decisions"]["rows_with_missing"] >= 1
        assert "tenure" in log["numerical_cols"]
        assert "gender" in log["categorical_cols"] or "contract" in log["categorical_cols"]
        decisions = {item["column"]: item for item in log["missing_value_decisions"]["column_decisions"]}
        assert decisions["tenure"]["action"] == "impute_median"
        assert decisions["gender"]["action"] == "impute_most_frequent"

        experiment = db_session.get(Experiment, upload.experiment_id)
        n_train = experiment.result["split"]["n_train"]
        n_test = experiment.result["split"]["n_test"]
        n_full = n_train + n_test
        assert sizes
        assert max(sizes) == n_train
        assert n_full not in sizes
        assert all(size <= n_train for size in sizes)

        import joblib

        members = Path(experiment.artifact_dir) / "members"
        pipelines = list(members.glob("*.joblib"))
        assert pipelines
        pipeline = joblib.load(pipelines[0])
        prep = pipeline.named_steps["prep"]
        encoder = prep.named_transformers_["cat"].named_steps["onehot"]
        assert encoder.handle_unknown == "ignore"
        unseen = pd.DataFrame(
            {
                "tenure": [12.0],
                "MonthlyCharges": [40.0],
                "gender": ["UnknownGender"],
                "contract": ["Hourly"],
            }
        )
        cols = list(experiment.result["best_single"]["features"])
        transformed = pipeline.named_steps["prep"].transform(unseen.loc[:, cols])
        assert transformed.shape[0] == 1


class Test4Regression:
    def test_regression_csv_uses_kfold_and_mae_rmse_r2(self, auth_client, db_session, monkeypatch):
        _disable_background_job(monkeypatch)
        created = _post_csv(auth_client, "value.csv", _regression_frame())
        assert created.status_code == 200, created.text
        run_id = created.json()["run_id"]
        upload = db_session.get(ClientLabUpload, run_id)
        run_auto_train_job(db_session, upload.id)
        db_session.expire_all()
        db_session.refresh(upload)
        assert upload.pipeline_status == "completed", upload.pipeline_log
        experiment = db_session.get(Experiment, upload.experiment_id)
        result = experiment.result
        assert result["task"]["task_type"] == "regression"
        assert result["validation"]["cv_strategy"] == "KFold"
        assert result["validation"]["n_folds"] == 5
        families = {row["model_family"] for row in result["candidates"] if row["status"] == "trained"}
        assert "linear_regression" in families
        assert "random_forest_regressor" in families
        assert "mae" in result["test_metrics"]
        assert "rmse" in result["test_metrics"]
        assert "r2" in result["test_metrics"]
        assert len(result["test_predictions"]) == result["split"]["n_test"]
        detail = auth_client.get(f"/app/labs/uploads/{run_id}")
        assert detail.status_code == 200
        outcome = detail.json()["outcome"]
        assert outcome is not None
        assert outcome["task_kind"] == "regression"
        assert outcome["prediction_count"] > 0
        assert find_banned_terms(detail.text) == []


class Test5Failure:
    def test_unsupported_file_is_rejected_and_does_not_create_a_run(self, auth_client, db_session):
        response = auth_client.post(
            "/app/labs/uploads",
            data={"category": "Revenue"},
            files={"file": ("photo.png", b"\x89PNG\r\nnot-an-image", "image/png")},
        )
        assert response.status_code == 422
        assert db_session.query(ClientLabUpload).count() == 0
        assert find_banned_terms(response.text) == []

    def test_no_label_csv_fails_the_run_and_client_leaves_processing(self, auth_client, db_session, monkeypatch):
        _disable_background_job(monkeypatch)
        frame = pd.DataFrame(
            {
                "customer_id": [f"C{i}" for i in range(80)],
                "plan_name": (["Gold", "Silver"] * 40),
                "amount": list(range(80)),
            }
        )
        created = _post_csv(auth_client, "nolabel.csv", frame)
        assert created.status_code == 200
        run_id = created.json()["id"]
        looking = auth_client.get(f"/app/labs/uploads/{run_id}")
        assert looking.json()["status"] in {"queued", "processing"}
        assert looking.json()["outcome"] is None

        run_auto_train_job(db_session, run_id)
        db_session.expire_all()
        failed = auth_client.get(f"/app/labs/uploads/{run_id}")
        body = failed.json()
        assert body["status"] == "failed"
        assert body["outcome"] is None
        upload = db_session.get(ClientLabUpload, run_id)
        assert upload.pipeline_status == "failed"
        assert "no label column found" in (upload.pipeline_log or {}).get("reason", "")
        assert find_banned_terms(failed.text) == []
        again = auth_client.get(f"/app/labs/uploads/{run_id}")
        assert again.json()["status"] == "failed"
        assert again.json()["outcome"] is None


class Test6RefreshDuringProcessing:
    def test_reopen_run_url_reads_live_backend_stage(self, auth_client, db_session, monkeypatch):
        _disable_background_job(monkeypatch)
        created = _post_csv(auth_client, "mid.csv", _classification_frame_with_missing(n=80))
        run_id = created.json()["run_id"]
        upload = db_session.get(ClientLabUpload, run_id)
        upload.pipeline_status = "cleaning"
        db_session.commit()

        mid = auth_client.get(f"/app/labs/uploads/{run_id}")
        body = mid.json()
        assert body["status"] == "processing"
        assert body["pipeline_status"] == "processing"
        assert body["outcome"] is None
        assert body["milestone"] == "Preparing your data"
        assert body["headline"] == "Preparing your data"
        assert [row["label"] for row in body["steps"]][2] == "Preparing your data"
        assert [row["state"] for row in body["steps"]][2] == "current"
        assert "training" not in mid.text.lower()
        assert "data cleaning" not in mid.text.lower()
        assert "feature_engineering" not in mid.text.lower()
        assert find_banned_terms(mid.text) == []

        upload.pipeline_status = "completed"
        db_session.commit()
        done = auth_client.get(f"/app/labs/uploads/{run_id}")
        assert done.json()["status"] == "completed"
        assert done.json()["outcome"] is None


class Test8TestSetIntegrity:
    def test_cv_and_preprocessing_never_fit_on_holdout(self, tmp_path, monkeypatch):
        from app.engine.experiments.runner import run_experiment
        from app.engine.lab.auto_prepare import split_column_roles
        from app.engine.types import SearchConfig, TaskSpec

        frame = _classification_frame_with_missing(n=200, seed=3)
        columns = [c for c in frame.columns if c != "churn"]
        num_cols, cat_cols = split_column_roles(frame, columns)
        task = TaskSpec(
            id="integrity",
            name="integrity",
            task_type="binary",
            target="churn",
            entity_id="tenure",
            prediction_time_column=None,
            evaluation_metric="pr_auc",
            feature_groups={"features": num_cols + cat_cols},
            validation_strategy="stratified",
            column_roles={"numerical": num_cols, "categorical": cat_cols},
        )
        sizes = _track_preprocessor_fit_sizes(monkeypatch)
        result = run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=tmp_path,
            dataset_version="v1",
        )
        n_train = result["split"]["n_train"]
        n_test = result["split"]["n_test"]
        assert result["best_single"]["locked"] is True
        assert max(sizes) == n_train
        assert (n_train + n_test) not in sizes
        winner_id = result["best_single"]["candidate_id"]
        for row in result["candidates"]:
            if row.get("status") != "trained":
                continue
            assert row["n_train_rows"] == n_train
            if row["candidate_id"] != winner_id:
                assert row["score"] == row["cv_score"]["mean"]
        assert result["test_metrics"]
        preprocessor = build_preprocessor(["tenure"], ["contract"])
        train = pd.DataFrame({"tenure": [1.0, 2.0, np.nan], "contract": ["A", "B", "A"]})
        test = pd.DataFrame({"tenure": [99.0], "contract": ["unseen"]})
        preprocessor.fit(train)
        numeric = preprocessor.named_transformers_["num"].named_steps["imputer"]
        assert numeric.statistics_[0] == pytest.approx(1.5)
        encoded = preprocessor.transform(test)
        assert encoded.shape[0] == 1
        encoder = preprocessor.named_transformers_["cat"].named_steps["onehot"]
        assert encoder.handle_unknown == "ignore"
        assert "unseen" not in set(encoder.categories_[0])
