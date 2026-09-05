"""Deterministic, read-only verification of persisted automatic ML-run evidence."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.engine.features.encode import coerce_binary_target
from app.engine.modeling.holdout_planner import (
    GROUP_DISJOINT,
    GROUP_HOLDOUT_STRATEGIES,
    RANDOM,
    STRATIFIED_RANDOM,
    TEMPORAL_FUTURE,
    TEMPORAL_HOLDOUT_STRATEGIES,
)
from app.engine.modeling.validation_planner import (
    GROUP_KFOLD,
    KFOLD,
    STRATIFIED_GROUP_KFOLD,
    STRATIFIED_KFOLD,
    TIME_SERIES_SPLIT,
    UNSUPPORTED,
)
from app.services.artifact_store import ArtifactAccess, LocalArtifactAccess

CHECK_PASS = "PASS"
CHECK_WARN = "WARN"
CHECK_FAIL = "FAIL"
CHECK_NOT_VERIFIABLE = "NOT_VERIFIABLE"

GROUP_STRATEGIES = {STRATIFIED_GROUP_KFOLD, GROUP_KFOLD}
_CV_TO_HOLDOUT = {
    STRATIFIED_KFOLD: STRATIFIED_RANDOM,
    KFOLD: RANDOM,
    STRATIFIED_GROUP_KFOLD: GROUP_DISJOINT,
    GROUP_KFOLD: GROUP_DISJOINT,
    TIME_SERIES_SPLIT: TEMPORAL_FUTURE,
}
_HOLDOUT_KEYS = {
    "test_source_rows",
    "train_source_rows",
    "all_source_rows",
    "n_test",
    "holdout_metrics",
    "test_metrics",
    "test_predictions",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# JSON (`json.dumps`/`json.loads` in the technical report) and pandas CSV
# default float text can move a float64 target by a few ULPs. At magnitude
# ~200 that is ~1e-14. This is serialization, not a rewritten label.
_REGRESSION_LABEL_REL_TOL = 1e-12
_REGRESSION_LABEL_ABS_TOL = 1e-9


def _labels_match(expected: Any, actual: Any, *, task_type: str = "") -> bool:
    if pd.isna(expected) and pd.isna(actual):
        return True
    if expected == actual:
        return True
    try:
        expected_f = float(expected)
        actual_f = float(actual)
    except (TypeError, ValueError):
        return False
    if expected_f == actual_f:
        return True
    if task_type == "regression":
        return math.isclose(
            expected_f,
            actual_f,
            rel_tol=_REGRESSION_LABEL_REL_TOL,
            abs_tol=_REGRESSION_LABEL_ABS_TOL,
        )
    return False


def _artifact_target_values(frame: pd.DataFrame, column: str, task_type: str) -> pd.Series:
    series = frame[column]
    if task_type == "binary":
        encoded = coerce_binary_target(series)
        if int(encoded.notna().sum()) == int(series.notna().sum()):
            return encoded
    if task_type == "regression":
        return pd.to_numeric(series, errors="coerce")
    return series


def _candidate_feature_names(task: dict[str, Any], candidates: list[Any]) -> set[str]:
    names: set[str] = set()
    for values in _as_dict(task.get("feature_groups")).values():
        names.update(str(item) for item in _as_list(values))
    for row in candidates:
        if not isinstance(row, dict):
            continue
        names.update(str(item) for item in _as_list(row.get("feature_set") or row.get("features")))
    return names


def _plan_identity(
    holdout: dict[str, Any],
    validation: dict[str, Any],
    metric: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    return {
        "holdout_plan_version": holdout.get("plan_version"),
        "holdout_strategy": holdout.get("strategy"),
        "validation_plan_version": validation.get("version"),
        "validation_strategy": validation.get("strategy"),
        "primary_metric": metric.get("primary_metric"),
        "model_development_plan_version": plan.get("plan_version"),
    }


def _holdout_keys_present(payload: dict[str, Any]) -> list[str]:
    return sorted(key for key in _HOLDOUT_KEYS if key in payload)


def _strategy_matches_task(task_type: str, validation: dict[str, Any]) -> bool:
    strategy = str(validation.get("strategy") or "")
    group_column = validation.get("group_column")
    time_column = validation.get("time_column")
    if strategy == UNSUPPORTED:
        return False
    if group_column and time_column:
        return False
    if task_type == "binary":
        if group_column:
            return strategy in GROUP_STRATEGIES
        if time_column:
            return strategy == TIME_SERIES_SPLIT
        return strategy == STRATIFIED_KFOLD and validation.get("stratified") is True
    if task_type == "regression":
        if group_column:
            return strategy == GROUP_KFOLD
        if time_column:
            return strategy == TIME_SERIES_SPLIT
        return strategy == KFOLD and validation.get("stratified") is not True
    return strategy in {STRATIFIED_KFOLD, KFOLD, STRATIFIED_GROUP_KFOLD, GROUP_KFOLD, TIME_SERIES_SPLIT}


def _holdout_strategy(holdout: dict[str, Any], split: dict[str, Any]) -> str:
    return str(holdout.get("strategy") or split.get("strategy") or "")


def _verify_holdout_plan(
    add,
    *,
    report: dict[str, Any],
    split: dict[str, Any],
    validation: dict[str, Any],
) -> None:
    holdout = _as_dict(report.get("holdout_plan"))
    strategy = _holdout_strategy(holdout, split)
    if not holdout or not holdout.get("strategy") or not holdout.get("plan_version"):
        add(
            "holdout_plan_exists",
            "holdout_plan",
            CHECK_NOT_VERIFIABLE,
            "HoldoutPlan evidence is missing.",
            "holdout_plan",
        )
    else:
        add(
            "holdout_plan_exists",
            "holdout_plan",
            CHECK_PASS,
            "A HoldoutPlan was selected from pre-split structural evidence.",
            "holdout_plan.plan_version",
        )

    cv_strategy = str(validation.get("strategy") or "")
    expected = _CV_TO_HOLDOUT.get(cv_strategy)
    plan_strategy = str(holdout.get("strategy") or "")
    split_strategy = str(split.get("strategy") or "")
    if not plan_strategy or not cv_strategy or expected is None:
        add(
            "holdout_strategy_matches_problem_structure",
            "holdout_plan",
            CHECK_NOT_VERIFIABLE,
            "Holdout strategy cannot be checked against the problem structure.",
            "holdout_plan",
            "validation_plan",
        )
    elif plan_strategy != expected or (split_strategy and split_strategy != expected):
        add(
            "holdout_strategy_matches_problem_structure",
            "holdout_plan",
            CHECK_FAIL,
            (
                f"Holdout strategy {plan_strategy or split_strategy!r} does not match "
                f"validation strategy {cv_strategy!r}."
            ),
            "holdout_plan.strategy",
            "split.strategy",
            "validation_plan.strategy",
        )
    else:
        add(
            "holdout_strategy_matches_problem_structure",
            "holdout_plan",
            CHECK_PASS,
            "The final holdout strategy matches the profiled problem structure.",
            "holdout_plan.strategy",
            "validation_plan.strategy",
        )

    train_rows = _as_list(split.get("train_source_rows"))
    test_rows = _as_list(split.get("test_source_rows"))
    train_set, test_set = set(train_rows), set(test_rows)
    if not train_rows or not test_rows:
        add(
            "holdout_train_test_disjoint",
            "splitting",
            CHECK_NOT_VERIFIABLE,
            "Train/test provenance is missing for the final holdout.",
            "split",
        )
    elif train_set & test_set:
        add(
            "holdout_train_test_disjoint",
            "splitting",
            CHECK_FAIL,
            "Train and final-test provenance overlap.",
            "split.train_source_rows",
            "split.test_source_rows",
        )
    else:
        add(
            "holdout_train_test_disjoint",
            "splitting",
            CHECK_PASS,
            "Final holdout train and test provenance are disjoint.",
            "split.train_source_rows",
            "split.test_source_rows",
        )

    if strategy not in GROUP_HOLDOUT_STRATEGIES:
        add(
            "group_holdout_has_zero_group_overlap",
            "holdout_plan",
            CHECK_PASS,
            "Group-disjoint holdout was not selected.",
            "holdout_plan.strategy",
        )
    else:
        overlap = _as_list(split.get("group_overlap"))
        count = split.get("group_overlap_count")
        if count is None and "group_overlap" not in split:
            add(
                "group_holdout_has_zero_group_overlap",
                "holdout_plan",
                CHECK_NOT_VERIFIABLE,
                "Group-overlap evidence is missing from the locked holdout.",
                "split.group_overlap_count",
            )
        elif overlap or (isinstance(count, int) and count > 0):
            add(
                "group_holdout_has_zero_group_overlap",
                "holdout_plan",
                CHECK_FAIL,
                "The same group appears in the training partition and the final holdout.",
                "split.group_overlap_count",
            )
        else:
            add(
                "group_holdout_has_zero_group_overlap",
                "holdout_plan",
                CHECK_PASS,
                "Group-disjoint holdout has zero group overlap.",
                "split.group_overlap_count",
            )

    if strategy not in TEMPORAL_HOLDOUT_STRATEGIES:
        add(
            "temporal_holdout_respects_order",
            "holdout_plan",
            CHECK_PASS,
            "Temporal holdout was not selected.",
            "holdout_plan.strategy",
        )
    else:
        train_max = pd.to_datetime(split.get("train_time_max"), errors="coerce")
        test_min = pd.to_datetime(split.get("test_time_min"), errors="coerce")
        if pd.isna(train_max) or pd.isna(test_min):
            add(
                "temporal_holdout_respects_order",
                "holdout_plan",
                CHECK_NOT_VERIFIABLE,
                "Chronological holdout timestamps are missing.",
                "split.train_time_max",
                "split.test_time_min",
            )
        elif train_max > test_min:
            add(
                "temporal_holdout_respects_order",
                "holdout_plan",
                CHECK_FAIL,
                "The final holdout is not in the future of the training period.",
                "split.train_time_max",
                "split.test_time_min",
            )
        else:
            add(
                "temporal_holdout_respects_order",
                "holdout_plan",
                CHECK_PASS,
                "Temporal holdout uses a future test slice.",
                "split.train_time_max",
                "split.test_time_min",
            )


def _verify_scientific_plan(
    add,
    *,
    report: dict[str, Any],
    task: dict[str, Any],
    selection: dict[str, Any],
    candidates: list[Any],
    split: dict[str, Any],
) -> None:
    plan = _as_dict(report.get("model_development_plan"))
    profile = _as_dict(report.get("problem_profile")) or _as_dict(plan.get("problem_profile"))
    validation = _as_dict(report.get("validation_plan")) or _as_dict(plan.get("validation_plan"))
    metric = _as_dict(report.get("metric_plan")) or _as_dict(plan.get("metric_plan"))
    audit = _as_dict(plan.get("leakage_assessment")) or _as_dict(report.get("leakage"))
    trained = [row for row in candidates if isinstance(row, dict) and row.get("status") == "trained"]
    modeled_features = _candidate_feature_names(task, trained)
    _verify_holdout_plan(add, report=report, split=split, validation=validation)

    if not plan or not plan.get("plan_version") or "allowed_features" not in plan:
        add(
            "model_development_plan_exists",
            "model_development_plan",
            CHECK_NOT_VERIFIABLE,
            "ModelDevelopmentPlan evidence is missing.",
            "model_development_plan",
        )
    else:
        add(
            "model_development_plan_exists",
            "model_development_plan",
            CHECK_PASS,
            "A ModelDevelopmentPlan was locked from train-only evidence.",
            "model_development_plan.plan_version",
        )

    if not validation or not validation.get("strategy"):
        add(
            "validation_plan_exists",
            "validation_plan",
            CHECK_NOT_VERIFIABLE,
            "ValidationPlan evidence is missing.",
            "validation_plan",
        )
    else:
        add(
            "validation_plan_exists",
            "validation_plan",
            CHECK_PASS,
            "A ValidationPlan was selected from the train-only ProblemProfile.",
            "validation_plan.strategy",
        )

    task_type = str(task.get("task_type") or "")
    if not validation.get("strategy"):
        add(
            "validation_strategy_matches_task",
            "validation_plan",
            CHECK_NOT_VERIFIABLE,
            "Validation strategy cannot be checked without a ValidationPlan.",
            "validation_plan",
        )
    elif not _strategy_matches_task(task_type, validation):
        add(
            "validation_strategy_matches_task",
            "validation_plan",
            CHECK_FAIL,
            f"Validation strategy {validation.get('strategy')!r} does not match task {task_type!r}.",
            "validation_plan",
            "task.task_type",
        )
    else:
        add(
            "validation_strategy_matches_task",
            "validation_plan",
            CHECK_PASS,
            "The selected validation strategy matches the profiled task.",
            "validation_plan.strategy",
            "task.task_type",
        )

    plan_actual = validation.get("actual_folds")
    plan_requested = validation.get("requested_folds")
    fold_mismatches = []
    missing_fold_counts = False
    for row in trained:
        actual = row.get("actual_folds")
        requested = row.get("requested_folds")
        fold_count = len(_as_list(row.get("folds") or row.get("fold_metrics")))
        if actual is None or requested is None:
            missing_fold_counts = True
            continue
        if actual != plan_actual or requested != plan_requested or fold_count != plan_actual:
            fold_mismatches.append(str(row.get("candidate_id")))
        if plan_actual != plan_requested and not (
            validation.get("fallback_reason") or row.get("adaptation_reason")
        ):
            fold_mismatches.append(str(row.get("candidate_id")))
    if not validation or plan_actual is None or plan_requested is None:
        add(
            "validation_fold_count_truthful",
            "validation_plan",
            CHECK_NOT_VERIFIABLE,
            "Requested/actual fold counts are missing from the ValidationPlan.",
            "validation_plan",
        )
    elif missing_fold_counts or not trained:
        add(
            "validation_fold_count_truthful",
            "validation_plan",
            CHECK_NOT_VERIFIABLE,
            "Candidate fold counts are missing.",
            "candidate_models",
            "validation_plan",
        )
    elif fold_mismatches:
        add(
            "validation_fold_count_truthful",
            "validation_plan",
            CHECK_FAIL,
            f"Recorded fold counts do not match the ValidationPlan: {sorted(set(fold_mismatches))}.",
            "validation_plan",
            "candidate_models",
        )
    else:
        add(
            "validation_fold_count_truthful",
            "validation_plan",
            CHECK_PASS,
            "Requested and actual fold counts match trained candidate evidence.",
            "validation_plan.actual_folds",
        )

    strategy = str(validation.get("strategy") or "")
    if strategy not in GROUP_STRATEGIES:
        add(
            "group_validation_has_zero_group_overlap",
            "validation_plan",
            CHECK_PASS,
            "Group-aware validation was not selected.",
            "validation_plan.strategy",
        )
    else:
        overlapping = []
        missing_overlap = False
        for row in trained:
            for fold in _as_list(row.get("folds")):
                if not isinstance(fold, dict):
                    missing_overlap = True
                    continue
                overlap = _as_list(fold.get("group_overlap"))
                count = fold.get("group_overlap_count")
                if count is None and "group_overlap" not in fold:
                    missing_overlap = True
                    continue
                if overlap or (isinstance(count, int) and count > 0):
                    overlapping.append(str(row.get("candidate_id")))
        if missing_overlap or not trained:
            add(
                "group_validation_has_zero_group_overlap",
                "validation_plan",
                CHECK_NOT_VERIFIABLE,
                "Group-overlap evidence is missing from fold records.",
                "candidate_models.folds",
            )
        elif overlapping:
            add(
                "group_validation_has_zero_group_overlap",
                "validation_plan",
                CHECK_FAIL,
                f"The same group appears in fold train and validation for: {sorted(set(overlapping))}.",
                "candidate_models.folds.group_overlap",
            )
        else:
            add(
                "group_validation_has_zero_group_overlap",
                "validation_plan",
                CHECK_PASS,
                "Group-aware folds have zero group overlap.",
                "candidate_models.folds.group_overlap_count",
            )

    if strategy != TIME_SERIES_SPLIT:
        add(
            "temporal_validation_respects_order",
            "validation_plan",
            CHECK_PASS,
            "Temporal validation was not selected.",
            "validation_plan.strategy",
        )
    else:
        leaks = []
        missing_times = False
        for row in trained:
            for fold in _as_list(row.get("folds")):
                if not isinstance(fold, dict):
                    missing_times = True
                    continue
                train_max = pd.to_datetime(fold.get("train_time_max"), errors="coerce")
                val_min = pd.to_datetime(fold.get("validation_time_min"), errors="coerce")
                if pd.isna(train_max) or pd.isna(val_min):
                    missing_times = True
                    continue
                if val_min < train_max:
                    leaks.append(str(row.get("candidate_id")))
        if missing_times or not trained:
            add(
                "temporal_validation_respects_order",
                "validation_plan",
                CHECK_NOT_VERIFIABLE,
                "Chronological fold timestamps are missing.",
                "candidate_models.folds",
            )
        elif leaks:
            add(
                "temporal_validation_respects_order",
                "validation_plan",
                CHECK_FAIL,
                f"Validation timestamps occur before the training period ends for: {sorted(set(leaks))}.",
                "candidate_models.folds.train_time_max",
                "candidate_models.folds.validation_time_min",
            )
        else:
            add(
                "temporal_validation_respects_order",
                "validation_plan",
                CHECK_PASS,
                "TimeSeriesSplit folds respect chronological order.",
                "candidate_models.folds",
            )

    primary = metric.get("primary_metric")
    selected_metric = selection.get("selection_metric")
    task_metric = task.get("evaluation_metric")
    if not primary or not selected_metric:
        add(
            "primary_metric_matches_selection_metric",
            "metric_plan",
            CHECK_NOT_VERIFIABLE,
            "Primary metric or selection metric evidence is missing.",
            "metric_plan",
            "selection",
        )
    elif primary != selected_metric or (task_metric and task_metric != primary):
        add(
            "primary_metric_matches_selection_metric",
            "metric_plan",
            CHECK_FAIL,
            f"Winner selection used {selected_metric!r} while MetricPlan primary is {primary!r}.",
            "metric_plan.primary_metric",
            "selection.selection_metric",
        )
    else:
        add(
            "primary_metric_matches_selection_metric",
            "metric_plan",
            CHECK_PASS,
            "The locked winner was selected with the MetricPlan primary metric.",
            "metric_plan.primary_metric",
            "selection.selection_metric",
        )

    if not audit or not audit.get("partition"):
        add(
            "leakage_audit_exists",
            "leakage_audit",
            CHECK_NOT_VERIFIABLE,
            "Leakage audit evidence is missing.",
            "model_development_plan.leakage_assessment",
        )
    elif audit.get("partition") != "train":
        add(
            "leakage_audit_exists",
            "leakage_audit",
            CHECK_FAIL,
            f"Leakage audit partition is {audit.get('partition')!r}, not train.",
            "model_development_plan.leakage_assessment.partition",
        )
    else:
        add(
            "leakage_audit_exists",
            "leakage_audit",
            CHECK_PASS,
            "A train-only leakage audit is present.",
            "model_development_plan.leakage_assessment",
        )

    excluded_rows = [row for row in _as_list(plan.get("excluded_features")) if isinstance(row, dict)]
    critical = {
        str(row.get("column"))
        for row in excluded_rows
        if row.get("risk") in {"HIGH", "CRITICAL"} and row.get("column")
    }
    leaked = sorted(critical & modeled_features)
    if not plan:
        add(
            "critical_leakage_feature_not_modeled",
            "leakage_audit",
            CHECK_NOT_VERIFIABLE,
            "Excluded leakage features cannot be checked without a ModelDevelopmentPlan.",
            "model_development_plan",
        )
    elif leaked:
        add(
            "critical_leakage_feature_not_modeled",
            "leakage_audit",
            CHECK_FAIL,
            f"HIGH/CRITICAL excluded features were still modeled: {leaked}.",
            "model_development_plan.excluded_features",
            "candidate_models.feature_set",
        )
    else:
        add(
            "critical_leakage_feature_not_modeled",
            "leakage_audit",
            CHECK_PASS,
            "HIGH/CRITICAL leakage features are not in candidate feature sets.",
            "model_development_plan.excluded_features",
        )

    excluded_names = {
        str(row.get("column"))
        for row in excluded_rows
        if row.get("column")
        and (row.get("action") == "exclude" or row.get("risk") in {"HIGH", "CRITICAL"})
    }
    used_excluded = sorted(excluded_names & modeled_features)
    if not plan:
        add(
            "excluded_features_not_in_candidates",
            "leakage_audit",
            CHECK_NOT_VERIFIABLE,
            "Excluded features cannot be checked without a ModelDevelopmentPlan.",
            "model_development_plan",
        )
    elif used_excluded:
        add(
            "excluded_features_not_in_candidates",
            "leakage_audit",
            CHECK_FAIL,
            f"Excluded features appear in candidate feature sets: {used_excluded}.",
            "model_development_plan.excluded_features",
            "candidate_models.feature_set",
        )
    else:
        add(
            "excluded_features_not_in_candidates",
            "leakage_audit",
            CHECK_PASS,
            "Excluded features are absent from candidate feature sets.",
            "model_development_plan.excluded_features",
        )

    nested_profile = _as_dict(plan.get("problem_profile"))
    forbidden = _holdout_keys_present(profile) + _holdout_keys_present(nested_profile) + _holdout_keys_present(audit)
    profile_rows = profile.get("row_count")
    n_train = split.get("n_train")
    n_test = split.get("n_test")
    contaminated_count = (
        isinstance(profile_rows, int)
        and isinstance(n_train, int)
        and isinstance(n_test, int)
        and profile_rows == n_train + n_test
    )
    mismatched_train = (
        isinstance(profile_rows, int) and isinstance(n_train, int) and profile_rows != n_train
    )
    if not profile:
        add(
            "final_test_not_used_in_problem_profile",
            "problem_profile",
            CHECK_NOT_VERIFIABLE,
            "ProblemProfile evidence is missing.",
            "problem_profile",
        )
    elif forbidden or contaminated_count or mismatched_train:
        add(
            "final_test_not_used_in_problem_profile",
            "problem_profile",
            CHECK_FAIL,
            "ProblemProfile/model-development evidence includes final-test provenance or statistics.",
            "problem_profile",
            "split.n_train",
        )
    else:
        add(
            "final_test_not_used_in_problem_profile",
            "problem_profile",
            CHECK_PASS,
            "ProblemProfile row counts match the locked training partition only.",
            "problem_profile.row_count",
            "split.n_train",
        )

    nested_validation = _as_dict(plan.get("validation_plan"))
    nested_metric = _as_dict(plan.get("metric_plan"))
    top_validation = _as_dict(report.get("validation_plan"))
    top_metric = _as_dict(report.get("metric_plan"))
    if not plan:
        add(
            "single_authoritative_development_plan",
            "model_development_plan",
            CHECK_NOT_VERIFIABLE,
            "A single ModelDevelopmentPlan cannot be verified without the locked plan.",
            "model_development_plan",
        )
    elif nested_validation != top_validation or nested_metric != top_metric:
        add(
            "single_authoritative_development_plan",
            "model_development_plan",
            CHECK_FAIL,
            "result.validation_plan or result.metric_plan is not the nested ModelDevelopmentPlan payload.",
            "model_development_plan.validation_plan",
            "validation_plan",
            "metric_plan",
        )
    else:
        add(
            "single_authoritative_development_plan",
            "model_development_plan",
            CHECK_PASS,
            "ValidationPlan and MetricPlan are the nested objects from one ModelDevelopmentPlan.",
            "model_development_plan",
        )

    cv_strategy = validation.get("strategy")
    validation_summary = _as_dict(report.get("validation"))
    cv_mismatches = [
        str(row.get("candidate_id"))
        for row in trained
        if row.get("cv_strategy") != cv_strategy
    ]
    summary_mismatch = bool(
        validation_summary.get("cv_strategy")
        and validation_summary.get("cv_strategy") != cv_strategy
    )
    if not plan or not cv_strategy:
        add(
            "runner_validation_matches_plan",
            "validation_plan",
            CHECK_NOT_VERIFIABLE,
            "Runner validation cannot be checked without a ValidationPlan.",
            "validation_plan",
        )
    elif not trained:
        add(
            "runner_validation_matches_plan",
            "validation_plan",
            CHECK_NOT_VERIFIABLE,
            "Trained candidate CV strategy evidence is missing.",
            "candidate_models",
        )
    elif cv_mismatches or summary_mismatch:
        add(
            "runner_validation_matches_plan",
            "validation_plan",
            CHECK_FAIL,
            "Candidate CV strategy does not match development_plan.validation_plan.",
            "validation_plan.strategy",
            "candidate_models.cv_strategy",
        )
    else:
        add(
            "runner_validation_matches_plan",
            "validation_plan",
            CHECK_PASS,
            "The runner used the locked ValidationPlan for CV.",
            "validation_plan.strategy",
        )

    if not primary or not selected_metric:
        add(
            "runner_metric_matches_plan",
            "metric_plan",
            CHECK_NOT_VERIFIABLE,
            "Runner selection metric cannot be checked without MetricPlan evidence.",
            "metric_plan",
            "selection",
        )
    elif selected_metric != primary:
        add(
            "runner_metric_matches_plan",
            "metric_plan",
            CHECK_FAIL,
            f"Runner winner selection used {selected_metric!r} while MetricPlan primary is {primary!r}.",
            "metric_plan.primary_metric",
            "selection.selection_metric",
        )
    else:
        add(
            "runner_metric_matches_plan",
            "metric_plan",
            CHECK_PASS,
            "The runner selected the winner with MetricPlan.primary_metric.",
            "metric_plan.primary_metric",
        )

    allowed = {str(name) for name in _as_list(plan.get("allowed_features")) if name}
    group_column = plan.get("group_column")
    extra_features = sorted(name for name in modeled_features if allowed and name not in allowed)
    excluded_used = sorted(modeled_features & excluded_names)
    group_modeled = group_column in modeled_features if group_column else False
    if not plan:
        add(
            "candidate_features_match_plan",
            "model_development_plan",
            CHECK_NOT_VERIFIABLE,
            "Candidate features cannot be checked without a ModelDevelopmentPlan.",
            "model_development_plan",
        )
    elif extra_features or excluded_used or group_modeled:
        add(
            "candidate_features_match_plan",
            "model_development_plan",
            CHECK_FAIL,
            "Candidate features are not a subset of allowed_features or include excluded/group columns.",
            "model_development_plan.allowed_features",
            "candidate_models.feature_set",
        )
    else:
        add(
            "candidate_features_match_plan",
            "model_development_plan",
            CHECK_PASS,
            "Candidate features stay inside the locked allowed feature set.",
            "model_development_plan.allowed_features",
        )

    if not primary or not task_metric:
        add(
            "task_metric_matches_plan",
            "metric_plan",
            CHECK_NOT_VERIFIABLE,
            "Task evaluation_metric cannot be checked without MetricPlan evidence.",
            "metric_plan",
            "task",
        )
    elif task_metric != primary:
        add(
            "task_metric_matches_plan",
            "metric_plan",
            CHECK_FAIL,
            f"TaskSpec.evaluation_metric is {task_metric!r} while MetricPlan primary is {primary!r}.",
            "metric_plan.primary_metric",
            "task.evaluation_metric",
        )
    else:
        add(
            "task_metric_matches_plan",
            "metric_plan",
            CHECK_PASS,
            "TaskSpec.evaluation_metric matches MetricPlan.primary_metric.",
            "task.evaluation_metric",
        )

    holdout = _as_dict(report.get("holdout_plan"))
    identity = _plan_identity(holdout, validation, metric, plan)
    fingerprint_rows = [row for row in candidates if isinstance(row, dict)]
    missing_fingerprint = [
        str(row.get("candidate_id"))
        for row in fingerprint_rows
        if not row.get("fingerprint")
    ]
    identity_mismatches = []
    for row in fingerprint_rows:
        meta = _as_dict(row.get("metadata"))
        for key, expected in identity.items():
            if expected in {None, ""}:
                continue
            if meta.get(key) != expected:
                identity_mismatches.append(str(row.get("candidate_id")))
                break
    if not plan:
        add(
            "candidate_fingerprint_contains_plan_identity",
            "model_development_plan",
            CHECK_NOT_VERIFIABLE,
            "Candidate fingerprints cannot be checked without a ModelDevelopmentPlan.",
            "model_development_plan",
        )
    elif not fingerprint_rows:
        add(
            "candidate_fingerprint_contains_plan_identity",
            "model_development_plan",
            CHECK_NOT_VERIFIABLE,
            "Candidate fingerprint evidence is missing.",
            "candidate_models",
        )
    elif missing_fingerprint or identity_mismatches:
        add(
            "candidate_fingerprint_contains_plan_identity",
            "model_development_plan",
            CHECK_FAIL,
            "Candidate fingerprints are missing or do not carry the locked plan identity.",
            "candidate_models.fingerprint",
            "candidate_models.metadata",
        )
    else:
        add(
            "candidate_fingerprint_contains_plan_identity",
            "model_development_plan",
            CHECK_PASS,
            "Candidate fingerprints include holdout, validation, metric, and plan-version identity.",
            "candidate_models.fingerprint",
        )


def _verify_reproducibility_lineage(add, report: dict[str, Any], db: Session) -> None:
    """DB-backed lineage checks. Skipped when verify() is called without a session."""

    from uuid import UUID

    from app.config import get_settings
    from app.db.models import Dataset, Experiment, ExperimentCandidate, ModelVersion
    from app.storage.factory import get_object_storage

    run = _as_dict(report.get("run"))
    raw_experiment_id = run.get("experiment_id")
    if not raw_experiment_id:
        return
    try:
        experiment_id = UUID(str(raw_experiment_id))
    except ValueError:
        add(
            "model_version_candidate_lineage",
            "reproducibility",
            CHECK_FAIL,
            "Pipeline run id in the technical report is not a UUID.",
            "run.experiment_id",
        )
        return
    experiment = db.get(Experiment, experiment_id)
    model_version = db.query(ModelVersion).filter(
        ModelVersion.pipeline_run_id == experiment_id
    ).one_or_none()
    if model_version is None:
        # Completed Labs runs without a workflow do not publish a ModelVersion.
        # Filesystem evidence already decided overall status; do not downgrade it.
        return
    candidate = db.get(ExperimentCandidate, model_version.selected_candidate_id)
    if (
        candidate is None
        or candidate.experiment_id != model_version.pipeline_run_id
        or experiment is None
    ):
        add(
            "model_version_candidate_lineage",
            "reproducibility",
            CHECK_FAIL,
            "ModelVersion is not linked to the selected candidate of this pipeline run.",
            "model_versions.selected_candidate_id",
        )
    else:
        add(
            "model_version_candidate_lineage",
            "reproducibility",
            CHECK_PASS,
            "ModelVersion points at the selected candidate of this pipeline run.",
            "model_versions.selected_candidate_id",
        )
    dataset = db.get(Dataset, model_version.dataset_id)
    if dataset is None or dataset.id != model_version.dataset_id:
        add(
            "dataset_lineage_exists",
            "reproducibility",
            CHECK_FAIL,
            "ModelVersion is missing dataset lineage.",
            "model_versions.dataset_id",
        )
    else:
        add(
            "dataset_lineage_exists",
            "reproducibility",
            CHECK_PASS,
            "ModelVersion is linked to the training dataset.",
            "model_versions.dataset_id",
        )
    if model_version.feature_set_version_id is None:
        add(
            "feature_set_lineage_exists",
            "reproducibility",
            CHECK_WARN,
            "Feature-set version is not linked on ModelVersion.",
            "model_versions.feature_set_version_id",
        )
    else:
        add(
            "feature_set_lineage_exists",
            "reproducibility",
            CHECK_PASS,
            "ModelVersion is linked to a FeatureSetVersion.",
            "model_versions.feature_set_version_id",
        )
    model_artifact = model_version.model_artifact
    if model_artifact is None:
        add(
            "model_artifact_registered",
            "reproducibility",
            CHECK_FAIL,
            "Published ModelVersion has no model Artifact.",
            "model_versions.model_artifact_id",
        )
        add(
            "model_artifact_digest_exists",
            "reproducibility",
            CHECK_FAIL,
            "Model artifact digest is missing because the Artifact row is missing.",
            "artifacts.content_digest",
        )
    else:
        storage = get_object_storage()
        if not storage.exists(model_artifact.object_key):
            add(
                "model_artifact_registered",
                "reproducibility",
                CHECK_FAIL,
                "Model Artifact is registered but the blob is missing from object storage.",
                "artifacts.object_key",
            )
        else:
            add(
                "model_artifact_registered",
                "reproducibility",
                CHECK_PASS,
                "Model Artifact exists in the registry and object storage.",
                "model_versions.model_artifact_id",
            )
        if not model_artifact.content_digest:
            add(
                "model_artifact_digest_exists",
                "reproducibility",
                CHECK_FAIL,
                "Model Artifact is missing a content digest.",
                "artifacts.content_digest",
            )
        else:
            add(
                "model_artifact_digest_exists",
                "reproducibility",
                CHECK_PASS,
                "Model Artifact has a content digest.",
                "artifacts.content_digest",
            )
    if model_version.runtime_environment_id is None:
        add(
            "runtime_environment_exists",
            "reproducibility",
            CHECK_FAIL,
            "ModelVersion has no RuntimeEnvironment.",
            "model_versions.runtime_environment_id",
        )
    else:
        add(
            "runtime_environment_exists",
            "reproducibility",
            CHECK_PASS,
            "ModelVersion is linked to a RuntimeEnvironment.",
            "model_versions.runtime_environment_id",
        )
    if get_settings().reproducible_code_export_enabled:
        if model_version.code_snapshot_id is None:
            add(
                "code_snapshot_exists",
                "reproducibility",
                CHECK_FAIL,
                "Reproducible code export is enabled but no CodeSnapshot was persisted.",
                "model_versions.code_snapshot_id",
            )
        else:
            add(
                "code_snapshot_exists",
                "reproducibility",
                CHECK_PASS,
                "ModelVersion is linked to a CodeSnapshot.",
                "model_versions.code_snapshot_id",
            )
    else:
        add(
            "code_snapshot_exists",
            "reproducibility",
            CHECK_PASS,
            "Reproducible code export is disabled; CodeSnapshot is not required.",
            "model_versions.code_snapshot_id",
        )


class PipelineVerifier:
    """Verify persisted evidence without trusting the run's completion label."""

    def __init__(self, artifacts: ArtifactAccess | None = None) -> None:
        self.artifacts = artifacts or LocalArtifactAccess()

    def verify(self, report: dict[str, Any], *, db: Session | None = None) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def add(
            check_id: str,
            stage: str,
            status: str,
            message: str,
            *evidence_refs: str,
        ) -> None:
            checks.append(
                {
                    "check_id": check_id,
                    "stage": stage,
                    "status": status,
                    "message": message,
                    "evidence_refs": list(evidence_refs),
                }
            )

        run = _as_dict(report.get("run"))
        profile = _as_dict(report.get("raw_profile"))
        target = _as_dict(report.get("target_decision"))
        task = _as_dict(report.get("task"))
        split = _as_dict(report.get("split"))
        cleaning = _as_dict(report.get("cleaning"))
        role_evidence = _as_dict(report.get("column_role_evidence"))
        feature_engineering = _as_dict(report.get("feature_engineering"))
        preprocessing = _as_dict(report.get("preprocessing"))
        candidates = _as_list(report.get("candidate_models"))
        selection = _as_dict(report.get("selection"))
        final_fit = _as_dict(report.get("final_fit"))
        final_test = _as_dict(report.get("final_test_evaluation"))
        predictions = _as_list(report.get("prediction_evidence"))
        artifacts = _as_dict(report.get("artifacts"))

        if str(run.get("status") or "").lower() == "failed":
            add("run_failed", "pipeline", CHECK_FAIL, "The ML run ended in a failed state.", "run.status")

        input_path = artifacts.get("input")
        input_frame: pd.DataFrame | None = None
        if not input_path:
            add("input_artifact_exists", "file_ingestion", CHECK_NOT_VERIFIABLE, "Input artifact path is missing.")
        elif self.artifacts.artifact_exists(str(input_path)):
            add("input_artifact_exists", "file_ingestion", CHECK_PASS, "The uploaded input artifact exists.", "artifacts.input")
            try:
                input_frame = self.artifacts.load_table(str(input_path))
            except Exception as exc:  # noqa: BLE001 - evidence must convert loader errors into a check
                add(
                    "input_artifact_loadable",
                    "file_ingestion",
                    CHECK_FAIL,
                    f"The input artifact could not be loaded as a table: {exc}",
                    "artifacts.input",
                )
            else:
                if input_frame.empty or not len(input_frame.columns):
                    add("input_artifact_loadable", "file_ingestion", CHECK_FAIL, "The input artifact loaded without usable rows or columns.", "artifacts.input")
                else:
                    add("input_artifact_loadable", "file_ingestion", CHECK_PASS, "The input artifact is loadable as a non-empty table.", "artifacts.input")
        else:
            add("input_artifact_exists", "file_ingestion", CHECK_FAIL, "The uploaded input artifact does not exist.", "artifacts.input")
            add("input_artifact_loadable", "file_ingestion", CHECK_NOT_VERIFIABLE, "The absent input artifact cannot be loaded.", "artifacts.input")

        if profile.get("row_count") is None or profile.get("column_count") is None:
            add("file_load_counts_recorded", "file_ingestion", CHECK_NOT_VERIFIABLE, "Loaded row or column count is missing.", "raw_profile")
        elif input_frame is not None and (
            int(profile["row_count"]) != len(input_frame)
            or int(profile["column_count"]) != len(input_frame.columns)
        ):
            add("file_load_counts_recorded", "file_ingestion", CHECK_FAIL, "Persisted load counts differ from the input artifact.", "raw_profile", "artifacts.input")
        else:
            add("file_load_counts_recorded", "file_ingestion", CHECK_PASS, "Loaded row and column counts are persisted and consistent.", "raw_profile")

        profile_columns = [row for row in _as_list(profile.get("columns")) if isinstance(row, dict)]
        profile_names = {str(row.get("name")) for row in profile_columns if row.get("name") is not None}
        expected_names = {str(name) for name in _as_list(profile.get("column_names"))}
        required_column_fields = {
            "name",
            "dtype",
            "missing_count",
            "missing_ratio",
            "unique_count",
            "unique_ratio",
            "constant",
            "high_cardinality",
            "identifier_like",
        }
        missing_profile_fields = [
            str(row.get("name"))
            for row in profile_columns
            if not required_column_fields.issubset(row)
            or ("mean" in row and "skewness" not in row)
        ]
        if not profile_columns:
            add("eda_profile_complete", "profiling", CHECK_NOT_VERIFIABLE, "Per-column EDA profiles are missing.", "raw_profile.columns")
        elif (
            expected_names != profile_names
            or missing_profile_fields
            or (
                input_frame is not None
                and expected_names != {str(name) for name in input_frame.columns}
            )
        ):
            add(
                "eda_profile_complete",
                "profiling",
                CHECK_FAIL,
                f"EDA coverage or required statistics are inconsistent; affected columns: {missing_profile_fields}.",
                "raw_profile.columns",
                "raw_profile.column_names",
            )
        else:
            add("eda_profile_complete", "profiling", CHECK_PASS, "EDA covers the recorded schema and required statistics.", "raw_profile.columns")

        target_column = target.get("target_column", target.get("column"))
        target_task = target.get("task_type")
        task_type = task.get("task_type")
        supported_task = target_task in {"binary", "regression"}
        target_profile = next((row for row in profile_columns if row.get("name") == target_column), {})
        compatible = (
            target_task == task_type
            and task.get("target") == target_column
            and (
                (target_task == "binary" and target_profile.get("unique_count") == 2)
                or (
                    target_task == "regression"
                    and input_frame is not None
                    and target_column in input_frame
                    and pd.api.types.is_numeric_dtype(input_frame[target_column])
                )
            )
        )
        target_fields = {"target_column", "task_type", "source", "confidence", "reason", "candidates", "locked_at"}
        target_locked_at = _timestamp(target.get("locked_at"))
        split_at = _timestamp(split.get("split_at"))
        if not target_fields.issubset(target) or not target_column or not target.get("locked_at"):
            add("target_locked", "target_task_resolution", CHECK_NOT_VERIFIABLE, "Target lock evidence is missing.", "target_decision")
        elif (
            target_column not in expected_names
            or not supported_task
            or not compatible
            or (split_at is not None and (target_locked_at is None or target_locked_at > split_at))
        ):
            add("target_locked", "target_task_resolution", CHECK_FAIL, "The locked target is absent, unsupported, or incompatible with the persisted task.", "target_decision", "task", "raw_profile.column_names")
        else:
            add("target_locked", "target_task_resolution", CHECK_PASS, "Target and supported task are explicitly locked.", "target_decision")

        train_rows = _as_list(split.get("train_source_rows"))
        test_rows = _as_list(split.get("test_source_rows"))
        train_set, test_set = set(train_rows), set(test_rows)
        split_fields = {"split_at", "random_state", "stratify", "strategy", "n_train", "n_test"}
        if not split_fields.issubset(split) or not train_rows or not test_rows:
            add("split_provenance_complete", "splitting", CHECK_NOT_VERIFIABLE, "Train/test provenance or split metadata is missing.", "split")
        elif len(train_set) != len(train_rows) or len(test_set) != len(test_rows) or train_set & test_set:
            add("split_provenance_complete", "splitting", CHECK_FAIL, "Train and final-test provenance overlap.", "split.train_source_rows", "split.test_source_rows")
        elif len(train_rows) != split.get("n_train") or len(test_rows) != split.get("n_test"):
            add("split_provenance_complete", "splitting", CHECK_FAIL, "Split counts do not match provenance counts.", "split")
        elif (
            set(_as_list(split.get("all_source_rows"))) != train_set | test_set
            or len(train_rows) + len(test_rows) != cleaning.get("rows_out")
            or (
                split.get("modeling_row_count") is not None
                and split.get("modeling_row_count") != len(train_rows) + len(test_rows)
            )
        ):
            add("split_provenance_complete", "splitting", CHECK_FAIL, "Split provenance does not cover all modeling rows.", "split.all_source_rows")
        else:
            add("split_provenance_complete", "splitting", CHECK_PASS, "Train/test partitions are disjoint and complete.", "split")

        cleaning_fields = {"transformations", "rows_in", "rows_out", "columns_in", "columns_out"}
        if not cleaning_fields.issubset(cleaning):
            add("cleaning_evidence_consistent", "structural_cleaning", CHECK_NOT_VERIFIABLE, "Cleaning actions or row deltas are missing.", "cleaning")
        elif (
            int(cleaning["rows_out"]) > int(cleaning["rows_in"])
            or not set(_as_list(cleaning.get("columns_out"))) <= set(_as_list(cleaning.get("columns_in")))
        ):
            add("cleaning_evidence_consistent", "structural_cleaning", CHECK_FAIL, "Cleaning row or column deltas are internally inconsistent.", "cleaning")
        else:
            add("cleaning_evidence_consistent", "structural_cleaning", CHECK_PASS, "Cleaning actions and row deltas are internally consistent.", "cleaning")

        missing_plan = _as_dict(cleaning.get("missing_value_plan"))
        decision_rows = set(_as_list(missing_plan.get("evidence_source_rows")))
        if missing_plan.get("decision_partition") != "train" or not decision_rows:
            add("train_only_decision_scope", "train_only_decisions", CHECK_NOT_VERIFIABLE, "Train-only decision provenance is missing.", "cleaning.missing_value_plan")
        elif decision_rows & test_set or not decision_rows <= train_set:
            add("train_only_decision_scope", "train_only_decisions", CHECK_FAIL, "Train-only decisions include non-training provenance.", "cleaning.missing_value_plan.evidence_source_rows")
        else:
            add("train_only_decision_scope", "train_only_decisions", CHECK_PASS, "Modeling decisions use only locked training rows.", "cleaning.missing_value_plan")

        role_rows = [row for row in _as_list(role_evidence.get("columns")) if isinstance(row, dict)]
        role_fields = {
            "column",
            "original_dtype",
            "final_role",
            "source",
            "reason",
            "confidence",
            "validator_verdict",
            "llm_used",
        }
        valid_roles = {"numerical", "categorical", "boolean", "datetime", "identifier", "ignored/free_text", "target"}
        roles_by_column: dict[str, list[str]] = {}
        for row in role_rows:
            roles_by_column.setdefault(str(row.get("column")), []).append(str(row.get("final_role")))
        modeled = [str(name) for values in _as_dict(task.get("feature_groups")).values() for name in _as_list(values)]
        identifier_columns = {name for name, roles in roles_by_column.items() if "identifier" in roles}
        invalid_modeled = [
            name
            for name in modeled
            if len(roles_by_column.get(name, [])) != 1
            or roles_by_column.get(name, [""])[0] not in {"numerical", "categorical", "boolean", "datetime"}
        ]
        role_decision_rows = set(_as_list(role_evidence.get("evidence_source_rows")))
        incomplete_role_rows = [
            str(row.get("column"))
            for row in role_rows
            if not role_fields.issubset(row) or row.get("final_role") not in valid_roles
        ]
        role_coverage_invalid = set(roles_by_column) != expected_names or any(
            len(values) != 1 for values in roles_by_column.values()
        )
        if not role_rows or role_evidence.get("decision_partition") != "train" or not role_decision_rows:
            add("column_roles_valid", "column_roles", CHECK_NOT_VERIFIABLE, "Per-column role evidence is missing.", "column_role_evidence")
        elif (
            incomplete_role_rows
            or role_coverage_invalid
            or role_decision_rows & test_set
            or not role_decision_rows <= train_set
            or target_column in modeled
            or roles_by_column.get(str(target_column)) != ["target"]
            or identifier_columns & set(modeled)
            or invalid_modeled
        ):
            add("column_roles_valid", "column_roles", CHECK_FAIL, f"Modeled role assignments or evidence are invalid: {sorted(set(incomplete_role_rows + invalid_modeled))}.", "column_role_evidence", "task.feature_groups")
        else:
            add("column_roles_valid", "column_roles", CHECK_PASS, "Every modeled feature has one valid role; target and identifiers are excluded.", "column_role_evidence")

        actions = [row for row in _as_list(feature_engineering.get("feature_engineering_actions")) if isinstance(row, dict)]
        action_fields = {
            "transformation",
            "input_columns",
            "output_columns",
            "reason",
            "parameters",
            "learned_from_data",
            "decision_partition",
        }
        invalid_actions = [row for row in actions if not action_fields.issubset(row)]
        declared_outputs = {str(name) for row in actions for name in _as_list(row.get("output_columns"))}
        declared_inputs = {str(name) for row in actions for name in _as_list(row.get("input_columns"))}
        feature_fields = {"original_features", "generated_features", "transformed_features", "removed_features", "feature_engineering_actions"}
        declared_features = (
            set(str(name) for name in _as_list(feature_engineering.get("original_features")))
            | set(str(name) for name in _as_list(feature_engineering.get("generated_features")))
        )
        if not feature_fields.issubset(feature_engineering):
            add("feature_actions_match_schema", "feature_engineering", CHECK_NOT_VERIFIABLE, "Feature-engineering summary evidence is incomplete.", "feature_engineering")
        elif invalid_actions:
            add("feature_actions_match_schema", "feature_engineering", CHECK_FAIL, "Feature-engineering action metadata is incomplete.", "feature_engineering.feature_engineering_actions")
        elif (
            not declared_outputs <= set(modeled)
            or not declared_inputs <= declared_features
            or any(row.get("decision_partition") != "train" for row in actions)
        ):
            add("feature_actions_match_schema", "feature_engineering", CHECK_FAIL, "Declared transformation outputs are absent from the modeled schema.", "feature_engineering", "task.feature_groups")
        else:
            add("feature_actions_match_schema", "feature_engineering", CHECK_PASS, "Feature-engineering declarations match the modeled schema.", "feature_engineering")

        preprocessing_expected = {
            "numeric_columns",
            "categorical_columns",
            "numeric_imputer_strategy",
            "numeric_scaler",
            "categorical_imputer_strategy",
            "categorical_encoder",
            "handle_unknown",
            "fit_partition",
        }
        numeric_preprocessing = set(str(name) for name in _as_list(preprocessing.get("numeric_columns")))
        categorical_preprocessing = set(str(name) for name in _as_list(preprocessing.get("categorical_columns")))
        if not preprocessing_expected.issubset(preprocessing):
            add("preprocessing_config_complete", "preprocessing_setup", CHECK_NOT_VERIFIABLE, "Preprocessing configuration evidence is incomplete.", "preprocessing")
        elif (
            preprocessing.get("handle_unknown") != "ignore"
            or "fold_train_only" not in str(preprocessing.get("fit_partition"))
            or numeric_preprocessing & categorical_preprocessing
            or numeric_preprocessing | categorical_preprocessing != set(modeled)
        ):
            add("preprocessing_config_complete", "preprocessing_setup", CHECK_FAIL, "Preprocessing safety configuration is invalid.", "preprocessing")
        else:
            add("preprocessing_config_complete", "preprocessing_setup", CHECK_PASS, "Expected preprocessing components and train-only fit scope are recorded.", "preprocessing")

        cv_failures: list[str] = []
        cv_missing = False
        trained_candidates = [row for row in candidates if isinstance(row, dict) and row.get("status") == "trained"]
        plan = report.get("validation_plan") if isinstance(report.get("validation_plan"), dict) else {}
        allowed_cv = {
            "StratifiedKFold",
            "KFold",
            "StratifiedGroupKFold",
            "GroupKFold",
            "TimeSeriesSplit",
        }
        expected_cv_strategy = str(plan.get("strategy") or "")
        if expected_cv_strategy not in allowed_cv:
            expected_cv_strategy = "StratifiedKFold" if task_type == "binary" else "KFold"
        kfold_covers_train = expected_cv_strategy != "TimeSeriesSplit"
        for candidate in trained_candidates:
            folds = [row for row in _as_list(candidate.get("folds")) if isinstance(row, dict)]
            actual = candidate.get("actual_folds")
            if not folds or actual is None:
                cv_missing = True
                continue
            if (
                len(folds) != actual
                or candidate.get("requested_folds") != 5
                or candidate.get("cv_strategy") != expected_cv_strategy
                or (actual != 5 and not candidate.get("adaptation_reason"))
            ):
                cv_failures.append(str(candidate.get("candidate_id")))
                continue
            validation_union: set[Any] = set()
            failed_candidate = False
            for fold in folds:
                fold_train = set(_as_list(fold.get("train_provenance")))
                fold_validation = set(_as_list(fold.get("validation_provenance")))
                validation_union |= fold_validation
                covers_fold = (fold_train | fold_validation) == train_set if kfold_covers_train else fold_train | fold_validation <= train_set
                if (
                    not {"fold_number", "train_row_count", "validation_row_count", "metrics", "fit_duration_ms"}.issubset(fold)
                    or len(fold_train) != fold.get("train_row_count")
                    or len(fold_validation) != fold.get("validation_row_count")
                    or fold.get("fit_duration_ms", 0) <= 0
                    or fold_train & fold_validation
                    or not covers_fold
                    or test_set & (fold_train | fold_validation)
                ):
                    cv_failures.append(str(candidate.get("candidate_id")))
                    failed_candidate = True
                    break
            if failed_candidate:
                continue
            if kfold_covers_train and validation_union != train_set:
                cv_failures.append(str(candidate.get("candidate_id")))
            elif not kfold_covers_train and (not validation_union or not validation_union <= train_set):
                cv_failures.append(str(candidate.get("candidate_id")))
        if cv_failures:
            add("cross_validation_provenance", "cross_validation", CHECK_FAIL, f"Invalid fold provenance for candidates: {sorted(set(cv_failures))}.", "candidate_models.folds", "split")
        elif cv_missing or not trained_candidates:
            add("cross_validation_provenance", "cross_validation", CHECK_NOT_VERIFIABLE, "Fold provenance is missing for one or more trained candidates.", "candidate_models.folds")
        else:
            add("cross_validation_provenance", "cross_validation", CHECK_PASS, "Every CV fold is disjoint, train-only, and covers the training partition.", "candidate_models.folds", "split")

        expected_candidates = {str(value) for value in _as_list(report.get("expected_candidate_ids"))}
        recorded_candidates = {str(row.get("candidate_id")) for row in candidates if isinstance(row, dict)}
        candidate_fields = {
            "candidate_id",
            "model_family",
            "hyperparameters",
            "feature_set",
            "preprocessing_config",
            "cv_strategy",
            "requested_folds",
            "actual_folds",
            "fold_metrics",
            "cv_mean",
            "cv_std",
            "fit_duration_ms",
            "status",
            "failure_reason",
        }
        incomplete_candidates = [
            str(row.get("candidate_id"))
            for row in candidates
            if not isinstance(row, dict)
            or not candidate_fields.issubset(row)
            or (
                row.get("status") == "trained"
                and (
                    not row.get("fold_metrics")
                    or len(_as_list(row.get("fold_metrics"))) != row.get("actual_folds")
                    or row.get("cv_mean") is None
                    or row.get("cv_std") is None
                )
            )
            or (row.get("status") in {"FAILED", "failed"} and not row.get("failure_reason"))
        ]
        if not expected_candidates:
            add("candidate_audit_complete", "candidate_training", CHECK_NOT_VERIFIABLE, "Expected candidate portfolio is missing.", "expected_candidate_ids")
        elif expected_candidates != recorded_candidates:
            add("candidate_audit_complete", "candidate_training", CHECK_FAIL, "Expected and recorded candidate portfolios differ.", "expected_candidate_ids", "candidate_models")
        elif incomplete_candidates or any(row.get("status") not in {"trained", "FAILED", "failed"} for row in candidates):
            add("candidate_audit_complete", "candidate_training", CHECK_FAIL, f"Candidate audit records are incomplete: {incomplete_candidates}.", "candidate_models")
        else:
            add("candidate_audit_complete", "candidate_training", CHECK_PASS, "Every expected candidate has a trained or failed audit record.", "candidate_models")

        winner_id = selection.get("candidate_id", selection.get("selected_candidate_id"))
        eligible = [row for row in trained_candidates if row.get("candidate_id") in _as_list(selection.get("eligible_candidate_ids"))]
        best_score = max((float(row.get("score", float("-inf"))) for row in eligible), default=None)
        winner_row = next((row for row in eligible if row.get("candidate_id") == winner_id), None)
        if (
            not winner_id
            or not selection.get("selection_metric")
            or selection.get("selection_source") != "cross_validation"
            or not selection.get("locked")
        ):
            add("winner_selected_from_cv", "model_selection", CHECK_NOT_VERIFIABLE, "CV-only winner lock evidence is missing.", "selection")
        elif (
            best_score is None
            or winner_row is None
            or float(winner_row.get("score", float("-inf"))) != best_score
            or selection.get("cv_score") != winner_row.get("score")
        ):
            add("winner_selected_from_cv", "model_selection", CHECK_FAIL, "The locked winner is not the best eligible CV result.", "selection", "candidate_models")
        else:
            add("winner_selected_from_cv", "model_selection", CHECK_PASS, "The winner is the best eligible CV candidate and was locked from CV evidence.", "selection")

        locked_at = _timestamp(selection.get("locked_at"))
        fit_started = _timestamp(final_fit.get("started_at"))
        fit_ended = _timestamp(final_fit.get("ended_at"))
        test_started = _timestamp(final_test.get("started_at"))
        if not all((locked_at, fit_started, fit_ended, test_started)):
            add("final_fit_after_lock", "final_fit", CHECK_NOT_VERIFIABLE, "Final-fit or lock timestamps are missing.", "selection", "final_fit", "final_test_evaluation")
        elif (
            not (locked_at <= fit_started <= fit_ended <= test_started)
            or final_fit.get("candidate_id") != winner_id
            or final_fit.get("fit_partition") != "full_train"
            or final_fit.get("fit_row_count") != split.get("n_train")
        ):
            add("final_fit_after_lock", "final_fit", CHECK_FAIL, "Final fit did not occur after lock and before test evaluation.", "selection", "final_fit", "final_test_evaluation")
        else:
            add("final_fit_after_lock", "final_fit", CHECK_PASS, "The locked winner was refit on full training data before test evaluation.", "selection", "final_fit")

        winner_rows = [row for row in trained_candidates if row.get("candidate_id") == winner_id]
        rejected_with_test = [row.get("candidate_id") for row in trained_candidates if row.get("candidate_id") != winner_id and row.get("test_metrics") is not None]
        if not winner_rows or final_test.get("evaluation_count") != 1:
            add("winner_only_final_test", "final_test_evaluation", CHECK_NOT_VERIFIABLE, "Final winner evaluation evidence is missing.", "final_test_evaluation")
        elif (
            rejected_with_test
            or not winner_rows[0].get("test_metrics")
            or final_test.get("candidate_id") != winner_id
            or final_test.get("test_row_count") != split.get("n_test")
            or final_test.get("metrics") != winner_rows[0].get("test_metrics")
        ):
            add("winner_only_final_test", "final_test_evaluation", CHECK_FAIL, f"Rejected candidates have final-test metrics: {rejected_with_test}.", "candidate_models", "final_test_evaluation")
        else:
            add("winner_only_final_test", "final_test_evaluation", CHECK_PASS, "Only the selected winner has final-test metrics and it was evaluated once.", "candidate_models", "final_test_evaluation")

        _verify_scientific_plan(
            add,
            report=report,
            task=task,
            selection=selection,
            candidates=candidates,
            split=split,
        )

        prediction_rows = [row for row in predictions if isinstance(row, dict)]
        prediction_sources = [row.get("source_row_index") for row in prediction_rows]
        prediction_truth_mismatches: list[Any] = []
        artifact_targets = None
        label_task_type = str(target.get("task_type") or task.get("task_type") or "")
        if input_frame is not None and target_column in input_frame:
            artifact_targets = _artifact_target_values(
                input_frame,
                target_column,
                label_task_type,
            )
            for row in prediction_rows:
                source_row = row.get("source_row_index")
                if isinstance(source_row, int) and 0 <= source_row < len(artifact_targets):
                    expected = artifact_targets.iloc[source_row]
                    actual = row.get("y_true")
                    if not _labels_match(expected, actual, task_type=label_task_type):
                        prediction_truth_mismatches.append(source_row)
        if (
            not predictions
            or len(prediction_rows) != len(predictions)
            or any(value is None for value in prediction_sources)
            or any("y_true" not in row for row in prediction_rows)
        ):
            add("prediction_provenance_complete", "prediction_persistence", CHECK_NOT_VERIFIABLE, "Prediction provenance is missing.", "prediction_evidence")
        elif len(predictions) != split.get("n_test") or set(prediction_sources) != test_set or set(prediction_sources) & train_set:
            add("prediction_provenance_complete", "prediction_persistence", CHECK_FAIL, "Predictions do not map exactly to final-test provenance.", "prediction_evidence", "split")
        elif prediction_truth_mismatches:
            add("prediction_provenance_complete", "prediction_persistence", CHECK_FAIL, f"Persisted true labels differ from the input artifact for rows: {prediction_truth_mismatches}.", "prediction_evidence", "artifacts.input")
        else:
            add("prediction_provenance_complete", "prediction_persistence", CHECK_PASS, "Every final-test row has exactly one persisted prediction.", "prediction_evidence", "split")

        required_timing_stages = {
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
        }
        timing_rows = [row for row in _as_list(report.get("stage_timings")) if isinstance(row, dict)]
        timing_by_stage = {str(row.get("stage")): row for row in timing_rows}
        missing_timing_stages = sorted(required_timing_stages - set(timing_by_stage))
        invalid_timing_stages = sorted(
            stage
            for stage in required_timing_stages & set(timing_by_stage)
            if not _timestamp(timing_by_stage[stage].get("started_at"))
            or not _timestamp(timing_by_stage[stage].get("ended_at"))
            or not isinstance(timing_by_stage[stage].get("duration_ms"), (int, float))
            or timing_by_stage[stage].get("duration_ms", 0) <= 0
            or not timing_by_stage[stage].get("status")
        )
        if missing_timing_stages:
            add("stage_timings_complete", "pipeline", CHECK_NOT_VERIFIABLE, f"Required stage timings are missing: {missing_timing_stages}.", "stage_timings")
        elif invalid_timing_stages:
            add("stage_timings_complete", "pipeline", CHECK_FAIL, f"Required stage timings are invalid: {invalid_timing_stages}.", "stage_timings")
        else:
            add("stage_timings_complete", "pipeline", CHECK_PASS, "All required stages have real timing and status evidence.", "stage_timings")

        artifact_keys = ("model", "result", "predictions")
        missing_artifacts = [key for key in artifact_keys if not artifacts.get(key)]
        absent_artifacts = [
            key
            for key in artifact_keys
            if artifacts.get(key) and not self.artifacts.artifact_exists(str(artifacts[key]))
        ]
        if missing_artifacts:
            add("model_artifacts_persisted", "artifact_persistence", CHECK_NOT_VERIFIABLE, f"Artifact paths are missing: {missing_artifacts}.", "artifacts")
        elif absent_artifacts:
            add("model_artifacts_persisted", "artifact_persistence", CHECK_FAIL, f"Persisted artifacts do not exist: {absent_artifacts}.", "artifacts")
        else:
            add("model_artifacts_persisted", "artifact_persistence", CHECK_PASS, "Model, result, and prediction artifacts exist.", "artifacts")

        if db is not None:
            _verify_reproducibility_lineage(add, report, db)

        failures = [row for row in checks if row["status"] == CHECK_FAIL]
        missing = [row for row in checks if row["status"] == CHECK_NOT_VERIFIABLE]
        warnings = [row for row in checks if row["status"] == CHECK_WARN]
        if failures:
            overall = "FAILED"
        elif missing:
            overall = "NOT_VERIFIABLE"
        elif warnings:
            overall = "VERIFIED_WITH_WARNINGS"
        else:
            overall = "VERIFIED"

        stages: list[dict[str, Any]] = []
        for stage in dict.fromkeys(row["stage"] for row in checks):
            stage_checks = [row for row in checks if row["stage"] == stage]
            statuses = {row["status"] for row in stage_checks}
            if CHECK_FAIL in statuses:
                status = "FAILED"
            elif CHECK_NOT_VERIFIABLE in statuses:
                status = "NOT_VERIFIABLE"
            elif CHECK_WARN in statuses:
                status = "VERIFIED_WITH_WARNINGS"
            else:
                status = "VERIFIED"
            stages.append(
                {
                    "stage": stage,
                    "status": status,
                    "check_ids": [row["check_id"] for row in stage_checks],
                }
            )

        return {
            "schema_version": 1,
            "overall_status": overall,
            "checks": checks,
            "stages": stages,
            "warnings": warnings,
            "failures": failures,
            "missing_evidence": missing,
            "summary": (
                "All required deterministic pipeline checks passed."
                if overall == "VERIFIED"
                else f"Deterministic verification finished with status {overall}."
            ),
        }


def verify_pipeline(report: dict[str, Any], *, db: Session | None = None) -> dict[str, Any]:
    return PipelineVerifier().verify(report, db=db)
