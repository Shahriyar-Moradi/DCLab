"""P1.6-A: finish-line end-to-end suite (spec §27 eight tests + §28 datasets A–E).

Every test drives the real upload → durable job → open-ingest engine path; the
engine is never mocked. Spies only observe (and call through to) real code.
"""

from __future__ import annotations

import io
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.db.models import (
    Artifact,
    ClientLabUpload,
    Experiment,
    ExperimentTestPrediction,
    ModelSelectionDecision,
)
from app.services import auto_train_service
from app.services.auto_train import training as training_stage
from app.services.artifact_service import read_artifact_bytes
from app.services.auto_train_service import run_auto_train_job
from app.translation.banned_terms import find_banned_terms

TERMINAL = {"completed", "failed", "needs_input", "skipped"}


def _no_background(monkeypatch) -> None:
    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)


def _upload(auth_client, frame: pd.DataFrame, *, name: str = "data.csv", target: str | None = None) -> str:
    data = {"category": "Revenue"}
    if target is not None:
        data["target_column"] = target
    response = auth_client.post(
        "/app/labs/uploads",
        data=data,
        files={"file": (name, frame.to_csv(index=False).encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def _train(db_session, upload_id: str) -> tuple[ClientLabUpload, dict]:
    run_auto_train_job(db_session, upload_id)
    db_session.expire_all()
    upload = db_session.get(ClientLabUpload, upload_id)
    experiment = db_session.get(Experiment, upload.experiment_id) if upload.experiment_id else None
    return upload, (experiment.result if experiment is not None else {})


def _winner_pipeline(db_session, upload: ClientLabUpload):
    row = db_session.scalar(
        select(Artifact)
        .where(Artifact.pipeline_run_id == upload.experiment_id, Artifact.artifact_type == "model")
        .order_by(Artifact.created_at.desc())
        .limit(1)
    )
    assert row is not None, "the winner model is published as an artifact"
    payload = read_artifact_bytes(db_session, workspace_id=row.workspace_id, artifact_id=row.id)
    return joblib.load(io.BytesIO(payload))


def _classification(n: int = 260, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 72, n).astype(float)
    monthly = rng.uniform(20, 120, n)
    contract = rng.choice(["Month-to-month", "One year", "Two year"], n).astype(object)
    churn_p = np.where(contract == "Month-to-month", 0.6, 0.15)
    churn = np.where(rng.binomial(1, churn_p) == 1, "Yes", "No")
    return pd.DataFrame({"tenure": tenure, "MonthlyCharges": monthly, "contract": contract, "churn": churn})


def _regression(n: int = 240, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    temperature = rng.uniform(-5, 35, n)
    humidity = rng.uniform(10, 95, n)
    pressure = rng.normal(1013, 8, n)
    energy = 120 + 3.2 * temperature - 0.4 * humidity + 0.2 * (pressure - 1013) + rng.normal(0, 4, n)
    return pd.DataFrame(
        {"temperature": temperature, "humidity": humidity, "pressure": pressure, "energy_output": energy}
    )


# ---------------------------------------------------------------------------
# §27 Test 1 — real classification lifecycle, and the client route uses that run
# ---------------------------------------------------------------------------


def test_1_classification_lifecycle_and_client_route(auth_client, db_session, monkeypatch):
    _no_background(monkeypatch)
    upload_id = _upload(auth_client, _classification())
    queued = auth_client.get(f"/app/labs/uploads/{upload_id}").json()
    assert queued["status"] in {"queued", "processing"} and queued["outcome"] is None

    upload, result = _train(db_session, upload_id)
    assert upload.pipeline_status == "completed", upload.pipeline_log
    stages = [row["stage"] for row in upload.pipeline_log["stage_timings"]]
    for stage in ("file_ingestion", "profiling", "structural_cleaning", "splitting", "feature_engineering"):
        assert stage in stages
    assert result["validation"]["n_folds"] == 5
    learned = [
        row for row in result["candidates"]
        if row["status"] == "trained" and row["model_family"] not in {"majority", "median", "mean"}
    ]
    assert len(learned) >= 3
    assert result["selection"]["locked"] is True
    assert result["final_test_evaluation"]["evaluation_count"] == 1
    n_test = result["split"]["n_test"]
    assert len(result["test_predictions"]) == n_test
    persisted = db_session.scalars(
        select(ExperimentTestPrediction).where(ExperimentTestPrediction.experiment_id == upload.experiment_id)
    ).all()
    assert len(persisted) == n_test

    detail = auth_client.get(f"/app/labs/uploads/{upload_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "completed"
    assert body["outcome"]["prediction_count"] == n_test
    assert find_banned_terms(detail.text) == []


# ---------------------------------------------------------------------------
# §27 Test 2 — missing values; preprocessing statistics come from training rows only
# ---------------------------------------------------------------------------


def test_2_imputers_and_scaler_are_fit_on_training_rows_only(auth_client, db_session, monkeypatch):
    _no_background(monkeypatch)
    frame = _classification(n=300, seed=3)
    frame.loc[frame.index[::9], "tenure"] = np.nan
    frame.loc[frame.index[::13], "contract"] = None
    upload_id = _upload(auth_client, frame)
    upload, result = _train(db_session, upload_id)
    assert upload.pipeline_status == "completed", upload.pipeline_log

    pipeline = _winner_pipeline(db_session, upload)
    prep = pipeline.named_steps["prep"]
    numeric = dict(prep.named_transformers_)["num"]
    numeric_cols = list(prep.transformers_[0][2])
    train_rows = frame.iloc[result["split"]["train_source_rows"]]
    test_rows = frame.iloc[result["split"]["test_source_rows"]]

    imputer = numeric.named_steps[next(name for name in numeric.named_steps if "imput" in name)]
    scaler = numeric.named_steps[next(name for name in numeric.named_steps if "scal" in name)]
    for position, column in enumerate(numeric_cols):
        expected_median = float(train_rows[column].median())
        assert imputer.statistics_[position] == pytest.approx(expected_median)
        filled = train_rows[column].fillna(expected_median)
        assert scaler.mean_[position] == pytest.approx(float(filled.mean()))
        everything = pd.concat([train_rows, test_rows])[column]
        if not np.isclose(float(everything.median()), expected_median):
            # Holdout values would have moved the statistic: they did not.
            assert not np.isclose(imputer.statistics_[position], float(everything.median()))

    # Missing values are imputed (no NaN reaches the model) on the holdout too.
    transformed = prep.transform(test_rows.drop(columns=["churn"]))
    assert not np.isnan(np.asarray(transformed, dtype=float)).any()


# ---------------------------------------------------------------------------
# §27 Test 3 — a category seen only at prediction time is ignored, not an error
# ---------------------------------------------------------------------------


def test_3_unseen_category_is_ignored(auth_client, db_session, monkeypatch):
    _no_background(monkeypatch)
    frame = _classification(seed=21)
    upload_id = _upload(auth_client, frame)
    upload, result = _train(db_session, upload_id)
    assert upload.pipeline_status == "completed"
    pipeline = _winner_pipeline(db_session, upload)
    encoder = dict(pipeline.named_steps["prep"].named_transformers_)["cat"]
    onehot = encoder.named_steps[next(name for name in encoder.named_steps if "onehot" in name or "encod" in name)]
    assert onehot.handle_unknown == "ignore"

    unseen = frame.drop(columns=["churn"]).iloc[:3].copy()
    unseen["contract"] = "Week-to-week"  # never present in the upload
    proba = pipeline.predict_proba(unseen)
    assert proba.shape == (3, 2) and np.isfinite(proba).all()


# ---------------------------------------------------------------------------
# §27 Test 4 — regression on a generic, non-Telco dataset
# ---------------------------------------------------------------------------


def test_4_regression_uses_kfold_and_regression_metrics(auth_client, db_session, monkeypatch):
    _no_background(monkeypatch)
    upload_id = _upload(auth_client, _regression())
    upload, result = _train(db_session, upload_id)
    assert upload.pipeline_status == "completed", upload.pipeline_log
    assert result["task"]["task_type"] == "regression"
    assert result["task"]["target"] == "energy_output"
    assert result["validation"]["cv_strategy"] == "KFold"
    families = {row["model_family"] for row in result["candidates"] if row["status"] == "trained"}
    assert {"linear_regression", "random_forest_regressor"} <= families
    assert not families & {"logistic_regression", "random_forest", "majority"}
    for metric in ("mae", "rmse", "r2"):
        assert metric in result["test_metrics"]
    assert result["test_metrics"]["r2"] > 0.8
    assert len(result["test_predictions"]) == result["split"]["n_test"]
    assert all(isinstance(row["y_pred"], float) for row in result["test_predictions"])


# ---------------------------------------------------------------------------
# §27 Test 5 — an unusable dataset fails for real, visibly and terminally
# ---------------------------------------------------------------------------


def test_5_invalid_dataset_fails_cleanly(auth_client, db_session, monkeypatch):
    _no_background(monkeypatch)
    rng = np.random.default_rng(0)
    frame = pd.DataFrame(
        {
            "feature_a": rng.normal(size=60),
            "feature_b": rng.choice(["x", "y"], 60),
            "outcome": ["same"] * 60,  # a single class: nothing to learn
        }
    )
    upload_id = _upload(auth_client, frame, target="outcome")
    upload, _result = _train(db_session, upload_id)
    assert upload.pipeline_status == "failed"
    assert upload.pipeline_log.get("reason")
    detail = auth_client.get(f"/app/labs/uploads/{upload_id}").json()
    assert detail["status"] in TERMINAL
    assert detail["status"] != "completed"
    assert detail["outcome"] is None
    experiment = db_session.get(Experiment, upload.experiment_id) if upload.experiment_id else None
    assert experiment is None or experiment.status != "COMPLETED"


# ---------------------------------------------------------------------------
# §27 Test 6 — a refresh mid-run rehydrates from persisted backend state
# ---------------------------------------------------------------------------


def test_6_mid_run_refresh_reads_backend_state(auth_client, db_session, monkeypatch):
    _no_background(monkeypatch)
    upload_id = _upload(auth_client, _classification(seed=8))
    seen: dict = {}
    real_execute = training_stage.execute_experiment

    def observe_then_run(*args, **kwargs):
        # A "browser refresh" while the job is mid-way: only persisted state answers.
        db_session.commit()
        seen["detail"] = auth_client.get(f"/app/labs/uploads/{upload_id}").json()
        return real_execute(*args, **kwargs)

    monkeypatch.setattr(training_stage, "execute_experiment", observe_then_run)
    upload, _result = _train(db_session, upload_id)
    assert upload.pipeline_status == "completed"
    mid = seen["detail"]
    assert mid["status"] == "processing"
    assert mid["pipeline_status"] not in TERMINAL | {"queued"}
    assert mid["outcome"] is None


# ---------------------------------------------------------------------------
# §27 Test 7 — a completed run reloads from persistence without retraining
# ---------------------------------------------------------------------------


def test_7_completed_refresh_does_not_retrain(auth_client, db_session, monkeypatch):
    _no_background(monkeypatch)
    upload_id = _upload(auth_client, _classification(seed=9))
    upload, _result = _train(db_session, upload_id)
    assert upload.pipeline_status == "completed"
    first = auth_client.get(f"/app/labs/uploads/{upload_id}").json()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a page load must never retrain")

    monkeypatch.setattr(training_stage, "execute_experiment", forbidden)
    monkeypatch.setattr(auto_train_service, "run_auto_train_job", forbidden)
    before = db_session.get(Experiment, upload.experiment_id)
    result_before, status_before = dict(before.result), before.status
    experiments_before = db_session.query(Experiment).count()
    second = auth_client.get(f"/app/labs/uploads/{upload_id}").json()
    assert second == first
    db_session.expire_all()
    after = db_session.get(Experiment, upload.experiment_id)
    assert after.result == result_before and after.status == status_before
    assert db_session.query(Experiment).count() == experiments_before


# ---------------------------------------------------------------------------
# §27 Test 8 — holdout integrity: never fit, never in CV, scored after the lock
# ---------------------------------------------------------------------------


def test_8_holdout_integrity_is_programmatically_enforced(auth_client, db_session, monkeypatch):
    from sklearn.impute import SimpleImputer

    _no_background(monkeypatch)
    fit_sizes: list[int] = []
    real_fit = SimpleImputer.fit

    def counting_fit(self, X, y=None):
        fit_sizes.append(len(X))
        return real_fit(self, X, y)

    monkeypatch.setattr(SimpleImputer, "fit", counting_fit)
    upload_id = _upload(auth_client, _classification(seed=12))
    upload, result = _train(db_session, upload_id)
    assert upload.pipeline_status == "completed"
    split = result["split"]
    train_rows, test_rows = set(split["train_source_rows"]), set(split["test_source_rows"])
    assert train_rows and test_rows and not train_rows & test_rows

    # Preprocessing never sees more rows than the training partition.
    assert fit_sizes and max(fit_sizes) <= split["n_train"]
    # CV folds draw only on training rows.
    for candidate in result["candidates"]:
        for fold in candidate.get("folds") or []:
            assert set(fold["train_provenance"]) <= train_rows
            assert set(fold["validation_provenance"]) <= train_rows
    # The winner was locked (and persisted) before the holdout was scored once.
    locked_at = datetime.fromisoformat(result["selection"]["locked_at"])
    scored_at = datetime.fromisoformat(result["final_test_evaluation"]["test_evaluation_started_at"])
    assert locked_at <= scored_at
    assert result["final_test_evaluation"]["evaluation_count"] == 1
    decision = db_session.scalar(
        select(ModelSelectionDecision).where(ModelSelectionDecision.pipeline_run_id == upload.experiment_id)
    )
    assert decision is not None
    rejected = [
        row for row in result["candidates"]
        if row["candidate_id"] != result["selection"]["selected_candidate_id"] and row.get("test_metrics")
    ]
    assert not rejected
    checks = {item["check_id"]: item["status"] for item in result["deterministic_verification"]["checks"]}
    assert "FAIL" not in checks.values(), [key for key, value in checks.items() if value == "FAIL"]


# ---------------------------------------------------------------------------
# §28 Datasets A–E — unfamiliar schemas, no business aliases
# ---------------------------------------------------------------------------


def _dataset(kind: str) -> tuple[pd.DataFrame, str, str, str | None]:
    """(frame, expected target, expected task type, explicit target or None)."""
    rng = np.random.default_rng({"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}[kind])
    n = 240
    if kind == "A":
        age = rng.integers(18, 80, n)
        income = rng.normal(52_000, 15_000, n)
        region = rng.choice(["north", "south", "east", "west"], n)
        p = 1 / (1 + np.exp(-(-2 + 0.04 * (age - 45) - (income - 52_000) / 20_000)))
        frame = pd.DataFrame(
            {"age": age, "income": income, "region": region, "defaulted": rng.binomial(1, p).astype(bool)}
        )
        return frame, "defaulted", "binary", None
    if kind == "B":
        return _regression(n, seed=2), "energy_output", "regression", None
    if kind == "C":
        amount = rng.lognormal(3.5, 1.0, n)
        merchant = rng.choice(["grocery", "travel", "electronics", "online"], n)
        country = rng.choice(["DE", "FR", "US", "BR"], n)
        p = np.clip(0.03 + 0.25 * (merchant == "online") + 0.002 * amount / 10, 0, 0.9)
        frame = pd.DataFrame(
            {
                "transaction_amount": amount,
                "merchant_type": merchant,
                "country": country,
                "fraud_flag": rng.binomial(1, p),
            }
        )
        return frame, "fraud_flag", "binary", None
    if kind == "D":
        f1, f2, f3 = rng.normal(size=(3, n))
        frame = pd.DataFrame(
            {"f1": f1, "f2": f2, "f3": f3, "outcome_x": np.where(f1 + 0.5 * f2 > 0, "pos", "neg")}
        )
        return frame, "outcome_x", "binary", "outcome_x"
    region_code = rng.integers(1, 5, n)  # E: integer category codes
    spend = rng.normal(100, 20, n)
    frame = pd.DataFrame(
        {
            "region_code": region_code,
            "spend": spend,
            "renewed": np.where(rng.uniform(size=n) < np.where(region_code == 2, 0.7, 0.3), "yes", "no"),
        }
    )
    return frame, "renewed", "binary", None


@pytest.mark.parametrize("kind", ["A", "B", "C", "D", "E"])
def test_unfamiliar_schema_trains_end_to_end(kind, auth_client, db_session, monkeypatch):
    _no_background(monkeypatch)
    frame, target, task_type, explicit = _dataset(kind)
    upload_id = _upload(auth_client, frame, name=f"dataset_{kind}.csv", target=explicit)
    upload, result = _train(db_session, upload_id)
    assert upload.pipeline_status == "completed", upload.pipeline_log.get("reason")
    assert result["task"]["target"] == target
    assert result["task"]["task_type"] == task_type
    assert result["baseline_comparison"] is not None
    if kind == "E":
        from app.engine.lab.evidence import build_column_type_evidence, is_ambiguous_column_type

        # A few-level integer code is flagged ambiguous for the column-type
        # agent (test_column_type_agent.py covers the reclassification); with
        # the agent off the deterministic dtype rule keeps it numeric.
        assert is_ambiguous_column_type(frame, "region_code", build_column_type_evidence(frame, "region_code"))
        assert "region_code" in result["preprocessing"]["numeric_columns"]


def test_explicit_two_label_target_positive_class_is_value_based():
    from app.engine.features.encode import binary_positive_label, coerce_binary_target

    labels = pd.Series(["neg", " pos", "neg", None, "pos "])
    assert binary_positive_label(labels) == "pos"
    assert coerce_binary_target(labels).tolist()[:3] == [0.0, 1.0, 0.0]
    # Order and counts never change the mapping; yes/no tokens keep their meaning.
    assert binary_positive_label(pd.Series(["pos"] * 9 + ["neg"])) == "pos"
    assert binary_positive_label(pd.Series(["yes", "no"])) is None



# ---------------------------------------------------------------------------
# P6.4-A — the Critic (AI-after) is queued on every completed run when AI is on,
# and its run never changes the experiment
# ---------------------------------------------------------------------------


def test_critic_runs_on_every_completed_run_when_enabled(auth_client, db_session, monkeypatch):
    from types import SimpleNamespace

    from app.agents.gateway.limits import GatewayLimits
    from app.agents.gateway.providers.fake import FakeProvider
    from app.agents.gateway.service import GatewayService
    from app.agents.governance.seed import seed_platform_governance
    from app.agents.harness.service import AgentService, spec_for_job
    from app.agents.prompt_releases import sync_prompt_releases
    from app.db.models import AgentProposal, AgentRun, MlJob

    _no_background(monkeypatch)
    on = SimpleNamespace(ai_enabled=True, service_tokens_enabled=True, dclab_env="test")
    seed_platform_governance(db_session, environment="test")
    sync_prompt_releases(db_session)
    db_session.commit()
    # AI on for the run's post-commit hooks only (the legacy decision agent stays off).
    monkeypatch.setattr("app.services.auto_train.context.get_settings", lambda: on)
    monkeypatch.setattr("app.services.pipeline_audit_service.get_settings",
                        lambda: SimpleNamespace(ai_enabled=False, pipeline_llm_timeout_seconds=1.0))

    reviewed = []
    for frame in (_classification(), _regression()):
        upload, _result = _train(db_session, _upload(auth_client, frame))
        assert upload.pipeline_status == "completed", upload.pipeline_log
        experiment = db_session.get(Experiment, upload.experiment_id)
        run = db_session.scalar(select(AgentRun).where(AgentRun.experiment_id == experiment.id,
                                                       AgentRun.agent_key == "experiment_critic"))
        assert run is not None and run.status == "queued" and run.decision_point_key == "experiment.review"
        reviewed.append((experiment, run))

    advice = {"verdict": "needs_work", "summary": "Insufficient evidence for promotion.", "cv_metrics": [],
              "findings": [], "confidence": 0.4}
    fake = FakeProvider(handler=lambda _call: dict(advice), environment="test")
    service = AgentService(gateway=GatewayService(providers={"openai": fake}, limits=GatewayLimits(),
                                                  settings=lambda: on), settings=lambda: on)
    for experiment, run in reviewed:
        before = (experiment.status, experiment.scientific_evidence_locked_at, dict(experiment.result))
        job = db_session.scalar(select(MlJob).where(MlJob.target_id == run.id))
        result = service.run(db_session, spec_for_job(db_session, job))
        assert result.status == "completed", result
        db_session.expire_all()
        experiment = db_session.get(Experiment, experiment.id)
        assert (experiment.status, experiment.scientific_evidence_locked_at, experiment.result) == before
        [proposal] = db_session.scalars(select(AgentProposal).where(AgentProposal.run_id == run.id))
        assert proposal.proposal_type == "ExperimentReviewProposal" and proposal.status in ("shadow", "proposed")
    assert len(fake.calls) == 2
    assert not any("holdout" in call.input_json or "final_test" in call.input_json for call in fake.calls)
