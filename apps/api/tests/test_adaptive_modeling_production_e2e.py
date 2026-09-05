"""Correctness Repair 3: production Labs E2E scientific verification.

Every core assertion uses POST /app/labs/uploads → run_auto_train_job, not a
direct run_experiment(frame) substitute.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.config import REPO_ROOT
from app.engine.validation.splits import SOURCE_ROW_COLUMN
from app.services.pipeline_verifier import PipelineVerifier
from adaptive_modeling.fixtures import (
    leakage,
    ordinary_binary,
    regression,
    repeated_entity,
    temporal,
)
from adaptive_modeling.production import (
    assert_event_order,
    assert_event_replay_identical,
    assert_runtime_lineage,
    assert_single_authoritative_plan,
    assert_verifier_acceptable,
    assert_winner_only_holdout,
    cached_ordinary_binary_report,
    candidate_features,
    corrupt_report,
    estimator_columns,
    events_for,
    excluded_columns,
    labs_upload_and_train,
    load_persisted_frame,
    partition_by_source_rows,
)


def _as_timestamp(value) -> pd.Timestamp:
    stamp = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(stamp):
        numeric = pd.to_numeric(value, errors="coerce")
        if pd.notna(numeric):
            stamp = pd.to_datetime(float(numeric), unit="s", utc=True, errors="coerce")
    assert pd.notna(stamp), value
    return stamp


def _assert_common_production_proofs(db_session, result, experiment) -> None:
    assert_single_authoritative_plan(result)
    assert_winner_only_holdout(result)
    verification = assert_verifier_acceptable(result)
    assert verification["overall_status"] in {"VERIFIED", "VERIFIED_WITH_WARNINGS"}
    events = events_for(db_session, experiment.id)
    assert_event_order(events)
    assert_event_replay_identical(events, events_for(db_session, experiment.id))


def test_catalog_includes_production_probes():
    from adaptive_modeling.fixtures import fixture_catalog

    catalog = fixture_catalog()
    assert {"ordinary_binary", "regression", "repeated_entity", "temporal", "leakage"} <= set(
        catalog
    )


def test_e2e_ordinary_classification_labs_path(auth_client, admin_client, db_session, monkeypatch):
    frame = ordinary_binary()
    upload, workflow_run, experiment, model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="ordinary_binary.csv",
        target="outcome",
    )
    result = experiment.result
    selected = assert_runtime_lineage(
        db_session, upload, workflow_run, experiment, model_version, task_type="binary"
    )
    _assert_common_production_proofs(db_session, result, experiment)
    assert result["deterministic_verification"]["overall_status"] == "VERIFIED"

    assert result["holdout_plan"]["strategy"] == "stratified_random"
    assert result["split"]["strategy"] == "stratified_random"
    assert result["validation_plan"]["strategy"] == "StratifiedKFold"
    assert result["validation"]["cv_strategy"] == "StratifiedKFold"
    assert result["metric_plan"]["primary_metric"] == "pr_auc"
    assert result["selection"]["selection_metric"] == "pr_auc"
    trained = [row for row in result["candidates"] if row["status"] == "trained"]
    assert all(row["cv_strategy"] == "StratifiedKFold" for row in trained)
    assert {"age", "income", "region"} <= candidate_features(result)
    assert selected.candidate_key == result["selection"]["selected_candidate_id"]

    monitor = admin_client.get(f"/admin/pipeline-runs/{experiment.id}/monitor")
    assert monitor.status_code == 200, monitor.text
    plan = monitor.json()["scientific_plan"]
    assert plan["holdout"]["strategy"] == "stratified_random"
    assert plan["validation"]["strategy"] == "StratifiedKFold"
    assert plan["metric"]["primary_metric"] == "pr_auc"
    assert plan["leakage"]["partition"] == "train"
    assert plan["allowed_features"]
    assert any(row["event_type"] == "cv_fold_completed" for row in monitor.json()["events"])
    assert any(row["selected"] is True for row in monitor.json()["candidates"])


def test_e2e_regression_labs_path(auth_client, db_session, monkeypatch):
    frame = regression()
    upload, workflow_run, experiment, model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="regression.csv",
        target="revenue",
        category="Customer Value",
    )
    result = experiment.result
    assert_runtime_lineage(
        db_session, upload, workflow_run, experiment, model_version, task_type="regression"
    )
    _assert_common_production_proofs(db_session, result, experiment)
    assert result["deterministic_verification"]["overall_status"] == "VERIFIED"

    assert result["holdout_plan"]["strategy"] == "random"
    assert result["split"]["strategy"] == "random"
    assert result["validation_plan"]["strategy"] == "KFold"
    assert result["metric_plan"]["primary_metric"] == "mae"
    assert result["selection"]["selection_metric"] == "mae"
    assert set(result["test_metrics"]) >= {"mae", "rmse", "r2"}
    trained = [row for row in result["candidates"] if row["status"] == "trained"]
    assert all(row["cv_strategy"] == "KFold" for row in trained)


def test_e2e_repeated_entity_labs_path(auth_client, db_session, monkeypatch):
    frame = repeated_entity()
    upload, workflow_run, experiment, model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="repeated_customer.csv",
        target="outcome",
    )
    result = experiment.result
    assert_runtime_lineage(
        db_session, upload, workflow_run, experiment, model_version, task_type="binary"
    )
    _assert_common_production_proofs(db_session, result, experiment)
    assert result["deterministic_verification"]["overall_status"] == "VERIFIED"

    assert result["holdout_plan"]["strategy"] == "group_disjoint"
    assert result["holdout_plan"]["group_column"] == "customer_id"
    assert result["split"]["group_overlap_count"] == 0
    assert result["split"]["group_overlap"] == []
    assert result["validation_plan"]["strategy"] in {"StratifiedGroupKFold", "GroupKFold"}
    assert result["validation_plan"]["group_column"] == "customer_id"
    assert "customer_id" not in estimator_columns(result)
    assert "customer_id" not in candidate_features(result)

    persisted = load_persisted_frame(experiment)
    train, test = partition_by_source_rows(persisted, result["split"])
    train_groups = set(train["customer_id"].astype(str))
    test_groups = set(test["customer_id"].astype(str))
    assert train_groups
    assert test_groups
    assert train_groups.isdisjoint(test_groups)

    for row in result["candidates"]:
        if row.get("status") != "trained":
            continue
        for fold in row.get("folds") or []:
            assert fold.get("group_overlap") == []
            assert fold.get("group_overlap_count") == 0
            fold_train = set(
                persisted.loc[
                    pd.to_numeric(persisted[SOURCE_ROW_COLUMN], errors="coerce").isin(
                        fold.get("train_provenance") or []
                    ),
                    "customer_id",
                ].astype(str)
            )
            fold_val = set(
                persisted.loc[
                    pd.to_numeric(persisted[SOURCE_ROW_COLUMN], errors="coerce").isin(
                        fold.get("validation_provenance") or []
                    ),
                    "customer_id",
                ].astype(str)
            )
            assert fold_train
            assert fold_val
            assert fold_train.isdisjoint(fold_val)


def test_e2e_temporal_labs_path(auth_client, admin_client, db_session, monkeypatch):
    frame = temporal()
    upload, workflow_run, experiment, model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="temporal.csv",
        target="revenue",
    )
    result = experiment.result
    assert_runtime_lineage(
        db_session, upload, workflow_run, experiment, model_version, task_type="regression"
    )
    _assert_common_production_proofs(db_session, result, experiment)
    assert result["deterministic_verification"]["overall_status"] == "VERIFIED"

    assert result["holdout_plan"]["strategy"] == "temporal_future"
    assert result["holdout_plan"]["time_column"] == "as_of_date"
    assert result["validation_plan"]["strategy"] == "TimeSeriesSplit"
    assert result["validation_plan"]["time_column"] == "as_of_date"
    assert result["model_development_plan"]["time_column"] == "as_of_date"
    actions = (result.get("feature_engineering") or {}).get("feature_engineering_actions") or []
    assert any(item.get("step") == "datetime_to_unix_seconds" for item in actions)
    train_max = _as_timestamp(result["split"]["train_time_max"])
    test_min = _as_timestamp(result["split"]["test_time_min"])
    assert train_max <= test_min

    persisted = load_persisted_frame(experiment)
    assert pd.api.types.is_numeric_dtype(persisted["as_of_date"])
    train, test = partition_by_source_rows(persisted, result["split"])
    assert float(train["as_of_date"].max()) <= float(test["as_of_date"].min())

    for row in result["candidates"]:
        if row.get("status") != "trained":
            continue
        assert row["cv_strategy"] == "TimeSeriesSplit"
        for fold in row.get("folds") or []:
            fold_train_max = _as_timestamp(fold.get("train_time_max"))
            fold_val_min = _as_timestamp(fold.get("validation_time_min"))
            assert fold_train_max <= fold_val_min

    monitor = admin_client.get(f"/admin/pipeline-runs/{experiment.id}/monitor")
    assert monitor.status_code == 200, monitor.text
    holdout = monitor.json()["scientific_plan"]["holdout"]
    assert holdout["strategy"] == "temporal_future"
    assert holdout["time_column"] == "as_of_date"
    assert holdout["train_time_max"]
    assert holdout["test_time_min"]
    assert monitor.json()["scientific_plan"]["validation"]["strategy"] == "TimeSeriesSplit"


def test_e2e_leakage_labs_path(auth_client, db_session, monkeypatch):
    frame = leakage()
    upload, workflow_run, experiment, model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="leakage.csv",
        target="outcome",
    )
    result = experiment.result
    assert_runtime_lineage(
        db_session, upload, workflow_run, experiment, model_version, task_type="binary"
    )
    _assert_common_production_proofs(db_session, result, experiment)
    assert result["deterministic_verification"]["overall_status"] == "VERIFIED"

    excluded = excluded_columns(result)
    modeled = estimator_columns(result)
    assert "post_outcome_feature" in excluded
    assert "target_proxy" in excluded
    assert "post_outcome_feature" not in modeled
    assert "target_proxy" not in modeled
    assert "safe_feature" in modeled
    removed = set((result.get("feature_engineering") or {}).get("removed_features") or [])
    assert {"post_outcome_feature", "target_proxy"} <= excluded
    assert "post_outcome_feature" in removed
    assert "target_proxy" in removed


@pytest.mark.parametrize(
    ("case", "check_id"),
    [
        ("group_holdout_overlap", "group_holdout_has_zero_group_overlap"),
        ("temporal_holdout_reversal", "temporal_holdout_respects_order"),
        ("second_inconsistent_development_plan", "single_authoritative_development_plan"),
        ("metric_mismatch", "primary_metric_matches_selection_metric"),
        ("excluded_feature_in_candidate", "excluded_features_not_in_candidates"),
        ("winner_test_before_lock", "final_fit_after_lock"),
    ],
)
def test_production_report_corruption_fails_verifier(
    auth_client, db_session, monkeypatch, case, check_id
):
    report = corrupt_report(
        cached_ordinary_binary_report(auth_client, db_session, monkeypatch), case
    )
    result = PipelineVerifier().verify(report)
    assert result["overall_status"] in {"FAILED", "NOT_VERIFIABLE"}
    status = next(row["status"] for row in result["checks"] if row["check_id"] == check_id)
    assert status in {"FAIL", "NOT_VERIFIABLE"}


def test_monitor_page_exposes_required_scientific_panels():
    page = REPO_ROOT / "apps/web/app/admin/pipeline-runs/[pipelineId]/monitor/page.tsx"
    view = REPO_ROOT / "apps/web/app/components/explorer/PipelineMonitorView.tsx"
    page_source = page.read_text(encoding="utf-8")
    source = view.read_text(encoding="utf-8")
    assert "PipelineMonitorView" in page_source
    for title in (
        "Holdout Strategy",
        "Validation Strategy",
        "Final Holdout",
        "Metric Strategy",
        "Leakage Audit",
        "Allowed Features",
        "Excluded Features",
        "Fold-by-fold cross-validation",
        "Candidate comparison",
    ):
        assert title in source
    assert "Group overlap" in source
    assert "train_time_max" in source
    assert "test_time_min" in source
    assert "LOCKED WINNER" in source
