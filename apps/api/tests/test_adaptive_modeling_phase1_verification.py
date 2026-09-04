"""Phase 1 verification: events, E2E demonstrations, and benchmark comparisons.

Does not add modeling capabilities. Fixtures live in tests/adaptive_modeling/.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.engine.experiments.runner import run_experiment
from app.engine.lab.auto_prepare import split_column_roles
from app.engine.modeling.leakage_auditor import plan_model_development
from app.engine.modeling.problem_profile import build_problem_profile
from app.engine.types import SearchConfig, TaskSpec
from app.engine.validation.splits import SOURCE_ROW_COLUMN
from adaptive_modeling.compare import naive_holdout_accuracy, old_detector_high_risk_columns
from adaptive_modeling.fixtures import (
    binary_balanced,
    binary_imbalanced,
    datetime_detection,
    fixture_catalog,
    geo_detection,
    leakage_fixture,
    name_only_suspicious,
    regression,
    repeated_entity,
    temporal,
)

PLANNING_EVENTS = (
    "problem_profile_started",
    "problem_profile_completed",
    "validation_plan_selected",
    "metric_plan_selected",
    "leakage_audit_started",
    "leakage_audit_completed",
    "model_development_plan_locked",
)


def _roles(frame, target: str) -> tuple[list[str], list[str]]:
    columns = [name for name in frame.columns if name not in {target, SOURCE_ROW_COLUMN}]
    return split_column_roles(frame, columns)


def _task(frame, *, target: str, task_type: str, metric: str) -> TaskSpec:
    num_cols, cat_cols = _roles(frame, target)
    return TaskSpec(
        id="phase1-verify",
        name="phase1-verify",
        task_type=task_type,
        target=target,
        entity_id=None,
        prediction_time_column=None,
        evaluation_metric=metric,
        feature_groups={"features": num_cols + cat_cols},
        validation_strategy="stratified" if task_type == "binary" else "random",
        column_roles={"numerical": num_cols, "categorical": cat_cols},
    )


def _run(frame, *, target: str, task_type: str, metric: str, on_event=None):
    task = _task(frame, target=target, task_type=task_type, metric=metric)
    with tempfile.TemporaryDirectory() as tmp:
        return run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=Path(tmp),
            on_event=on_event,
        )


def _candidate_features(result: dict) -> set[str]:
    names: set[str] = set()
    for row in result.get("candidates") or []:
        names.update(row.get("feature_set") or row.get("features") or [])
    groups = (result.get("task") or {}).get("feature_groups") or {}
    for cols in groups.values():
        names.update(cols)
    return names


def _high_excluded(result: dict) -> set[str]:
    plan = result.get("model_development_plan") or {}
    return {
        row["column"]
        for row in plan.get("excluded_features") or []
        if row.get("risk") in {"HIGH", "CRITICAL"}
    }


def test_benchmark_fixture_catalog_contains_required_sets():
    catalog = fixture_catalog()
    assert set(catalog) >= {
        "binary_balanced",
        "binary_imbalanced",
        "regression",
        "repeated_entity",
        "temporal",
        "leakage_fixture",
        "datetime_detection",
        "geo_detection",
    }


def test_datetime_and_geo_detection_fixtures():
    time_profile = build_problem_profile(datetime_detection(), target="revenue", task_type="regression")
    assert any(item["column"] == "as_of_date" for item in time_profile.time_candidates)
    geo_profile = build_problem_profile(geo_detection(), target="outcome", task_type="binary")
    assert geo_profile.geo_coordinate_candidates
    pair = geo_profile.geo_coordinate_candidates[0]
    assert pair["lat_column"] == "latitude"
    assert pair["lon_column"] == "longitude"


def test_planning_events_are_bounded_and_ordered():
    frame = binary_balanced()
    events: list[tuple[str, dict]] = []

    def on_event(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    _, _, _, _, plan = plan_model_development(
        frame,
        target="outcome",
        task_type="binary",
        on_event=on_event,
    )
    types = [name for name, _ in events]
    for required in PLANNING_EVENTS:
        assert required in types
    assert types.index("problem_profile_started") < types.index("problem_profile_completed")
    assert types.index("problem_profile_completed") < types.index("validation_plan_selected")
    assert types.index("validation_plan_selected") < types.index("metric_plan_selected")
    assert types.index("leakage_audit_started") < types.index("leakage_audit_completed")
    assert types.index("leakage_audit_completed") < types.index("model_development_plan_locked")
    blob = json.dumps([payload for _, payload in events])
    assert "train_source_rows" not in blob
    assert "test_source_rows" not in blob
    assert plan.plan_version


def test_e2e_ordinary_classification():
    result = _run(binary_balanced(), target="outcome", task_type="binary", metric="pr_auc")
    assert result["status"] == "COMPLETED"
    assert result["validation_plan"]["strategy"] == "StratifiedKFold"
    assert result["metric_plan"]["primary_metric"] == "pr_auc"
    assert result["selection"]["selection_metric"] == "pr_auc"
    legitimate = {"age", "income", "region"}
    assert not (legitimate & _high_excluded(result))
    assert legitimate <= _candidate_features(result)
    assert result["final_test_evaluation"]["evaluation_count"] == 1


def test_e2e_imbalanced_classification():
    result = _run(binary_imbalanced(), target="outcome", task_type="binary", metric="pr_auc")
    assert result["status"] == "COMPLETED"
    profile = result["problem_profile"]
    assert profile["imbalance_ratio"] is not None and profile["imbalance_ratio"] >= 2
    assert profile["minority_class_fraction"] is not None and profile["minority_class_fraction"] < 0.35
    assert result["metric_plan"]["primary_metric"] == "pr_auc"
    assert "imbalance" in result["metric_plan"]["reason"].lower() or "PR-AUC" in result["metric_plan"]["reason"]
    assert result["selection"]["selection_metric"] == "pr_auc"


def test_e2e_repeated_entity_group_validation():
    result = _run(repeated_entity(), target="outcome", task_type="binary", metric="pr_auc")
    assert result["status"] == "COMPLETED"
    plan = result["validation_plan"]
    assert plan["strategy"] in {"StratifiedGroupKFold", "GroupKFold"}
    assert plan["group_column"] == "customer_id"
    assert "customer_id" not in _candidate_features(result)
    for candidate in result["candidates"]:
        if candidate.get("status") != "trained":
            continue
        for fold in candidate.get("folds") or []:
            assert fold.get("group_overlap") == []
            assert int(fold.get("group_overlap_count") or 0) == 0


def test_e2e_temporal_fixture():
    result = _run(temporal(), target="revenue", task_type="regression", metric="mae")
    assert result["status"] == "COMPLETED"
    plan = result["validation_plan"]
    assert plan["strategy"] == "TimeSeriesSplit"
    assert plan["time_column"] == "as_of_date"
    assert plan["shuffle"] is False
    for candidate in result["candidates"]:
        if candidate.get("status") != "trained":
            continue
        for fold in candidate.get("folds") or []:
            assert fold["train_time_max"]
            assert fold["validation_time_min"]
            assert fold["validation_time_min"] >= fold["train_time_max"]


def test_e2e_leakage_fixture_excludes_proxy_and_continues():
    result = _run(leakage_fixture(), target="outcome", task_type="binary", metric="pr_auc")
    assert result["status"] == "COMPLETED"
    features = _candidate_features(result)
    assert "result_code" not in features
    assert "result_code" in {row["column"] for row in result["model_development_plan"]["excluded_features"]}
    assert "age" in features
    assert result["funnel"]["trained"] >= 1
    events: list[str] = []

    def on_event(event_type: str, payload: dict) -> None:
        events.append(event_type)

    plan_model_development(
        leakage_fixture(),
        target="outcome",
        task_type="binary",
        on_event=on_event,
    )
    assert "feature_excluded_for_leakage" in events


def test_e2e_regression():
    result = _run(regression(), target="revenue", task_type="regression", metric="mae")
    assert result["status"] == "COMPLETED"
    assert result["validation_plan"]["strategy"] == "KFold"
    assert result["metric_plan"]["primary_metric"] == "mae"
    assert result["selection"]["selection_metric"] == "mae"
    assert result["final_test_evaluation"]["evaluation_count"] == 1
    assert result["split"]["n_test"] > 0
    assert result["test_metrics"]


def test_leakage_score_drop_is_correction_not_regression():
    frame = leakage_fixture()
    naive = naive_holdout_accuracy(frame, ["age", "income", "result_code"], "outcome")
    old_high = old_detector_high_risk_columns(frame, "outcome")
    result = _run(frame, target="outcome", task_type="binary", metric="pr_auc")
    phase1_accuracy = float((result.get("test_metrics") or {}).get("accuracy") or 0.0)
    assert "result_code" in old_high
    assert naive > phase1_accuracy
    assert naive >= 0.94
    assert "result_code" not in _candidate_features(result)
    # The inflated naive score used a post-outcome proxy. The drop is expected.


def test_name_only_suspicious_column_is_not_an_unjustified_phase1_exclusion():
    frame = name_only_suspicious()
    old_high = old_detector_high_risk_columns(frame, "outcome")
    result = _run(frame, target="outcome", task_type="binary", metric="pr_auc")
    assert "final_status_hint" in old_high
    assert "final_status_hint" in _candidate_features(result)
    assert "final_status_hint" not in _high_excluded(result)
