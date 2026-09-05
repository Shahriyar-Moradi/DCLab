"""Production Labs path helpers. Tests must go through /app/labs/uploads."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ClientLabUpload,
    Experiment,
    ExperimentCandidate,
    MlRunEvent,
    ModelVersion,
    WorkflowRun,
)
from app.engine.validation.splits import SOURCE_ROW_COLUMN
from app.services.auto_train_service import run_auto_train_job
from app.services.pipeline_verifier import PipelineVerifier

_CACHED_TECHNICAL_REPORT: dict[str, Any] | None = None

EVENT_ORDER = (
    "holdout_plan_selected",
    "holdout_locked",
    "model_development_plan_locked",
    "cv_fold_started",
    "winner_locked",
    "final_test_started",
    "final_test_completed",
)

ACCEPTABLE_VERIFICATION = {"VERIFIED", "VERIFIED_WITH_WARNINGS"}


def disable_background_job(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train",
        lambda _id: None,
    )


def post_labs_csv(
    auth_client,
    frame: pd.DataFrame,
    *,
    filename: str,
    target: str,
    category: str = "Revenue",
    project_id=None,
    problem_spec_id=None,
):
    data = {"category": category, "target_column": target}
    if project_id is not None:
        data["project_id"] = str(project_id)
    if problem_spec_id is not None:
        data["problem_spec_id"] = str(problem_spec_id)
    return auth_client.post(
        "/app/labs/uploads",
        data=data,
        files={
            "file": (
                filename,
                frame.to_csv(index=False).encode(),
                "text/csv",
            )
        },
    )


def labs_upload_and_train(
    auth_client,
    db_session: Session,
    monkeypatch,
    frame: pd.DataFrame,
    *,
    filename: str,
    target: str,
    category: str = "Revenue",
    project_id=None,
    problem_spec_id=None,
) -> tuple[ClientLabUpload, WorkflowRun, Experiment, ModelVersion]:
    """The real product path: Labs upload API → run_auto_train_job."""
    disable_background_job(monkeypatch)
    created = post_labs_csv(
        auth_client,
        frame,
        filename=filename,
        target=target,
        category=category,
        project_id=project_id,
        problem_spec_id=problem_spec_id,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    upload = db_session.get(ClientLabUpload, body["id"])
    assert upload is not None
    assert upload.dataset_id is not None
    pipeline_id = upload.experiment_id
    assert pipeline_id is not None
    run_auto_train_job(db_session, upload.id)
    db_session.expire_all()
    upload = db_session.get(ClientLabUpload, upload.id)
    assert upload is not None
    assert upload.pipeline_status == "completed", upload.pipeline_log
    assert upload.experiment_id == pipeline_id
    experiment = db_session.get(Experiment, upload.experiment_id)
    assert experiment is not None
    workflow_run = db_session.get(WorkflowRun, experiment.workflow_run_id)
    assert workflow_run is not None
    model_version = db_session.scalar(
        select(ModelVersion).where(ModelVersion.pipeline_run_id == experiment.id)
    )
    assert model_version is not None
    return upload, workflow_run, experiment, model_version


def events_for(db_session: Session, experiment_id) -> list[MlRunEvent]:
    return list(
        db_session.scalars(
            select(MlRunEvent)
            .where(MlRunEvent.experiment_id == experiment_id)
            .order_by(MlRunEvent.sequence)
        )
    )


def event_index(events: list[MlRunEvent], event_type: str) -> int:
    return next(index for index, row in enumerate(events) if row.event_type == event_type)


def candidate_features(result: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for row in result.get("candidates") or []:
        names.update(row.get("feature_set") or row.get("features") or [])
    for cols in ((result.get("task") or {}).get("feature_groups") or {}).values():
        names.update(cols or [])
    return names


def estimator_columns(result: dict[str, Any]) -> set[str]:
    prep = result.get("preprocessing") or {}
    names = set(prep.get("numeric_columns") or []) | set(prep.get("categorical_columns") or [])
    names |= candidate_features(result)
    return names


def excluded_columns(result: dict[str, Any]) -> set[str]:
    plan = result.get("model_development_plan") or {}
    return {
        str(row.get("column"))
        for row in plan.get("excluded_features") or []
        if isinstance(row, dict) and row.get("column")
    }


def allowed_columns(result: dict[str, Any]) -> set[str]:
    plan = result.get("model_development_plan") or {}
    return {str(name) for name in plan.get("allowed_features") or []}


def assert_runtime_lineage(
    db_session: Session,
    upload: ClientLabUpload,
    workflow_run: WorkflowRun,
    experiment: Experiment,
    model_version: ModelVersion,
    *,
    task_type: str,
) -> ExperimentCandidate:
    assert workflow_run.workspace_id == upload.workspace_id
    assert workflow_run.status == "completed"
    assert workflow_run.source_upload_id == upload.id
    assert upload.dataset is not None
    assert experiment.workflow_run_id == workflow_run.id
    assert experiment.workspace_id == upload.workspace_id
    assert experiment.status == "COMPLETED"
    assert experiment.dataset_id is not None
    assert experiment.task is not None
    assert experiment.task.spec["task_type"] == task_type
    assert len(workflow_run.pipeline_runs) >= 1
    assert len(experiment.candidates) > 1
    result = experiment.result or {}
    selected_key = (result.get("selection") or {}).get("selected_candidate_id")
    selected = next(row for row in experiment.candidates if row.candidate_key == selected_key)
    assert selected.status.lower() not in {"failed", "rejected"}
    assert model_version.selected_candidate_id == selected.id
    assert model_version.pipeline_run_id == experiment.id
    assert model_version.workflow_run_id == workflow_run.id
    assert model_version.dataset_id == experiment.dataset_id
    assert model_version.workspace_id == upload.workspace_id
    artifact_root = Path(model_version.artifact_uri or experiment.artifact_dir or "")
    assert (artifact_root / "model.joblib").exists()
    assert (artifact_root / "test_predictions.csv").exists()
    assert (artifact_root / f"members/{selected.candidate_key}.joblib").exists()
    published = db_session.scalars(
        select(ModelVersion).where(ModelVersion.pipeline_run_id == experiment.id)
    ).all()
    assert len(published) == 1
    assert published[0].id == model_version.id
    for row in experiment.candidates:
        if row.status.lower() in {"failed", "rejected"}:
            assert model_version.selected_candidate_id != row.id
    return selected


def assert_single_authoritative_plan(result: dict[str, Any]) -> None:
    holdout = result.get("holdout_plan") or {}
    plan = result.get("model_development_plan") or {}
    assert holdout.get("plan_version") == "dclab.holdout_plan.v1"
    assert plan.get("plan_version") == "dclab.model_development_plan.v1"
    assert result.get("validation_plan") == plan.get("validation_plan")
    assert result.get("metric_plan") == plan.get("metric_plan")
    assert result.get("scientific_plan_source") == "provided"
    nested_versions = {
        plan.get("plan_version"),
        (plan.get("validation_plan") or {}).get("version"),
        (plan.get("metric_plan") or {}).get("version"),
        (plan.get("problem_profile") or {}).get("version"),
    }
    assert None not in nested_versions
    trained = [row for row in result.get("candidates") or [] if row.get("status") == "trained"]
    assert trained
    strategy = (plan.get("validation_plan") or {}).get("strategy")
    primary = (plan.get("metric_plan") or {}).get("primary_metric")
    allowed = allowed_columns(result)
    excluded = excluded_columns(result)
    assert (result.get("selection") or {}).get("selection_metric") == primary
    for row in trained:
        assert row.get("cv_strategy") == strategy
        features = set(row.get("feature_set") or row.get("features") or [])
        assert features <= allowed
        assert features.isdisjoint(excluded)
    assert candidate_features(result) <= allowed
    assert candidate_features(result).isdisjoint(excluded)


def assert_winner_only_holdout(result: dict[str, Any]) -> None:
    selection = result.get("selection") or {}
    assert selection.get("locked") is True
    assert selection.get("selection_source") == "cross_validation"
    winner_id = selection.get("selected_candidate_id")
    locked_at = selection.get("locked_at")
    final_test = result.get("final_test_evaluation") or {}
    assert locked_at
    assert final_test.get("started_at")
    assert locked_at <= final_test["started_at"]
    assert final_test.get("evaluation_count") == 1
    assert final_test.get("candidate_id") == winner_id
    for row in result.get("candidates") or []:
        if row.get("candidate_id") == winner_id:
            assert row.get("locked") is True
            assert row.get("test_metrics")
        else:
            assert row.get("test_metrics") is None
            assert not row.get("locked")
    assert result.get("test_predictions")
    assert len(result["test_predictions"]) == (result.get("split") or {}).get("n_test")


def assert_event_order(events: list[MlRunEvent]) -> None:
    assert [row.sequence for row in events] == list(range(1, len(events) + 1))
    indexes = [event_index(events, name) for name in EVENT_ORDER]
    assert indexes == sorted(indexes)
    for name in EVENT_ORDER:
        assert sum(1 for row in events if row.event_type == name) >= 1


def assert_event_replay_identical(first: list[MlRunEvent], second: list[MlRunEvent]) -> None:
    assert [(row.sequence, row.event_type, row.stage) for row in first] == [
        (row.sequence, row.event_type, row.stage) for row in second
    ]


def assert_verifier_acceptable(result: dict[str, Any]) -> dict[str, Any]:
    report = result.get("technical_report") or {}
    verification = result.get("deterministic_verification") or report.get(
        "deterministic_verification"
    ) or {}
    status = verification.get("overall_status")
    assert status in ACCEPTABLE_VERIFICATION, verification
    if status != "VERIFIED":
        warnings = verification.get("warnings") or []
        assert warnings, "non-VERIFIED result must document warnings"
        failures = verification.get("failures") or []
        assert not failures
    rerun = PipelineVerifier().verify(report)
    assert rerun["overall_status"] in ACCEPTABLE_VERIFICATION, rerun
    return verification


def load_persisted_frame(experiment: Experiment) -> pd.DataFrame:
    location = experiment.dataset.location
    return pd.read_csv(location)


def partition_by_source_rows(frame: pd.DataFrame, split: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    train_ids = {int(row) for row in (split.get("train_source_rows") or [])}
    test_ids = {int(row) for row in (split.get("test_source_rows") or [])}
    assert SOURCE_ROW_COLUMN in frame.columns
    source = pd.to_numeric(frame[SOURCE_ROW_COLUMN], errors="coerce")
    train = frame[source.isin(train_ids)]
    test = frame[source.isin(test_ids)]
    return train, test


def cached_ordinary_binary_report(auth_client, db_session, monkeypatch) -> dict[str, Any]:
    """One real Labs technical_report reused by production corruption cases."""
    global _CACHED_TECHNICAL_REPORT
    if _CACHED_TECHNICAL_REPORT is None:
        from adaptive_modeling.fixtures import ordinary_binary

        _upload, _run, experiment, _version = labs_upload_and_train(
            auth_client,
            db_session,
            monkeypatch,
            ordinary_binary(),
            filename="corruption_base.csv",
            target="outcome",
        )
        _CACHED_TECHNICAL_REPORT = deepcopy(experiment.result["technical_report"])
    return deepcopy(_CACHED_TECHNICAL_REPORT)


def corrupt_report(report: dict[str, Any], case: str) -> dict[str, Any]:
    payload = deepcopy(report)
    split = payload.setdefault("split", {})
    selection = payload.setdefault("selection", {})
    candidates = payload.setdefault("candidate_models", [])
    plan = payload.setdefault("model_development_plan", {})
    if case == "group_holdout_overlap":
        split["group_overlap"] = ["C000"]
        split["group_overlap_count"] = 1
        split["strategy"] = "group_disjoint"
        payload.setdefault("holdout_plan", {})["strategy"] = "group_disjoint"
        payload["holdout_plan"]["group_column"] = "customer_id"
        payload.setdefault("validation_plan", {})["strategy"] = "StratifiedGroupKFold"
        payload["validation_plan"]["group_column"] = "customer_id"
        plan["validation_plan"] = dict(payload["validation_plan"])
        plan["group_column"] = "customer_id"
    elif case == "temporal_holdout_reversal":
        split["train_time_max"] = "2024-06-01T00:00:00"
        split["test_time_min"] = "2024-05-01T00:00:00"
        split["strategy"] = "temporal_future"
        payload.setdefault("holdout_plan", {})["strategy"] = "temporal_future"
        payload["holdout_plan"]["time_column"] = "as_of_date"
        payload.setdefault("validation_plan", {})["strategy"] = "TimeSeriesSplit"
        payload["validation_plan"]["time_column"] = "as_of_date"
        payload["validation_plan"]["stratified"] = False
        payload["validation_plan"]["shuffle"] = False
        plan["validation_plan"] = dict(payload["validation_plan"])
        plan["time_column"] = "as_of_date"
    elif case == "second_inconsistent_development_plan":
        payload["validation_plan"] = dict(payload.get("validation_plan") or {})
        payload["validation_plan"]["strategy"] = "KFold"
    elif case == "metric_mismatch":
        selection["selection_metric"] = "accuracy"
    elif case == "excluded_feature_in_candidate":
        plan.setdefault("excluded_features", []).append(
            {
                "column": "post_outcome_feature",
                "risk": "HIGH",
                "action": "exclude",
                "reason": "target proxy",
                "reasons": ["target_proxy"],
            }
        )
        trained = next(
            (row for row in candidates if isinstance(row, dict) and row.get("status") == "trained"),
            None,
        )
        if trained is not None:
            features = list(trained.get("feature_set") or [])
            features.append("post_outcome_feature")
            trained["feature_set"] = features
    elif case == "winner_test_before_lock":
        selection["locked_at"] = "2099-01-01T00:00:00+00:00"
    else:
        raise ValueError(case)
    return payload
