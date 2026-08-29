"""Assemble the admin ML-run visualization from persisted pipeline_log + Experiment.

No retraining, no LLM. Every field is copied from values already stored on the
upload or the linked Lab experiment.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.models import ClientLabUpload, Dataset, Experiment
from app.domain.admin_client_uploads import (
    AdminCleaningStep,
    AdminDataAnalysis,
    AdminFeatureEngineering,
    AdminFinalModel,
    AdminMlRun,
    AdminModelComparisonRow,
    AdminPredictions,
    AdminProcessingSummary,
    AdminValidation,
)
from app.domain.lab_run_stages import (
    CLEANING,
    COMPLETED,
    EVALUATING,
    FEATURE_ENGINEERING,
    PREDICTING,
    PREPROCESSING,
    TRAINING,
    stage_after,
)

_FAMILY_LABELS = {
    "logistic_regression": "Logistic Regression",
    "linear_regression": "Linear Regression",
    "random_forest": "Random Forest",
    "random_forest_regressor": "Random Forest",
    "xgboost": "XGBoost",
    "xgboost_regressor": "XGBoost",
    "lightgbm": "LightGBM",
    "lightgbm_regressor": "LightGBM",
    "gradient_boosting": "Gradient Boosting",
    "gradient_boosting_regressor": "Gradient Boosting",
}

_IMPUTE_ACTIONS = {
    "impute_median": ("Median imputation", "Filled at fit time by SimpleImputer(strategy='median')"),
    "impute_most_frequent": (
        "Most-frequent imputation",
        "Filled at fit time by SimpleImputer(strategy='most_frequent')",
    ),
    "drop_column": ("Dropped column", "Removed before modeling"),
    "domain_fill": ("Domain fill", "Filled with a fixed value"),
}

_FAMILY_ORDER = (
    "logistic_regression",
    "random_forest",
    "xgboost",
    "lightgbm",
    "linear_regression",
    "random_forest_regressor",
    "xgboost_regressor",
    "lightgbm_regressor",
)


def build_ml_run(
    upload: ClientLabUpload,
    experiment: Experiment | None,
    dataset: Dataset | None,
) -> AdminMlRun:
    log = upload.pipeline_log if isinstance(upload.pipeline_log, dict) else {}
    result = experiment.result if experiment is not None and isinstance(experiment.result, dict) else {}
    analysis_src = _analysis_source(log, result)
    target = _target_column(log, result)
    cleaning_log = _as_dict(log.get("cleaning") or result.get("cleaning"))
    fe_log = _as_dict(log.get("feature_engineering") or result.get("feature_engineering"))
    split = _as_dict(result.get("split"))
    validation_src = _as_dict(result.get("validation"))
    candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
    best = result.get("best_single") if isinstance(result.get("best_single"), dict) else {}
    predictions = result.get("test_predictions") if isinstance(result.get("test_predictions"), list) else []

    started_at = experiment.started_at if experiment is not None else None
    completed_at = experiment.ended_at if experiment is not None else None
    duration = _duration_seconds(result, started_at, completed_at)

    dataset_name = upload.original_filename
    if dataset is not None and dataset.name:
        dataset_name = dataset.name

    analysis = _build_analysis(analysis_src, log)
    validation = _build_validation(split, validation_src, candidates, result)
    final_model = _build_final_model(best, result)
    prediction_view = _build_predictions(predictions, experiment)
    return AdminMlRun(
        run_id=upload.id,
        dataset=dataset_name,
        dataset_id=(dataset.id if dataset is not None else upload.dataset_id),
        status=upload.pipeline_status,
        target=target,
        task_type=_task_type(result),
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration,
        analysis=analysis,
        processing_summary=_build_processing_summary(
            status=upload.pipeline_status,
            cleaning_log=cleaning_log,
            fe_log=fe_log,
            validation=validation,
            candidates=candidates,
            final_model=final_model,
            predictions=prediction_view,
            result=result,
        ),
        cleaning=cleaning_steps(cleaning_log, _as_dict(log.get("missing_value_decisions"))),
        feature_engineering=_build_feature_engineering(analysis_src, cleaning_log, fe_log, log, target),
        validation=validation,
        model_comparison=_build_comparison(candidates, best),
        final_model=final_model,
        predictions=prediction_view,
    )


def cleaning_steps(
    cleaning_log: dict[str, Any],
    missing_decisions: dict[str, Any] | None = None,
) -> list[AdminCleaningStep]:
    """Flatten every deterministic transform into Column / Problem / Action / Result."""
    rows: list[AdminCleaningStep] = []
    seen_drops: set[str] = set()

    for step in cleaning_log.get("transformations") or []:
        if not isinstance(step, dict):
            continue
        name = str(step.get("step") or "")
        if name == "replace_infinite":
            n = int(step.get("cells_cleared") or 0)
            rows.append(
                AdminCleaningStep(
                    column="—",
                    problem=f"{n} infinite values",
                    action="Replace with missing",
                    result=f"{n} cells cleared",
                )
            )
        elif name == "replace_invalid_strings":
            n = int(step.get("cells_cleared") or 0)
            rows.append(
                AdminCleaningStep(
                    column="—",
                    problem=f"{n} invalid string sentinels",
                    action="Replace with missing",
                    result=f"{n} cells cleared",
                )
            )
        elif name == "coerce_numeric":
            for column in step.get("columns") or []:
                rows.append(
                    AdminCleaningStep(
                        column=str(column),
                        problem="Stored as text",
                        action="Coerce to numeric",
                        result="Converted to float",
                    )
                )
        elif name == "drop_duplicate_rows":
            n = int(step.get("rows_removed") or 0)
            rows.append(
                AdminCleaningStep(
                    column="—",
                    problem=f"{n} duplicate rows",
                    action="Drop duplicates",
                    result=f"{n} rows removed",
                )
            )
        elif name == "drop_missing_target_rows":
            n = int(step.get("rows_removed") or 0)
            rows.append(
                AdminCleaningStep(
                    column="—",
                    problem=f"{n} rows missing the target",
                    action="Drop rows",
                    result=f"{n} rows removed",
                )
            )
        elif name == "drop_high_missing_columns":
            for column in step.get("columns") or []:
                seen_drops.add(str(column))
                rows.append(
                    AdminCleaningStep(
                        column=str(column),
                        problem="More than 50% missing",
                        action="Dropped column",
                        result="Removed before modeling",
                    )
                )
        elif name == "drop_constant_columns":
            for column in step.get("columns") or []:
                seen_drops.add(str(column))
                rows.append(
                    AdminCleaningStep(
                        column=str(column),
                        problem="Constant column",
                        action="Dropped column",
                        result="Removed before modeling",
                    )
                )

    plan = _as_dict(cleaning_log.get("missing_value_plan"))
    if not plan and missing_decisions:
        plan = missing_decisions
    extra = _as_dict(missing_decisions) if missing_decisions else {}
    decisions = plan.get("column_decisions") or extra.get("column_decisions") or []
    for item in decisions:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column") or "")
        action = str(item.get("action") or "")
        if not column or action in {"", "keep"}:
            continue
        if action == "drop_column" and column in seen_drops:
            continue
        missing_count = int(item.get("missing_count") or 0)
        labels = _IMPUTE_ACTIONS.get(action)
        if labels is None:
            action_label, result = action.replace("_", " "), "Applied"
        else:
            action_label, result = labels
        if action == "domain_fill" and item.get("fill_value") is not None:
            result = f"Filled with {item['fill_value']!r}"
        problem = f"{missing_count} missing values" if missing_count else "Missing values"
        rows.append(
            AdminCleaningStep(column=column, problem=problem, action=action_label, result=result)
        )
    return rows


def predictions_csv_text(experiment: Experiment) -> str | None:
    """The generated holdout prediction dataset (y_true, y_pred, score)."""
    if experiment.artifact_dir:
        path = Path(experiment.artifact_dir) / "test_predictions.csv"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    result = experiment.result if isinstance(experiment.result, dict) else {}
    rows = result.get("test_predictions")
    if not isinstance(rows, list) or not rows:
        return None
    fieldnames: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(str(key))
    if not fieldnames:
        return None
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        if isinstance(row, dict):
            writer.writerow({key: row.get(key) for key in fieldnames})
    return buf.getvalue()


def _analysis_source(log: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    for key in ("analysis", "profile"):
        blob = log.get(key) or result.get(key)
        if isinstance(blob, dict) and blob:
            return blob
    return _as_dict(log.get("eda"))


def _task_type(result: dict[str, Any]) -> str | None:
    task = result.get("task")
    if isinstance(task, dict) and task.get("task_type"):
        return str(task["task_type"])
    return None


def _build_processing_summary(
    *,
    status: str,
    cleaning_log: dict[str, Any],
    fe_log: dict[str, Any],
    validation: AdminValidation,
    candidates: list[Any],
    final_model: AdminFinalModel | None,
    predictions: AdminPredictions,
    result: dict[str, Any],
) -> AdminProcessingSummary:
    trained = any(isinstance(row, dict) and row.get("status") == "trained" for row in candidates)
    test_metrics = result.get("test_metrics") if isinstance(result.get("test_metrics"), dict) else {}
    if not test_metrics and final_model is not None:
        test_metrics = final_model.test_metrics
    split_text = None
    if validation.train_rows is not None and validation.test_rows is not None:
        split_text = f"{validation.train_rows} / {validation.test_rows}"
    cv_text = None
    if validation.cv_strategy:
        parts = [validation.cv_strategy]
        if validation.n_folds is not None:
            parts.append(f"{validation.n_folds} folds")
        cv_text = " · ".join(parts)
    done = status == COMPLETED
    return AdminProcessingSummary(
        cleaning_completed=done or bool(cleaning_log) or stage_after(status, CLEANING),
        feature_engineering_completed=done or bool(fe_log) or stage_after(status, FEATURE_ENGINEERING),
        preprocessing_completed=done
        or validation.train_rows is not None
        or stage_after(status, PREPROCESSING),
        train_test_split=split_text,
        cross_validation=cv_text,
        training_completed=done or trained or stage_after(status, TRAINING),
        evaluation_completed=done or bool(test_metrics) or stage_after(status, EVALUATING),
        predictions_completed=done or predictions.count > 0 or stage_after(status, PREDICTING),
    )


def _target_column(log: dict[str, Any], result: dict[str, Any]) -> str | None:
    target = log.get("target")
    if isinstance(target, dict) and target.get("column"):
        return str(target["column"])
    task = result.get("task")
    if isinstance(task, dict) and task.get("target"):
        return str(task["target"])
    return None


def _build_analysis(analysis: dict[str, Any], log: dict[str, Any]) -> AdminDataAnalysis:
    numerical = _string_list(analysis.get("numerical_statistics"))
    categorical = _string_list(analysis.get("categorical_statistics"))
    if not numerical:
        numerical = [str(name) for name in (log.get("numerical_cols") or [])]
    if not categorical:
        categorical = [str(name) for name in (log.get("categorical_cols") or [])]
    missing = analysis.get("missing_count")
    if missing is None:
        missing = analysis.get("missing_values")
    duplicates = analysis.get("duplicate_rows")
    if duplicates is None:
        duplicates = analysis.get("duplicate_count")
    return AdminDataAnalysis(
        rows=_int_or_none(analysis.get("row_count")),
        columns=_int_or_none(analysis.get("column_count")),
        numerical_columns=numerical,
        categorical_columns=categorical,
        missing_values=_int_or_none(missing),
        duplicates=_int_or_none(duplicates),
        constant_columns=[str(name) for name in (analysis.get("constant_columns") or [])],
        high_cardinality_columns=[str(name) for name in (analysis.get("high_cardinality_columns") or [])],
    )


def _build_feature_engineering(
    analysis: dict[str, Any],
    cleaning_log: dict[str, Any],
    fe_log: dict[str, Any],
    log: dict[str, Any],
    target: str | None,
) -> AdminFeatureEngineering:
    columns_in = [str(name) for name in (cleaning_log.get("columns_in") or analysis.get("column_names") or [])]
    original = [name for name in columns_in if name != target]
    if not original:
        original = [str(name) for name in (log.get("fields_noticed") or []) if name != target]
    numerical = [str(name) for name in (fe_log.get("numerical_cols") or log.get("numerical_cols") or [])]
    categorical = [str(name) for name in (fe_log.get("categorical_cols") or log.get("categorical_cols") or [])]
    modeled = set(numerical + categorical)
    removed = [name for name in original if name not in modeled]
    transformations = [item for item in (fe_log.get("transformations") or []) if isinstance(item, dict)]
    generated: list[str] = []
    for item in transformations:
        for column in item.get("columns") or []:
            generated.append(str(column))
    return AdminFeatureEngineering(
        original_features=original,
        generated_features=list(dict.fromkeys(generated)),
        removed_features=removed,
        transformations=transformations,
    )


def _build_validation(
    split: dict[str, Any],
    validation: dict[str, Any],
    candidates: list[Any],
    result: dict[str, Any],
) -> AdminValidation:
    trained = [row for row in candidates if isinstance(row, dict) and row.get("status") == "trained"]
    sample = trained[0] if trained else {}
    n_folds = validation.get("n_folds") or sample.get("n_folds")
    cv_strategy = validation.get("cv_strategy") or sample.get("cv_strategy")
    if not cv_strategy:
        task = result.get("task") if isinstance(result.get("task"), dict) else {}
        cv_strategy = "StratifiedKFold" if task.get("task_type") == "binary" else "KFold"
    random_state = validation.get("random_state")
    if random_state is None:
        random_state = split.get("random_state")
    if random_state is None:
        config = result.get("config") if isinstance(result.get("config"), dict) else {}
        random_state = config.get("seed")
    return AdminValidation(
        train_rows=_int_or_none(validation.get("train_rows") if validation.get("train_rows") is not None else split.get("n_train")),
        test_rows=_int_or_none(validation.get("test_rows") if validation.get("test_rows") is not None else split.get("n_test")),
        cv_strategy=str(cv_strategy) if cv_strategy else None,
        n_folds=_int_or_none(n_folds),
        random_state=_int_or_none(random_state),
    )


def _build_comparison(candidates: list[Any], best: dict[str, Any]) -> list[AdminModelComparisonRow]:
    winner_id = best.get("candidate_id")
    rows: list[AdminModelComparisonRow] = []
    for item in candidates:
        if not isinstance(item, dict) or not item.get("model_family"):
            continue
        family = str(item["model_family"])
        cv_metrics = item.get("cv_mean") if isinstance(item.get("cv_mean"), dict) else {}
        test_metrics = item.get("test_metrics") if isinstance(item.get("test_metrics"), dict) else None
        rows.append(
            AdminModelComparisonRow(
                name=_FAMILY_LABELS.get(family, family.replace("_", " ").title()),
                model_family=family,
                cv_auc=_metric_float(cv_metrics, "roc_auc"),
                test_auc=_metric_float(test_metrics, "roc_auc") if test_metrics else None,
                cv_metrics=cv_metrics,
                test_metrics=test_metrics,
                selected=bool(item.get("locked")) or item.get("candidate_id") == winner_id,
                status=str(item.get("status") or ""),
            )
        )
    rows.sort(key=lambda row: _family_sort_key(row.model_family))
    return rows


def _build_final_model(best: dict[str, Any], result: dict[str, Any]) -> AdminFinalModel | None:
    family = best.get("model_family")
    if not family:
        return None
    family_s = str(family)
    cv_metrics = best.get("cv_mean") if isinstance(best.get("cv_mean"), dict) else {}
    test_metrics = best.get("test_metrics") if isinstance(best.get("test_metrics"), dict) else None
    if not test_metrics and isinstance(result.get("test_metrics"), dict):
        test_metrics = result["test_metrics"]
    return AdminFinalModel(
        selected_model=_FAMILY_LABELS.get(family_s, family_s.replace("_", " ").title()),
        model_family=family_s,
        cv_metrics=cv_metrics,
        test_metrics=test_metrics or {},
    )


def _build_predictions(predictions: list[Any], experiment: Experiment | None) -> AdminPredictions:
    labels: list[str] = []
    for row in predictions:
        if isinstance(row, dict) and "y_pred" in row:
            labels.append(str(row["y_pred"]))
    download_available = False
    if experiment is not None:
        if predictions:
            download_available = True
        elif experiment.artifact_dir:
            path = Path(experiment.artifact_dir) / "test_predictions.csv"
            if path.is_file():
                download_available = True
                if not labels:
                    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8")))
                    for item in reader:
                        if "y_pred" in item:
                            labels.append(str(item["y_pred"]))
                    predictions = [{"y_pred": value} for value in labels]
    return AdminPredictions(
        count=len(predictions) if predictions else len(labels),
        distribution=dict(Counter(labels)),
        download_available=download_available,
    )


def _duration_seconds(
    result: dict[str, Any],
    started_at: datetime | None,
    completed_at: datetime | None,
) -> float | None:
    stored = result.get("duration_seconds")
    if isinstance(stored, (int, float)):
        return float(stored)
    if started_at is None or completed_at is None:
        return None
    start = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
    end = completed_at if completed_at.tzinfo else completed_at.replace(tzinfo=timezone.utc)
    return max(0.0, (end - start).total_seconds())


def _family_sort_key(family: str) -> tuple[int, str]:
    try:
        return (_FAMILY_ORDER.index(family), family)
    except ValueError:
        return (len(_FAMILY_ORDER), family)


def _metric_float(metrics: dict[str, Any] | None, key: str) -> float | None:
    if not isinstance(metrics, dict):
        return None
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []
