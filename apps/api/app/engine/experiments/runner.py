"""End-to-end experiment runner. One candidate failure does not fail the run."""

from __future__ import annotations

import json
from dataclasses import replace
import logging
import time
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline as SkPipeline

from app.domain.lab_run_stages import CROSS_VALIDATION, EVALUATING, PREDICTING, SPLITTING, TRAINING
from app.engine.data.quality import quality_report
from app.engine.evaluation.metrics import (
    LOWER_IS_BETTER,
    aggregate_fold_metrics,
    classification_metrics,
    multiclass_metrics,
    primary_score,
    regression_metrics,
    robustness_stats,
    selection_metric_value,
)
from app.engine.features.encode import coerce_binary_target
from app.engine.lab.auto_prepare import (
    apply_feature_engineering_actions,
    build_preprocessor,
    engineer_features,
    missing_plan_from_applied_imputers,
    split_column_roles,
)
from app.engine.models.registry import make_model
from app.engine.search.tuning import tune
from app.engine.modeling.objective import (
    DEFAULT_THRESHOLD,
    constraint_status,
    evaluate_constraints,
    fold_constraint_values,
    objective_from_dict,
    select_decision_threshold,
    threshold_metrics,
)
from app.engine.modeling.validation_planner import TIME_SERIES_SPLIT as _TIME_SERIES_SPLIT
from app.engine.modeling.holdout_planner import (
    TEMPORAL_FUTURE,
    HoldoutPlan,
    holdout_locked_event_payload,
    holdout_plan_event_payload,
    plan_holdout,
    require_supported_holdout,
)
from app.engine.modeling.coerce import from_mapping
from app.engine.modeling.importance import ImportanceFold, validation_fold_importance
from app.engine.modeling.leakage_auditor import (
    ModelDevelopmentPlan,
    consult_leakage_llm,
    leakage_report_from_audit,
    plan_model_development,
)
from app.engine.modeling.metric_planner import MetricPlan
from app.engine.modeling.problem_profile import ProblemProfile
from app.engine.modeling.validation_planner import (
    ValidationPlan,
    ValidationUnsupportedError,
    iter_validation_folds,
)
from app.engine.schema.profiler import profile_frame
from app.engine.search.generator import DUMMY_FAMILIES, assemble_candidates
from app.engine.types import Candidate, ExperimentStatus, SearchConfig, TaskSpec, is_classification
from app.engine.validation.split_assignment import folds_for_pool
from app.engine.validation.splits import (
    SOURCE_ROW_COLUMN,
    split_holdout_by_assignment,
    split_train_test_holdout,
)

logger = logging.getLogger(__name__)

RunEventCallback = Callable[[str, dict[str, Any]], None]


def _emit_event(
    callback: RunEventCallback | None,
    event_type: str,
    *,
    stage: str,
    status: str,
    **payload: Any,
) -> None:
    if callback is not None:
        callback(event_type, {"stage": stage, "status": status, **payload})


def _planning_event_sink(on_event: RunEventCallback | None):
    if on_event is None:
        return None

    def sink(event_type: str, payload: dict[str, Any]) -> None:
        data = dict(payload)
        stage = str(data.pop("stage", event_type))
        status = str(data.pop("status", "completed"))
        _emit_event(on_event, event_type, stage=stage, status=status, **data)

    return sink


def _lock_open_ingest_holdout(
    frame: pd.DataFrame,
    task: TaskSpec,
    config: SearchConfig,
    on_event: RunEventCallback | None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any], HoldoutPlan]:
    holdout_plan = plan_holdout(
        frame,
        target=task.target,
        task_type=task.task_type,
        test_size=0.2,
        random_state=config.seed,
    )
    _emit_event(
        on_event,
        "holdout_plan_selected",
        stage="holdout_plan",
        status="completed",
        **holdout_plan_event_payload(holdout_plan),
    )
    require_supported_holdout(holdout_plan)
    train, val, test, split_meta = split_train_test_holdout(
        frame,
        target=task.target,
        test_size=holdout_plan.test_size,
        seed=holdout_plan.random_state,
        plan=holdout_plan,
    )
    _emit_event(
        on_event,
        "holdout_locked",
        stage="holdout_lock",
        status="completed",
        **holdout_locked_event_payload(split_meta),
    )
    return train, val, test, split_meta, holdout_plan


def _as_plan_mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        return payload if isinstance(payload, dict) and payload else None
    if isinstance(value, dict) and value:
        return value
    return None


def _overlay_scientific_plans(
    config: SearchConfig,
    *,
    holdout_plan: Any = None,
    model_development_plan: Any = None,
) -> SearchConfig:
    payload = config.to_dict()
    holdout = _as_plan_mapping(holdout_plan)
    development = _as_plan_mapping(model_development_plan)
    if holdout is not None:
        payload["holdout_plan"] = holdout
    if development is not None:
        payload["model_development_plan"] = development
    payload["holdout_plan"] = _as_plan_mapping(payload.get("holdout_plan"))
    payload["model_development_plan"] = _as_plan_mapping(payload.get("model_development_plan"))
    return SearchConfig(**payload)


def _split_open_ingest_holdout(
    frame: pd.DataFrame,
    task: TaskSpec,
    config: SearchConfig,
    on_event: RunEventCallback | None,
    holdout_partition: tuple[Collection[int], Collection[int]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any], HoldoutPlan, str]:
    provided = from_mapping(HoldoutPlan, config.holdout_plan)
    if holdout_partition is not None:
        # A reused SplitPlan: partition by its stored map, never re-split.
        if provided is None:
            raise ValueError("a stored holdout partition requires the plan's HoldoutPlan")
        holdout_rows, train_rows = holdout_partition
        train, val, test, split_meta = split_holdout_by_assignment(
            frame, plan=provided, holdout_rows=holdout_rows, train_rows=train_rows
        )
        return train, val, test, split_meta, provided, "split_plan"
    if provided is not None:
        require_supported_holdout(provided)
        train, val, test, split_meta = split_train_test_holdout(
            frame,
            target=task.target,
            test_size=provided.test_size,
            seed=provided.random_state,
            plan=provided,
        )
        return train, val, test, split_meta, provided, "provided"
    train, val, test, split_meta, holdout_plan = _lock_open_ingest_holdout(
        frame, task, config, on_event
    )
    return train, val, test, split_meta, holdout_plan, "computed"


def _resolve_model_development_plan(
    train: pd.DataFrame,
    task: TaskSpec,
    config: SearchConfig,
    on_event: RunEventCallback | None,
) -> tuple[ProblemProfile, ValidationPlan, MetricPlan, dict[str, Any], ModelDevelopmentPlan, str]:
    provided = from_mapping(ModelDevelopmentPlan, config.model_development_plan)
    if provided is not None:
        problem_profile = ProblemProfile.from_dict(provided.problem_profile)
        validation_plan = ValidationPlan.from_dict(provided.validation_plan)
        metric_plan = MetricPlan.from_dict(provided.metric_plan)
        leakage = dict(provided.leakage_assessment or {})
        return problem_profile, validation_plan, metric_plan, leakage, provided, "provided"
    problem_profile, validation_plan, metric_plan, leakage_audit, development_plan = plan_model_development(
        train,
        target=task.target,
        task_type=task.task_type,
        requested_folds=5,
        random_state=config.seed,
        time_column=task.prediction_time_column,
        entity_column=task.entity_id,
        reviewer=consult_leakage_llm,
        conservative_auto_train=config.exclude_high_leakage,
        on_event=_planning_event_sink(on_event),
        objective=objective_from_dict(config.objective),
    )
    return (
        problem_profile,
        validation_plan,
        metric_plan,
        leakage_report_from_audit(leakage_audit),
        development_plan,
        "computed",
    )


def _align_task_to_development_plan(
    task: TaskSpec,
    *,
    frame: pd.DataFrame,
    validation_plan: ValidationPlan,
    metric_plan: MetricPlan,
    development_plan: ModelDevelopmentPlan,
) -> TaskSpec:
    group_column = development_plan.group_column
    time_column = development_plan.time_column
    entity_id = (
        group_column
        if group_column and group_column in frame.columns
        else (task.entity_id if task.entity_id and task.entity_id in frame.columns else None)
    )
    return TaskSpec(
        **{
            **task.to_dict(),
            "evaluation_metric": metric_plan.primary_metric,
            "validation_strategy": validation_plan.strategy,
            "entity_id": entity_id,
            "prediction_time_column": time_column if time_column and time_column in frame.columns else None,
        }
    )


