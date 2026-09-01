"""Translate an open-ingest auto-train Experiment into client-safe insights.

Mirrors `_translate_result` in `client_lab_service` and `_translate_run` in
`insight_query`: those take a raw engine payload and return only
`ClientFacingInsight` objects. This module does the same for a Labs custom-box
upload whose background job persisted a real Lab `Experiment`.

The admin-only failure reason, target column, model family, and metric names
never leave this module as client-visible copy — every string is scanned with
`find_banned_terms` before it is returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import ClientLabUpload, Dataset, Experiment, ExperimentTestPrediction
from app.domain.client_lab import ClientLabPredictionRow, ClientLabRunOutcome
from app.domain.lab_use_cases import LAB_USE_CASES
from app.engine.lab.column_map import normalize
from app.domain.lab_run_stages import IN_PROGRESS_STAGES
from app.translation.bands import probability_to_band
from app.translation.banned_terms import find_banned_terms, is_clean
from app.translation.models import ClientFacingInsight, InsightCategory

LOOKING_STATUS = "Analyzing your data..."
FAILED_STATUS = "We received your file, but nothing usable was found."
READY_STATUS = "We've looked at your file."
COMPLETE_TITLE = "Analysis complete"

_METHOD_LABELS = {
    "logistic_regression": "Linear",
    "linear_regression": "Linear",
    "random_forest": "Trees",
    "random_forest_regressor": "Trees",
    "xgboost": "Boosted trees",
    "xgboost_regressor": "Boosted trees",
    "lightgbm": "Boosted trees",
    "lightgbm_regressor": "Boosted trees",
    "gradient_boosting": "Boosted trees",
    "gradient_boosting_regressor": "Boosted trees",
}
_FALLBACK_METHOD = "Patterns"

_POSITIVE_LABEL = {
    "churn": "Likely to leave",
    "conversion": "Likely to convert",
    "lead_conversion": "Likely to convert",
    "purchase": "Likely to buy",
    "customer_value": "Higher value",
}
_NEGATIVE_LABEL = {
    "churn": "Likely to stay",
    "conversion": "Less likely to convert",
    "lead_conversion": "Less likely to convert",
    "purchase": "Less likely to buy",
    "customer_value": "Lower value",
}

# Business phrasing for a known label — never the raw column name.
_OUTCOME_BY_SLUG = {
    "churn": "who is likely to leave",
    "conversion": "who is likely to convert",
    "lead_conversion": "who is likely to convert",
    "purchase": "who is likely to buy",
    "customer_value": "which relationships are worth more",
}
_HEADLINE_BY_SLUG = {
    "churn": "Who may leave",
    "conversion": "Who may convert",
    "lead_conversion": "Who may convert",
    "purchase": "Who may buy",
    "customer_value": "Where value is concentrated",
}
_FALLBACK_OUTCOME = "the outcome in your file"
_FALLBACK_HEADLINE = "What this file points to"


@dataclass(frozen=True)
class UploadInsights:
    insights: list[ClientFacingInsight]
    status: str


def insights_for_upload(db: Session, upload: ClientLabUpload) -> UploadInsights:
    """Client-safe view of an open-ingest auto-train job.

    Takes the upload row (and a session, so the linked Experiment can be loaded).
    """
    status = upload.pipeline_status
    if status in IN_PROGRESS_STAGES:
        return UploadInsights(insights=[], status=_safe_status(LOOKING_STATUS))
    if status in {"failed", "skipped"}:
        return UploadInsights(insights=[], status=_safe_status(FAILED_STATUS))
    if status != "completed":
        return UploadInsights(insights=[], status=_safe_status(FAILED_STATUS))

    insights = _translate_completed(db, upload)
    if not insights:
        return UploadInsights(insights=[], status=_safe_status(FAILED_STATUS))
    return UploadInsights(insights=insights, status=_safe_status(COMPLETE_TITLE))


def _safe_status(text: str) -> str:
    if is_clean(text):
        return text
    return FAILED_STATUS if is_clean(FAILED_STATUS) else "We received your file."


def _translate_completed(db: Session, upload: ClientLabUpload) -> list[ClientFacingInsight]:
    if upload.experiment_id is None:
        return []
    experiment = db.get(Experiment, upload.experiment_id)
    if experiment is None or not experiment.result:
        return []

    result = experiment.result
    best = result.get("best_single") if isinstance(result.get("best_single"), dict) else None
    if not best:
        return []

    score = _holdout_primary_score(result, best)
    if score is None:
        return []

    target_column = _target_column(upload, result)
    slug = _slug_for_target(target_column)
    outcome = _OUTCOME_BY_SLUG.get(slug or "", _FALLBACK_OUTCOME)
    headline = _HEADLINE_BY_SLUG.get(slug or "", _FALLBACK_HEADLINE)
    percent = _as_percent(score)
    generated_at = experiment.ended_at or experiment.created_at or datetime.now(UTC)
    category = _category(upload)

    quality = ClientFacingInsight(
        subject_id=str(upload.id),
        category=category,
        headline=headline,
        confidence_band=probability_to_band(score),
        recommended_action="Review the people this points to",
        expected_value=0.0,
        reasoning=[
            f"We can tell {outcome} for your data with an estimated accuracy of {percent} percent.",
            "This is based on the file you sent, not on a template.",
        ],
        generated_at=generated_at,
    )
    next_step = ClientFacingInsight(
        subject_id=f"{upload.id}:next",
        category=category,
        headline="Where to start",
        confidence_band=probability_to_band(score),
        recommended_action="Start with the people this file highlights",
        expected_value=0.0,
        reasoning=[
            "We compared several ways of reading your file and kept the strongest one.",
            "Use this as a starting list, then apply your own judgment.",
        ],
        generated_at=generated_at,
    )
    insights = [item for item in (quality, next_step) if _insight_is_clean(item)]
    return insights[:2]


def _holdout_metrics(result: dict[str, Any], best: dict[str, Any]) -> dict[str, Any]:
    """Test-set metrics only. CV fold scores live on best['metrics'] / best['score']."""
    metrics: dict[str, Any] = {}
    if isinstance(result.get("test_metrics"), dict):
        metrics.update(result["test_metrics"])
    if isinstance(best.get("test_metrics"), dict):
        metrics.update(best["test_metrics"])
    return metrics


def _holdout_primary_score(result: dict[str, Any], best: dict[str, Any]) -> float | None:
    """Selected model's primary metric on the held-out test set — never CV."""
    metrics = _holdout_metrics(result, best)
    if not metrics:
        return None
    task = result.get("task") if isinstance(result.get("task"), dict) else {}
    name = task.get("evaluation_metric")
    if isinstance(name, str):
        raw = metrics.get(name)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return _unit_interval(float(raw))
    for key in ("roc_auc", "pr_auc", "accuracy", "r2"):
        raw = metrics.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return _unit_interval(float(raw))
    return None


