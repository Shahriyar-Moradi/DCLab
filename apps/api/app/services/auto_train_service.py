"""Automatic training job for a simple, already-structured open-ingest file.

Runs a full EDA -> missing-value decision -> ColumnTransformer -> train/test +
K-fold -> RandomForest/XGBoost pipeline behind the scenes, exactly the workflow
from the plan, and persists it as a real Lab `Experiment` (never a
`ClientLabRun`/`ClientLabRunAudit` — those are for the translated, quota-bound
trial cards). Nothing here is shown to a client; see
apps/api/app/services/client_lab_upload_service.py for the client-safe side
and docs/LABS_DATA_UNDERSTANDING.md for the full split.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.db.models import ClientLabUpload
from app.db.session import get_session_factory
from app.domain.lab_run_stages import (
    ANALYZING,
    CLEANING,
    COMPLETED,
    FAILED,
    FEATURE_ENGINEERING,
    INGESTING,
    PREPROCESSING,
    QUEUED,
    SKIPPED,
)
from app.engine.data.quality import quality_report
from app.engine.features.combinations import features_for_groups, generate_group_combinations
from app.engine.lab.auto_prepare import (
    clean_frame,
    coerce_numeric_like,
    engineer_features,
    pick_target_heuristic,
    plan_missing_values,
    split_column_roles,
)
from app.engine.lab.column_map import MIN_TRAIN_ROWS
from app.engine.models.registry import available_families
from app.engine.schema.profiler import profile_frame
from app.engine.types import SearchConfig, TaskSpec
from app.services.lab_decision_ledger import record_column_type_decisions, record_missing_value_decisions
from app.services.lab_service import create_experiment, execute_experiment, ingest_dataset, seed_dogfood, upsert_task

logger = logging.getLogger(__name__)

# Gate from the plan: only spreadsheet/json/table_file with named columns and
# enough rows attempt auto-train. Raw logs and headerless files stay skipped.
SIMPLE_KINDS = {"spreadsheet", "json", "table_file"}


def is_simple_tabular(upload: ClientLabUpload) -> bool:
    return upload.kind in SIMPLE_KINDS and upload.has_named_fields and upload.record_count >= MIN_TRAIN_ROWS


def _load_upload_frame(stored_path: str) -> pd.DataFrame:
    """Re-read the saved file as a DataFrame regardless of format. `load_table`
    (used by every other Lab dataset) only understands CSV/Parquet, so this
    mirrors `open_ingest`'s format coverage and normalizes to CSV afterwards.
    """
    path = Path(stored_path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".tsv", ".tab"}:
        return pd.read_csv(path, sep="\t")
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix in {".json", ".jsonl", ".ndjson"}:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return pd.DataFrame()
        if text[0] != "[" and "\n" in text:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            return pd.json_normalize(rows)
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return pd.json_normalize(parsed)
        if isinstance(parsed, dict):
            for key in ("records", "data", "rows", "items"):
                if isinstance(parsed.get(key), list):
                    return pd.json_normalize(parsed[key])
            return pd.json_normalize([parsed])
        return pd.DataFrame()
    return pd.read_csv(path, sep=None, engine="python")


def _search_config() -> SearchConfig:
    return SearchConfig(
        strategy="open_ingest",
        max_candidates=8,
        max_feature_group_combinations=1,
        max_ensemble_size=1,
        max_training_seconds=120.0,
        n_robustness_folds=5,
        min_metric=0.0,
        retain_min=1,
        retain_max=1,
        seed=42,
    )


def _mark(
    db: Session,
    upload: ClientLabUpload,
    *,
    status: str,
    log: dict[str, Any] | None = None,
    experiment_id: UUID | None = None,
) -> None:
    """Commit `pipeline_status` at a real operation boundary (no synthetic delays)."""
    merged = dict(upload.pipeline_log or {})
    if log is not None:
        merged.update(log)
    history = list(merged.get("stages") or [])
    if not history or history[-1] != status:
        history.append(status)
    merged["stages"] = history
    merged["current_stage"] = status
    upload.pipeline_status = status
    upload.pipeline_log = merged
    if experiment_id is not None:
        upload.experiment_id = experiment_id
    db.commit()


def run_auto_train_job(db: Session, upload_id: UUID) -> None:
    """Runs synchronously against the given session. In production this is
    called from `enqueue_auto_train`'s background thread (its own session) so
    `POST /app/labs/uploads` is never blocked on training.
    """
    upload = db.get(ClientLabUpload, upload_id)
    if upload is None:
        return
    if not is_simple_tabular(upload):
        reasons = []
        if upload.kind not in SIMPLE_KINDS:
            reasons.append(f"file kind '{upload.kind}' is not a simple tabular kind")
        if not upload.has_named_fields:
            reasons.append("file has no named fields")
        if upload.record_count < MIN_TRAIN_ROWS:
            reasons.append(f"only {upload.record_count} rows (need at least {MIN_TRAIN_ROWS})")
        _mark(db, upload, status=SKIPPED, log={"reason": "; ".join(reasons) or "not a simple tabular file"})
        return

    current_stage = QUEUED
    trace: list[dict[str, Any]] = []

    def _stage(stage: str) -> None:
        nonlocal current_stage
        current_stage = stage
        row = db.get(ClientLabUpload, upload_id)
        if row is not None:
            _mark(db, row, status=stage)

    def _trace(step: str, fn: str, **payload: Any) -> None:
        entry = {"step": step, "fn": fn, **payload}
        trace.append(entry)
        logger.info("auto-train %s %s %s", upload_id, step, fn)

    def _fail(
        reason: str,
        extra: dict[str, Any] | None = None,
        experiment_id: UUID | None = None,
    ) -> None:
        row = db.get(ClientLabUpload, upload_id)
        if row is None:
            return
        payload = dict(extra or {})
        payload["reason"] = reason
        payload["failed_at"] = current_stage
        payload["pipeline_trace"] = list(trace)
        _mark(db, row, status=FAILED, log=payload, experiment_id=experiment_id)

    _stage(INGESTING)
    try:
        frame = _load_upload_frame(upload.stored_path)
        frame.columns = [str(c) for c in frame.columns]
        columns = list(frame.columns)
        if not columns or frame.empty:
            _fail("the file loaded but had no usable rows or columns")
            return

        _stage(ANALYZING)
        profile = profile_frame(frame)
        quality = quality_report(frame)
        _trace(
            "profiling",
            "app.engine.schema.profiler.profile_frame",
            row_count=profile["row_count"],
            column_count=profile["column_count"],
            column_names=list(profile.get("column_names") or []),
            missing_count=profile.get("missing_count"),
            duplicate_rows=profile.get("duplicate_rows", profile.get("duplicate_count")),
        )

        coerced = coerce_numeric_like(frame, columns)
        target = pick_target_heuristic(coerced, columns)
        if target.column is None:
            _fail(
                target.reason,
                extra={
                    "analysis": profile,
                    "eda": {
                        "row_count": profile["row_count"],
                        "column_count": profile["column_count"],
                        "duplicate_rows": profile.get("duplicate_rows", profile.get("duplicate_count")),
                    },
                    "quality": quality,
                },
            )
            return

        _stage(CLEANING)
        feature_columns = [c for c in columns if c != target.column]
        frame, cleaning_log = clean_frame(frame, target=target.column, feature_columns=feature_columns)
        if len(frame) < MIN_TRAIN_ROWS:
            _fail(
                f"only {len(frame)} rows left after cleaning",
                extra={
                    "target": {"column": target.column, "reason": target.reason},
                    "analysis": profile,
                    "cleaning": cleaning_log,
                },
            )
            return

        kept_columns = [c for c in frame.columns if c != target.column]
        missing_plan = plan_missing_values(frame, kept_columns)
        frame = record_missing_value_decisions(db, upload.id, frame, missing_plan, target.column)
        db.commit()
        kept_columns = [c for c in kept_columns if c not in missing_plan.dropped_columns and c in frame.columns]
        _trace(
            "cleaning",
            "app.engine.lab.auto_prepare.plan_missing_values",
            row_count=int(len(frame)),
            dropped_columns=list(missing_plan.dropped_columns),
            rows_with_missing=missing_plan.rows_with_missing,
            column_decisions=[item.column + ":" + item.action for item in missing_plan.column_decisions],
        )
        _stage(FEATURE_ENGINEERING)
        frame, fe_transformations = engineer_features(frame, kept_columns)
        _trace(
            "feature_engineering_transforms",
            "app.engine.lab.auto_prepare.engineer_features",
            transformations=fe_transformations,
            column_count=int(frame.shape[1]),
        )
        num_cols, cat_cols = split_column_roles(frame, kept_columns)
        num_cols, cat_cols = record_column_type_decisions(db, upload.id, frame, num_cols, cat_cols)
        db.commit()
        if not num_cols and not cat_cols:
            _fail(
                "no usable feature columns after removing identifiers, constants, and mostly-empty columns",
                extra={
                    "target": {"column": target.column, "reason": target.reason},
                    "dropped_columns": missing_plan.dropped_columns,
                    "analysis": profile,
                    "cleaning": cleaning_log,
                },
            )
            return

        search = _search_config()
        groups_map = {"features": num_cols + cat_cols}
        combos = generate_group_combinations(
            list(groups_map.keys()),
            strategy="limited",
            max_combinations=search.max_feature_group_combinations,
            seed=search.seed,
        )
        combo = combos[0] if combos else tuple(groups_map.keys())
        modeled_cols = features_for_groups(groups_map, combo)
        _trace(
            "feature_engineering",
            "app.engine.features.combinations.generate_group_combinations",
            groups=list(groups_map.keys()),
            combinations=[list(item) for item in combos],
            selected_group=list(combo),
            selected_columns=list(modeled_cols),
        )
        _trace(
            "column_roles",
            "app.engine.lab.auto_prepare.split_column_roles",
            numerical_cols=list(num_cols),
            categorical_cols=list(cat_cols),
        )

        _stage(PREPROCESSING)
        dataset_dir = REPO_ROOT / "data" / "client_lab_datasets"
        dataset_dir.mkdir(parents=True, exist_ok=True)
        dataset_path = dataset_dir / f"{upload.id}.csv"
        frame.to_csv(dataset_path, index=False)

        env = seed_dogfood(db)
        dataset = ingest_dataset(
            db,
            environment=env,
            name=f"client-upload-{upload.id}",
            location=str(dataset_path),
            source_type="csv",
            version="v1",
        )

        task_type = target.task_type if target.task_type in {"binary", "regression"} else "binary"
        metric = target.evaluation_metric if task_type == "regression" else "pr_auc"
        task_spec = TaskSpec(
            id=f"open_ingest_{upload.id.hex[:12]}",
            name=f"Auto-train: {upload.original_filename}",
            description="Automatic training job for a Labs custom-box upload (simple tabular file).",
            task_type=task_type,
            target=target.column,
            entity_id=kept_columns[0] if kept_columns else target.column,
            prediction_time_column=None,
            evaluation_metric=metric,
            feature_groups={"features": list(modeled_cols)},
            validation_strategy="stratified" if task_type == "binary" else "random",
            column_roles={"numerical": num_cols, "categorical": cat_cols},
        )
        task_row = upsert_task(db, env, task_spec)
        experiment = create_experiment(db, environment=env, dataset=dataset, task=task_row, config=search)
        experiment = execute_experiment(db, experiment, on_stage=_stage)
        if experiment.status != "COMPLETED":
            result = dict(experiment.result or {})
            reason = result.get("error") or f"experiment ended with status {experiment.status}"
            _fail(str(reason), extra={"experiment_status": experiment.status}, experiment_id=experiment.id)
            return

        avail = available_families(task_type)
        boost_family_used = next(
            (
                name
                for name in ("xgboost", "lightgbm", "xgboost_regressor", "lightgbm_regressor")
                if name in avail
            ),
            None,
        )
        result = dict(experiment.result or {})
        result["analysis"] = profile
        result["cleaning"] = cleaning_log
        result["feature_engineering"] = {
            "transformations": fe_transformations,
            "numerical_cols": num_cols,
            "categorical_cols": cat_cols,
            "group_combinations": [list(item) for item in combos],
        }
        split_meta = dict(result.get("split") or {})
        trained = [row for row in (result.get("candidates") or []) if row.get("status") == "trained"]
        winner = dict(result.get("best_single") or {})
        _trace(
            "preprocessing",
            "app.engine.lab.auto_prepare.build_preprocessor",
            numerical_cols=list(winner.get("numerical_cols") or num_cols),
            categorical_cols=list(winner.get("categorical_cols") or cat_cols),
            kind="column_transformer",
        )
        _trace(
            "splitting",
            "app.engine.validation.splits.split_train_test_holdout",
            strategy=split_meta.get("strategy"),
            n_train=split_meta.get("n_train"),
            n_test=split_meta.get("n_test"),
            n_val=split_meta.get("n_val"),
        )
        _trace(
            "cross_validation",
            "app.engine.experiments.runner._run_open_ingest_candidates",
            n_folds=(trained[0].get("n_folds") if trained else None),
            cv_strategy=(trained[0].get("cv_strategy") if trained else None),
            families=[row.get("model_family") for row in trained],
        )
        _trace(
            "training",
            "app.engine.models.registry.make_model",
            winner_family=winner.get("model_family"),
            winner_id=winner.get("candidate_id"),
            n_trained=len(trained),
        )
        test_metrics = dict(result.get("test_metrics") or {})
        eval_fn = (
            "app.engine.evaluation.metrics.classification_metrics"
            if task_type == "binary"
            else "app.engine.evaluation.metrics.regression_metrics"
        )
        _trace(
            "evaluating",
            eval_fn,
            metric_names=sorted(test_metrics.keys()),
            n_test=split_meta.get("n_test"),
        )
        predictions = list(result.get("test_predictions") or [])
        _trace(
            "predicting",
            "app.engine.experiments.runner._prediction_rows",
            n_predictions=len(predictions),
        )
        experiment.result = result
        db.commit()

        dropped_columns = list(
            dict.fromkeys(list(cleaning_log.get("dropped_columns") or []) + list(missing_plan.dropped_columns))
        )
        cleaning_decisions = {
            item["column"]: item
            for item in (cleaning_log.get("missing_value_plan") or {}).get("column_decisions") or []
        }
        for item in missing_plan.column_decisions:
            cleaning_decisions[item.column] = asdict(item)
        log = {
            "analysis": profile,
            "eda": {
                "row_count": profile["row_count"],
                "column_count": profile["column_count"],
                "duplicate_rows": profile.get("duplicate_rows", profile.get("duplicate_count")),
                "missing_count": profile.get("missing_count"),
                "constant_columns": profile.get("constant_columns"),
                "high_cardinality_columns": profile.get("high_cardinality_columns"),
                "likely_identifier_columns": profile.get("likely_identifier_columns"),
            },
            "quality": quality,
            "cleaning": cleaning_log,
            "feature_engineering": {
                "transformations": fe_transformations,
                "numerical_cols": num_cols,
                "categorical_cols": cat_cols,
            },
            "preprocessing": {
                "numerical": ["imputer:median", "scaler:standard"],
                "categorical": ["imputer:most_frequent", "onehot:drop_first"],
            },
            "target": {"column": target.column, "reason": target.reason},
            "missing_value_decisions": {
                "dropped_columns": dropped_columns,
                "rows_with_missing": missing_plan.rows_with_missing,
                "row_missing_fraction": missing_plan.row_missing_fraction,
                "drop_rows_recommended": missing_plan.drop_rows_recommended,
                "column_decisions": list(cleaning_decisions.values()),
            },
            "numerical_cols": num_cols,
            "categorical_cols": cat_cols,
            "boost_family_used": boost_family_used,
            "model_families": [row.get("model_family") for row in (result.get("candidates") or [])],
            "experiment_status": experiment.status,
            "pipeline_trace": list(trace),
        }
        upload = db.get(ClientLabUpload, upload_id)
        if upload is not None:
            _mark(db, upload, status=COMPLETED, log=log, experiment_id=experiment.id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("auto-train job failed for upload %s", upload_id)
        db.rollback()
        _fail(f"unexpected error: {exc}")


def enqueue_auto_train(upload_id: UUID) -> None:
    """Fire-and-forget: runs in a background thread with its own DB session so
    the upload request is never blocked on training."""

    def _worker() -> None:
        session = get_session_factory()()
        try:
            run_auto_train_job(session, upload_id)
        except Exception:  # noqa: BLE001
            logger.exception("auto-train worker crashed for upload %s", upload_id)
        finally:
            session.close()

    threading.Thread(target=_worker, daemon=True, name=f"auto-train-{upload_id}").start()
