from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import joblib
import numpy as np
import pandas as pd

from app.db.models import ClientLabUpload, DEFAULT_WORKSPACE_ID, Experiment
from app.engine.lab.llm_client import MissingValueDecision
from app.engine.types import SearchConfig, TaskSpec
from app.engine.validation.splits import SOURCE_ROW_COLUMN, split_train_test_holdout
from app.services.auto_train_service import run_auto_train_job
from app.services.ml_run_docx import render_ml_run_report_docx


def _integrity_frame(n: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(19)
    frame = pd.DataFrame(
        {
            SOURCE_ROW_COLUMN: np.arange(n),
            "measure": rng.normal(10, 2, n),
            "segment": np.where(np.arange(n) % 2, "A", "B"),
            "outcome": np.where(np.arange(n) % 3, 0, 1),
        }
    )
    _, _, _, split = split_train_test_holdout(
        frame,
        target="outcome",
        seed=42,
        stratify=True,
    )
    test_rows = set(split["test_source_rows"])
    train_rows = [index for index in range(n) if index not in test_rows]
    frame.loc[frame[SOURCE_ROW_COLUMN].isin(test_rows), "measure"] = 1_000_000_000.0
    frame.loc[frame[SOURCE_ROW_COLUMN].isin(test_rows), "segment"] = "TEST_ONLY"
    frame.loc[frame[SOURCE_ROW_COLUMN].isin(train_rows[::11]), "measure"] = np.nan
    return frame


def _integrity_task() -> TaskSpec:
    return TaskSpec(
        id="ml2-integrity",
        name="ML-2 integrity",
        task_type="binary",
        target="outcome",
        entity_id=None,
        prediction_time_column=None,
        evaluation_metric="pr_auc",
        feature_groups={"features": ["measure", "segment"]},
        validation_strategy="stratified",
        column_roles={"numerical": ["measure"], "categorical": ["segment"]},
    )


def test_holdout_cannot_fit_preprocessing_or_enter_cv_and_winner_is_locked_first(tmp_path, monkeypatch):
    from app.engine.experiments import runner

    checkpoints: list[dict] = []
    holdout_calls = 0
    original = runner._fit_and_score_holdout

    def guarded_holdout(*args, **kwargs):
        nonlocal holdout_calls
        holdout_calls += 1
        assert checkpoints and checkpoints[-1]["selection"]["locked"] is True
        assert checkpoints[-1]["selection"]["selection_source"] == "cross_validation"
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, "_fit_and_score_holdout", guarded_holdout)
    result = runner.run_experiment(
        _integrity_frame(),
        _integrity_task(),
        SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
        artifact_dir=tmp_path,
        on_checkpoint=lambda payload: checkpoints.append(payload),
    )

    assert holdout_calls == 1
    assert result["final_test_evaluation"]["evaluation_count"] == 1
    assert result["selection"]["locked_at"] <= result["final_test_evaluation"]["started_at"]
    split = result["split"]
    assert split["provenance_disjoint"] is True
    assert set(split["train_source_rows"]).isdisjoint(split["test_source_rows"])
    trained = [row for row in result["candidates"] if row["status"] == "trained"]
    assert trained and all(row["n_train_rows"] == split["n_train"] for row in trained)
    assert all(len(row["fold_metrics"]) == 5 for row in trained)
    winner_id = result["selection"]["selected_candidate_id"]
    assert all(row["test_metrics"] is None for row in trained if row["candidate_id"] != winner_id)

    pipelines = list((tmp_path / "members").glob("*.joblib"))
    assert len(pipelines) == 1
    pipeline = joblib.load(pipelines[0])
    prep = pipeline.named_steps["prep"]
    numeric = prep.named_transformers_["num"]
    imputer = numeric.named_steps["imputer"]
    scaler = numeric.named_steps["scaler"]
    encoder = prep.named_transformers_["cat"].named_steps["onehot"]
    assert imputer.statistics_[0] < 100
    assert scaler.mean_[0] < 100
    assert "TEST_ONLY" not in set(encoder.categories_[0])


def test_missing_value_llm_evidence_contains_training_targets_only(db_session, tmp_path, monkeypatch):
    from app.services import lab_decision_ledger

    n = 100
    rng = np.random.default_rng(44)
    frame = pd.DataFrame(
        {
            "feature": rng.normal(0, 2, n),
            "aux": rng.normal(10, 3, n),
            "outcome": rng.normal(150, 20, n),
        }
    )
    provenance = frame.copy()
    provenance[SOURCE_ROW_COLUMN] = np.arange(n)
    _, _, _, split = split_train_test_holdout(provenance, target="outcome", stratify=False, seed=42)
    test_rows = set(split["test_source_rows"])
    train_rows = [index for index in range(n) if index not in test_rows]
    frame.loc[list(test_rows), "outcome"] = 9_000_000 + np.arange(len(test_rows))
    frame.loc[train_rows[::8], "feature"] = np.nan
    frame.loc[list(test_rows)[:5], "feature"] = np.nan

    captured = []
    settings = SimpleNamespace(decision_agent_enabled=True, decision_agent_api_key="test")
    monkeypatch.setattr(lab_decision_ledger, "get_settings", lambda: settings)

    def fake_decision(evidence, _prompt_version):
        captured.append(evidence)
        return MissingValueDecision(
            action="impute_median",
            evidence_field="dtype",
            fill_value=None,
            rationale="Numeric training evidence supports median imputation.",
            confidence=0.99,
        )

    monkeypatch.setattr(lab_decision_ledger, "request_decision", fake_decision)
    path = tmp_path / "llm_train_only.csv"
    frame.to_csv(path, index=False)
    upload = ClientLabUpload(
        workspace_id=DEFAULT_WORKSPACE_ID,
        category="Revenue",
        original_filename=path.name,
        stored_path=str(path),
        kind="spreadsheet",
        record_count=n,
        fields_noticed=list(frame.columns),
        has_named_fields=True,
        explicit_target_column="outcome",
    )
    db_session.add(upload)
    db_session.commit()

    run_auto_train_job(db_session, upload.id)
    db_session.refresh(upload)
    assert upload.pipeline_status == "completed", upload.pipeline_log
    feature_evidence = next(item for item in captured if item.column == "feature")
    sampled_targets = [row["outcome"] for row in feature_evidence.sample_rows]
    assert sampled_targets
    assert max(sampled_targets) < 1_000_000
    assert feature_evidence.missing_count == len(train_rows[::8])