def _unit_interval(value: float) -> float:
    if value > 1.0:
        return max(0.0, min(value / 100.0, 1.0))
    return max(0.0, min(value, 1.0))


def _as_percent(score: float) -> int:
    return int(round(_unit_interval(score) * 100))


def _target_column(upload: ClientLabUpload, result: dict[str, Any]) -> str | None:
    log = upload.pipeline_log if isinstance(upload.pipeline_log, dict) else {}
    target = log.get("target") if isinstance(log.get("target"), dict) else {}
    column = target.get("column")
    if isinstance(column, str) and column.strip():
        return column
    task = result.get("task") if isinstance(result.get("task"), dict) else {}
    raw = task.get("target")
    if isinstance(raw, str) and raw.strip():
        return raw
    return None


def _slug_for_target(column: str | None) -> str | None:
    if not column:
        return None
    key = normalize(column)
    for definition in LAB_USE_CASES:
        for alias in definition.target_aliases:
            if normalize(alias) == key:
                return definition.slug
    return None


def _category(upload: ClientLabUpload) -> InsightCategory:
    try:
        return InsightCategory(upload.category)
    except ValueError:
        return InsightCategory.CUSTOM


def _insight_is_clean(insight: ClientFacingInsight) -> bool:
    blob = insight.model_dump_json()
    return not find_banned_terms(blob)