def _estimator_columns_from_plan(
    task: TaskSpec,
    train: pd.DataFrame,
    development_plan: ModelDevelopmentPlan,
) -> tuple[list[str], list[str], dict[str, list[str]]]:
    allowed = set(development_plan.allowed_features)
    excluded = {
        str(item.get("column"))
        for item in development_plan.excluded_features
        if isinstance(item, dict) and item.get("column")
    }
    reserved = {task.target, SOURCE_ROW_COLUMN}
    if development_plan.group_column:
        reserved.add(development_plan.group_column)

    def keep(column: str) -> bool:
        return (
            column in train.columns
            and column in allowed
            and column not in excluded
            and column not in reserved
        )

    groups = {
        name: [column for column in cols if keep(column)]
        for name, cols in task.feature_groups.items()
    }
    groups = {name: cols for name, cols in groups.items() if cols}
    if not groups:
        groups = {"features": [column for column in train.columns if keep(column)]}
        groups = {name: cols for name, cols in groups.items() if cols}
    roles = task.column_roles or {}
    numerical_cols = [column for column in (roles.get("numerical") or []) if keep(column) and any(column in cols for cols in groups.values())]
    categorical_cols = [
        column for column in (roles.get("categorical") or []) if keep(column) and any(column in cols for cols in groups.values())
    ]
    roles_provided = bool(roles.get("numerical") or roles.get("categorical"))
    if not roles_provided:
        role_source = [column for cols in groups.values() for column in cols]
        numerical_cols, categorical_cols = split_column_roles(train, role_source)
    modeled = numerical_cols + categorical_cols
    if modeled:
        groups = {"features": modeled}
    return numerical_cols, categorical_cols, groups


