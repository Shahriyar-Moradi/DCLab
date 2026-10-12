"""P4.9-A: score new data with a model version (/v1 batch predictions).

Real auto-train runs and real worker jobs (``process_next_job``, as the durable
worker) with no engine mocks. A scoring upload is a normal ``POST /v1/datasets``
with ``purpose=scoring``; the API only queues ``models.batch_predict``. Scoring the
training rows must reproduce the run's own holdout predictions exactly, which
proves the pre-pipeline transforms (here a datetime -> epoch action) are replayed.
"""

from __future__ import annotations

import io
import re
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db.models import Artifact, BatchPrediction, ExecutionRequest, Experiment, MlJob, ModelVersion
from app.domain.batch_predictions import REASON_METHODS
from app.engine.explain import REASON_COLUMNS
from app.engine.lab.auto_prepare import build_preprocessor, engineer_features
from app.engine.serving.batch_scoring import ScoringError, prepare_features, score_frame, scoring_spec
from app.engine.serving.model_package import read_package, sha256_hex
from app.services.artifact_service import read_artifact_bytes
from app.services.ml_job_service import process_next_job
from app.services.service_token_service import create_service_token
from test_v1_contract_conventions import _assert_envelope, _headers
from test_v1_resources_experiments import _h, _key, _root, setup  # noqa: F401 (fixture)


