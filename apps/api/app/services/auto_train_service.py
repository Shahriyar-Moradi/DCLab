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
from app.engine.data.quality import quality_report
from app.engine.lab.auto_prepare import (
    coerce_numeric_like,
    pick_target_heuristic,
    plan_missing_values,
    split_column_roles,
)
from app.engine.lab.column_map import MIN_TRAIN_ROWS
from app.engine.models.registry import available_families
from app.engine.schema.profiler import profile_frame
from app.engine.types import SearchConfig, TaskSpec
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
    upload.pipeline_status = status
    if log is not None:
        upload.pipeline_log = log
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
        _mark(db, upload, status="skipped", log={"reason": "; ".join(reasons) or "not a simple tabular file"})
        return

    _mark(db, upload, status="running")
    try:
        frame = _load_upload_frame(upload.stored_path)
        frame.columns = [str(c) for c in frame.columns]
        columns = list(frame.columns)
        if not columns or frame.empty:
            _mark(db, upload, status="failed", log={"reason": "the file loaded but had no usable rows or columns"})
            return

        profile = profile_frame(frame)
        quality = quality_report(frame)

        frame = coerce_numeric_like(frame, columns)
        target = pick_target_heuristic(frame, columns)
        if target.column is None:
            _mark(
                db,
                upload,
                status="failed",
                log={
                    "reason": target.reason,
                    "eda": {
                        "row_count": profile["row_count"],
                        "column_count": profile["column_count"],
                        "duplicate_rows": profile["duplicate_rows"],
                    },
                    "quality": quality,
                },
            )
            return

        frame = frame.dropna(subset=[target.column]).reset_index(drop=True)
        if len(frame) < MIN_TRAIN_ROWS:
            _mark(
                db,
                upload,
                status="failed",
                log={
                    "reason": f"only {len(frame)} rows left after dropping rows with a missing target",
                    "target": {"column": target.column, "reason": target.reason},
                },
            )
            return

        feature_columns = [c for c in columns if c != target.column]
        missing_plan = plan_missing_values(frame, feature_columns)
        kept_columns = [c for c in feature_columns if c not in missing_plan.dropped_columns]
        num_cols, cat_cols = split_column_roles(frame, kept_columns)
        if not num_cols and not cat_cols:
            _mark(
                db,
                upload,
                status="failed",
                log={
                    "reason": "no usable feature columns after removing identifiers, constants, and mostly-empty columns",
                    "target": {"column": target.column, "reason": target.reason},
                    "dropped_columns": missing_plan.dropped_columns,
                },
            )
            return

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

        task_spec = TaskSpec(
            id=f"open_ingest_{upload.id.hex[:12]}",
            name=f"Auto-train: {upload.original_filename}",
            description="Automatic training job for a Labs custom-box upload (simple tabular file).",
            task_type="binary",
            target=target.column,
            entity_id=kept_columns[0] if kept_columns else target.column,
            prediction_time_column=None,
            evaluation_metric="pr_auc",
            feature_groups={"features": num_cols + cat_cols},
            validation_strategy="stratified",
            column_roles={"numerical": num_cols, "categorical": cat_cols},
        )
        task_row = upsert_task(db, env, task_spec)
        experiment = create_experiment(db, environment=env, dataset=dataset, task=task_row, config=_search_config())
        experiment = execute_experiment(db, experiment)

        boost_family_used = "xgboost" if "xgboost" in available_families("binary") else "gradient_boosting"
        log = {
            "eda": {
                "row_count": profile["row_count"],
                "column_count": profile["column_count"],
                "duplicate_rows": profile["duplicate_rows"],
            },
            "quality": quality,
            "target": {"column": target.column, "reason": target.reason},
            "missing_value_decisions": {
                "dropped_columns": missing_plan.dropped_columns,
                "rows_with_missing": missing_plan.rows_with_missing,
                "row_missing_fraction": missing_plan.row_missing_fraction,
                "drop_rows_recommended": missing_plan.drop_rows_recommended,
                "column_decisions": [asdict(item) for item in missing_plan.column_decisions],
            },
            "numerical_cols": num_cols,
            "categorical_cols": cat_cols,
            "boost_family_used": boost_family_used,
            "experiment_status": experiment.status,
        }
        status = "completed" if experiment.status == "COMPLETED" else "failed"
        _mark(db, upload, status=status, log=log, experiment_id=experiment.id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("auto-train job failed for upload %s", upload_id)
        db.rollback()
        upload = db.get(ClientLabUpload, upload_id)
        if upload is not None:
            _mark(db, upload, status="failed", log={"reason": f"unexpected error: {exc}"})


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
