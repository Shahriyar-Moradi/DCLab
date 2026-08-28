"""End-to-end experiment runner. One candidate failure does not fail the run."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import KFold, ShuffleSplit, StratifiedKFold
from sklearn.pipeline import Pipeline as SkPipeline

from app.engine.data.quality import quality_report
from app.engine.ensemble import blend_probabilities, blend_weights, choose_fusion
from app.engine.evaluation.metrics import (
    classification_metrics,
    primary_score,
    regression_metrics,
    robustness_stats,
)
from app.engine.features.combinations import generate_group_combinations
from app.engine.features.encode import coerce_binary_target, encode_feature_columns
from app.engine.lab.auto_prepare import apply_missing_value_variant, build_preprocessor
from app.engine.leakage.detector import detect_leakage
from app.engine.models.registry import make_model
from app.engine.schema.profiler import profile_frame
from app.engine.search.generator import DUMMY_FAMILIES, assemble_candidates
from app.engine.selection import greedy_diverse_selection
from app.engine.types import Candidate, ExperimentStatus, SearchConfig, TaskSpec
from app.engine.validation.splits import split_frame

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


def _run_open_ingest_candidates(
    candidates: list[Candidate],
    train: pd.DataFrame,
    val: pd.DataFrame,
    test: pd.DataFrame,
    task: TaskSpec,
    *,
    artifact_dir: Path,
    members_dir: Path,
) -> dict[str, Any]:
    """Train/test + real K-fold retrain path for `strategy="open_ingest"`.

    Each candidate carries its own missing-value variant (drop sparse rows vs.
    impute everything) and is fit as ``Pipeline([ColumnTransformer, model])``
    on raw (uncoerced) columns, so impute/scale/one-hot never leak from the
    held-out test set. This intentionally skips the generic ensemble/greedy
    selection machinery below (blending across differently-imputed variants
    does not make sense) and instead reports the single best candidate.
    """
    classifier = task.task_type == "binary"
    pool_raw = pd.concat([train, val], ignore_index=True)
    funnel_updates = {"trained": 0, "failed": 0, "cache_hits": 0}
    records: list[dict[str, Any]] = []
    fitted: dict[str, Any] = {}

    for candidate in candidates:
        t0 = time.time()
        try:
            variant = candidate.preprocessing.get("missing_variant", "impute_all")
            cols = list(candidate.features)
            num_cols = [c for c in (task.column_roles or {}).get("numerical", []) if c in cols]
            cat_cols = [c for c in (task.column_roles or {}).get("categorical", []) if c in cols]
            if not num_cols and not cat_cols:
                raise ValueError("no numeric or categorical columns to model")

            pool_v = apply_missing_value_variant(pool_raw, cols, variant=variant)
            test_v = apply_missing_value_variant(test, cols, variant=variant)
            if len(pool_v) < 10 or len(test_v) == 0:
                raise ValueError("not enough rows left after applying the missing-value policy")

            X_pool = pool_v.loc[:, cols]
            y_pool = pool_v[task.target].to_numpy()
            X_test = test_v.loc[:, cols]
            y_test = test_v[task.target].to_numpy()

            def _fresh_pipeline() -> SkPipeline:
                return SkPipeline(
                    [
                        ("prep", build_preprocessor(num_cols, cat_cols)),
                        ("model", make_model(candidate.model_family, seed=candidate.random_seed)),
                    ]
                )

            if classifier:
                counts = pd.Series(y_pool).value_counts()
                n_splits = max(2, min(5, int(counts.min()))) if len(counts) > 1 else 2
                splitter = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=candidate.random_seed)
                split_iter = list(splitter.split(X_pool, y_pool))
            else:
                n_splits = max(2, min(5, len(X_pool) // 5 or 2))
                splitter = KFold(n_splits=n_splits, shuffle=True, random_state=candidate.random_seed)
                split_iter = list(splitter.split(X_pool))

            fold_scores = []
            for fold_train_idx, fold_holdout_idx in split_iter:
                fold_pipeline = _fresh_pipeline()
                fold_pipeline.fit(X_pool.iloc[fold_train_idx], y_pool[fold_train_idx])
                fold_pred = _predict(fold_pipeline, X_pool.iloc[fold_holdout_idx], classifier)
                fold_y = y_pool[fold_holdout_idx]
                fold_metrics = (
                    classification_metrics(fold_y, fold_pred) if classifier else regression_metrics(fold_y, fold_pred)
                )
                fold_scores.append(primary_score(fold_metrics, task.evaluation_metric, task.task_type))

            pipeline = _fresh_pipeline()
            pipeline.fit(X_pool, y_pool)

            robust = robustness_stats(fold_scores)
            test_pred = _predict(pipeline, X_test, classifier)
            metrics = classification_metrics(y_test, test_pred) if classifier else regression_metrics(y_test, test_pred)
            score = robust["mean"]

            fitted[candidate.candidate_id] = pipeline
            records.append(
                {
                    **candidate.to_dict(),
                    "status": "trained",
                    "metrics": metrics,
                    "cv_score": robust,
                    "score": score,
                    "robustness": robust,
                    "train_seconds": time.time() - t0,
                    "stage": "trained",
                    "n_folds": n_splits,
                    "n_pool_rows": int(len(X_pool)),
                    "n_test_rows": int(len(X_test)),
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
    test_metrics = best_single["metrics"] if best_single else {}
    selected_ids = [best_single["candidate_id"]] if best_single else []
    funnel_updates["robust"] = len(pool_rows)
    funnel_updates["strong"] = len(pool_rows)
    funnel_updates["diverse"] = len(selected_ids)

    if best_single:
        joblib.dump(fitted[best_single["candidate_id"]], members_dir / f"{best_single['candidate_id']}.joblib")
        joblib.dump(
            {"fusion": None, "members": selected_ids, "weights": {}, "task_id": task.id},
            artifact_dir / "model.joblib",
        )

    return {
        "funnel": funnel_updates,
        "records": records,
        "best_single": best_single,
        "test_metrics": test_metrics,
        "selected_ids": selected_ids,
    }


def run_experiment(
    frame: pd.DataFrame,
    task: TaskSpec,
    config: SearchConfig | None = None,
    *,
    artifact_dir: Path | None = None,
    dataset_version: str = "v1",
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
    if config.strategy != "open_ingest":
        # open_ingest keeps raw dtypes: its ColumnTransformer (SimpleImputer +
        # StandardScaler / OneHotEncoder) needs real strings/NaNs, not factor codes.
        work = encode_feature_columns(work, feature_cols)
    task = TaskSpec(**{**task.to_dict(), "feature_groups": groups})
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
            candidates, train, val, test, task, artifact_dir=artifact_dir, members_dir=members_dir
        )
        funnel.update(outcome["funnel"])
        records = outcome["records"]
        selected_ids = outcome["selected_ids"]
        fusion = None
        weights = {}
        blend_metrics = {}
        best_single = outcome["best_single"]
        test_metrics = outcome["test_metrics"]
        group_scores = {}
        combo_table = []
        have_result = best_single is not None
        status = ExperimentStatus.REPORTING.value
        result = {
            "task": task.to_dict(),
            "config": config.to_dict(),
            "status": ExperimentStatus.COMPLETED.value if have_result else ExperimentStatus.FAILED.value,
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