def _frame(task: str = "binary", n: int = 240, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    plan = rng.choice(["basic", "plus", "pro"], n)
    code = rng.choice(["1", "2", "A", "B"], n)  # digits and letters: categorical text
    tenure = rng.integers(1, 72, n).astype(float)
    visits = rng.integers(0, 4, n)
    days = rng.integers(0, 700, n)
    signal = 0.9 * (plan == "basic") - 0.03 * tenure + 0.4 * visits + 0.004 * (days - 350) + 0.5 * (code == "A")
    # Cells training cleans before the pipeline: "?"/"--" markers make tenure < 90%
    # numeric as uploaded (numeric only after cleaning), padded numbers, "n/a".
    roll = rng.random(n)
    tenure_text = np.array([f"{value:g}" for value in tenure], dtype=object)
    tenure_text[roll < 0.08] = "?"
    tenure_text[(roll >= 0.08) & (roll < 0.13)] = "--"
    tenure_text[(roll >= 0.13) & (roll < 0.17)] = "n/a"
    padded = (roll >= 0.17) & (roll < 0.25)
    tenure_text[padded] = [f" {value:g} " for value in tenure[padded]]
    spend = rng.uniform(20, 120, n).round(2)
    spend[rng.random(n) < 0.03] = np.nan  # scoring files carry inf here (_with_inf)
    frame = pd.DataFrame(
        {
            "customer_id": [f"c{index:05d}" for index in range(n)],
            "tenure": tenure_text,
            "spend": spend,
            "plan": plan,
            "code": code,
            "visits": visits,
            "signup_date": (pd.Timestamp("2023-01-01") + pd.to_timedelta(days, unit="D")).strftime("%Y-%m-%d"),
        }
    )
    if task == "binary":
        frame["label"] = np.where(rng.binomial(1, 1 / (1 + np.exp(-(signal - 0.4)))) == 1, "yes", "no")
    elif task == "multiclass":
        frame["tier"] = np.select([signal < -0.8, signal < 0.3], ["bronze", "silver"], "gold")
    else:
        frame["amount"] = (50 + 20 * signal + rng.normal(0, 2, n)).round(3)
    return frame


def _with_inf(frame: pd.DataFrame) -> pd.DataFrame:
    """The same rows with +inf where training had no spend: cleaning must make it missing.
    (Training files keep NaN: an inf in an upload breaks the run's JSON profile today.)"""

    return frame.assign(spend=frame["spend"].fillna(np.inf))


def _upload(client, setup, frame: pd.DataFrame, *, purpose: str = "scoring", workspace: str = "alpha") -> dict:  # noqa: F811
    response = client.post(
        "/v1/datasets",
        headers=_h(setup, setup[f"{workspace}_admin"], key=_key(), workspace=setup[workspace]),
        data={"project_id": str(setup[f"{workspace}_project"].id), "purpose": purpose},
        files={"file": ("rows.csv", frame.to_csv(index=False).encode(), "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _trained(client, db, setup, frame: pd.DataFrame, target: str) -> tuple[Experiment, UUID]:  # noqa: F811
    dataset = _upload(client, setup, frame, purpose="training")
    assert dataset["purpose"] == "training"
    root = _root(client, setup, dataset["id"], target_column=target)
    assert root.status_code == 202, root.text
    job = db.scalar(select(MlJob).where(MlJob.pipeline_run_id == UUID(root.json()["id"])))
    assert process_next_job(db, job_id=job.id, claimed_by="p49-test").status == "completed"
    db.expire_all()
    experiment = db.get(Experiment, UUID(root.json()["id"]))
    return experiment, db.scalar(select(ModelVersion.id).where(ModelVersion.pipeline_run_id == experiment.id))


def _predict(client, setup, mv_id, dataset_id: str, *, key: str | None = None, headers=None, **body):  # noqa: F811
    return client.post(
        f"/v1/model-versions/{mv_id}/predictions",
        json={"dataset_id": dataset_id, **body},
        headers=headers or _h(setup, key=key or _key()),
    )


def _work(db, prediction_id: str) -> MlJob:
    db.expire_all()
    job = db.scalar(select(MlJob).where(MlJob.target_id == UUID(prediction_id)))
    done = process_next_job(db, job_id=job.id, claimed_by="p49-test")
    db.expire_all()
    return done


def _download(client, setup, prediction_id: str, headers=None) -> pd.DataFrame:  # noqa: F811
    response = client.get(f"/v1/predictions/{prediction_id}/download", headers=headers or _h(setup))
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"].startswith("attachment; filename=")
    if response.headers["content-type"].startswith("application/vnd.apache.parquet"):
        return pd.read_parquet(io.BytesIO(response.content))
    assert response.headers["content-type"].startswith("text/csv")
    return pd.read_csv(io.BytesIO(response.content), float_precision="round_trip")


def _assert_reason_names(out: pd.DataFrame, features: list[str]) -> None:
    """Every reason names one of the model's input columns (never an encoded column) and
    says the direction; none shows a number from the row."""

    texts = [text for text in out[list(REASON_COLUMNS)].to_numpy().ravel() if isinstance(text, str)]
    assert texts
    for text in texts:
        name = re.match(r"^(.+?)(?::| =) ", text)
        assert name and name.group(1) in features, text
        assert re.search(r", (raised|lowered) the (model's score|predicted value|predicted class's)", text), text
        assert "__" not in text and not re.search(r"\d\.\d", text), text


def _holdout(experiment: Experiment) -> pd.DataFrame:
    """The run's own (single) holdout scoring, keyed by the raw source row."""

    return pd.DataFrame(experiment.result["test_predictions"]).set_index("source_row_index")


# --- binary end to end ------------------------------------------------------------------


def test_binary_scoring_end_to_end(client, db_session, setup, monkeypatch):  # noqa: F811
    db = db_session
    frame = _frame("binary")
    experiment, mv_id = _trained(client, db, setup, frame, "label")
    actions = experiment.result["scientific_evidence"]["feature_actions"]
    assert any("signup_date" in (action.get("columns") or []) for action in actions), actions
    assert actions[0]["parameters"]["formats"] == {"signup_date": "%Y-%m-%d"}  # learned on train rows
    features = experiment.result["best_single"]["features"]
    assert {"signup_date", "tenure", "spend", "code"} <= set(features)  # the cleaning matters
    cleaning = {step["step"] for step in experiment.result["cleaning"]["transformations"]}
    assert {"replace_invalid_strings", "coerce_numeric"} <= cleaning

    # Scoring rows: the training rows plus an extra column; the target is present too.
    scoring = _upload(client, setup, _with_inf(frame).assign(notes="free text"))
    assert scoring["purpose"] == "scoring"
    # A scoring upload is never a training source.
    refused = _root(client, setup, scoring["id"])
    _assert_envelope(refused, 409, "dataset_purpose_scoring")

    # The API only queues: it never loads a model (package or pickle).
    def no_model_load(*_args, **_kwargs):
        raise AssertionError("the API process must not load a model")

    monkeypatch.setattr("joblib.load", no_model_load)
    monkeypatch.setattr("skops.io.loads", no_model_load)
    key = _key()
    created = _predict(client, setup, mv_id, scoring["id"], key=key)
    assert created.status_code == 202, created.text
    body = created.json()
    assert created.headers["Location"] == f"/v1/predictions/{body['id']}"
    assert body["status"] == "queued" and body["model_release_id"] is None and body["output"] is None
    assert "object_key" not in created.text and "bucket" not in created.text
    replay = _predict(client, setup, mv_id, scoring["id"], key=key)
    assert replay.status_code == 202 and replay.json()["id"] == body["id"]
    assert replay.headers["Idempotent-Replayed"] == "true"
    _assert_envelope(_predict(client, setup, mv_id, scoring["id"], key=key, output_format="parquet"), 409,
                     "idempotency_key_conflict")
    job = db.scalar(select(MlJob).where(MlJob.target_id == UUID(body["id"])))
    assert (job.handler_key, job.status, job.job_type) == ("models.batch_predict", "queued", "batch_predict")
    request = db.get(ExecutionRequest, job.execution_request_id)
    assert request.operation == "model_batch_predict" and request.status == "accepted"
    assert client.get(f"/v1/predictions/{body['id']}", headers=_h(setup)).json()["status"] == "queued"
    _assert_envelope(client.get(f"/v1/predictions/{body['id']}/download", headers=_h(setup)), 409,
                     "prediction_not_ready")
    monkeypatch.undo()

    assert _work(db, body["id"]).status == "completed"
    read = client.get(f"/v1/predictions/{body['id']}", headers=_h(setup))
    done = read.json()
    assert done["status"] == "completed" and done["rows_in"] == done["rows_out"] == len(frame)
    assert read.headers["ETag"] == client.get(f"/v1/predictions/{body['id']}", headers=_h(setup)).headers["ETag"]
    threshold = experiment.result["decision_threshold"]["value"]
    assert done["decision_threshold"] == pytest.approx(threshold)
    contract = done["contract_check"]
    assert contract["status"] == "passed" and contract["missing_columns"] == [] and contract["empty_columns"] == []
    assert contract["parse_rates"] == {} and contract["unseen_categories"] == {}
    assert "notes" in contract["ignored_columns"] and "label" not in contract["ignored_columns"]
    assert contract["target_column"] == "label" and contract["target_column_ignored"] is True
    assert done["output"]["download_path"] == f"/v1/predictions/{body['id']}/download"
    assert db.get(ExecutionRequest, job.execution_request_id).status == "completed"
    # P7.1-A: scored from the model package, whose manifest is the version's pinned feature manifest.
    assert db.get(ExecutionRequest, job.execution_request_id).result_summary["model_format"] == "package_v1"
    version = client.get(f"/v1/model-versions/{mv_id}", headers=_h(setup)).json()
    assert version["saved_format"] == "package_v1"
    refs = {ref["role"]: ref for ref in version["artifacts"]}
    assert refs["model"]["mime_type"] == "application/zip" and refs["preprocessor"]["artifact_type"] == "preprocessor"
    package = read_artifact_bytes(db, workspace_id=setup["alpha"].id, artifact_id=UUID(refs["model"]["id"]))
    manifest = read_package(package).manifest
    assert sha256_hex(read_package(package).manifest_bytes) == refs["feature_manifest"]["content_digest"]
    assert manifest["training_run_id"] == str(experiment.id) and manifest["features"] == features
    assert manifest["cv_metrics"]["mean"] == experiment.result["best_single"]["cv_mean"]

    out = _download(client, setup, body["id"])
    assert len(out) == len(frame) and list(out["row_number"]) == list(range(1, len(frame) + 1))
    assert set(out.columns) >= {"row_number", "probability", "label"}
    assert (out["label"] == (out["probability"] >= threshold).astype(int)).all()
    # Same rows -> exactly the run's own holdout scores (transforms replayed, full pipeline).
    holdout = _holdout(experiment)
    mine = out.set_index(out["row_number"] - 1).loc[holdout.index]
    np.testing.assert_allclose(mine["probability"], holdout["probability"].astype(float), rtol=0, atol=1e-9)
    assert (mine["label"].to_numpy() == holdout["y_pred"].astype(int).to_numpy()).all()
    # P7.7-A: per-row reasons appended after every existing column, in the file's own column
    # names; the read and the request summary say how they were made (no row values there).
    assert list(out.columns)[-3:] == list(REASON_COLUMNS)
    reasons = done["reasons"]
    assert reasons["available"] is True and reasons["top_k"] == 3 and reasons["rows_explained"] == len(frame)
    assert reasons["method"] in REASON_METHODS and reasons["additive"] is (reasons["method"] != "occlusion")
    summary = db.get(ExecutionRequest, job.execution_request_id).result_summary
    assert summary["reasons_method"] == reasons["method"] and summary["reasons"] == reasons
    _assert_reason_names(out, features)

    # A finished prediction is frozen in PostgreSQL.
    with pytest.raises(DBAPIError, match="immutable"):
        db.execute(text("UPDATE batch_predictions SET rows_out = 1 WHERE id = :id"), {"id": body["id"]})
        db.commit()
    db.rollback()

    # Missing required column: failed with the names, never retried, no file.
    broken = _upload(client, setup, frame.drop(columns=["tenure", "label"]))
    failed_id = _predict(client, setup, mv_id, broken["id"]).json()["id"]
    failed_job = _work(db, failed_id)
    assert (failed_job.status, failed_job.attempts) == ("failed", 1)
    failed = client.get(f"/v1/predictions/{failed_id}", headers=_h(setup)).json()
    assert failed["status"] == "failed" and failed["error_code"] == "feature_contract_failed"
    assert failed["contract_check"]["missing_columns"] == ["tenure"] and "tenure" in failed["error_message"]
    assert failed["output"] is None and failed["rows_out"] is None
    _assert_envelope(client.get(f"/v1/predictions/{failed_id}/download", headers=_h(setup)), 409,
                     "prediction_not_ready")

    # Tenant isolation: another workspace sees nothing and cannot score across.
    beta = _headers(setup["beta_admin"], setup["beta"].id)
    _assert_envelope(client.get(f"/v1/predictions/{body['id']}", headers=beta), 404, "not_found")
    _assert_envelope(client.get(f"/v1/predictions/{body['id']}/download", headers=beta), 404, "not_found")
    _assert_envelope(_predict(client, setup, mv_id, scoring["id"], headers={**beta, "Idempotency-Key": _key()}),
                     404, "not_found")
    beta_rows = _upload(client, setup, frame, workspace="beta")
    _assert_envelope(_predict(client, setup, mv_id, beta_rows["id"]), 404, "dataset_not_found")

    # Service tokens: read may read and download, never score; experiments:write may score.
    read_token = create_service_token(db, creator=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="r",
                                      scopes=("read",), expires_in_days=1, current_password="test-password")[1]
    write_token = create_service_token(db, creator=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="w",
                                       scopes=("read", "experiments:write"), expires_in_days=1,
                                       current_password="test-password")[1]
    db.commit()
    as_reader = {"Authorization": f"Bearer {read_token}"}
    assert client.get(f"/v1/predictions/{body['id']}", headers=as_reader).status_code == 200
    assert len(_download(client, setup, body["id"], headers=as_reader)) == len(frame)
    _assert_envelope(_predict(client, setup, mv_id, scoring["id"], headers={**as_reader, "Idempotency-Key": _key()}),
                     403, "insufficient_scope")
    by_agent = _predict(client, setup, mv_id, scoring["id"],
                        headers={"Authorization": f"Bearer {write_token}", "Idempotency-Key": _key()})
    assert by_agent.status_code == 202, by_agent.text
    assert db.get(BatchPrediction, UUID(by_agent.json()["id"])).initiated_by_service_token_id is not None


# --- regression and multiclass -----------------------------------------------------------


def test_regression_scoring_parquet_and_transient_failure(client, db_session, setup, monkeypatch):  # noqa: F811
    db = db_session
    frame = _frame("regression", seed=5)
    experiment, mv_id = _trained(client, db, setup, frame, "amount")
    scoring = _upload(client, setup, _with_inf(frame).drop(columns=["amount"]).assign(extra=1))
    prediction_id = _predict(client, setup, mv_id, scoring["id"], output_format="parquet").json()["id"]
    assert _work(db, prediction_id).status == "completed"
    done = client.get(f"/v1/predictions/{prediction_id}", headers=_h(setup)).json()
    assert done["rows_in"] == done["rows_out"] == len(frame) and done["decision_threshold"] is None
    assert done["contract_check"]["target_column_ignored"] is False
    out = _download(client, setup, prediction_id)
    assert {"row_number", "prediction"} <= set(out.columns) and "probability" not in out.columns
    holdout = _holdout(experiment)
    mine = out.set_index(out["row_number"] - 1).loc[holdout.index]
    np.testing.assert_allclose(mine["prediction"], holdout["y_pred"].astype(float), rtol=0, atol=1e-9)
    assert done["reasons"]["available"] is True and list(out.columns)[-3:] == list(REASON_COLUMNS)
    _assert_reason_names(out, experiment.result["best_single"]["features"])

    # P7.7-A: reasons turned off -> the same columns as before and a read that says so.
    with monkeypatch.context() as off_switch:
        off_switch.setattr(get_settings(), "batch_reasons_enabled", False)
        off_id = _predict(client, setup, mv_id, scoring["id"]).json()["id"]
        assert _work(db, off_id).status == "completed"
    off = client.get(f"/v1/predictions/{off_id}", headers=_h(setup)).json()
    assert off["reasons"]["available"] is False and off["reasons"]["method"] is None
    assert off["reasons"]["unavailable_reason"] == "per-row reasons are turned off on this server"
    off_out = _download(client, setup, off_id)
    assert list(off_out.columns) == list(out.columns)[:-3]

    # A transient worker error is retried by the queue, then fails with generic text.
    def storage_down(*_args, **_kwargs):
        raise RuntimeError("object store unreachable at s3://internal/secret-key")

    monkeypatch.setattr("app.services.batch_prediction_service.read_artifact_bytes", storage_down)
    flaky_id = _predict(client, setup, mv_id, scoring["id"]).json()["id"]
    first = _work(db, flaky_id)
    assert (first.status, first.attempts) == ("queued", 1)  # requeued for another attempt
    assert db.get(BatchPrediction, UUID(flaky_id)).status == "queued"
    first.max_attempts = 2
    db.commit()
    final = _work(db, flaky_id)
    assert (final.status, final.attempts) == ("failed", 2)
    row = db.get(BatchPrediction, UUID(flaky_id))
    read = client.get(f"/v1/predictions/{flaky_id}", headers=_h(setup))
    assert row.status == "failed" and read.json()["error_code"] == "scoring_failed"
    assert "s3://" not in read.text and "secret" not in read.text


def test_multiclass_scoring_maps_labels_back(client, db_session, setup):  # noqa: F811
    db = db_session
    frame = _frame("multiclass", seed=3)
    experiment, mv_id = _trained(client, db, setup, frame, "tier")
    labels = experiment.result["class_labels"]
    assert sorted(labels) == ["bronze", "gold", "silver"]
    scoring = _upload(client, setup, _with_inf(frame))
    prediction_id = _predict(client, setup, mv_id, scoring["id"]).json()["id"]
    assert _work(db, prediction_id).status == "completed"
    out = _download(client, setup, prediction_id)
    columns = [f"probability_{label}" for label in labels]
    assert len(out) == len(frame) and set(columns + ["label"]) <= set(out.columns)
    np.testing.assert_allclose(out[columns].sum(axis=1), 1.0, atol=1e-9)
    assert (out["label"] == [labels[index] for index in out[columns].to_numpy().argmax(axis=1)]).all()
    holdout = _holdout(experiment)
    mine = out.set_index(out["row_number"] - 1).loc[holdout.index]
    assert (mine["label"].to_numpy() == holdout["y_pred"].to_numpy()).all()
    np.testing.assert_allclose(mine[columns].max(axis=1), holdout["probability"].astype(float), atol=1e-9)
    texts = [text for text in out[list(REASON_COLUMNS)].to_numpy().ravel() if isinstance(text, str)]
    assert texts and all("the predicted class's" in text for text in texts)
    # A scoring finished before P7.7-A recorded nothing about reasons: the read says so.
    request = db.get(ExecutionRequest, db.get(BatchPrediction, UUID(prediction_id)).execution_request_id)
    request.result_summary = {k: v for k, v in request.result_summary.items() if k not in {"reasons", "reasons_method"}}
    db.commit()
    legacy = client.get(f"/v1/predictions/{prediction_id}", headers=_h(setup)).json()["reasons"]
    assert legacy["available"] is False and legacy["unavailable_reason"].startswith("not recorded")


# --- P7.1-A: a model saved before the package format is quarantined ------------------------


def test_legacy_pickle_model_is_quarantined(client, db_session, setup, monkeypatch, caplog):  # noqa: F811
    """A run written the pre-P7.1-A way (a pickle, no package) is stored as a quarantined model:
    the read says so, the worker loads it only where legacy models are allowed (with a
    warning and a ``legacy_quarantined`` result), and refuses it otherwise."""

    import joblib

    from app.config import get_settings

    def legacy_writer(pipeline, *_args, **_kwargs):
        buffer = io.BytesIO()
        joblib.dump(pipeline, buffer)
        return buffer.getvalue()

    monkeypatch.setattr("app.engine.experiments.runner._saved_package", legacy_writer)
    monkeypatch.setattr("app.engine.experiments.runner.PACKAGE_FILENAME", "model.joblib")
    db = db_session
    frame = _frame("regression", seed=8)
    experiment, mv_id = _trained(client, db, setup, frame, "amount")
    monkeypatch.undo()
    version = client.get(f"/v1/model-versions/{mv_id}", headers=_h(setup)).json()
    assert version["saved_format"] == "legacy_quarantined"
    scoring = _upload(client, setup, frame.drop(columns=["amount"]))

    with caplog.at_level("WARNING", logger="app.services.batch_prediction_service"):
        allowed = _predict(client, setup, mv_id, scoring["id"]).json()["id"]
        assert _work(db, allowed).status == "completed"  # tests/dev: legacy models allowed by default
    assert any("QUARANTINED legacy pickle" in record.getMessage() for record in caplog.records)
    request = db.get(ExecutionRequest, db.get(BatchPrediction, UUID(allowed)).execution_request_id)
    assert request.result_summary["model_format"] == "legacy_quarantined"

    monkeypatch.setattr(get_settings(), "legacy_pickle_models_enabled", False)  # as production by default
    refused = _predict(client, setup, mv_id, scoring["id"]).json()["id"]
    job = _work(db, refused)
    assert (job.status, job.attempts) == ("failed", 1)  # deterministic: never retried
    body = client.get(f"/v1/predictions/{refused}", headers=_h(setup)).json()
    assert body["status"] == "failed" and body["error_code"] == "unsupported_model"
    assert body["error_message"] == (
        "this model version was saved in an older format that this server does not load; "
        "train it again to score new data"
    )


def test_parquet_decimal_and_date_columns_train_into_a_package_and_score(client, db_session, setup):  # noqa: F811
    """P7.1-A (review SF-4): Parquet decimal / date32 columns (Python objects in pandas) reach
    training through the prepared CSV as numbers and dates, so the winner is packaged; a
    Parquet scoring file with the same columns scores."""

    import datetime
    import decimal

    import pyarrow as pa
    import pyarrow.parquet as pq

    db = db_session
    frame = _frame("regression", seed=6)

    def parquet(rows: pd.DataFrame) -> bytes:
        table = pa.table({
            **{name: rows[name].tolist() for name in ("customer_id", "plan", "code", "visits")},
            "spend": pa.array([None if pd.isna(v) else decimal.Decimal(f"{v:.2f}") for v in rows["spend"]],
                              pa.decimal128(8, 2)),
            "signup_date": pa.array([datetime.date.fromisoformat(v) for v in rows["signup_date"]], pa.date32()),
            **({"amount": rows["amount"].tolist()} if "amount" in rows else {}),
        })
        buffer = io.BytesIO()
        pq.write_table(table, buffer)
        return buffer.getvalue()

    def upload(data: bytes, purpose: str) -> dict:
        response = client.post(
            "/v1/datasets", headers=_h(setup, setup["alpha_admin"], key=_key(), workspace=setup["alpha"]),
            data={"project_id": str(setup["alpha_project"].id), "purpose": purpose},
            files={"file": ("rows.parquet", data, "application/vnd.apache.parquet")},
        )
        assert response.status_code == 201, response.text
        return response.json()

    dataset = upload(parquet(frame), "training")
    root = _root(client, setup, dataset["id"], target_column="amount")
    assert root.status_code == 202, root.text
    job = db.scalar(select(MlJob).where(MlJob.pipeline_run_id == UUID(root.json()["id"])))
    assert process_next_job(db, job_id=job.id, claimed_by="p71-test").status == "completed"
    db.expire_all()
    mv_id = db.scalar(select(ModelVersion.id).where(ModelVersion.pipeline_run_id == UUID(root.json()["id"])))
    assert client.get(f"/v1/model-versions/{mv_id}", headers=_h(setup)).json()["saved_format"] == "package_v1"
    scoring = upload(parquet(frame.drop(columns=["amount"])), "scoring")
    prediction_id = _predict(client, setup, mv_id, scoring["id"]).json()["id"]
    assert _work(db, prediction_id).status == "completed"
    done = client.get(f"/v1/predictions/{prediction_id}", headers=_h(setup)).json()
    assert done["status"] == "completed" and done["rows_out"] == len(frame), done


def test_a_packaging_failure_fails_the_run_without_spending_the_final_test(client, db_session, setup, monkeypatch):  # noqa: F811
    """P7.1-A (ML review): the package is built between the final fit and the final test, so a
    run whose winner cannot be saved fails with a plain reason and leaves no selection decision,
    no final evaluation and no extra look on its split plan."""

    from app.db.models import ModelEvaluation, ModelSelectionDecision
    from app.engine.serving.model_package import PACKAGE_NOT_SAVED, ModelPackageError
    from app.services.experiment_verdict_service import plan_looks

    def no_safe_format(*_args, **_kwargs):
        raise ModelPackageError("internal detail at /tmp/run")

    monkeypatch.setattr("app.engine.experiments.runner.build_package", no_safe_format)
    db = db_session
    dataset = _upload(client, setup, _frame("binary", seed=12), purpose="training")
    root = _root(client, setup, dataset["id"], target_column="label")
    assert root.status_code == 202, root.text
    experiment_id = UUID(root.json()["id"])
    job = db.scalar(select(MlJob).where(MlJob.pipeline_run_id == experiment_id))
    job.max_attempts = 1  # (the queue retries training failures; one attempt is enough here)
    db.commit()
    assert process_next_job(db, job_id=job.id, claimed_by="p71-test").status == "failed"
    db.expire_all()
    experiment = db.get(Experiment, experiment_id)
    assert experiment.status == "FAILED"
    read = client.get(f"/v1/experiments/{experiment_id}", headers=_h(setup)).json()
    assert read["failure_reason"] == PACKAGE_NOT_SAVED  # the allowlisted plain sentence, no internals
    for model in (ModelSelectionDecision, ModelEvaluation):
        column = model.pipeline_run_id if model is ModelSelectionDecision else model.candidate_id
        query = select(model).where(model.workspace_id == experiment.workspace_id)
        if model is ModelSelectionDecision:
            query = query.where(column == experiment_id)
        else:
            from app.db.models import ExperimentCandidate

            query = query.join(ExperimentCandidate, ExperimentCandidate.id == column).where(
                ExperimentCandidate.experiment_id == experiment_id)
        assert db.scalars(query).first() is None
    looks, _comparisons = plan_looks(db, experiment.workspace_id, experiment.split_plan_id)
    assert experiment_id not in looks  # no look spent on the plan


# --- upload purpose and fail-closed replay (no training) ----------------------------------


def test_upload_purpose_is_validated_and_defaults_to_training(client, setup):  # noqa: F811
    bad = client.post(
        "/v1/datasets",
        headers=_h(setup, key=_key()),
        data={"project_id": str(setup["alpha_project"].id), "purpose": "labels"},
        files={"file": ("rows.csv", _frame().to_csv(index=False).encode(), "text/csv")},
    )
    assert _assert_envelope(bad, 422, "validation_failed")["details"]["errors"][0]["loc"] == ["body", "purpose"]
    plain = client.post(
        "/v1/datasets",
        headers=_h(setup, key=_key()),
        data={"project_id": str(setup["alpha_project"].id)},
        files={"file": ("rows.csv", _frame().to_csv(index=False).encode(), "text/csv")},
    )
    assert plain.status_code == 201 and plain.json()["purpose"] == "training"
    listed = client.get(f"/v1/datasets/{plain.json()['id']}", headers=_h(setup)).json()
    assert listed["purpose"] == "training"


def _fitted(columns_num, columns_cat, frame, target):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    pipeline = Pipeline([("prep", build_preprocessor(columns_num, columns_cat)), ("model", LogisticRegression())])
    return pipeline.fit(frame[columns_num + columns_cat], frame[target])


def _result(**evidence) -> dict:
    return {
        "task": {"task_type": "binary", "target": "y", "entity_id": None},
        "decision_threshold": {"value": 0.4},
        "scientific_evidence": {"feature_actions": [], "missing_value_plan": {"column_decisions": []}, **evidence},
    }


def test_replay_fails_closed_and_never_refits_on_scoring_rows():
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "code": ["1", "2", "x", "1", "2", "x"],
                          "y": [0, 0, 1, 0, 1, 1]})
    pipeline = _fitted(["x"], ["code"], train, "y")
    spec = scoring_spec(_result(), pipeline)
    assert spec.feature_columns == ("x", "code") and spec.decision_threshold == 0.4
    # Unknown or out-of-place preparation steps fail closed instead of scoring raw rows.
    for evidence in (
        {"feature_actions": [{"step": "log_transform", "columns": ["x"]}]},
        {"feature_actions": [{"step": "datetime_to_unix_seconds", "input_columns": ["x"], "output_columns": ["x2"]}]},
        {"missing_value_plan": {"column_decisions": [{"column": "x", "action": "winsorize"}]}},
        {"missing_value_plan": None},
    ):
        with pytest.raises(ScoringError) as caught:
            scoring_spec(_result(**evidence), pipeline)
        assert caught.value.code == "unsupported_transform"
    with pytest.raises(ScoringError) as caught:
        scoring_spec(_result(), {"fusion": None, "members": []})
    assert caught.value.code == "unsupported_model"
    # Training decided ``x`` is numeric and ``code`` is text: a scoring batch whose values
    # look otherwise is NOT re-decided (no 90% rule, no refit) and rows are kept.
    rows = pd.DataFrame({"x": ["oops", " 3 ", "n/a"], "code": [1, 2, 7], "y": [1, 1, 1], "z": [0, 0, 0]})
    prepared, facts = prepare_features(rows, spec)
    assert prepared["x"].tolist()[1] == 3.0 and np.isnan(prepared["x"].tolist()[0])
    assert facts["unparsed_values"] == {"x": 1} and facts["parse_rates"] == {"x": 0.5}  # "n/a" is a marker
    assert prepared["code"].tolist() == ["1", "2", "7"]  # matches the fitted encoder's text categories
    assert facts["unseen_categories"] == {"code": 1}
    scored = score_frame(rows, spec, pipeline)
    assert scored.contract["status"] == "warning"  # under 90% of tenure-like values parsed
    alone = score_frame(rows.iloc[[1]], spec, pipeline)
    assert len(scored.predictions) == 3
    assert scored.predictions["probability"].iloc[1] == pytest.approx(alone.predictions["probability"].iloc[0])
    assert scored.contract["ignored_columns"] == ["z"] and scored.contract["target_column_ignored"] is True
    with pytest.raises(ScoringError) as caught:
        score_frame(rows.drop(columns=["code"]), spec, pipeline)
    assert caught.value.code == "feature_contract_failed" and caught.value.contract["missing_columns"] == ["code"]
    # A required column with no usable value is a contract failure, never imputed.
    with pytest.raises(ScoringError) as caught:
        score_frame(rows.assign(x=["?", "n/a", None]), spec, pipeline)
    assert caught.value.code == "feature_contract_failed" and caught.value.contract["empty_columns"] == ["x"]
    clean = score_frame(pd.DataFrame({"x": [" 1 ", "2", "--"], "code": ["1", "x", None]}), spec, pipeline)
    assert clean.contract["status"] == "passed" and clean.contract["parse_rates"] == {}


def test_domain_fill_is_replayed_before_the_pipeline():
    train = pd.DataFrame({"x": [1.0, 2.0, np.nan, 4.0, 5.0, np.nan], "y": [0, 0, 1, 0, 1, 1]})
    pipeline = _fitted(["x"], [], train.assign(x=train["x"].fillna(-1.0)), "y")  # as decisions.py fills
    plan = {"column_decisions": [{"column": "x", "action": "domain_fill", "fill_value": -1.0}]}
    spec = scoring_spec(_result(missing_value_plan=plan), pipeline)
    prepared, _facts = prepare_features(pd.DataFrame({"x": [None, "n/a", "3"]}), spec)
    assert prepared["x"].tolist() == [-1.0, -1.0, 3.0]


def _with_actions(actions: list[dict], evidence: list[dict] | None = None) -> dict:
    result = _result(feature_actions=actions if evidence is None else evidence)
    result["task"]["feature_engineering"] = {"feature_engineering_actions": actions}
    return result


def test_dates_parse_with_the_training_format_and_fail_closed_otherwise():
    train = pd.DataFrame({"event_date": ["13/04/2024", "02/05/2024", "28/02/2024", "15/01/2024", "30/06/2024",
                                         "21/03/2024"], "y": [0, 1, 0, 1, 1, 0]})
    engineered, actions = engineer_features(train, ["event_date"])
    assert actions[0]["parameters"]["formats"] == {"event_date": "%d/%m/%Y"}  # day-first, from train rows
    pipeline = _fitted(["event_date"], [], engineered, "y")
    spec = scoring_spec(_with_actions(actions), pipeline)
    # This file starts with an ambiguous date: guessed per file, 03/04 would be March 4.
    prepared, _facts = prepare_features(pd.DataFrame({"event_date": ["03/04/2024", "13/04/2024"]}), spec)
    assert prepared["event_date"].tolist() == [pd.Timestamp("2024-04-03").timestamp(),
                                               pd.Timestamp("2024-04-13").timestamp()]
    # Training parsed text: numbers or tz-aware datetimes in the scoring file fail closed.
    for column in (pd.Series([20240403, 20240413]), pd.Series(pd.to_datetime(["2024-04-03", "2024-04-13"], utc=True))):
        with pytest.raises(ScoringError) as caught:
            prepare_features(pd.DataFrame({"event_date": column}), spec)
        assert caught.value.code == "unsupported_transform"
    # The evidence must agree with what the runner applied (here it says "no transform").
    with pytest.raises(ScoringError) as caught:
        scoring_spec(_with_actions(actions, evidence=[]), pipeline)
    assert caught.value.code == "unsupported_transform"
    # A runner action on a column the pipeline never uses does not matter.
    extra = [{**actions[0], "columns": ["other_date"], "output_columns": ["other_date"], "input_columns": ["other_date"]}]
    assert scoring_spec(_with_actions(actions + extra, evidence=actions), pipeline).date_formats == {
        "event_date": "%d/%m/%Y"}
    # Actions recorded before formats existed: only unambiguous ISO-8601 dates are scored.
    legacy = [{**actions[0], "parameters": {"unit": "seconds", "epoch": "unix"}}]
    legacy_spec = scoring_spec(_with_actions(legacy), pipeline)
    with pytest.raises(ScoringError) as caught:
        prepare_features(pd.DataFrame({"event_date": ["03/04/2024", "13/04/2024"]}), legacy_spec)
    assert caught.value.code == "unsupported_transform"
    iso, _facts = prepare_features(pd.DataFrame({"event_date": ["2024-04-03", None]}), legacy_spec)
    assert iso["event_date"].iloc[0] == pd.Timestamp("2024-04-03").timestamp()


# --- P7.2-A: scoring with the model in use carries its release --------------------------------


def test_scoring_carries_the_release_of_the_model_in_use_and_rechecks_it(client, db_session, setup):  # noqa: F811
    from app.db.models import ModelRelease

    db = db_session
    frame = _frame("binary", n=200)
    experiment, mv_id = _trained(client, db, setup, frame, "label")
    scoring = _upload(client, setup, frame.drop(columns=["label"]).head(40))

    # The first run's rule put it in use, but no person approved it: no release is claimed.
    unapproved = _predict(client, setup, mv_id, scoring["id"])
    assert unapproved.status_code == 202, unapproved.text
    assert unapproved.json()["model_release_id"] is None and unapproved.json()["release"] is None

    # A person approves the model in use (the existing "Put this model in use" route).
    project_id = experiment.project_id
    refs = {r["ref_kind"]: r for r in client.get(f"/v1/projects/{project_id}/refs", headers=_h(setup)).json()["items"]}
    model = db.get(ModelVersion, mv_id)
    companions = [{"ref_kind": "feature_recipe", "target_id": str(model.feature_set_version_id),
                   "expected_version": refs["feature_recipe"]["version"]}] if "feature_recipe" in refs else []
    approve = client.post(
        f"/v1/projects/{project_id}/refs/champion_model",
        json={"target_id": str(mv_id), "rationale": "approved for scoring", "companion_moves": companions,
              "evidence_refs": [{"kind": "candidate", "id": str(model.selected_candidate_id),
                                 "scope": "final_holdout"}]},
        headers=_h(setup, key=_key(), **{"If-Match": refs["champion_model"]["etag"]}),
    )
    assert approve.status_code == 200, approve.text
    release = db.scalar(select(ModelRelease).where(ModelRelease.model_version_id == mv_id))
    assert release.saved_format == "package_v1"

    approved = _predict(client, setup, mv_id, scoring["id"])
    assert approved.status_code == 202, approved.text
    assert approved.json()["model_release_id"] == str(release.id) and approved.json()["release"]["is_current"]
    assert _work(db, approved.json()["id"]).status == "completed"
    read = client.get(f"/v1/predictions/{approved.json()['id']}", headers=_h(setup)).json()
    assert read["status"] == "completed" and read["release"]["id"] == str(release.id)

    # The model artifact's recorded package format (what the loader branches on) no longer matches
    # the approved saved format: refused before anything is loaded or scored.
    artifact_id = model.model_artifact_id
    package_format = db.get(Artifact, artifact_id).extra_metadata["package_format"]
    queued = _predict(client, setup, mv_id, scoring["id"]).json()
    db.execute(text("UPDATE artifacts SET metadata = metadata - 'package_format' WHERE id = :id"), {"id": artifact_id})
    db.commit()
    _work(db, queued["id"])
    db.expire_all()
    flipped = db.get(BatchPrediction, UUID(queued["id"]))
    assert (flipped.status, flipped.error_code, flipped.output_artifact_id, flipped.rows_out) == (
        "failed", "release_mismatch", None, None)
    db.execute(text("UPDATE artifacts SET metadata = jsonb_set(metadata, '{package_format}', to_jsonb(CAST(:f AS text))) "
                    "WHERE id = :id"), {"f": package_format, "id": artifact_id})
    db.commit()

    # TOCTOU: the release no longer pins this package (simulated by bypassing the append-only
    # trigger): the worker refuses with a clear code instead of scoring under the approval.
    queued = _predict(client, setup, mv_id, scoring["id"]).json()
    db.execute(text("ALTER TABLE model_releases DISABLE TRIGGER model_releases_append_only"))
    db.execute(text("UPDATE model_releases SET package_manifest_digest = :d WHERE id = :id"),
               {"d": "0" * 64, "id": release.id})
    db.execute(text("ALTER TABLE model_releases ENABLE TRIGGER model_releases_append_only"))
    db.commit()
    _work(db, queued["id"])
    failed = db.get(BatchPrediction, UUID(queued["id"]))
    assert (failed.status, failed.error_code, failed.model_release_id) == ("failed", "release_mismatch", release.id)
