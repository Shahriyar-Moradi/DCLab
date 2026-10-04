"""P4.9-A: score new data with a model version (/v1 batch predictions).

Real auto-train runs and real worker jobs (``process_next_job``, as the durable
worker) with no engine mocks. A scoring upload is a normal ``POST /v1/datasets``
with ``purpose=scoring``; the API only queues ``models.batch_predict``. Scoring the
training rows must reproduce the run's own holdout predictions exactly, which
proves the pre-pipeline transforms (here a datetime -> epoch action) are replayed.
"""

from __future__ import annotations

import io
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import BatchPrediction, ExecutionRequest, Experiment, MlJob, ModelVersion
from app.engine.lab.auto_prepare import build_preprocessor, engineer_features
from app.engine.serving.batch_scoring import ScoringError, prepare_features, score_frame, scoring_spec
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

    # The API only queues: it never unpickles a model.
    def no_model_load(*_args, **_kwargs):
        raise AssertionError("the API process must not load a model")

    monkeypatch.setattr("joblib.load", no_model_load)
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

    out = _download(client, setup, body["id"])
    assert len(out) == len(frame) and list(out["row_number"]) == list(range(1, len(frame) + 1))
    assert set(out.columns) >= {"row_number", "probability", "label"}
    assert (out["label"] == (out["probability"] >= threshold).astype(int)).all()
    # Same rows -> exactly the run's own holdout scores (transforms replayed, full pipeline).
    holdout = _holdout(experiment)
    mine = out.set_index(out["row_number"] - 1).loc[holdout.index]
    np.testing.assert_allclose(mine["probability"], holdout["probability"].astype(float), rtol=0, atol=1e-9)
    assert (mine["label"].to_numpy() == holdout["y_pred"].astype(int).to_numpy()).all()

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