def _open_ingest_feature_actions(
    task: TaskSpec,
    train: pd.DataFrame,
    val: pd.DataFrame,
    test: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    existing = [
        row
        for row in list((task.feature_engineering or {}).get("feature_engineering_actions") or [])
        if isinstance(row, dict)
    ]
    if existing:
        return train, val, test, existing
    reserved = {task.target, SOURCE_ROW_COLUMN}
    if task.entity_id:
        reserved.add(task.entity_id)
    if task.prediction_time_column:
        reserved.add(task.prediction_time_column)
    columns = [name for name in train.columns if name not in reserved]
    engineered, actions = engineer_features(train, columns)
    if not actions:
        return train, val, test, []
    return (
        engineered,
        apply_feature_engineering_actions(val, actions),
        apply_feature_engineering_actions(test, actions),
        actions,
    )


def _scientific_evidence_payload(
    *,
    missing_plan: Any = None,
    leakage_exclusions: list[dict[str, Any]] | None = None,
    feature_actions: list[dict[str, Any]] | None = None,
    numerical_cols: list[str] | None = None,
    categorical_cols: list[str] | None = None,
    dropped_columns: list[str] | None = None,
    fit_scope: str = "fold_train",
) -> dict[str, Any]:
    numerical = list(numerical_cols or [])
    categorical = list(categorical_cols or [])
    to_dict = getattr(missing_plan, "to_dict", None)
    missing_payload = to_dict() if callable(to_dict) else None
    return {
        "missing_value_plan": missing_payload,
        "leakage_exclusions": list(leakage_exclusions or []),
        "feature_actions": list(feature_actions or []),
        "numerical_columns": numerical,
        "categorical_columns": categorical,
        "modeled_features": list(dict.fromkeys([*numerical, *categorical])),
        "dropped_columns": list(dropped_columns or []),
        "preprocessing_fit_scope": fit_scope,
    }


def _class_probabilities(model, X, n_classes: int) -> np.ndarray:
    """(n, n_classes) probabilities aligned to label codes 0..n_classes-1.

    A class missing from the fitted rows keeps a zero column, so its rows are
    scored as misclassified rather than silently dropped.
    """
    out = np.zeros((len(X), n_classes), dtype=float)
    if hasattr(model, "predict_proba"):
        classes = np.asarray(model.classes_, dtype=int)
        out[:, classes] = np.asarray(model.predict_proba(X), dtype=float)
    else:
        predicted = np.asarray(model.predict(X), dtype=int)
        out[np.arange(len(predicted)), predicted] = 1.0
    return out


def _predict(model, X: np.ndarray, classifier: bool, n_classes: int | None = None) -> np.ndarray:
    if classifier and n_classes:
        return _class_probabilities(model, X, n_classes)
    if classifier:
        if hasattr(model, "predict_proba"):
            proba = model.predict_proba(X)
            return np.asarray(proba[:, 1] if proba.shape[1] > 1 else proba[:, 0], dtype=float)
        return np.asarray(model.predict(X), dtype=float)
    return np.asarray(model.predict(X), dtype=float)


def _encode_multiclass_target(work: pd.DataFrame, target: str) -> tuple[pd.DataFrame, list[Any]]:
    """Code labels 0..k-1 over the full label set before the split.

    Only the set of label values is used (no statistic is fitted), so a class
    present only in the holdout still has a code and counts as misclassified.
    """
    work = work.dropna(subset=[target]).copy()
    values = [_json_safe(value) for value in pd.unique(work[target])]
    try:
        class_labels = sorted(values)
    except TypeError:
        class_labels = sorted(values, key=str)
    if len(class_labels) < 2:
        raise ValueError(f"target {target!r} has fewer than two classes")
    codes = {label: index for index, label in enumerate(class_labels)}
    work[target] = [codes[_json_safe(value)] for value in work[target]]
    work[target] = work[target].astype(int)
    return work, class_labels


class _CandidateSkipped(Exception):
    """A candidate deliberately not evaluated (budget); recorded as SKIPPED."""


def _skipped_record(candidate: Candidate, reason: str, validation_plan: ValidationPlan) -> dict[str, Any]:
    return {
        **candidate.to_dict(),
        "candidate": candidate.candidate_id,
        "feature_set": list(candidate.features),
        "preprocessing_config": {
            "numerical": ["imputer:median", "scaler:standard"],
            "categorical": ["imputer:most_frequent", "onehot:all_categories"],
        },
        "status": "SKIPPED",
        "failure_reason": reason,
        "cv_strategy": validation_plan.strategy,
        "requested_folds": validation_plan.requested_folds,
        "actual_folds": None,
        "metrics": None,
        "fold_metrics": [],
        "folds": [],
        "cv_mean": None,
        "cv_std": None,
        "train_seconds": 0.0,
        "fit_duration_ms": 0.001,
        "test_metrics": None,
    }


def _tuning_axes(
    pool: pd.DataFrame, validation_plan: ValidationPlan | None
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Group labels or time values so inner tuning splits respect the outer plan."""
    if validation_plan is None:
        return None, None
    if validation_plan.strategy == _TIME_SERIES_SPLIT and validation_plan.time_column in pool.columns:
        return None, pool[validation_plan.time_column].to_numpy()
    if validation_plan.group_column and validation_plan.group_column in pool.columns:
        return pool[validation_plan.group_column].to_numpy(), None
    return None, None


def _lock_decision_threshold(
    task_type: str,
    winner: dict[str, Any],
    oof: tuple[list[np.ndarray], list[np.ndarray]] | None,
    y_pool: np.ndarray,
    pool: pd.DataFrame,
    objective,
    *,
    primary_metric: str | None = None,
    last_fold_only: bool = False,
) -> dict[str, Any]:
    """Choose the winner's decision threshold from its out-of-fold CV scores only.

    K-fold / group folds pool every validation row (each row once). Under
    TimeSeriesSplit early fold models see little data and older regimes, so only
    the most recent validation fold is used.
    """
    cv_metrics = dict(winner.get("cv_mean") or {})
    fold_index = list(oof[0]) if oof else []
    fold_scores = list(oof[1]) if oof else []
    if last_fold_only and fold_index:
        fold_index, fold_scores = fold_index[-1:], fold_scores[-1:]
    index = np.concatenate(fold_index) if fold_index else np.asarray([], dtype=int)
    if task_type == "binary":
        scores = np.concatenate(fold_scores) if fold_scores else np.asarray([], dtype=float)
        decision = select_decision_threshold(
            y_pool[index], scores, objective, cv_metrics=cv_metrics, primary_metric=primary_metric
        )
        decision["oof_folds"] = "last_fold" if last_fold_only else "all_folds"
        all_folds = list(zip(oof[0], oof[1])) if oof else []
        decision["per_fold"] = fold_constraint_values(
            [(y_pool[idx], fold_score) for idx, fold_score in all_folds],
            float(decision["value"]),
            sorted({row["metric"] for row in decision["constraints"]} | {"precision", "recall", str(primary_metric)}),
        )
    else:
        constraints = evaluate_constraints(objective, oof_metrics=cv_metrics)
        decision = {
            "value": None,
            "source": "not_applicable",
            "selected_on": "out_of_fold_cv",
            "oof_row_count": int(len(index)),
            "status": constraint_status(constraints),
            "reason": f"No decision threshold applies to {task_type}; constraints are checked on CV means.",
            "expected_cost": None,
            "constraints": constraints,
        }
    # Provenance of the rows the threshold saw: training-pool rows only.
    decision["oof_source_rows"] = (
        sorted(int(value) for value in pool.iloc[np.unique(index)][SOURCE_ROW_COLUMN].tolist())
        if SOURCE_ROW_COLUMN in pool.columns and len(index)
        else []
    )
    decision["candidate_id"] = winner.get("candidate_id")
    return decision


def _apply_holdout_constraints(
    decision: dict[str, Any], objective, test_metrics: dict[str, Any]
) -> dict[str, Any]:
    """Report each constraint on the holdout at the locked threshold (never re-tuned)."""
    holdout_rows = evaluate_constraints(objective, holdout_metrics=test_metrics)
    for row, holdout in zip(decision.get("constraints") or [], holdout_rows):
        row["holdout_value"] = holdout["holdout_value"]
        row["holdout_satisfied"] = holdout["holdout_satisfied"]
    metrics = dict(test_metrics)
    if decision.get("value") is not None:
        metrics["decision_threshold"] = float(decision["value"])
    matrix = metrics.get("confusion_matrix")
    if (
        objective is not None
        and objective.has_cost_matrix
        and isinstance(matrix, dict)
        and sum(matrix.values())
    ):
        metrics["expected_cost"] = (
            matrix["fp"] * objective.cost_false_positive + matrix["fn"] * objective.cost_false_negative
        ) / sum(matrix.values())
    rows = decision.get("constraints") or []
    for row in rows:
        if row.get("holdout_satisfied") is not None:
            metrics[f"{row['label']}_satisfied"] = 1.0 if row["holdout_satisfied"] else 0.0
    if rows:
        metrics["constraints_satisfied"] = 1.0 if all(row.get("holdout_satisfied") for row in rows) else 0.0
    decision["holdout_status"] = constraint_status(rows, "holdout_satisfied") if rows else None
    return metrics


def _decoded_target(frame: pd.DataFrame, target: str, class_labels: list[Any] | None) -> pd.DataFrame:
    """Planning and the leakage audit see original labels, so a feature that
    copies the raw target still matches it exactly and evidence names classes."""
    if not class_labels:
        return frame
    decoded = frame.copy()
    decoded[target] = pd.Series(
        [class_labels[int(code)] for code in frame[target]], index=frame.index, dtype=object
    )
    return decoded


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _matrix(frame: pd.DataFrame, features: tuple[str, ...]) -> np.ndarray:
    missing = [col for col in features if col not in frame.columns]
    if missing:
        raise ValueError(f"Missing features: {missing}")
    return frame.loc[:, list(features)].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=float)


def _metrics(
    y_true,
    pred,
    *,
    classifier: bool,
    n_classes: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    if classifier and n_classes:
        return multiclass_metrics(y_true, pred, n_classes=n_classes)
    if classifier:
        return {
            **classification_metrics(y_true, pred, threshold=threshold),
            **threshold_metrics(y_true, pred, threshold),
        }
    return regression_metrics(y_true, pred)


def _timing(stage: str, started_at: datetime, timer: float, *, status: str = "completed") -> dict[str, Any]:
    ended_at = datetime.now(UTC)
    return {
        "stage": stage,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_ms": max(0.001, (time.perf_counter() - timer) * 1000.0),
        "status": status,
    }


def _open_ingest_validation(
    split_meta: dict[str, Any],
    records: list[dict[str, Any]],
    seed: int,
    task_type: str,
    plan: ValidationPlan | None = None,
) -> dict[str, Any]:
    trained = [row for row in records if row.get("status") == "trained"]
    sample = trained[0] if trained else {}
    if plan is not None:
        return {
            "train_rows": split_meta.get("n_train"),
            "test_rows": split_meta.get("n_test"),
            "cv_strategy": plan.strategy,
            "n_folds": plan.actual_folds,
            "requested_folds": plan.requested_folds,
            "actual_folds": plan.actual_folds,
            "adaptation_reason": plan.fallback_reason,
            "shuffle": plan.shuffle,
            "random_state": plan.random_state,
            "group_column": plan.group_column,
            "time_column": plan.time_column,
            "stratified": plan.stratified,
            "reason": plan.reason,
        }
    default_cv = "StratifiedKFold" if is_classification(task_type) else "KFold"
    return {
        "train_rows": split_meta.get("n_train"),
        "test_rows": split_meta.get("n_test"),
        "cv_strategy": sample.get("cv_strategy") or default_cv,
        "n_folds": sample.get("n_folds"),
        "requested_folds": sample.get("requested_folds", 5),
        "actual_folds": sample.get("actual_folds", sample.get("n_folds")),
        "adaptation_reason": sample.get("adaptation_reason"),
        "random_state": split_meta.get("random_state", seed),
    }


def _prediction_rows(
    y_true,
    y_score,
    *,
    classifier: bool,
    test: pd.DataFrame | None = None,
    entity_col: str | None = None,
    class_labels: list[Any] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[dict[str, Any]]:
    y_true_arr = np.asarray(y_true)
    y_score_arr = np.asarray(y_score, dtype=float)
    if classifier and class_labels:
        # Multiclass: report original labels and the winning class probability.
        codes = y_score_arr.argmax(axis=1)
        y_pred = np.asarray([class_labels[int(code)] for code in codes], dtype=object)
        y_true_arr = np.asarray([class_labels[int(code)] for code in y_true_arr], dtype=object)
        y_score_arr = y_score_arr.max(axis=1)
    else:
        y_pred = (y_score_arr >= threshold).astype(int) if classifier else y_score_arr
    rows: list[dict[str, Any]] = []
    for index in range(len(y_true_arr)):
        record_id = str(index)
        source_row_index = int(index)
        if test is not None and SOURCE_ROW_COLUMN in test.columns:
            source_row_index = int(test.iloc[index][SOURCE_ROW_COLUMN])
        if test is not None and entity_col and entity_col in test.columns:
            value = _json_safe(test.iloc[index][entity_col])
            if value is not None:
                record_id = str(value)
        item = {
            "row_index": int(index),
            "source_row_index": source_row_index,
            "record_id": record_id,
            "y_true": _json_safe(y_true_arr[index]),
            "y_pred": _json_safe(y_pred[index]),
        }
        if classifier:
            score = _json_safe(y_score_arr[index])
            item["score"] = score
            item["probability"] = score
        rows.append(item)
    return rows


def _row_pipeline(row: dict[str, Any], task: TaskSpec) -> Callable[[Mapping[str, Any] | None], SkPipeline]:
    """Factory for a fresh, unfitted copy of a trained candidate's full Pipeline."""

    num_cols = list(row["numerical_cols"])
    cat_cols = list(row["categorical_cols"])
    base_hyperparameters = {
        key: value for key, value in (row.get("hyperparameters") or {}).items() if key != "tuning"
    }

    def _pipeline(tuned: Mapping[str, Any] | None = None) -> SkPipeline:
        hyperparameters = dict(base_hyperparameters)
        if tuned:
            hyperparameters["tuned"] = dict(tuned)
        return SkPipeline(
            [
                ("prep", build_preprocessor(num_cols, cat_cols)),
                (
                    "model",
                    make_model(
                        row["model_family"],
                        seed=row["random_seed"],
                        hyperparameters=hyperparameters,
                        task_type=task.task_type,
                    ),
                ),
            ]
        )

    return _pipeline


def _winner_feature_importance(
    winner: dict[str, Any],
    pool: pd.DataFrame,
    y_pool: np.ndarray,
    fold_splits: list[Any],
    task: TaskSpec,
    *,
    classifier: bool,
    n_classes: int | None,
    selection_metric: str,
    threshold: float,
    deadline: float | None,
    on_event: RunEventCallback | None = None,
) -> dict[str, Any]:
    """Permutation importance of the locked winner on its CV validation folds.

    Only the training pool is passed: final-holdout rows are never scored here. Binary
    scoring uses the locked out-of-fold decision threshold, like the shipped model.
    """

    def _scorer(estimator, X_val, y_val) -> float:
        value = selection_metric_value(
            selection_metric,
            y_val,
            _predict(estimator, X_val, classifier, n_classes),
            task_type=task.task_type,
            n_classes=n_classes,
            threshold=threshold,
        )
        return -value if selection_metric in LOWER_IS_BETTER else value

    def _on_fold(payload: dict[str, Any]) -> None:
        _emit_event(
            on_event,
            "feature_importance_fold_completed",
            stage="feature_importance",
            status="completed",
            candidate_id=winner.get("candidate_id"),
            **payload,
        )

    try:
        tuned = {
            int(row["fold_number"]): (row.get("tuning") or {}).get("params")
            for row in winner.get("folds") or []
            if isinstance(row, dict) and row.get("fold_number") is not None
        }
        if set(tuned) != {int(split.fold_number) for split in fold_splits}:
            return {"status": "skipped", "method": "permutation_validation_folds", "features": [],
                    "reason": "the winner's stored folds do not match the validation folds"}
        folds = [
            ImportanceFold(
                fold_number=int(split.fold_number),
                train_index=np.asarray(split.train_index),
                validation_index=np.asarray(split.validation_index),
                params=tuned[int(split.fold_number)],
            )
            for split in fold_splits
        ]
        X_pool = pool.loc[:, list(winner["features"])]
    except Exception as exc:  # noqa: BLE001 - advisory; never fail the run
        logger.warning("feature importance skipped: %s", type(exc).__name__)
        return {"status": "skipped", "method": "permutation_validation_folds", "features": [],
                "reason": f"feature importance could not be prepared ({type(exc).__name__})"}
    return validation_fold_importance(
        _row_pipeline(winner, task),
        X_pool,
        y_pool,
        folds,
        _scorer,
        scoring=selection_metric,
        seed=int(winner.get("random_seed") or 0),
        stratify=classifier,
        deadline=deadline,
        on_fold=_on_fold,
    )


def _fit_and_score_holdout(
    row: dict[str, Any],
    pool: pd.DataFrame,
    test: pd.DataFrame,
    task: TaskSpec,
    classifier: bool,
    on_stage: Callable[[str], None] | None = None,
    on_event: RunEventCallback | None = None,
    n_classes: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    selection_metric: str | None = None,
    validation_plan: ValidationPlan | None = None,
) -> tuple[
    Any,
    dict[str, Any],
    dict[str, Any],
    np.ndarray,
    np.ndarray,
    int,
    dict[str, Any],
    dict[str, Any],
]:
    """Fit on the full training pool and score train + untouched test. Not used for ranking."""
    cols = list(row["features"])
    X_train = pool.loc[:, cols]
    y_train = pool[task.target].to_numpy()
    _pipeline = _row_pipeline(row, task)

    final_tuning: dict[str, Any] | None = None
    tuning_spec = (row.get("hyperparameters") or {}).get("tuning")
    if tuning_spec:
        # Re-tune on the full training pool (inner split of training rows only).
        def _score(params, X_fit, y_fit, X_val, y_val) -> float:
            pipe = _pipeline(params)
            pipe.fit(X_fit, y_fit)
            return primary_score(
                _metrics(y_val, _predict(pipe, X_val, classifier, n_classes), classifier=classifier, n_classes=n_classes),
                selection_metric or task.evaluation_metric,
                task.task_type,
            )

        groups, times = _tuning_axes(pool, validation_plan)
        final_plan = {**tuning_spec, "n_trials": int(row.get("tuning_trials_used") or tuning_spec["n_trials"])}
        final_tuning = tune(
            final_plan,
            X_train,
            y_train,
            classification=classifier,
            score=_score,
            groups=groups,
            time_values=times,
        )
        row["tuned_params"] = dict(final_tuning.get("params") or {})
    pipeline = _pipeline((final_tuning or {}).get("params"))
    if on_stage:
        on_stage(TRAINING)
    fit_started = datetime.now(UTC)
    fit_timer = time.perf_counter()
    _emit_event(
        on_event,
        "final_fit_started",
        stage="final_fit",
        status="started",
        candidate_id=row.get("candidate_id"),
        fit_row_count=int(len(X_train)),
    )
    pipeline.fit(X_train, y_train)
    train_pred = _predict(pipeline, X_train, classifier, n_classes)
    train_metrics = _metrics(
        y_train, train_pred, classifier=classifier, n_classes=n_classes, threshold=threshold
    )
    final_fit = _timing("final_fit", fit_started, fit_timer)
    final_fit.update(
        {
            "candidate_id": row.get("candidate_id"),
            "fit_row_count": int(len(X_train)),
            "fit_partition": "full_train",
            "tuning": final_tuning,
            "final_fit_started_at": final_fit["started_at"],
            "final_fit_completed_at": final_fit["ended_at"],
            "final_fit_duration_ms": final_fit["duration_ms"],
        }
    )
    _emit_event(
        on_event,
        "final_fit_completed",
        stage="final_fit",
        status="completed",
        candidate_id=row.get("candidate_id"),
        fit_row_count=int(len(X_train)),
        duration_ms=final_fit["duration_ms"],
    )
    X_test = test.loc[:, cols]
    y_test = test[task.target].to_numpy()
    if on_stage:
        on_stage(EVALUATING)
    test_started = datetime.now(UTC)
    test_timer = time.perf_counter()
    _emit_event(
        on_event,
        "final_test_started",
        stage="final_test",
        status="started",
        candidate_id=row.get("candidate_id"),
        test_row_count=int(len(X_test)),
    )
    test_pred = _predict(pipeline, X_test, classifier, n_classes)
    test_metrics = _metrics(
        y_test, test_pred, classifier=classifier, n_classes=n_classes, threshold=threshold
    )
    test_evaluation = _timing("final_test_evaluation", test_started, test_timer)
    test_evaluation.update(
        {
            "candidate_id": row.get("candidate_id"),
            "evaluation_count": 1,
            "test_row_count": int(len(X_test)),
            "metrics": test_metrics,
            "test_evaluation_started_at": test_evaluation["started_at"],
            "test_evaluation_completed_at": test_evaluation["ended_at"],
            "test_evaluation_duration_ms": test_evaluation["duration_ms"],
        }
    )
    _emit_event(
        on_event,
        "final_test_completed",
        stage="final_test",
        status="completed",
        candidate_id=row.get("candidate_id"),
        evaluation_count=1,
        test_row_count=int(len(X_test)),
        metrics=test_metrics,
        duration_ms=test_evaluation["duration_ms"],
    )
    return (
        pipeline,
        train_metrics,
        test_metrics,
        y_test,
        test_pred,
        int(len(X_test)),
        final_fit,
        test_evaluation,
    )


def _run_open_ingest_candidates(
    candidates: list[Candidate],
    train: pd.DataFrame,
    val: pd.DataFrame,
    test: pd.DataFrame,
    task: TaskSpec,
    *,
    artifact_dir: Path,
    members_dir: Path,
    validation_plan: ValidationPlan,
    primary_metric: str | None = None,
    on_stage: Callable[[str], None] | None = None,
    on_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    on_event: RunEventCallback | None = None,
    max_training_seconds: float | None = None,
    class_labels: list[Any] | None = None,
    objective: dict[str, Any] | None = None,
    outer_fold_assignment: Mapping[int, int] | None = None,
) -> dict[str, Any]:
    """ColumnTransformer + planned K-fold on train only; test is scored after the winner is locked.

    ``outer_fold_assignment`` (source row -> fold) is a stored SplitPlan's fold
    map: when given, the outer folds are taken from it instead of re-derived.
    """
    classifier = is_classification(task.task_type)
    # Multiclass labels arrive as codes 0..k-1 over the full label set.
    n_classes = len(class_labels) if task.task_type == "multiclass" and class_labels else None
    selection_metric = primary_metric or task.evaluation_metric
    # Val is empty for the 80/20 holdout path; never concatenate test.
    pool = pd.concat([train, val], ignore_index=True) if len(val) else train
    y_pool = pool[task.target].to_numpy()
    fold_splits = (
        folds_for_pool(validation_plan, pool, outer_fold_assignment)
        if outer_fold_assignment is not None
        else list(iter_validation_folds(validation_plan, pool, y_pool))
    )
    n_splits = len(fold_splits)
    funnel_updates = {"trained": 0, "failed": 0, "cache_hits": 0}
    records: list[dict[str, Any]] = []
    # Out-of-fold (validation-row) scores per candidate; the only evidence the
    # decision threshold may be tuned on. Never includes holdout rows.
    oof_scores: dict[str, tuple[list[np.ndarray], list[np.ndarray]]] = {}
    stage_timings: list[dict[str, Any]] = []

    if on_stage:
        on_stage(CROSS_VALIDATION)
    cv_started = datetime.now(UTC)
    cv_timer = time.perf_counter()

    # Baselines first (cheap) so a time budget can never drop the chance check.
    ordered = sorted(candidates, key=lambda c: c.model_family not in DUMMY_FAMILIES)
    budget_started = time.time()
    learned_trained = 0
    for candidate in ordered:
        t0 = time.time()
        if (
            max_training_seconds
            and learned_trained > 0
            and candidate.model_family not in DUMMY_FAMILIES
            and (t0 - budget_started) > max_training_seconds
        ):
            reason = f"skipped: training time budget of {max_training_seconds:.0f}s reached"
            records.append(_skipped_record(candidate, reason, validation_plan))
            funnel_updates["skipped"] = funnel_updates.get("skipped", 0) + 1
            _emit_event(
                on_event,
                "candidate_skipped",
                stage="candidate_training",
                status="skipped",
                candidate_id=candidate.candidate_id,
                model_family=candidate.model_family,
                reason=reason,
            )
            continue
        _emit_event(
            on_event,
            "candidate_started",
            stage="candidate_training",
            status="started",
            candidate_id=candidate.candidate_id,
            model_family=candidate.model_family,
        )
        try:
            cols = list(candidate.features)
            num_cols = [c for c in (task.column_roles or {}).get("numerical", []) if c in cols]
            cat_cols = [c for c in (task.column_roles or {}).get("categorical", []) if c in cols]
            if not num_cols and not cat_cols:
                raise ValueError("no numeric or categorical columns to model")
            if len(pool) < 10:
                raise ValueError("not enough training rows")

            X_train = pool.loc[:, cols]
            y_train = y_pool

            tuning_spec = (candidate.hyperparameters or {}).get("tuning")

            def _fresh_pipeline(tuned: dict[str, Any] | None = None) -> SkPipeline:
                hyperparameters = {
                    key: value
                    for key, value in (candidate.hyperparameters or {}).items()
                    if key != "tuning"
                }
                if tuned:
                    hyperparameters["tuned"] = dict(tuned)
                return SkPipeline(
                    [
                        ("prep", build_preprocessor(num_cols, cat_cols)),
                        (
                            "model",
                            make_model(
                                candidate.model_family,
                                seed=candidate.random_seed,
                                hyperparameters=hyperparameters,
                                task_type=task.task_type,
                            ),
                        ),
                    ]
                )

            def _tuning_score(params, X_fit, y_fit, X_val, y_val) -> float:
                pipe = _fresh_pipeline(params)
                pipe.fit(X_fit, y_fit)
                pred = _predict(pipe, X_val, classifier, n_classes)
                return primary_score(
                    _metrics(y_val, pred, classifier=classifier, n_classes=n_classes),
                    selection_metric,
                    task.task_type,
                )

            tuning_deadline = (
                budget_started + max_training_seconds if max_training_seconds else None
            )
            tuning_groups, tuning_times = _tuning_axes(pool, validation_plan)

            fold_metrics_list: list[dict[str, Any]] = []
            fold_scores: list[float] = []
            oof_index: list[np.ndarray] = []
            oof_pred: list[np.ndarray] = []
            fold_evidence: list[dict[str, Any]] = []
            for fold in fold_splits:
                fold_number = fold.fold_number
                fold_train_idx = fold.train_index
                fold_holdout_idx = fold.validation_index
                fold_started = datetime.now(UTC)
                fold_timer = time.perf_counter()
                _emit_event(
                    on_event,
                    "cv_fold_started",
                    stage="cross_validation",
                    status="started",
                    candidate_id=candidate.candidate_id,
                    fold_number=fold_number,
                    train_row_count=int(len(fold_train_idx)),
                    validation_row_count=int(len(fold_holdout_idx)),
                )
                fold_tuning: dict[str, Any] | None = None
                if tuning_spec:
                    # Nested: tune on this fold's training rows only.
                    fold_tuning = tune(
                        tuning_spec,
                        X_train.iloc[fold_train_idx],
                        y_train[fold_train_idx],
                        classification=classifier,
                        score=_tuning_score,
                        deadline=tuning_deadline,
                        groups=None if tuning_groups is None else tuning_groups[fold_train_idx],
                        time_values=None if tuning_times is None else tuning_times[fold_train_idx],
                    )
                    if not fold_tuning["trials_completed"]:
                        # Defaults in this fold would make the CV score describe a
                        # different procedure than the tuned model that would ship.
                        raise _CandidateSkipped(
                            f"skipped: training time budget of {max_training_seconds or 0:.0f}s "
                            f"left no tuning trial in fold {fold.fold_number}"
                        )
                fold_pipeline = _fresh_pipeline((fold_tuning or {}).get("params"))
                fold_pipeline.fit(X_train.iloc[fold_train_idx], y_train[fold_train_idx])
                fold_pred = _predict(
                    fold_pipeline, X_train.iloc[fold_holdout_idx], classifier, n_classes
                )
                fold_y = y_train[fold_holdout_idx]
                fold_metrics = _metrics(fold_y, fold_pred, classifier=classifier, n_classes=n_classes)
                oof_index.append(np.asarray(fold_holdout_idx))
                oof_pred.append(np.asarray(fold_pred))
                fold_metrics_list.append(fold_metrics)
                fold_scores.append(primary_score(fold_metrics, selection_metric, task.task_type))
                train_provenance = (
                    pool.iloc[fold_train_idx][SOURCE_ROW_COLUMN].astype(int).tolist()
                    if SOURCE_ROW_COLUMN in pool.columns
                    else [int(value) for value in fold_train_idx]
                )
                validation_provenance = (
                    pool.iloc[fold_holdout_idx][SOURCE_ROW_COLUMN].astype(int).tolist()
                    if SOURCE_ROW_COLUMN in pool.columns
                    else [int(value) for value in fold_holdout_idx]
                )
                group_col = validation_plan.group_column
                train_group_count = validation_group_count = None
                if group_col and group_col in pool.columns:
                    train_group_count = int(
                        pool.iloc[fold_train_idx][group_col].nunique(dropna=True)
                    )
                    validation_group_count = int(
                        pool.iloc[fold_holdout_idx][group_col].nunique(dropna=True)
                    )
                duration = max(0.001, (time.perf_counter() - fold_timer) * 1000.0)
                fold_evidence.append(
                    {
                        "strategy": validation_plan.strategy,
                        "fold": fold_number,
                        "fold_number": fold_number,
                        "train_count": fold.train_count,
                        "validation_count": fold.validation_count,
                        "train_row_count": fold.train_count,
                        "validation_row_count": fold.validation_count,
                        "train_group_count": train_group_count,
                        "validation_group_count": validation_group_count,
                        "group_overlap": list(fold.group_overlap),
                        "group_overlap_count": len(fold.group_overlap),
                        "train_time_min": fold.train_time_min,
                        "train_time_max": fold.train_time_max,
                        "validation_time_min": fold.validation_time_min,
                        "validation_time_max": fold.validation_time_max,
                        "metrics": fold_metrics,
                        "duration": duration,
                        "fit_duration_ms": duration,
                        "tuning": fold_tuning,
                        "train_provenance": train_provenance,
                        "validation_provenance": validation_provenance,
                        "started_at": fold_started.isoformat(),
                        "ended_at": datetime.now(UTC).isoformat(),
                    }
                )
                _emit_event(
                    on_event,
                    "cv_fold_completed",
                    stage="cross_validation",
                    status="completed",
                    candidate_id=candidate.candidate_id,
                    fold_number=fold_number,
                    train_row_count=int(len(fold_train_idx)),
                    validation_row_count=int(len(fold_holdout_idx)),
                    metrics=fold_metrics,
                    duration_ms=fold_evidence[-1]["fit_duration_ms"],
                )

            cv_mean, cv_std = aggregate_fold_metrics(fold_metrics_list)
            robust = robustness_stats(fold_scores)
            oof_scores[candidate.candidate_id] = (oof_index, oof_pred)
            tuning_summary: dict[str, Any] = {}
            if tuning_spec:
                completed = [fold["tuning"]["trials_completed"] for fold in fold_evidence]
                tuning_summary = {
                    # The final fit re-tunes with what every fold managed, so the
                    # shipped model follows the procedure CV evaluated.
                    "tuning_trials_used": int(min(completed)),
                    # Representative constructor values until a final fit re-tunes.
                    "tuned_params": dict(fold_evidence[0]["tuning"]["params"]),
                }
            records.append(
                {
                    **candidate.to_dict(),
                    "candidate": candidate.candidate_id,
                    "feature_set": list(candidate.features),
                    "preprocessing_config": {
                        "numerical": ["imputer:median", "scaler:standard"],
                        "categorical": ["imputer:most_frequent", "onehot:all_categories"],
                    },
                    "status": "trained",
                    "failure_reason": None,
                    "metrics": cv_mean,
                    "fold_metrics": fold_metrics_list,
                    "folds": fold_evidence,
                    "cv_mean": cv_mean,
                    "cv_std": cv_std,
                    "cv_score": robust,
                    "score": robust["mean"],
                    "robustness": robust,
                    "train_seconds": time.time() - t0,
                    "fit_duration_ms": max(0.001, (time.time() - t0) * 1000.0),
                    "stage": "trained",
                    "cv_strategy": validation_plan.strategy,
                    "requested_folds": validation_plan.requested_folds,
                    "actual_folds": n_splits,
                    "adaptation_reason": validation_plan.fallback_reason,
                    "n_folds": n_splits,
                    "n_train_rows": int(len(X_train)),
                    "numerical_cols": num_cols,
                    "categorical_cols": cat_cols,
                    "test_metrics": None,
                    **tuning_summary,
                }
            )
            funnel_updates["trained"] += 1
            if candidate.model_family not in DUMMY_FAMILIES:
                learned_trained += 1
            _emit_event(
                on_event,
                "candidate_completed",
                stage="candidate_training",
                status="completed",
                candidate_id=candidate.candidate_id,
                model_family=candidate.model_family,
                cv_score=robust["mean"],
                actual_folds=n_splits,
                duration_ms=max(0.001, (time.time() - t0) * 1000.0),
            )
        except _CandidateSkipped as exc:
            records.append(_skipped_record(candidate, str(exc), validation_plan))
            funnel_updates["skipped"] = funnel_updates.get("skipped", 0) + 1
            _emit_event(
                on_event,
                "candidate_skipped",
                stage="candidate_training",
                status="skipped",
                candidate_id=candidate.candidate_id,
                model_family=candidate.model_family,
                reason=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            funnel_updates["failed"] += 1
            logger.exception("open-ingest candidate %s failed", candidate.candidate_id)
            records.append(
                {
                    **candidate.to_dict(),
                    "candidate": candidate.candidate_id,
                    "feature_set": list(candidate.features),
                    "preprocessing_config": {
                        "numerical": ["imputer:median", "scaler:standard"],
                        "categorical": ["imputer:most_frequent", "onehot:all_categories"],
                    },
                    "status": "FAILED",
                    "error": str(exc),
                    "failure_reason": str(exc),
                    "metrics": None,
                    "fold_metrics": [],
                    "folds": [],
                    "cv_mean": None,
                    "cv_std": None,
                    "cv_strategy": validation_plan.strategy,
                    "requested_folds": validation_plan.requested_folds,
                    "actual_folds": None,
                    "adaptation_reason": validation_plan.fallback_reason,
                    "train_seconds": time.time() - t0,
                    "fit_duration_ms": max(0.001, (time.time() - t0) * 1000.0),
                    "test_metrics": None,
                }
            )
            _emit_event(
                on_event,
                "candidate_failed",
                stage="candidate_training",
                status="failed",
                candidate_id=candidate.candidate_id,
                model_family=candidate.model_family,
                reason=str(exc),
                duration_ms=max(0.001, (time.time() - t0) * 1000.0),
            )

    stage_timings.append(_timing("cross_validation", cv_started, cv_timer))
    stage_timings.append(
        {
            **stage_timings[-1],
            "stage": "candidate_training",
            "candidate_count": len(records),
        }
    )
    selection_started = datetime.now(UTC)
    selection_timer = time.perf_counter()
    trained = [row for row in records if row.get("status") == "trained"]
    learned = [row for row in trained if row.get("model_family") not in DUMMY_FAMILIES]
    # A chance-level dummy is a baseline, never a shippable winner: when no
    # learned model trains, the run fails instead of locking a dummy.
    pool_rows = learned
    best_single = max(pool_rows, key=lambda row: row["score"]) if pool_rows else None
    selected_ids = [best_single["candidate_id"]] if best_single else []
    baseline_row = next(
        (row for row in trained if row.get("model_family") in DUMMY_FAMILIES), None
    )
    baseline_comparison = None
    if baseline_row is not None and best_single is not None:
        margin = float(best_single["score"]) - float(baseline_row["score"])
        winner_std = (best_single.get("cv_std") or {}).get(selection_metric)
        winner_std = float(winner_std) if isinstance(winner_std, (int, float)) else 0.0
        baseline_comparison = {
            "metric": selection_metric,
            "baseline_candidate_id": baseline_row.get("candidate_id"),
            "baseline_cv_score": baseline_row["score"],
            "winner_candidate_id": best_single.get("candidate_id"),
            "winner_cv_score": best_single["score"],
            # Scores are oriented so larger is better (primary_score).
            "margin": margin,
            "beats_baseline": margin > 0,
            # Margin larger than the winner's fold-to-fold spread on that metric.
            "winner_cv_std": winner_std,
            "clear_margin": margin > winner_std,
        }
    funnel_updates["robust"] = len(pool_rows)
    funnel_updates["strong"] = len(pool_rows)
    funnel_updates["diverse"] = len(selected_ids)

    train_metrics: dict[str, Any] = {}
    test_metrics: dict[str, Any] = {}
    test_predictions: list[dict[str, Any]] = []
    selection = {
        "candidate_id": best_single.get("candidate_id") if best_single else None,
        "selected_candidate_id": best_single.get("candidate_id") if best_single else None,
        "selection_metric": selection_metric,
        "cv_score": best_single.get("score") if best_single else None,
        "selection_source": "cross_validation",
        "selection_policy": "maximum eligible primary CV score",
        "eligible_candidate_ids": [row.get("candidate_id") for row in pool_rows],
        "locked": best_single is not None,
        "locked_at": datetime.now(UTC).isoformat() if best_single else None,
    }
    stage_timings.append(_timing("model_selection", selection_started, selection_timer))
    _emit_event(
        on_event,
        "model_selection_completed",
        stage="model_selection",
        status="completed" if best_single is not None else "failed",
        selected_candidate_id=(best_single or {}).get("candidate_id"),
        eligible_candidate_ids=[row.get("candidate_id") for row in pool_rows],
        selection_metric=selection_metric,
        duration_ms=stage_timings[-1]["duration_ms"],
    )
    final_test_evaluation = {
        "candidate_id": best_single.get("candidate_id") if best_single else None,
        "evaluation_count": 0,
        "started_at": None,
        "ended_at": None,
        "duration_ms": None,
        "metrics": {},
    }

    parsed_objective = objective_from_dict(objective, task_type=task.task_type)
    decision_threshold: dict[str, Any] | None = None
    threshold = DEFAULT_THRESHOLD
    if best_single is not None:
        decision_threshold = _lock_decision_threshold(
            task.task_type,
            best_single,
            oof_scores.get(best_single["candidate_id"]),
            y_pool,
            pool,
            parsed_objective,
            primary_metric=selection_metric,
            last_fold_only=validation_plan.strategy == _TIME_SERIES_SPLIT,
        )
        if decision_threshold.get("value") is not None:
            threshold = float(decision_threshold["value"])
        selection["decision_threshold"] = decision_threshold.get("value")
        _emit_event(
            on_event,
            "decision_threshold_locked",
            stage="decision_threshold",
            status="completed",
            candidate_id=best_single.get("candidate_id"),
            threshold=decision_threshold.get("value"),
            source=decision_threshold.get("source"),
            constraint_status=decision_threshold.get("status"),
        )

    feature_importance: dict[str, Any] | None = None
    # Persist the CV-only selection checkpoint before the holdout is touched.
    if best_single is not None:
        best_single["locked"] = True
        if on_checkpoint:
            on_checkpoint({"selection": selection, "status": "SELECTION_LOCKED"})
        _emit_event(
            on_event,
            "winner_locked",
            stage="winner_lock",
            status="completed",
            candidate_id=best_single.get("candidate_id"),
            selection_metric=selection_metric,
            cv_score=best_single.get("score"),
        )
        # Drivers of the locked winner from its CV validation folds (pool rows only;
        # before the holdout is touched). Advisory: a skip never fails the run.
        importance_started = datetime.now(UTC)
        importance_timer = time.perf_counter()
        feature_importance = _winner_feature_importance(
            best_single,
            pool,
            y_pool,
            fold_splits,
            task,
            classifier=classifier,
            n_classes=n_classes,
            selection_metric=selection_metric,
            threshold=threshold,
            deadline=budget_started + max_training_seconds if max_training_seconds else None,
            on_event=on_event,
        )
        stage_timings.append(
            _timing("feature_importance", importance_started, importance_timer, status=feature_importance["status"])
        )
        (
            winner_pipeline,
            train_metrics,
            test_metrics,
            winner_y_test,
            winner_test_pred,
            n_test,
            final_fit,
            final_test_evaluation,
        ) = _fit_and_score_holdout(
            best_single,
            pool,
            test,
            task,
            classifier,
            on_stage=on_stage,
            on_event=on_event,
            n_classes=n_classes,
            threshold=threshold,
            selection_metric=selection_metric,
            validation_plan=validation_plan,
        )
        stage_timings.extend([final_fit, {key: value for key, value in final_test_evaluation.items() if key != "metrics"}])
        if decision_threshold is not None:
            test_metrics = _apply_holdout_constraints(decision_threshold, parsed_objective, test_metrics)
            final_test_evaluation["metrics"] = test_metrics
        best_single["train_metrics"] = train_metrics
        best_single["test_metrics"] = test_metrics
        best_single["n_test_rows"] = n_test
        if on_stage:
            on_stage(PREDICTING)
        prediction_started = datetime.now(UTC)
        prediction_timer = time.perf_counter()
        test_predictions = _prediction_rows(
            winner_y_test,
            winner_test_pred,
            classifier=classifier,
            test=test,
            entity_col=task.entity_id,
            class_labels=class_labels if n_classes else None,
            threshold=threshold,
        )
        stage_timings.append(_timing("prediction_persistence", prediction_started, prediction_timer))
        _emit_event(
            on_event,
            "predictions_persisted",
            stage="predictions",
            status="completed",
            candidate_id=best_single.get("candidate_id"),
            prediction_count=len(test_predictions),
            duration_ms=stage_timings[-1]["duration_ms"],
        )
        artifact_started = datetime.now(UTC)
        artifact_timer = time.perf_counter()
        joblib.dump(winner_pipeline, members_dir / f"{best_single['candidate_id']}.joblib")
        joblib.dump(
            {
                "fusion": None,
                "members": selected_ids,
                "weights": {},
                "task_id": task.id,
                # Scoring a positive needs probability >= decision_threshold (binary).
                "decision_threshold": (decision_threshold or {}).get("value"),
            },
            artifact_dir / "model.joblib",
        )
        pd.DataFrame(test_predictions).to_csv(artifact_dir / "test_predictions.csv", index=False)
        stage_timings.append(_timing("artifact_persistence", artifact_started, artifact_timer))
        _emit_event(
            on_event,
            "artifacts_persisted",
            stage="artifact_persistence",
            status="completed",
            candidate_id=best_single.get("candidate_id"),
            artifact_names=["model.joblib", "test_predictions.csv"],
            duration_ms=stage_timings[-1]["duration_ms"],
        )

    return {
        "funnel": funnel_updates,
        "records": records,
        "best_single": best_single,
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
        "test_predictions": test_predictions,
        "selected_ids": selected_ids,
        "selection": selection,
        "final_test_evaluation": final_test_evaluation,
        "final_fit": final_fit if best_single is not None else {},
        "stage_timings": stage_timings,
        "baseline_comparison": baseline_comparison,
        "decision_threshold": decision_threshold,
        "feature_importance": feature_importance,
    }


def _run_open_ingest_experiment(
    frame: pd.DataFrame,
    task: TaskSpec,
    config: SearchConfig,
    *,
    artifact_dir: Path,
    members_dir: Path,
    dataset_version: str,
    dataset_content_digest: str | None = None,
    started: float,
    on_stage: Callable[[str], None] | None,
    on_checkpoint: Callable[[dict[str, Any]], None] | None,
    on_event: RunEventCallback | None,
    outer_fold_assignment: Mapping[int, int] | None = None,
    holdout_partition: tuple[Collection[int], Collection[int]] | None = None,
) -> dict[str, Any]:
    """Leakage-safe open-ingest experiment with an early locked holdout."""
    profile = profile_frame(frame)
    quality = quality_report(frame, task.target)
    work = frame.copy()
    if SOURCE_ROW_COLUMN not in work.columns:
        work[SOURCE_ROW_COLUMN] = work.index.astype(int)
    if task.target not in work.columns:
        raise ValueError(f"Frame is missing target {task.target!r}")
    class_labels: list[Any] | None = None
    if task.task_type == "binary":
        work[task.target] = coerce_binary_target(work[task.target])
        work = work.dropna(subset=[task.target])
        work[task.target] = work[task.target].astype(int)
    elif task.task_type == "multiclass":
        work, class_labels = _encode_multiclass_target(work, task.target)
    else:
        work[task.target] = pd.to_numeric(work[task.target], errors="coerce")
        work = work.dropna(subset=[task.target])

    if on_stage:
        on_stage(SPLITTING)
    train, val, test, split_meta, holdout_plan, _holdout_source = _split_open_ingest_holdout(
        work, task, config, on_event, holdout_partition
    )
    (
        problem_profile,
        validation_plan,
        metric_plan,
        leakage,
        development_plan,
        plan_source,
    ) = _resolve_model_development_plan(
        _decoded_target(train, task.target, class_labels), task, config, on_event
    )
    if validation_plan.strategy == "unsupported" or not validation_plan.actual_folds:
        raise ValidationUnsupportedError(
            validation_plan.reason
            or validation_plan.fallback_reason
            or "Validation plan is unsupported."
        )
    if validation_plan.group_column and validation_plan.group_column not in train.columns:
        raise ValueError(
            f"group column {validation_plan.group_column!r} is missing from the training frame"
        )
    if validation_plan.time_column and validation_plan.time_column not in train.columns:
        raise ValueError(
            f"time column {validation_plan.time_column!r} is missing from the training frame"
        )
    task = _align_task_to_development_plan(
        task,
        frame=train,
        validation_plan=validation_plan,
        metric_plan=metric_plan,
        development_plan=development_plan,
    )
    train, val, test, fe_actions = _open_ingest_feature_actions(task, train, val, test)
    if fe_actions:
        feature_eng = dict(task.feature_engineering or {})
        if not feature_eng.get("feature_engineering_actions"):
            converted = [
                str(name)
                for action in fe_actions
                for name in (action.get("columns") or action.get("output_columns") or [])
            ]
            task = TaskSpec(
                **{
                    **task.to_dict(),
                    "feature_engineering": {
                        **feature_eng,
                        "feature_engineering_actions": list(fe_actions),
                        "transformed_features": list(
                            feature_eng.get("transformed_features") or converted
                        ),
                    },
                }
            )
    numerical_cols, categorical_cols, groups = _estimator_columns_from_plan(
        task, train, development_plan
    )
    modeled = numerical_cols + categorical_cols
    if not modeled:
        raise ValueError("no numeric or categorical columns to model")
    task = TaskSpec(
        **{
            **task.to_dict(),
            "feature_groups": groups,
            "column_roles": {"numerical": numerical_cols, "categorical": categorical_cols},
        }
    )

    candidates = assemble_candidates(
        task,
        config,
        dataset_version=dataset_version,
        dataset_content_digest=dataset_content_digest,
        holdout_plan=holdout_plan,
        development_plan=development_plan,
    )
    funnel = {
        "generated": len(candidates),
        "valid": len(candidates),
        "leakage_safe": len(modeled),
        "trained": 0,
        "robust": 0,
        "strong": 0,
        "diverse": 0,
        "failed": 0,
        "cache_hits": 0,
    }

    def _checkpoint(payload: dict[str, Any]) -> None:
        if on_checkpoint:
            on_checkpoint({**payload, "split": split_meta, "task": task.to_dict()})

    outcome = _run_open_ingest_candidates(
        candidates,
        train,
        val,
        test,
        task,
        artifact_dir=artifact_dir,
        members_dir=members_dir,
        validation_plan=validation_plan,
        primary_metric=metric_plan.primary_metric,
        on_stage=on_stage,
        on_checkpoint=_checkpoint,
        on_event=on_event,
        max_training_seconds=config.max_training_seconds,
        class_labels=class_labels,
        objective=config.objective,
        outer_fold_assignment=outer_fold_assignment,
    )
    funnel.update(outcome["funnel"])
    records = outcome["records"]
    best_single = outcome["best_single"]
    feature_report = dict(task.feature_engineering or {})
    original_features = list(feature_report.get("original_features") or modeled)
    feature_report = {
        "original_features": original_features,
        "generated_features": list(feature_report.get("generated_features") or []),
        "transformed_features": list(feature_report.get("transformed_features") or []),
        "removed_features": list(feature_report.get("removed_features") or []),
        "feature_engineering_actions": list(feature_report.get("feature_engineering_actions") or []),
        "transformations": list(feature_report.get("feature_engineering_actions") or []),
    }
    development_payload = development_plan.to_dict()
    holdout_payload = holdout_plan.to_dict()
    missing_plan = missing_plan_from_applied_imputers(train, numerical_cols, categorical_cols)
    leakage_exclusions = [
        row
        for row in list(development_plan.excluded_features or [])
        if isinstance(row, dict)
    ]
    scientific_evidence = _scientific_evidence_payload(
        missing_plan=missing_plan,
        leakage_exclusions=leakage_exclusions,
        feature_actions=list(feature_report.get("feature_engineering_actions") or fe_actions),
        numerical_cols=numerical_cols,
        categorical_cols=categorical_cols,
        dropped_columns=list(feature_report.get("removed_features") or []),
        fit_scope="fold_train",
    )
    result = {
        "task": task.to_dict(),
        "config": config.to_dict(),
        "status": ExperimentStatus.COMPLETED.value if best_single is not None else ExperimentStatus.FAILED.value,
        "funnel": funnel,
        "profile": profile,
        "profile_summary": {
            "row_count": profile["row_count"],
            "column_count": profile["column_count"],
            "duplicate_rows": profile.get("duplicate_rows", profile.get("duplicate_count")),
        },
        "quality": quality,
        "leakage": leakage or development_payload.get("leakage_assessment") or {},
        "split": split_meta,
        "holdout_plan": holdout_payload,
        "problem_profile": development_payload.get("problem_profile") or problem_profile.to_dict(),
        "validation_plan": development_payload.get("validation_plan") or validation_plan.to_dict(),
        "metric_plan": development_payload.get("metric_plan") or metric_plan.to_dict(),
        "model_development_plan": development_payload,
        "scientific_plan_source": plan_source,
        "validation": _open_ingest_validation(
            split_meta, records, config.seed, task.task_type, plan=validation_plan
        ),
        "feature_engineering": feature_report,
        "scientific_evidence": scientific_evidence,
        "preprocessing": {
            "numeric_columns": numerical_cols,
            "categorical_columns": categorical_cols,
            "numeric_imputer_strategy": "median",
            "numeric_scaler": "StandardScaler",
            "categorical_imputer_strategy": "most_frequent",
            "categorical_encoder": "OneHotEncoder",
            "categorical_encoder_drop": None,
            "handle_unknown": "ignore",
            "numerical": ["imputer:median", "scaler:standard"],
            "categorical": ["imputer:most_frequent", "onehot:all_categories"],
            "fit_scope": "cv_fold_train_only_then_full_training_partition",
            "fit_partition": "fold_train_only_then_full_train_for_locked_winner",
        },
        "candidates": records,
        "expected_candidate_ids": [candidate.candidate_id for candidate in candidates],
        "selected_ids": outcome["selected_ids"],
        "selection": outcome["selection"],
        "best_single": best_single,
        "fusion": None,
        "weights": {},
        "validation_blend_metrics": {},
        "train_metrics": outcome["train_metrics"],
        "test_metrics": outcome["test_metrics"],
        "final_test_evaluation": outcome["final_test_evaluation"],
        "final_fit": outcome["final_fit"],
        "test_predictions": outcome["test_predictions"],
        "execution_stage_timings": outcome["stage_timings"],
        "feature_group_scores": {},
        "combination_table": [],
        "artifact_dir": str(artifact_dir),
        "duration_seconds": time.time() - started,
        "baselines": [
            row
            for row in records
            if row.get("model_family") in DUMMY_FAMILIES | {"logistic_regression", "linear_regression"}
        ],
        "baseline_comparison": outcome.get("baseline_comparison"),
        # Multiclass label code i is class_labels[i]; None for binary/regression.
        "class_labels": class_labels,
        "objective": config.objective,
        "decision_threshold": outcome.get("decision_threshold"),
        "feature_importance": outcome.get("feature_importance"),
    }
    result = _json_safe(result)
    (artifact_dir / "result.json").write_text(json.dumps(result, default=str, indent=2) + "\n")
    from app.engine.reporting.report import render_markdown

    (artifact_dir / "report.md").write_text(render_markdown(result))
    return result


def run_experiment(
    frame: pd.DataFrame,
    task: TaskSpec,
    config: SearchConfig | None = None,
    *,
    artifact_dir: Path | None = None,
    dataset_version: str = "v1",
    dataset_content_digest: str | None = None,
    on_stage: Callable[[str], None] | None = None,
    on_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    on_event: RunEventCallback | None = None,
    holdout_plan: Any = None,
    model_development_plan: Any = None,
    outer_fold_assignment: Mapping[int, int] | None = None,
    holdout_partition: tuple[Collection[int], Collection[int]] | None = None,
) -> dict[str, Any]:
    """Train, filter, select, and report. Returns a JSON-serializable result dict.

    ``holdout_partition`` (holdout rows, train rows) and ``outer_fold_assignment``
    come from a stored SplitPlan (ADR 0006 §3); the run then applies them
    instead of re-deriving the holdout or the outer folds.
    """
    started = time.time()
    config = _overlay_scientific_plans(
        config or SearchConfig(),
        holdout_plan=holdout_plan,
        model_development_plan=model_development_plan,
    )
    artifact_dir = Path(artifact_dir or ".")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    members_dir = artifact_dir / "members"
    members_dir.mkdir(parents=True, exist_ok=True)

    # P1.2-A: one engine. Any legacy strategy ("progressive", "use_case") is
    # executed by the open-ingest pipeline (fold-local preprocessing, CV-only
    # selection, single holdout evaluation). The legacy branch was removed: it
    # profiled, audited and encoded the full frame before splitting.
    task, config = _normalize_to_open_ingest(frame, task, config)
    return _run_open_ingest_experiment(
        frame,
        task,
        config,
        artifact_dir=artifact_dir,
        members_dir=members_dir,
        dataset_version=dataset_version,
        dataset_content_digest=dataset_content_digest,
        started=started,
        on_stage=on_stage,
        on_checkpoint=on_checkpoint,
        on_event=on_event,
        outer_fold_assignment=outer_fold_assignment,
        holdout_partition=holdout_partition,
    )


def _normalize_to_open_ingest(
    frame: pd.DataFrame, task: TaskSpec, config: SearchConfig
) -> tuple[TaskSpec, SearchConfig]:
    """Map a legacy task/config onto the open-ingest contract.

    * Features are the task's declared feature groups (never other columns, so
      declared exclusions such as other labels stay excluded); without groups,
      every column except target, entity and prediction time.
    * Column roles are NOT fixed here: the runner infers them on the engineered
      train partition, so holdout rows never inform them.
    * A declared time-ordered task keeps a time-ordered final holdout: it is
      passed as an explicit ``temporal_future`` plan instead of letting the
      planner re-infer structure (which can fall back to a random split on
      few snapshot dates). Too few distinct times fails loudly.
    """
    if config.strategy == "open_ingest":
        return task, config
    excluded = {task.target, task.entity_id, task.prediction_time_column}
    declared = [
        column
        for columns in (task.feature_groups or {}).values()
        for column in columns
        if column in frame.columns and column not in excluded
    ]
    features = list(dict.fromkeys(declared)) or [c for c in frame.columns if c not in excluded]
    task = replace(task, column_roles={}, feature_groups={"features": features})
    holdout_plan = config.holdout_plan
    time_column = task.prediction_time_column
    if (
        holdout_plan is None
        and task.validation_strategy in {"time", "rolling"}
        and time_column
        and time_column in frame.columns
    ):
        distinct_times = int(frame[time_column].nunique(dropna=True))
        if distinct_times < 2:
            raise ValueError(
                f"task declares a time-ordered split on {time_column!r} but it has "
                f"{distinct_times} distinct value(s); refusing a random fallback"
            )
        holdout_plan = HoldoutPlan(
            strategy=TEMPORAL_FUTURE,
            test_size=0.2,
            random_state=config.seed,
            stratified=False,
            group_column=None,
            time_column=time_column,
            reason=(
                "The task declares a time-ordered split; the final holdout is the "
                "latest chronological slice."
            ),
            evidence={"declared_by": "task.validation_strategy", "distinct_times": distinct_times},
        ).to_dict()
    return task, replace(config, strategy="open_ingest", holdout_plan=holdout_plan)
