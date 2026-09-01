"""End-to-end experiment runner. One candidate failure does not fail the run."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
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
from app.engine.validation.splits import split_frame, split_train_test_holdout

logger = logging.getLogger(__name__)


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
        if test is not None and entity_col and entity_col in test.columns:
            value = _json_safe(test.iloc[index][entity_col])
            if value is not None:
                record_id = str(value)
        item = {
            "row_index": int(index),
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
        "random_state": split_meta.get("random_state", seed),
    }


def _fit_and_score_holdout(
    row: dict[str, Any],
    pool: pd.DataFrame,
    test: pd.DataFrame,
    task: TaskSpec,
    classifier: bool,
) -> tuple[Any, dict[str, Any], dict[str, Any], np.ndarray, np.ndarray, int]:
    """Fit on the full training pool and score train + untouched test. Not used for ranking."""
    cols = list(row["features"])
    num_cols = list(row["numerical_cols"])
    cat_cols = list(row["categorical_cols"])
    X_train = pool.loc[:, cols]
    y_train = pool[task.target].to_numpy()
    pipeline = SkPipeline(
        [
            ("prep", build_preprocessor(num_cols, cat_cols)),
            ("model", make_model(row["model_family"], seed=row["random_seed"])),
        ]
    )
    pipeline.fit(X_train, y_train)
    train_pred = _predict(pipeline, X_train, classifier)
    train_metrics = _metrics(y_train, train_pred, classifier=classifier)
    X_test = test.loc[:, cols]
    y_test = test[task.target].to_numpy()
    test_pred = _predict(pipeline, X_test, classifier)
    test_metrics = _metrics(y_test, test_pred, classifier=classifier)
    return pipeline, train_metrics, test_metrics, y_test, test_pred, int(len(X_test))


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
) -> dict[str, Any]:
    """ColumnTransformer + K-fold on train only; test is scored after the winner is locked."""
    classifier = task.task_type == "binary"
    # Val is empty for the 80/20 holdout path; never concatenate test.
    pool = pd.concat([train, val], ignore_index=True) if len(val) else train
    funnel_updates = {"trained": 0, "failed": 0, "cache_hits": 0}
    records: list[dict[str, Any]] = []

    if on_stage:
        on_stage(CROSS_VALIDATION)

    for candidate in candidates:
        t0 = time.time()
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
                        ("model", make_model(candidate.model_family, seed=candidate.random_seed)),
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
            for fold_train_idx, fold_holdout_idx in split_iter:
                fold_pipeline = _fresh_pipeline()
                fold_pipeline.fit(X_train.iloc[fold_train_idx], y_train[fold_train_idx])
                fold_pred = _predict(fold_pipeline, X_train.iloc[fold_holdout_idx], classifier)
                fold_y = y_train[fold_holdout_idx]
                fold_metrics = _metrics(fold_y, fold_pred, classifier=classifier)
                fold_metrics_list.append(fold_metrics)
                fold_scores.append(primary_score(fold_metrics, task.evaluation_metric, task.task_type))

            cv_mean, cv_std = aggregate_fold_metrics(fold_metrics_list)
            robust = robustness_stats(fold_scores)
            records.append(
                {
                    **candidate.to_dict(),
                    "status": "trained",
                    "metrics": cv_mean,
                    "fold_metrics": fold_metrics_list,
                    "cv_mean": cv_mean,
                    "cv_std": cv_std,
                    "cv_score": robust,
                    "score": robust["mean"],
                    "robustness": robust,
                    "train_seconds": time.time() - t0,
                    "stage": "trained",
                    "cv_strategy": type(splitter).__name__,
                    "n_folds": n_splits,
                    "n_train_rows": int(len(X_train)),
                    "numerical_cols": num_cols,
                    "categorical_cols": cat_cols,
                }
            )
            funnel_updates["trained"] += 1
        except Exception as exc:  # noqa: BLE001
            funnel_updates["failed"] += 1
            logger.exception("open-ingest candidate %s failed", candidate.candidate_id)
            records.append(
                {
                    **candidate.to_dict(),
                    "status": "FAILED",
                    "error": str(exc),
                    "train_seconds": time.time() - t0,
                }
            )

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

    # Holdout scoring is reporting-only. Ranking already locked on CV `score`.
    if trained:
        if on_stage:
            on_stage(TRAINING)
        winner_pipeline = None
        winner_y_test = None
        winner_test_pred = None
        for row in trained:
            pipeline, row_train, row_test, y_test, test_pred, n_test = _fit_and_score_holdout(
                row, pool, test, task, classifier
            )
            row["train_metrics"] = row_train
            row["test_metrics"] = row_test
            row["n_test_rows"] = n_test
            if best_single is not None and row.get("candidate_id") == best_single.get("candidate_id"):
                row["locked"] = True
                train_metrics = row_train
                test_metrics = row_test
                winner_pipeline = pipeline
                winner_y_test = y_test
                winner_test_pred = test_pred

        if on_stage:
            on_stage(EVALUATING)
        if on_stage:
            on_stage(PREDICTING)
        if (
            best_single is not None
            and winner_pipeline is not None
            and winner_y_test is not None
            and winner_test_pred is not None
        ):
            test_predictions = _prediction_rows(
                winner_y_test,
                winner_test_pred,
                classifier=classifier,
                test=test,
                entity_col=task.entity_id,
            )
            joblib.dump(winner_pipeline, members_dir / f"{best_single['candidate_id']}.joblib")
            joblib.dump(
                {"fusion": None, "members": selected_ids, "weights": {}, "task_id": task.id},
                artifact_dir / "model.joblib",
            )
            pd.DataFrame(test_predictions).to_csv(artifact_dir / "test_predictions.csv", index=False)

    return {
        "funnel": funnel_updates,
        "records": records,
        "best_single": best_single,
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
        "test_predictions": test_predictions,
        "selected_ids": selected_ids,
    }


def run_experiment(
    frame: pd.DataFrame,
    task: TaskSpec,
    config: SearchConfig | None = None,
    *,
    artifact_dir: Path | None = None,
    dataset_version: str = "v1",
    on_stage: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Train, filter, select, and report. Returns a JSON-serializable result dict."""
    started = time.time()
    config = config or SearchConfig()
    artifact_dir = Path(artifact_dir or ".")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    members_dir = artifact_dir / "members"
    members_dir.mkdir(parents=True, exist_ok=True)

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
