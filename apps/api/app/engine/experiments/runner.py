"""End-to-end experiment runner. One candidate failure does not fail the run."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import KFold, ShuffleSplit, StratifiedKFold
from sklearn.pipeline import Pipeline as SkPipeline

from app.domain.lab_run_stages import CROSS_VALIDATION, EVALUATING, PREDICTING, SPLITTING, TRAINING
from app.engine.data.quality import quality_report
from app.engine.ensemble import blend_probabilities, blend_weights, choose_fusion
from app.engine.evaluation.metrics import (
    aggregate_fold_metrics,
    classification_metrics,
    primary_score,
    regression_metrics,
    robustness_stats,
)
from app.engine.features.combinations import generate_group_combinations
from app.engine.features.encode import coerce_binary_target, encode_feature_columns
from app.engine.lab.auto_prepare import build_preprocessor, engineer_features, split_column_roles
from app.engine.leakage.detector import detect_leakage
from app.engine.models.registry import make_model
from app.engine.schema.profiler import profile_frame
from app.engine.search.generator import DUMMY_FAMILIES, assemble_candidates
from app.engine.selection import greedy_diverse_selection
from app.engine.types import Candidate, ExperimentStatus, SearchConfig, TaskSpec
from app.engine.validation.splits import SOURCE_ROW_COLUMN, split_frame, split_train_test_holdout

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


def _predict(model, X: np.ndarray, classifier: bool) -> np.ndarray:
    if classifier:
        if hasattr(model, "predict_proba"):
            proba = model.predict_proba(X)
            return np.asarray(proba[:, 1] if proba.shape[1] > 1 else proba[:, 0], dtype=float)
        return np.asarray(model.predict(X), dtype=float)
    return np.asarray(model.predict(X), dtype=float)


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


def _metrics(y_true, pred, *, classifier: bool) -> dict[str, Any]:
    return classification_metrics(y_true, pred) if classifier else regression_metrics(y_true, pred)


def _timing(stage: str, started_at: datetime, timer: float, *, status: str = "completed") -> dict[str, Any]:
    ended_at = datetime.now(UTC)
    return {
        "stage": stage,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_ms": max(0.001, (time.perf_counter() - timer) * 1000.0),
        "status": status,
    }


def _open_ingest_cv_splitter(
    y: np.ndarray,
    *,
    classifier: bool,
    n_samples: int,
    seed: int,
    n_splits: int = 5,
):
    if classifier:
        counts = pd.Series(y).value_counts()
        min_class = int(counts.min()) if len(counts) else 0
        splits = n_splits
        if min_class < splits:
            splits = max(2, min(n_splits, min_class)) if min_class >= 2 else 2
        return StratifiedKFold(n_splits=splits, shuffle=True, random_state=seed), splits
    splits = n_splits
    if n_samples < splits * 2:
        splits = max(2, min(n_splits, max(2, n_samples // 2)))
    return KFold(n_splits=splits, shuffle=True, random_state=seed), splits


def _prediction_rows(
    y_true,
    y_score,
    *,
    classifier: bool,
    test: pd.DataFrame | None = None,
    entity_col: str | None = None,
) -> list[dict[str, Any]]:
    y_true_arr = np.asarray(y_true)
    y_score_arr = np.asarray(y_score, dtype=float)
    y_pred = (y_score_arr >= 0.5).astype(int) if classifier else y_score_arr
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


def _open_ingest_validation(
    split_meta: dict[str, Any],
    records: list[dict[str, Any]],
    seed: int,
    task_type: str,
) -> dict[str, Any]:
    trained = [row for row in records if row.get("status") == "trained"]
    sample = trained[0] if trained else {}
    default_cv = "StratifiedKFold" if task_type == "binary" else "KFold"
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


def _fit_and_score_holdout(
    row: dict[str, Any],
    pool: pd.DataFrame,
    test: pd.DataFrame,
    task: TaskSpec,
    classifier: bool,
    on_stage: Callable[[str], None] | None = None,
    on_event: RunEventCallback | None = None,
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
    num_cols = list(row["numerical_cols"])
    cat_cols = list(row["categorical_cols"])
    X_train = pool.loc[:, cols]
    y_train = pool[task.target].to_numpy()
    pipeline = SkPipeline(
        [
            ("prep", build_preprocessor(num_cols, cat_cols)),
            (
                "model",
                make_model(
                    row["model_family"],
                    seed=row["random_seed"],
                    hyperparameters=row.get("hyperparameters"),
                ),
            ),
        ]
    )
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
    train_pred = _predict(pipeline, X_train, classifier)
    train_metrics = _metrics(y_train, train_pred, classifier=classifier)
    final_fit = _timing("final_fit", fit_started, fit_timer)
    final_fit.update(
        {
            "candidate_id": row.get("candidate_id"),
            "fit_row_count": int(len(X_train)),
            "fit_partition": "full_train",
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
    test_pred = _predict(pipeline, X_test, classifier)
    test_metrics = _metrics(y_test, test_pred, classifier=classifier)
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
    on_stage: Callable[[str], None] | None = None,
    on_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    on_event: RunEventCallback | None = None,
) -> dict[str, Any]:
    """ColumnTransformer + K-fold on train only; test is scored after the winner is locked."""
    classifier = task.task_type == "binary"
    # Val is empty for the 80/20 holdout path; never concatenate test.
    pool = pd.concat([train, val], ignore_index=True) if len(val) else train
    funnel_updates = {"trained": 0, "failed": 0, "cache_hits": 0}
    records: list[dict[str, Any]] = []
    stage_timings: list[dict[str, Any]] = []

    if on_stage:
        on_stage(CROSS_VALIDATION)
    cv_started = datetime.now(UTC)
    cv_timer = time.perf_counter()

    for candidate in candidates:
        t0 = time.time()
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
            y_train = pool[task.target].to_numpy()

            def _fresh_pipeline() -> SkPipeline:
                return SkPipeline(
                    [
                        ("prep", build_preprocessor(num_cols, cat_cols)),
                        (
                            "model",
                            make_model(
                                candidate.model_family,
                                seed=candidate.random_seed,
                                hyperparameters=candidate.hyperparameters,
                            ),
                        ),
                    ]
                )

            splitter, n_splits = _open_ingest_cv_splitter(
                y_train,
                classifier=classifier,
                n_samples=len(X_train),
                seed=candidate.random_seed,
            )
            split_iter = (
                list(splitter.split(X_train, y_train)) if classifier else list(splitter.split(X_train))
            )

            fold_metrics_list: list[dict[str, Any]] = []
            fold_scores: list[float] = []
            fold_evidence: list[dict[str, Any]] = []
            for fold_number, (fold_train_idx, fold_holdout_idx) in enumerate(split_iter, start=1):
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
                fold_pipeline = _fresh_pipeline()
                fold_pipeline.fit(X_train.iloc[fold_train_idx], y_train[fold_train_idx])
                fold_pred = _predict(fold_pipeline, X_train.iloc[fold_holdout_idx], classifier)
                fold_y = y_train[fold_holdout_idx]
                fold_metrics = _metrics(fold_y, fold_pred, classifier=classifier)
                fold_metrics_list.append(fold_metrics)
                fold_scores.append(primary_score(fold_metrics, task.evaluation_metric, task.task_type))
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
                fold_evidence.append(
                    {
                        "fold_number": fold_number,
                        "train_provenance": train_provenance,
                        "validation_provenance": validation_provenance,
                        "train_row_count": int(len(fold_train_idx)),
                        "validation_row_count": int(len(fold_holdout_idx)),
                        "metrics": fold_metrics,
                        "fit_duration_ms": max(0.001, (time.perf_counter() - fold_timer) * 1000.0),
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
            adaptation_reason = None
            if n_splits != 5:
                adaptation_reason = (
                    "Reduced folds because the training partition cannot support five valid folds."
                )
            records.append(
                {
                    **candidate.to_dict(),
                    "candidate": candidate.candidate_id,
                    "feature_set": list(candidate.features),
                    "preprocessing_config": {
                        "numerical": ["imputer:median", "scaler:standard"],
                        "categorical": ["imputer:most_frequent", "onehot:drop_first"],
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
                    "cv_strategy": type(splitter).__name__,
                    "requested_folds": 5,
                    "actual_folds": n_splits,
                    "adaptation_reason": adaptation_reason,
                    "n_folds": n_splits,
                    "n_train_rows": int(len(X_train)),
                    "numerical_cols": num_cols,
                    "categorical_cols": cat_cols,
                    "test_metrics": None,
                }
            )
            funnel_updates["trained"] += 1
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
                        "categorical": ["imputer:most_frequent", "onehot:drop_first"],
                    },
                    "status": "FAILED",
                    "error": str(exc),
                    "failure_reason": str(exc),
                    "metrics": None,
                    "fold_metrics": [],
                    "folds": [],
                    "cv_mean": None,
                    "cv_std": None,
                    "cv_strategy": "StratifiedKFold" if classifier else "KFold",
                    "requested_folds": 5,
                    "actual_folds": None,
                    "adaptation_reason": None,
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
    pool_rows = learned or trained
    best_single = max(pool_rows, key=lambda row: row["score"]) if pool_rows else None
    selected_ids = [best_single["candidate_id"]] if best_single else []
    funnel_updates["robust"] = len(pool_rows)
    funnel_updates["strong"] = len(pool_rows)
    funnel_updates["diverse"] = len(selected_ids)

    train_metrics: dict[str, Any] = {}
    test_metrics: dict[str, Any] = {}
    test_predictions: list[dict[str, Any]] = []
    selection = {
        "candidate_id": best_single.get("candidate_id") if best_single else None,
        "selected_candidate_id": best_single.get("candidate_id") if best_single else None,
        "selection_metric": task.evaluation_metric,
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
        selection_metric=task.evaluation_metric,
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
            selection_metric=task.evaluation_metric,
            cv_score=best_single.get("score"),
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
        )
        stage_timings.extend([final_fit, {key: value for key, value in final_test_evaluation.items() if key != "metrics"}])
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
            {"fusion": None, "members": selected_ids, "weights": {}, "task_id": task.id},
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
    }


def _run_open_ingest_experiment(
    frame: pd.DataFrame,
    task: TaskSpec,
    config: SearchConfig,
    *,
    artifact_dir: Path,
    members_dir: Path,
    dataset_version: str,
    started: float,
    on_stage: Callable[[str], None] | None,
    on_checkpoint: Callable[[dict[str, Any]], None] | None,
    on_event: RunEventCallback | None,
) -> dict[str, Any]:
    """Leakage-safe open-ingest experiment with an early locked holdout."""
    profile = profile_frame(frame)
    quality = quality_report(frame, task.target)
    work = frame.copy()
    if SOURCE_ROW_COLUMN not in work.columns:
        work[SOURCE_ROW_COLUMN] = work.index.astype(int)
    if task.target not in work.columns:
        raise ValueError(f"Frame is missing target {task.target!r}")
    if task.task_type == "binary":
        work[task.target] = coerce_binary_target(work[task.target])
        work = work.dropna(subset=[task.target])
        work[task.target] = work[task.target].astype(int)
    else:
        work[task.target] = pd.to_numeric(work[task.target], errors="coerce")
        work = work.dropna(subset=[task.target])

    if on_stage:
        on_stage(SPLITTING)
    train, val, test, split_meta = split_train_test_holdout(
        work,
        target=task.target,
        test_size=0.2,
        seed=config.seed,
        stratify=task.task_type == "binary",
    )

    # Leakage screening and all feature eligibility evidence use train only.
    leakage = detect_leakage(
        train,
        target=task.target,
        time_col=task.prediction_time_column,
        entity_col=task.entity_id,
    )
    blocked = set(leakage["high_risk_columns"]) if config.exclude_high_leakage else set()
    groups = {
        name: [
            col
            for col in cols
            if col in train.columns
            and col not in blocked
            and col != task.target
            and col != task.prediction_time_column
            and col != task.entity_id
        ]
        for name, cols in task.feature_groups.items()
    }
    groups = {name: cols for name, cols in groups.items() if cols}
    roles = task.column_roles or {}
    numerical_cols = [c for c in (roles.get("numerical") or []) if any(c in cols for cols in groups.values())]
    categorical_cols = [c for c in (roles.get("categorical") or []) if any(c in cols for cols in groups.values())]
    modeled = numerical_cols + categorical_cols
    if not modeled:
        raise ValueError("no numeric or categorical columns to model")
    task = TaskSpec(
        **{
            **task.to_dict(),
            "feature_groups": {"features": modeled},
            "column_roles": {"numerical": numerical_cols, "categorical": categorical_cols},
        }
    )

    candidates = assemble_candidates(task, config, dataset_version=dataset_version)
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
        on_stage=on_stage,
        on_checkpoint=_checkpoint,
        on_event=on_event,
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
        "leakage": leakage,
        "split": split_meta,
        "validation": _open_ingest_validation(split_meta, records, config.seed, task.task_type),
        "feature_engineering": feature_report,
        "preprocessing": {
            "numeric_columns": numerical_cols,
            "categorical_columns": categorical_cols,
            "numeric_imputer_strategy": "median",
            "numeric_scaler": "StandardScaler",
            "categorical_imputer_strategy": "most_frequent",
            "categorical_encoder": "OneHotEncoder",
            "categorical_encoder_drop": "first",
            "handle_unknown": "ignore",
            "numerical": ["imputer:median", "scaler:standard"],
            "categorical": ["imputer:most_frequent", "onehot:drop_first"],
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
            if row.get("model_family") in {"majority", "mean", "logistic_regression", "linear_regression"}
        ],
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
    on_stage: Callable[[str], None] | None = None,
    on_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    on_event: RunEventCallback | None = None,
) -> dict[str, Any]:
    """Train, filter, select, and report. Returns a JSON-serializable result dict."""
    started = time.time()
    config = config or SearchConfig()
    artifact_dir = Path(artifact_dir or ".")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    members_dir = artifact_dir / "members"
    members_dir.mkdir(parents=True, exist_ok=True)

    if config.strategy == "open_ingest":
        return _run_open_ingest_experiment(
            frame,
            task,
            config,
            artifact_dir=artifact_dir,
            members_dir=members_dir,
            dataset_version=dataset_version,
            started=started,
            on_stage=on_stage,
            on_checkpoint=on_checkpoint,
            on_event=on_event,
        )

    funnel = {
        "generated": 0,
        "valid": 0,
        "leakage_safe": 0,
        "trained": 0,
        "robust": 0,
        "strong": 0,
        "diverse": 0,
        "failed": 0,
        "cache_hits": 0,
    }
    status = ExperimentStatus.PROFILING.value
    profile = profile_frame(frame)
    quality = quality_report(frame, task.target)
    leakage = detect_leakage(
        frame,
        target=task.target,
        time_col=task.prediction_time_column,
        entity_col=task.entity_id,
    )
    blocked = set(leakage["high_risk_columns"]) if config.exclude_high_leakage else set()
    funnel["leakage_safe"] = int(frame.shape[1] - len(blocked))

    work = frame.copy()
    if task.target not in work.columns:
        raise ValueError(f"Frame is missing target {task.target!r}")
    if task.task_type == "binary":
        work = work.dropna(subset=[task.target])
        work[task.target] = coerce_binary_target(work[task.target])
        work = work.dropna(subset=[task.target])
        work[task.target] = work[task.target].astype(int)
    else:
        work[task.target] = pd.to_numeric(work[task.target], errors="coerce")
        work = work.dropna(subset=[task.target])

    status = ExperimentStatus.FEATURE_ENGINEERING.value
    groups = {
        name: [col for col in cols if col in work.columns and col not in blocked and col != task.target]
        for name, cols in task.feature_groups.items()
    }
    groups = {name: cols for name, cols in groups.items() if cols}
    feature_cols = [
        col
        for cols in groups.values()
        for col in cols
        if col != task.prediction_time_column and col != task.entity_id
    ]
    feature_engineering_log: list[dict[str, Any]] = []
    if config.strategy != "open_ingest":
        # open_ingest keeps raw dtypes: its ColumnTransformer (SimpleImputer +
        # StandardScaler / OneHotEncoder) needs real strings/NaNs, not factor codes.
        work = encode_feature_columns(work, feature_cols)
        task = TaskSpec(**{**task.to_dict(), "feature_groups": groups})
    else:
        work, feature_engineering_log = engineer_features(work, feature_cols)
        role_cols = [c for c in feature_cols if c in work.columns]
        roles = task.column_roles or {}
        if "numerical" in roles or "categorical" in roles:
            numerical_cols = [c for c in (roles.get("numerical") or []) if c in role_cols]
            categorical_cols = [c for c in (roles.get("categorical") or []) if c in role_cols]
        else:
            numerical_cols, categorical_cols = split_column_roles(work, role_cols)
        modeled = numerical_cols + categorical_cols
        task = TaskSpec(
            **{
                **task.to_dict(),
                "feature_groups": {"features": modeled} if modeled else groups,
                "column_roles": {"numerical": numerical_cols, "categorical": categorical_cols},
            }
        )
    logger.info(
        "lab features groups=%s columns=%s",
        {name: len(cols) for name, cols in groups.items()},
        feature_cols,
    )

    status = ExperimentStatus.GENERATING_CANDIDATES.value
    candidates = assemble_candidates(task, config, dataset_version=dataset_version)
    funnel["generated"] = len(candidates)
    funnel["valid"] = len(candidates)
    logger.info(
        "lab generated %s candidates: %s",
        len(candidates),
        [f"{row.model_family}[{'+'.join(row.feature_groups)}]" for row in candidates],
    )
    cache_dir = artifact_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    status = ExperimentStatus.TRAINING.value
    if on_stage:
        on_stage(SPLITTING)
    if config.strategy == "open_ingest":
        train, val, test, split_meta = split_train_test_holdout(
            work,
            target=task.target,
            test_size=0.2,
            seed=config.seed,
            stratify=task.task_type == "binary",
        )
    else:
        train, val, test, split_meta = split_frame(
            work,
            strategy=task.validation_strategy,
            target=task.target,
            time_col=task.prediction_time_column,
            group_col=task.entity_id,
            seed=config.seed,
        )

    if config.strategy == "open_ingest":
        outcome = _run_open_ingest_candidates(
            candidates,
            train,
            val,
            test,
            task,
            artifact_dir=artifact_dir,
            members_dir=members_dir,
            on_stage=on_stage,
        )
        funnel.update(outcome["funnel"])
        records = outcome["records"]
        selected_ids = outcome["selected_ids"]
        fusion = None
        weights = {}
        blend_metrics = {}
        best_single = outcome["best_single"]
        test_metrics = outcome["test_metrics"]
        train_metrics = outcome["train_metrics"]
        test_predictions = outcome["test_predictions"]
        group_scores = {}
        combo_table = []
        have_result = best_single is not None
        status = ExperimentStatus.REPORTING.value
        result = {
            "task": task.to_dict(),
            "config": config.to_dict(),
            "status": ExperimentStatus.COMPLETED.value if have_result else ExperimentStatus.FAILED.value,
            "funnel": funnel,
            "profile": profile,
            "profile_summary": {
                "row_count": profile["row_count"],
                "column_count": profile["column_count"],
                "duplicate_rows": profile.get("duplicate_rows", profile.get("duplicate_count")),
            },
            "quality": quality,
            "leakage": leakage,
            "split": split_meta,
            "validation": _open_ingest_validation(split_meta, records, config.seed, task.task_type),
            "feature_engineering": {"transformations": feature_engineering_log},
            "preprocessing": {
                "numerical": ["imputer:median", "scaler:standard"],
                "categorical": ["imputer:most_frequent", "onehot:drop_first"],
            },
            "candidates": records,
            "selected_ids": selected_ids,
            "best_single": best_single,
            "fusion": fusion,
            "weights": weights,
            "validation_blend_metrics": blend_metrics,
            "train_metrics": train_metrics,
            "test_metrics": test_metrics,
            "test_predictions": test_predictions,
            "feature_group_scores": group_scores,
            "combination_table": combo_table,
            "artifact_dir": str(artifact_dir),
            "duration_seconds": time.time() - started,
            "baselines": [row for row in records if row.get("model_family") in {"majority", "mean", "logistic_regression", "linear_regression"}],
        }
        result = _json_safe(result)
        (artifact_dir / "result.json").write_text(json.dumps(result, default=str, indent=2) + "\n")
        from app.engine.reporting.report import render_markdown

        (artifact_dir / "report.md").write_text(render_markdown(result))
        logger.info("experiment completed status=%s funnel=%s", result["status"], funnel)
        return result

    records: list[dict[str, Any]] = []
    fitted: dict[str, Any] = {}
    val_preds: dict[str, np.ndarray] = {}
    classifier = task.task_type == "binary"

    for candidate in candidates:
        logger.info(
            "lab training %s family=%s groups=%s",
            candidate.candidate_id,
            candidate.model_family,
            "+".join(candidate.feature_groups),
        )
        t0 = time.time()
        try:
            X_train = _matrix(train, candidate.features)
            y_train = train[task.target].to_numpy()
            X_val = _matrix(val, candidate.features)
            y_val = val[task.target].to_numpy()
            cache_file = cache_dir / f"{candidate.fingerprint}.joblib"
            if cache_file.exists():
                model = joblib.load(cache_file)
                funnel["cache_hits"] += 1
            else:
                model = make_model(candidate.model_family, seed=candidate.random_seed)
                model.fit(X_train, y_train)
                joblib.dump(model, cache_file)
            pred_val = _predict(model, X_val, classifier)
            metrics = (
                classification_metrics(y_val, pred_val)
                if classifier
                else regression_metrics(y_val, pred_val)
            )
            score = primary_score(metrics, task.evaluation_metric, task.task_type)
            robust_scores = []
            if len(val) >= 30 and config.n_robustness_folds > 1:
                splitter = ShuffleSplit(
                    n_splits=config.n_robustness_folds, test_size=0.4, random_state=config.seed
                )
                for _, idx in splitter.split(X_val):
                    fold_pred = pred_val[idx]
                    fold_y = y_val[idx]
                    fold_m = (
                        classification_metrics(fold_y, fold_pred)
                        if classifier
                        else regression_metrics(fold_y, fold_pred)
                    )
                    robust_scores.append(primary_score(fold_m, task.evaluation_metric, task.task_type))
            robust = robustness_stats(robust_scores or [score])
            fitted[candidate.candidate_id] = model
            val_preds[candidate.candidate_id] = pred_val
            records.append(
                {
                    **candidate.to_dict(),
                    "status": "trained",
                    "metrics": metrics,
                    "score": score,
                    "robustness": robust,
                    "train_seconds": time.time() - t0,
                    "stage": "trained",
                }
            )
            funnel["trained"] += 1
        except Exception as exc:  # noqa: BLE001
            funnel["failed"] += 1
            logger.exception("candidate %s failed", candidate.candidate_id)
            records.append(
                {
                    **candidate.to_dict(),
                    "status": "FAILED",
                    "error": str(exc),
                    "train_seconds": time.time() - t0,
                }
            )

    status = ExperimentStatus.FILTERING.value
    trained = [row for row in records if row.get("status") == "trained"]
    learned = [row for row in trained if row.get("model_family") not in DUMMY_FAMILIES]
    pool = learned or trained
    if classifier:
        robust = [
            row
            for row in pool
            if row["robustness"]["std"] <= 0.15 and row["score"] > max(config.min_metric, 0.5)
        ]
        if not robust:
            robust = [row for row in pool if row["score"] > 0.5]
    else:
        robust = [row for row in pool if row["robustness"]["std"] <= abs(row["score"]) * 2 + 1]
    if not robust:
        robust = pool
    funnel["robust"] = len(robust)
    strong = sorted(robust, key=lambda row: row["score"], reverse=True)
    funnel["strong"] = len(strong)

    status = ExperimentStatus.SELECTING.value
    selected_ids: list[str] = []
    fusion = None
    weights: dict[str, float] = {}
    blend_metrics: dict[str, Any] = {}
    best_single: dict[str, Any] | None = None
    test_metrics: dict[str, Any] = {}
    test_predictions: list[dict[str, Any]] = []
    group_scores: dict[str, float] = {}

    if strong:
        pred_frame = pd.DataFrame({row["candidate_id"]: val_preds[row["candidate_id"]] for row in strong})
        scores = {row["candidate_id"]: row["score"] for row in strong}
        selected_ids = greedy_diverse_selection(
            pred_frame,
            scores,
            retain_max=min(config.retain_max, config.max_ensemble_size),
            retain_min=min(config.retain_min, len(strong)),
            max_abs_correlation=config.max_abs_correlation,
        )
        funnel["diverse"] = len(selected_ids)
        by_id = {row["candidate_id"]: row for row in strong}
        best_single = max(strong, key=lambda row: row["score"])
        member_scores = {mid: scores[mid] for mid in selected_ids}
        weights = blend_weights(member_scores, selected_ids)
        blended = blend_probabilities({mid: val_preds[mid] for mid in selected_ids}, weights)
        y_val = val[task.target].to_numpy()
        blend_metrics = classification_metrics(y_val, blended) if classifier else regression_metrics(y_val, blended)
        blend_score = primary_score(blend_metrics, task.evaluation_metric, task.task_type)
        fusion = choose_fusion(
            blend_metric=blend_score,
            best_single_metric=best_single["score"],
            best_single_id=best_single["candidate_id"],
        )
        persist_ids = selected_ids if fusion == "weighted_blend" else [best_single["candidate_id"]]
        for mid in persist_ids:
            joblib.dump(fitted[mid], members_dir / f"{mid}.joblib")

        status = ExperimentStatus.ENSEMBLING.value
        X_test_best = _matrix(test, tuple(best_single["features"]))
        y_test = test[task.target].to_numpy()
        if fusion == "weighted_blend":
            parts = {}
            for mid in selected_ids:
                feats = tuple(by_id[mid]["features"])
                parts[mid] = _predict(fitted[mid], _matrix(test, feats), classifier)
            test_pred = blend_probabilities(parts, weights)
        else:
            test_pred = _predict(fitted[best_single["candidate_id"]], X_test_best, classifier)
        test_metrics = (
            classification_metrics(y_test, test_pred) if classifier else regression_metrics(y_test, test_pred)
        )
        test_predictions = _prediction_rows(
            y_test,
            test_pred,
            classifier=classifier,
            test=test,
            entity_col=task.entity_id,
        )
        pd.DataFrame(test_predictions).to_csv(artifact_dir / "test_predictions.csv", index=False)

        # Feature-group contribution: best score among candidates that used the group.
        for name in task.feature_groups:
            group_rows = [row for row in strong if name in row["feature_groups"]]
            group_scores[name] = max((row["score"] for row in group_rows), default=0.0)

        combo_table = []
        for combo in generate_group_combinations(
            list(task.feature_groups), strategy="limited", max_combinations=24, seed=config.seed
        ):
            matching = [row for row in strong if tuple(row["feature_groups"]) == combo]
            if matching:
                combo_table.append(
                    {
                        "groups": list(combo),
                        "best_score": max(row["score"] for row in matching),
                        "n_candidates": len(matching),
                    }
                )

        serving = {
            "fusion": fusion,
            "members": persist_ids,
            "weights": weights,
            "task_id": task.id,
            "dataset_version": dataset_version,
        }
        joblib.dump(serving, artifact_dir / "model.joblib")
    else:
        combo_table = []
        persist_ids = []
        serving = {}

    status = ExperimentStatus.REPORTING.value
    result = {
        "task": task.to_dict(),
        "config": config.to_dict(),
        "status": ExperimentStatus.COMPLETED.value if strong else ExperimentStatus.FAILED.value,
        "funnel": funnel,
        "profile_summary": {
            "row_count": profile["row_count"],
            "column_count": profile["column_count"],
            "duplicate_rows": profile["duplicate_rows"],
        },
        "quality": quality,
        "leakage": leakage,
        "split": split_meta,
        "candidates": records,
        "selected_ids": selected_ids,
        "best_single": best_single,
        "fusion": fusion,
        "weights": weights,
        "validation_blend_metrics": blend_metrics,
        "test_metrics": test_metrics,
        "test_predictions": test_predictions,
        "feature_group_scores": group_scores,
        "combination_table": combo_table,
        "artifact_dir": str(artifact_dir),
        "duration_seconds": time.time() - started,
        "baselines": [row for row in records if row.get("model_family") in {"majority", "mean", "logistic_regression", "linear_regression"}],
    }
    result = _json_safe(result)
    (artifact_dir / "result.json").write_text(json.dumps(result, default=str, indent=2) + "\n")
    from app.engine.reporting.report import render_markdown

    (artifact_dir / "report.md").write_text(render_markdown(result))
    logger.info("experiment completed status=%s funnel=%s", result["status"], funnel)
    return result