def test_stage_timings_feature_truth_and_persisted_report(db_session, tmp_path):
    n = 120
    frame = pd.DataFrame(
        {
            "event_date": pd.date_range("2024-01-01", periods=n, freq="D").astype(str),
            "measure": np.linspace(1, 50, n),
            "outcome": np.where(np.arange(n) % 2, "Yes", "No"),
        }
    )
    path = tmp_path / "datetime.csv"
    frame.to_csv(path, index=False)
    upload = ClientLabUpload(
        workspace_id=DEFAULT_WORKSPACE_ID,
        category="Revenue",
        original_filename=path.name,
        stored_path=str(path),
        kind="spreadsheet",
        record_count=n,
        fields_noticed=list(frame.columns),
        has_named_fields=True,
        explicit_target_column="outcome",
    )
    db_session.add(upload)
    db_session.commit()
    run_auto_train_job(db_session, upload.id)
    db_session.refresh(upload)
    experiment = db_session.get(Experiment, upload.experiment_id)
    result = experiment.result

    feature_report = result["feature_engineering"]
    assert feature_report["generated_features"] == []
    assert feature_report["transformed_features"] == ["event_date"]
    assert feature_report["feature_engineering_actions"] == [
        {
            "step": "datetime_to_unix_seconds",
            "transformation": "datetime_to_epoch",
            "columns": ["event_date"],
            "input_columns": ["event_date"],
            "output_columns": ["event_date"],
            "reason": "Convert datetime values to the numeric representation supported by the tabular pipeline.",
            "parameters": {"unit": "seconds", "epoch": "unix"},
            "learned_from_data": False,
            "decision_partition": "train",
        }
    ]
    timings = result["stage_timings"]
    assert timings
    assert all(item["started_at"] and item["ended_at"] for item in timings)
    assert all(item["duration_ms"] > 0 for item in timings)
    report = result["technical_report"]
    assert report["selection"]["selection_source"] == "cross_validation"
    assert report["final_test_evaluation"]["evaluation_count"] == 1
    assert report["predictions_summary"]["count"] == result["split"]["n_test"]
    verification = report["deterministic_verification"]
    assert verification["overall_status"] == "VERIFIED", verification
    assert not verification["failures"]
    assert not verification["missing_evidence"]


def test_docx_uses_canonical_report_and_never_prints_rejected_holdout_metrics():
    report = {
        "run": {"run_id": "run-1", "experiment_id": "exp-1", "status": "completed"},
        "dataset": {"name": "dataset.csv", "record_count": 100},
        "raw_profile": {"row_count": 100, "column_count": 3},
        "data_quality": {"duplicate_rows": 0, "constant_columns": []},
        "target_decision": {"column": "outcome", "task_type": "binary", "source": "explicit"},
        "task": {"target": "outcome", "task_type": "binary", "evaluation_metric": "pr_auc"},
        "split": {"n_train": 80, "n_test": 20, "provenance_disjoint": True},
        "candidate_models": [
            {
                "candidate_id": "winner",
                "model_family": "logistic_regression",
                "status": "trained",
                "cv_mean": {"pr_auc": 0.8},
                "cv_std": {"pr_auc": 0.02},
                "fold_metrics": [{"pr_auc": 0.8}] * 5,
                "test_metrics": {"pr_auc": 0.79},
            },
            {
                "candidate_id": "rejected",
                "model_family": "random_forest",
                "status": "trained",
                "cv_mean": {"pr_auc": 0.7},
                "cv_std": {"pr_auc": 0.03},
                "fold_metrics": [{"pr_auc": 0.7}] * 5,
                # Poison value proves candidate sections do not consume this field.
                "test_metrics": {"pr_auc": 0.123456789},
            },
        ],
        "selection": {
            "selected_candidate_id": "winner",
            "selection_metric": "pr_auc",
            "selection_source": "cross_validation",
            "locked": True,
        },
        "final_model": {"candidate_id": "winner", "model_family": "logistic_regression"},
        "final_test_evaluation": {
            "candidate_id": "winner",
            "evaluation_count": 1,
            "metrics": {"pr_auc": 0.79},
        },
        "predictions_summary": {"count": 20, "artifact": "test_predictions.csv"},
        "stage_timings": [],
        "decision_records": [],
    }
    payload = render_ml_run_report_docx(report)
    assert payload.startswith(b"PK")
    with ZipFile(BytesIO(payload)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "DCLab ML Run Report" in document_xml
    assert "Data-quality findings" in document_xml
    assert "Final winner and holdout evaluation" in document_xml
    assert "0.123456789" not in document_xml