def outcome_for_upload(
    db: Session,
    upload: ClientLabUpload,
    *,
    include_predictions: bool = False,
) -> ClientLabRunOutcome | None:
    """Client-safe completed-run summary from the linked Experiment, or None."""
    if upload.pipeline_status != "completed" or upload.experiment_id is None:
        return None
    experiment = db.get(Experiment, upload.experiment_id)
    if experiment is None or not isinstance(experiment.result, dict):
        return None
    result = experiment.result
    best = result.get("best_single") if isinstance(result.get("best_single"), dict) else None
    if not best:
        return None
    score = _holdout_primary_score(result, best)
    if score is None:
        return None

    target_column = _target_column(upload, result)
    slug = _slug_for_target(target_column)
    target_label = _OUTCOME_BY_SLUG.get(slug or "", _FALLBACK_OUTCOME)
    task_spec = result.get("task") if isinstance(result.get("task"), dict) else {}
    task_kind = "classification" if task_spec.get("task_type", "binary") == "binary" else "regression"
    method_label = _METHOD_LABELS.get(str(best.get("model_family") or ""), _FALLBACK_METHOD)
    percent = _as_percent_tenths(score)
    record_count = _record_count(upload, result)
    feature_count = _feature_count(upload, result, best)
    dataset_name = _dataset_name(db, upload)
    prediction_count, raw_predictions = _persisted_predictions(db, experiment, result)
    rows: list[ClientLabPredictionRow] = []
    if include_predictions:
        rows = _prediction_rows(raw_predictions, slug, task_kind)

    title = COMPLETE_TITLE
    safe_target = _clean_text(target_label, _FALLBACK_OUTCOME)
    records_line = f"We analyzed {record_count:,} records."
    target_line = f"This run was set up to tell {safe_target}."
    summary = f"We analyzed your dataset to tell {safe_target}."
    performance_summary = f"{percent}% on new records from your file."
    outcome = ClientLabRunOutcome(
        dataset_name=_clean_text(dataset_name, "dataset"),
        record_count=record_count,
        feature_count=feature_count,
        target_label=safe_target,
        task_kind=_clean_text(task_kind, "classification"),
        method_label=_clean_text(method_label, _FALLBACK_METHOD),
        performance_percent=percent,
        performance_summary=_clean_text(performance_summary, f"{percent}% on new records."),
        prediction_count=prediction_count,
        title=_clean_text(title, COMPLETE_TITLE),
        summary=_clean_text(summary, f"We analyzed your dataset to tell {safe_target}."),
        records_line=_clean_text(records_line, f"We analyzed {record_count} records."),
        target_line=_clean_text(target_line, "This run was set up to tell the outcome in your file."),
        predictions=rows,
        download_available=prediction_count > 0,
    )
    blob = outcome.model_dump_json()
    if find_banned_terms(blob):
        return None
    return outcome


def predictions_csv_text(outcome: ClientLabRunOutcome) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["record", "prediction", "probability"])
    for row in outcome.predictions:
        writer.writerow(
            [
                row.record_id,
                row.prediction,
                "" if row.probability is None else f"{row.probability:.6f}",
            ]
        )
    return buffer.getvalue()


def _as_percent_tenths(score: float) -> float:
    return round(_unit_interval(score) * 1000) / 10


def _dataset_name(db: Session, upload: ClientLabUpload) -> str:
    if upload.dataset_id is not None:
        dataset = db.get(Dataset, upload.dataset_id)
        if dataset is not None and dataset.name:
            return str(dataset.name)
    return Path(upload.original_filename or "dataset").stem or "dataset"


def _persisted_predictions(
    db: Session,
    experiment: Experiment,
    result: dict[str, Any],
) -> tuple[int, list[Any]]:
    stored = (
        db.query(ExperimentTestPrediction)
        .filter(ExperimentTestPrediction.experiment_id == experiment.id)
        .order_by(ExperimentTestPrediction.row_index)
        .all()
    )
    if stored:
        raw = [
            {
                "record_id": row.record_id,
                "row_index": row.row_index,
                "y_pred": row.predicted_value,
                "score": row.probability,
                "probability": row.probability,
            }
            for row in stored
        ]
        return len(stored), raw
    json_rows = result.get("test_predictions") if isinstance(result.get("test_predictions"), list) else []
    if json_rows:
        return len(json_rows), json_rows
    split = result.get("split") if isinstance(result.get("split"), dict) else {}
    n_test = split.get("n_test")
    return (int(n_test) if isinstance(n_test, int) else 0), []


def _clean_text(text: str, fallback: str) -> str:
    if is_clean(text):
        return text
    if is_clean(fallback):
        return fallback
    return ""


def _record_count(upload: ClientLabUpload, result: dict[str, Any]) -> int:
    for key in ("analysis", "profile", "profile_summary"):
        block = result.get(key)
        if isinstance(block, dict) and isinstance(block.get("row_count"), int):
            return int(block["row_count"])
    log = upload.pipeline_log if isinstance(upload.pipeline_log, dict) else {}
    eda = log.get("eda") if isinstance(log.get("eda"), dict) else {}
    if isinstance(eda.get("row_count"), int):
        return int(eda["row_count"])
    return int(upload.record_count or 0)


def _feature_count(upload: ClientLabUpload, result: dict[str, Any], best: dict[str, Any]) -> int:
    roles = result.get("task") if isinstance(result.get("task"), dict) else {}
    column_roles = roles.get("column_roles") if isinstance(roles.get("column_roles"), dict) else {}
    numerical = column_roles.get("numerical") if isinstance(column_roles.get("numerical"), list) else []
    categorical = column_roles.get("categorical") if isinstance(column_roles.get("categorical"), list) else []
    if numerical or categorical:
        return len(numerical) + len(categorical)
    feats = best.get("features")
    if isinstance(feats, list) and feats:
        return len(feats)
    log = upload.pipeline_log if isinstance(upload.pipeline_log, dict) else {}
    nums = log.get("numerical_cols") if isinstance(log.get("numerical_cols"), list) else []
    cats = log.get("categorical_cols") if isinstance(log.get("categorical_cols"), list) else []
    if nums or cats:
        return len(nums) + len(cats)
    return 0


def _prediction_rows(
    raw: list[Any],
    slug: str | None,
    task_kind: str,
) -> list[ClientLabPredictionRow]:
    positive = _POSITIVE_LABEL.get(slug or "", "Yes")
    negative = _NEGATIVE_LABEL.get(slug or "", "No")
    rows: list[ClientLabPredictionRow] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        y_pred = item.get("y_pred")
        score = item.get("score")
        if score is None:
            score = item.get("probability")
        record_raw = item.get("record_id")
        if record_raw is None:
            record_raw = item.get("row_index", index)
        record_id = _clean_text(str(record_raw), str(index))
        if task_kind != "classification":
            label = _clean_text(str(y_pred), "—")
            probability = float(score) if isinstance(score, (int, float)) else (
                float(y_pred) if isinstance(y_pred, (int, float)) else None
            )
            rows.append(
                ClientLabPredictionRow(
                    record_id=record_id,
                    prediction=label,
                    probability=probability,
                )
            )
            continue
        flag = _positive_flag(y_pred, score)
        label = positive if flag else negative
        probability = float(score) if isinstance(score, (int, float)) else None
        rows.append(
            ClientLabPredictionRow(
                record_id=record_id,
                prediction=_clean_text(label, "Yes" if flag else "No"),
                probability=probability,
            )
        )
    return rows


def _positive_flag(y_pred: Any, score: Any) -> bool:
    if isinstance(y_pred, (int, float)) and not isinstance(y_pred, bool):
        return int(y_pred) == 1
    if isinstance(score, (int, float)):
        return float(score) >= 0.5
    return False
