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
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO_ROOT, get_settings
from app.db.models import (
    ClientLabUpload,
    Experiment,
    ExperimentCandidate,
    LabDecisionRecord,
    ModelAsset,
    ModelVersion,
    WorkflowRun,
)
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
    SPLITTING,
)
from app.engine.data.quality import quality_report
from app.engine.features.combinations import features_for_groups, generate_group_combinations
from app.engine.lab.auto_prepare import (
    apply_feature_engineering_actions,
    coerce_numeric_like,
    engineer_features,
    infer_column_roles,
    plan_missing_values,
    split_column_roles,
    structural_clean_frame,
)
from app.engine.features.encode import coerce_binary_target
from app.engine.lab.schema_inference import MIN_TRAIN_ROWS, infer_entity_column
from app.engine.modeling.leakage_auditor import consult_leakage_llm, plan_model_development
from app.engine.models.registry import available_families
from app.engine.schema.profiler import profile_frame
from app.engine.types import SearchConfig, TaskSpec
from app.engine.validation.splits import SOURCE_ROW_COLUMN, split_train_test_holdout
from app.services.lab_decision_ledger import (
    record_column_type_decisions,
    record_missing_value_decisions,
    resolve_target_selection,
)
from app.services.lab_service import create_experiment, execute_experiment, ingest_dataset, seed_dogfood, upsert_task
from app.services.observability_service import PipelineRunObserver
from app.services.pipeline_verifier import verify_pipeline
from app.services.pipeline_audit_service import request_pipeline_verification
from app.services.technical_run_report import build_technical_run_report

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
    workflow_run = db.scalar(
        select(WorkflowRun).where(WorkflowRun.source_upload_id == upload.id)
    )
    if workflow_run is not None:
        if status in {COMPLETED, FAILED, SKIPPED}:
            workflow_run.status = status
            workflow_run.completed_at = datetime.now(UTC)
            workflow_run.failure_reason = (
                str(merged.get("reason"))[:2048]
                if status in {FAILED, SKIPPED} and merged.get("reason")
                else None
            )
        elif status != QUEUED:
            workflow_run.status = "running"
            workflow_run.failure_reason = None
            if workflow_run.started_at is None:
                workflow_run.started_at = datetime.now(UTC)
    effective_experiment_id = experiment_id or upload.experiment_id
    experiment = (
        db.get(Experiment, effective_experiment_id)
        if effective_experiment_id is not None
        else None
    )
    if experiment is not None and status in {COMPLETED, FAILED, SKIPPED}:
        experiment.status = status.upper()
        if experiment.ended_at is None:
            experiment.ended_at = datetime.now(UTC)
        experiment.failure_reason = (
            str(merged.get("reason"))[:2048]
            if status in {FAILED, SKIPPED} and merged.get("reason")
            else None
        )
    db.commit()


def run_auto_train_job(db: Session, upload_id: UUID) -> None:
    """Runs synchronously against the given session. In production this is
    called from `enqueue_auto_train`'s background thread (its own session) so
    `POST /app/labs/uploads` is never blocked on training.
    """
    total_started_at = datetime.now(UTC)
    total_timer = time.perf_counter()
    upload = db.get(ClientLabUpload, upload_id)
    if upload is None:
        return
    observer = PipelineRunObserver.for_upload(db, upload_id)

    def _emit_event(
        stage: str,
        event_type: str,
        status: str,
        payload: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> None:
        if observer is not None:
            observer.emit(stage, event_type, status, payload, duration_ms)

    if not is_simple_tabular(upload):
        reasons = []
        if upload.kind not in SIMPLE_KINDS:
            reasons.append(f"file kind '{upload.kind}' is not a simple tabular kind")
        if not upload.has_named_fields:
            reasons.append("file has no named fields")
        if upload.record_count < MIN_TRAIN_ROWS:
            reasons.append(f"only {upload.record_count} rows (need at least {MIN_TRAIN_ROWS})")
        reason = "; ".join(reasons) or "not a simple tabular file"
        _mark(db, upload, status=SKIPPED, log={"reason": reason})
        _emit_event("terminal", "pipeline_terminal", "skipped", {"reason": reason})
        return

    current_stage = QUEUED
    trace: list[dict[str, Any]] = []
    stage_timings: list[dict[str, Any]] = []
    evidence_stage_timings: list[dict[str, Any]] = []
    current_rows = int(upload.record_count)
    active_stage: dict[str, Any] | None = None
    event_stage_names = {
        "file_ingestion": "ingestion",
        "profiling": "profiling_eda",
        "target_task_resolution": "target_task",
        "structural_cleaning": "structural_cleaning",
        "splitting": "holdout_lock",
        "train_only_decisions": "train_only_decisions",
        "column_roles": "column_roles",
        "feature_engineering": "feature_engineering",
        "preprocessing_setup": "preprocessing_configuration",
        "deterministic_verification": "deterministic_verification",
        "report_generation": "report",
    }

    def _evidence_start(stage: str) -> dict[str, Any]:
        event_stage = event_stage_names.get(stage, stage)
        _emit_event(event_stage, "operation_started", "started")
        return {
            "stage": stage,
            "event_stage": event_stage,
            "started_at": datetime.now(UTC),
            "timer": time.perf_counter(),
            "rows_in": current_rows,
        }

    def _evidence_finish(token: dict[str, Any], *, status: str = "completed") -> dict[str, Any]:
        ended_at = datetime.now(UTC)
        record = {
            "stage": token["stage"],
            "started_at": token["started_at"].isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_ms": max(0.001, (time.perf_counter() - token["timer"]) * 1000.0),
            "status": status,
            "rows_in": token["rows_in"],
            "rows_out": current_rows,
        }
        evidence_stage_timings.append(record)
        _emit_event(
            str(token["event_stage"]),
            "operation_completed",
            status,
            {"rows_in": token["rows_in"], "rows_out": current_rows},
            record["duration_ms"],
        )
        return record

    def _finish_stage(*, status: str = "completed") -> None:
        nonlocal active_stage
        if active_stage is None:
            return
        ended = datetime.now(UTC)
        stage_timings.append(
            {
                "stage": active_stage["stage"],
                "started_at": active_stage["started_at"],
                "ended_at": ended.isoformat(),
                "duration_ms": max(0.001, (time.perf_counter() - active_stage["timer"]) * 1000.0),
                "rows_in": active_stage["rows_in"],
                "rows_out": current_rows,
                "status": status,
            }
        )
        active_stage = None

    def _stage(stage: str) -> None:
        nonlocal active_stage, current_stage
        if active_stage is not None and active_stage["stage"] == stage:
            return
        _finish_stage()
        current_stage = stage
        active_stage = {
            "stage": stage,
            "started_at": datetime.now(UTC).isoformat(),
            "timer": time.perf_counter(),
            "rows_in": current_rows,
        }
        row = db.get(ClientLabUpload, upload_id)
        if row is not None:
            _mark(db, row, status=stage)

    def _trace(step: str, fn: str, **payload: Any) -> None:
        entry = {"step": step, "fn": fn, **payload}
        trace.append(entry)
        logger.info("auto-train %s %s %s", upload_id, step, fn)

    def _request_routine_advisory_verification() -> None:
        """Run only after ML state commits; provider failure cannot fail the job."""
        if not get_settings().pipeline_llm_verifier_enabled:
            return
        try:
            request_pipeline_verification(db, upload_id)
        except Exception:  # noqa: BLE001 - advisory isolation is intentional
            logger.exception("advisory pipeline verification failed for upload %s", upload_id)

    def _fail(
        reason: str,
        extra: dict[str, Any] | None = None,
        experiment_id: UUID | None = None,
    ) -> None:
        row = db.get(ClientLabUpload, upload_id)
        if row is None:
            return
        _finish_stage(status="failed")
        payload = {**dict(row.pipeline_log or {}), **dict(extra or {})}
        payload["reason"] = reason
        payload["failed_at"] = current_stage
        payload["pipeline_trace"] = list(trace)
        verification_timer = _evidence_start("deterministic_verification")
        partial_result = dict(extra or {})
        partial_result.setdefault("status", "FAILED")
        partial_result.setdefault("analysis", partial_result.get("analysis") or {})
        partial_result.setdefault("artifact_dir", None)
        ml_ended_at = datetime.now(UTC)
        ml_execution_total = {
            "stage": "ml_execution_total",
            "started_at": total_started_at.isoformat(),
            "ended_at": ml_ended_at.isoformat(),
            "duration_ms": max(0.001, (time.perf_counter() - total_timer) * 1000.0),
            "status": "failed",
            "rows_in": int(upload.record_count),
            "rows_out": current_rows,
        }
        partial_timings = [*evidence_stage_timings, ml_execution_total]
        partial_log = {**payload, "stage_timings": partial_timings}
        effective_experiment_id = experiment_id or row.experiment_id
        experiment = (
            db.get(Experiment, effective_experiment_id)
            if effective_experiment_id is not None
            else None
        )
        preliminary_report = build_technical_run_report(
            db,
            upload=row,
            experiment=experiment,
            result=partial_result,
            pipeline_log=partial_log,
        )
        verification = verify_pipeline(preliminary_report)
        _evidence_finish(verification_timer)
        partial_result["deterministic_verification"] = verification
        partial_log["deterministic_verification"] = verification
        report_timer = _evidence_start("report_generation")
        report = build_technical_run_report(
            db,
            upload=row,
            experiment=experiment,
            result=partial_result,
            pipeline_log={
                **partial_log,
                "stage_timings": [*partial_timings, evidence_stage_timings[-1]],
            },
        )
        _evidence_finish(report_timer)
        workflow_ended_at = datetime.now(UTC)
        workflow_elapsed = {
            "stage": "workflow_elapsed",
            "started_at": total_started_at.isoformat(),
            "ended_at": workflow_ended_at.isoformat(),
            "duration_ms": max(0.001, (time.perf_counter() - total_timer) * 1000.0),
            "status": "failed",
            "rows_in": int(upload.record_count),
            "rows_out": current_rows,
        }
        final_timings = [*partial_timings, *evidence_stage_timings[-2:], workflow_elapsed]
        report["stage_timings"] = final_timings
        payload["stage_timings"] = final_timings
        payload["deterministic_verification"] = verification
        payload["technical_report"] = report
        if experiment is not None:
            partial_result["deterministic_verification"] = verification
            partial_result["technical_report"] = report
            partial_result["stage_timings"] = final_timings
            partial_result["error"] = reason
            experiment.result = partial_result
            experiment.failure_reason = reason[:2048]
        _mark(
            db,
            row,
            status=FAILED,
            log=payload,
            experiment_id=effective_experiment_id,
        )
        _emit_event(
            "terminal",
            "pipeline_terminal",
            "failed",
            {"reason": reason, "failed_at": current_stage},
        )
        _request_routine_advisory_verification()

    _stage(INGESTING)
    try:
        evidence_timer = _evidence_start("file_ingestion")
        frame = _load_upload_frame(upload.stored_path)
        current_rows = int(len(frame))
        frame.columns = [str(c) for c in frame.columns]
        columns = list(frame.columns)
        if not columns or frame.empty:
            _evidence_finish(evidence_timer, status="failed")
            _fail("the file loaded but had no usable rows or columns")
            return
        _evidence_finish(evidence_timer)

        _stage(ANALYZING)
        evidence_timer = _evidence_start("profiling")
        profile = profile_frame(frame)
        quality = quality_report(frame)
        _evidence_finish(evidence_timer)
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
        evidence_timer = _evidence_start("target_task_resolution")
        target = resolve_target_selection(
            coerced,
            columns,
            explicit_target=upload.explicit_target_column,
            db=db,
            upload_id=upload.id,
        )
        target_evidence = {
            **target.audit_dict(),
            "target_column": target.column,
            "locked_at": datetime.now(UTC).isoformat() if target.column is not None else None,
        }
        workflow_run = db.scalar(
            select(WorkflowRun).where(WorkflowRun.source_upload_id == upload.id)
        )
        if workflow_run is not None:
            workflow_run.resolved_target = target.column
            workflow_run.task_type = target.task_type if target.column is not None else None
            db.commit()
        _evidence_finish(evidence_timer, status="completed" if target.column is not None else "failed")
        if target.column is None:
            _fail(
                target.reason,
                extra={
                    "target": target_evidence,
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
        if target.task_type not in {"binary", "regression"}:
            _fail(
                f"target {target.column!r} implies unsupported task type {target.task_type!r}",
                extra={"target": target_evidence, "analysis": profile, "quality": quality},
            )
            return

        _stage(CLEANING)
        evidence_timer = _evidence_start("structural_cleaning")
        feature_columns = [c for c in columns if c != target.column]
        frame, cleaning_log = structural_clean_frame(
            coerced,
            target=target.column,
            feature_columns=feature_columns,
            source_row_column=SOURCE_ROW_COLUMN,
        )
        if target.task_type == "binary":
            frame[target.column] = coerce_binary_target(frame[target.column])
        else:
            frame[target.column] = pd.to_numeric(frame[target.column], errors="coerce")
        invalid_target_rows = int(frame[target.column].isna().sum())
        if invalid_target_rows:
            frame = frame.dropna(subset=[target.column]).reset_index(drop=True)
            cleaning_log["transformations"].append(
                {"step": "drop_unusable_target_rows", "rows_removed": invalid_target_rows}
            )
            cleaning_log["missing_target_rows_removed"] += invalid_target_rows
            cleaning_log["rows_out"] = int(len(frame))
        if target.task_type == "binary":
            frame[target.column] = frame[target.column].astype(int)
        current_rows = int(len(frame))
        _evidence_finish(evidence_timer)
        if len(frame) < MIN_TRAIN_ROWS:
            _fail(
                f"only {len(frame)} rows left after cleaning",
                extra={
                    "target": target_evidence,
                    "analysis": profile,
                    "cleaning": cleaning_log,
                },
            )
            return

        # The final holdout is locked before any modeling decision is derived.
        _stage(SPLITTING)
        evidence_timer = _evidence_start("splitting")
        locked_train, _locked_val, _locked_test, locked_split = split_train_test_holdout(
            frame,
            target=target.column,
            test_size=0.2,
            seed=42,
            stratify=target.task_type == "binary",
        )
        _trace(
            "splitting",
            "app.engine.validation.splits.split_train_test_holdout",
            strategy=locked_split.get("strategy"),
            n_train=locked_split.get("n_train"),
            n_test=locked_split.get("n_test"),
            provenance_disjoint=locked_split.get("provenance_disjoint"),
        )
        _evidence_finish(evidence_timer)

        evidence_timer = _evidence_start("train_only_decisions")
        (
            _problem_profile,
            _validation_plan,
            _metric_plan,
            _leakage_audit,
            development_plan,
        ) = plan_model_development(
            locked_train,
            target=target.column,
            task_type=target.task_type,
            requested_folds=5,
            random_state=42,
            reviewer=consult_leakage_llm,
            conservative_auto_train=True,
        )
        allowed_predictors = set(development_plan.allowed_features)
        leakage_excluded = [item["column"] for item in development_plan.excluded_features]
        leakage_blocked = {
            item["column"]
            for item in development_plan.excluded_features
            if item["risk"] in {"HIGH", "CRITICAL"}
        }
        decision_columns = [
            c for c in feature_columns if c in locked_train.columns and c != SOURCE_ROW_COLUMN
        ]
        missing_plan = plan_missing_values(locked_train, decision_columns)
        locked_train = record_missing_value_decisions(
            db,
            upload.id,
            locked_train,
            missing_plan,
            target.column,
        )
        db.commit()
        _emit_event(
            "missing_value_decisions",
            "missing_value_decisions_completed",
            "completed",
            {
                "decision_count": len(missing_plan.column_decisions),
                "dropped_column_count": len(missing_plan.dropped_columns),
            },
        )
        for decision in missing_plan.column_decisions:
            if decision.action == "domain_fill" and decision.fill_value is not None and decision.column in frame:
                frame[decision.column] = frame[decision.column].fillna(decision.fill_value)
        if missing_plan.dropped_columns:
            frame = frame.drop(columns=[c for c in missing_plan.dropped_columns if c in frame.columns])
            locked_train = locked_train.drop(
                columns=[c for c in missing_plan.dropped_columns if c in locked_train.columns]
            )
        kept_columns = [
            c
            for c in decision_columns
            if c not in missing_plan.dropped_columns and c in locked_train.columns
        ]
        modeled_kept_columns = [c for c in kept_columns if c not in leakage_blocked]
        cleaning_log["decision_scope"] = "locked_training_partition_only"
        cleaning_log["dropped_columns"] = list(missing_plan.dropped_columns)
        cleaning_log["leakage_excluded_predictors"] = list(leakage_excluded)
        cleaning_log["missing_value_plan"] = {
            "evidence_rows": int(len(locked_train)),
            "decision_partition": "train",
            "evidence_source_rows": list(locked_split.get("train_source_rows") or []),
            "dropped_columns": list(missing_plan.dropped_columns),
            "rows_with_missing": missing_plan.rows_with_missing,
            "row_missing_fraction": missing_plan.row_missing_fraction,
            "drop_rows_recommended": missing_plan.drop_rows_recommended,
            "column_decisions": [asdict(item) for item in missing_plan.column_decisions],
        }
        _trace(
            "train_only_modeling_decisions",
            "app.engine.lab.auto_prepare.plan_missing_values",
            evidence_rows=int(len(locked_train)),
            evidence_scope="train_only",
            dropped_columns=list(missing_plan.dropped_columns),
            rows_with_missing=missing_plan.rows_with_missing,
            column_decisions=[item.column + ":" + item.action for item in missing_plan.column_decisions],
        )
        _evidence_finish(evidence_timer)

        # Record raw/train-only roles before feature transformations, then
        # derive final modeled roles from the transformed training partition.
        evidence_timer = _evidence_start("column_roles")
        initial_roles = infer_column_roles(locked_train, kept_columns)
        _evidence_finish(evidence_timer)

        _stage(FEATURE_ENGINEERING)
        evidence_timer = _evidence_start("feature_engineering")
        engineered_train, fe_transformations = engineer_features(locked_train, modeled_kept_columns)
        frame = apply_feature_engineering_actions(frame, fe_transformations)
        _evidence_finish(evidence_timer)
        _trace(
            "feature_engineering_transforms",
            "app.engine.lab.auto_prepare.engineer_features",
            transformations=fe_transformations,
            evidence_scope="train_only",
            column_count=int(engineered_train.shape[1]),
        )
        evidence_timer = _evidence_start("column_roles_finalization")
        final_roles = infer_column_roles(engineered_train, kept_columns)
        num_cols, cat_cols = split_column_roles(engineered_train, kept_columns)
        num_cols, cat_cols = record_column_type_decisions(
            db,
            upload.id,
            engineered_train,
            num_cols,
            cat_cols,
        )
        db.commit()
        llm_identifier_cols = [
            name
            for name in final_roles.numerical
            if name not in num_cols and name not in cat_cols
        ]
        identifier_cols = list(dict.fromkeys(final_roles.identifier + llm_identifier_cols))
        entity_column = infer_entity_column(engineered_train, kept_columns)
        num_cols = [name for name in num_cols if name in allowed_predictors]
        cat_cols = [name for name in cat_cols if name in allowed_predictors]
        _evidence_finish(evidence_timer)
        transformed_datetime = {
            str(name)
            for action in fe_transformations
            for name in (action.get("output_columns") or action.get("columns") or [])
        }
        role_decisions = {
            row.column: row
            for row in db.query(LabDecisionRecord)
            .filter(LabDecisionRecord.upload_id == upload.id)
            .all()
            if str(row.prompt_version).startswith("column_type_")
        }
        column_role_records: list[dict[str, Any]] = []
        raw_dtypes = {
            str(item.get("name")): str(item.get("dtype"))
            for item in profile.get("columns") or []
            if isinstance(item, dict)
        }
        for column in [*feature_columns, target.column]:
            if column == target.column:
                final_role = "target"
                source = target.source
                reason = target.reason
                confidence = target.confidence
                verdict = target.validator_verdict
                llm_used = target.raw_llm_output is not None
            elif column in missing_plan.dropped_columns:
                final_role = "ignored/free_text"
                source = "rule"
                reason = "Excluded by the train-only missing-value policy before modeling."
                confidence = 1.0
                verdict = "not_run"
                llm_used = False
            elif column in {item["column"] for item in development_plan.excluded_features}:
                exclusion = next(item for item in development_plan.excluded_features if item["column"] == column)
                identifier_excluded = "identifier_not_a_predictor" in (exclusion.get("reasons") or [])
                final_role = "identifier" if identifier_excluded else "ignored/free_text"
                source = "rule"
                reason = f"Excluded from estimators by the train-only leakage plan: {exclusion.get('reason')}."
                confidence = 1.0
                verdict = "not_run"
                llm_used = False
            else:
                role_decision = role_decisions.get(column)
                if column in num_cols:
                    final_role = "datetime" if column in transformed_datetime else "numerical"
                elif column in cat_cols:
                    final_role = "boolean" if column in initial_roles.boolean else "categorical"
                elif column in identifier_cols:
                    final_role = "identifier"
                else:
                    final_role = "ignored/free_text"
                source = role_decision.source if role_decision is not None else "rule"
                reason = (
                    "Validated semantic role decision."
                    if role_decision is not None
                    else f"Deterministic train-only role inference classified the column as {final_role}."
                )
                confidence = (
                    (role_decision.raw_llm_output or {}).get("confidence")
                    if role_decision is not None
                    else 1.0
                )
                verdict = role_decision.validator_verdict if role_decision is not None else "not_run"
                llm_used = bool(role_decision is not None and role_decision.raw_llm_output)
            column_role_records.append(
                {
                    "column": column,
                    "original_dtype": raw_dtypes.get(column, "unknown"),
                    "final_role": final_role,
                    "source": source,
                    "reason": reason,
                    "confidence": confidence,
                    "validator_verdict": verdict,
                    "llm_used": llm_used,
                }
            )
        column_role_evidence = {
            "decision_partition": "train",
            "evidence_source_rows": list(locked_split.get("train_source_rows") or []),
            "columns": column_role_records,
        }
        if not num_cols and not cat_cols:
            _fail(
                "no usable feature columns after removing identifiers, constants, and mostly-empty columns",
                extra={
                    "target": target_evidence,
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
        evidence_timer = _evidence_start("preprocessing_setup")
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
            workspace_id=upload.workspace_id,
        )

        task_type = target.task_type
        metric = target.evaluation_metric if task_type == "regression" else "pr_auc"
        transformed_features = [
            str(name)
            for action in fe_transformations
            for name in (action.get("columns") or [])
        ]
        removed_features = list(
            dict.fromkeys(
                list(missing_plan.dropped_columns)
                + list(identifier_cols)
                + list(final_roles.ignored_free_text)
                + list(leakage_excluded)
            )
        )
        feature_report = {
            "original_features": list(feature_columns),
            "generated_features": [],
            "transformed_features": transformed_features,
            "removed_features": removed_features,
            "feature_engineering_actions": list(fe_transformations),
        }
        task_spec = TaskSpec(
            id=f"open_ingest_{upload.id.hex[:12]}",
            name=f"Auto-train: {upload.original_filename}",
            description="Automatic training job for a Labs custom-box upload (simple tabular file).",
            task_type=task_type,
            target=target.column,
            entity_id=entity_column if entity_column in frame.columns else None,
            prediction_time_column=None,
            evaluation_metric=metric,
            feature_groups={"features": list(modeled_cols)},
            validation_strategy="stratified" if task_type == "binary" else "random",
            column_roles={"numerical": num_cols, "categorical": cat_cols},
            feature_engineering=feature_report,
        )
        task_row = upsert_task(db, env, task_spec)
        workflow_run = db.scalar(
            select(WorkflowRun).where(WorkflowRun.source_upload_id == upload.id)
        )
        if workflow_run is not None:
            from app.services.lineage_service import bind_pipeline_run, create_pipeline_run

            experiment = (
                db.get(Experiment, upload.experiment_id)
                if upload.experiment_id is not None
                else None
            )
            if experiment is not None:
                experiment = bind_pipeline_run(
                    db,
                    pipeline_run=experiment,
                    workflow_run=workflow_run,
                    environment=env,
                    dataset=dataset,
                    task=task_row,
                    config=search,
                )
            else:
                experiment = create_pipeline_run(
                    db,
                    workflow_run=workflow_run,
                    environment=env,
                    dataset=dataset,
                    task=task_row,
                    pipeline_name="open_ingest_deterministic_ml",
                    pipeline_index=len(workflow_run.pipeline_runs),
                    pipeline_purpose="training_and_scoring",
                    config=search,
                )
        else:
            experiment = create_experiment(
                db,
                environment=env,
                dataset=dataset,
                task=task_row,
                config=search,
            )
        _evidence_finish(evidence_timer)

        def _experiment_stage(stage: str) -> None:
            # The runner repeats the deterministic split from the persisted
            # prepared table; the holdout was already locked before decisions.
            if stage != SPLITTING:
                _stage(stage)

        experiment = execute_experiment(
            db,
            experiment,
            on_stage=_experiment_stage,
            on_event=observer.callback if observer is not None else None,
        )
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
        result.setdefault("model_development_plan", development_plan.to_dict())
        result["feature_engineering"] = {
            **feature_report,
            "transformations": list(fe_transformations),
            "numerical_cols": num_cols,
            "categorical_cols": cat_cols,
            "group_combinations": [list(item) for item in combos],
        }
        split_meta = dict(result.get("split") or {})
        if split_meta.get("test_source_rows") != locked_split.get("test_source_rows"):
            raise RuntimeError("persisted experiment did not preserve the locked holdout")
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
            "cross_validation",
            "app.engine.experiments.runner._run_open_ingest_candidates",
            n_folds=(trained[0].get("n_folds") if trained else None),
            cv_strategy=(trained[0].get("cv_strategy") if trained else None),
            families=[row.get("model_family") for row in trained],
        )
        _trace(
            "selection_lock",
            "app.engine.experiments.runner._run_open_ingest_candidates",
            selected_candidate_id=(result.get("selection") or {}).get("selected_candidate_id"),
            selection_metric=(result.get("selection") or {}).get("selection_metric"),
            selection_source=(result.get("selection") or {}).get("selection_source"),
            locked=(result.get("selection") or {}).get("locked"),
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
        _finish_stage()
        result["stage_timings"] = [
            *evidence_stage_timings,
            *list(result.get("execution_stage_timings") or []),
        ]

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
                **feature_report,
                "transformations": fe_transformations,
                "numerical_cols": num_cols,
                "categorical_cols": cat_cols,
            },
            "column_roles": {
                "numerical": [name for name in num_cols if name not in transformed_datetime],
                "categorical": [name for name in cat_cols if name not in initial_roles.boolean],
                "boolean": initial_roles.boolean,
                "datetime": sorted(transformed_datetime),
                "identifier": identifier_cols,
                "ignored_free_text": final_roles.ignored_free_text,
            },
            "column_role_evidence": column_role_evidence,
            "preprocessing": {
                "numeric_columns": list(num_cols),
                "categorical_columns": list(cat_cols),
                "numeric_imputer_strategy": "median",
                "numeric_scaler": "StandardScaler",
                "categorical_imputer_strategy": "most_frequent",
                "categorical_encoder": "OneHotEncoder",
                "categorical_encoder_drop": "first",
                "handle_unknown": "ignore",
                "fit_partition": "fold_train_only_then_full_train_for_locked_winner",
                "numerical": ["imputer:median", "scaler:standard"],
                "categorical": ["imputer:most_frequent", "onehot:drop_first"],
            },
            "target": target_evidence,
            "entity": {
                "column": entity_column if entity_column in frame.columns else None,
                "source": "deterministic" if entity_column in frame.columns else "none",
                "reason": (
                    "strong identifier evidence"
                    if entity_column in frame.columns
                    else "no identifier was inferred; prediction rows use stable held-out row indexes"
                ),
            },
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
            "stage_timings": list(result.get("stage_timings") or []),
            "problem_profile": result.get("problem_profile") or {},
            "validation_plan": result.get("validation_plan") or {},
            "metric_plan": result.get("metric_plan") or {},
            "model_development_plan": result.get("model_development_plan") or development_plan.to_dict(),
        }
        upload = db.get(ClientLabUpload, upload_id)
        if upload is not None:
            # Persist the completed ML evidence before the read-only verifier runs.
            ml_ended_at = datetime.now(UTC)
            ml_execution_total = {
                "stage": "ml_execution_total",
                "started_at": total_started_at.isoformat(),
                "ended_at": ml_ended_at.isoformat(),
                "duration_ms": max(0.001, (time.perf_counter() - total_timer) * 1000.0),
                "status": "completed",
                "rows_in": int(upload.record_count),
                "rows_out": current_rows,
            }
            result["stage_timings"] = [*result["stage_timings"], ml_execution_total]
            log["stage_timings"] = list(result["stage_timings"])
            experiment.result = result
            db.commit()
            preliminary_report = build_technical_run_report(
                db,
                upload=upload,
                experiment=experiment,
                result=result,
                pipeline_log=log,
            )
            verification_timer = _evidence_start("deterministic_verification")
            verification = verify_pipeline(preliminary_report)
            _evidence_finish(verification_timer)
            result["deterministic_verification"] = verification
            log["deterministic_verification"] = verification

            report_timer = _evidence_start("report_generation")
            result["technical_report"] = build_technical_run_report(
                db,
                upload=upload,
                experiment=experiment,
                result=result,
                pipeline_log={**log, "stage_timings": [*result["stage_timings"], evidence_stage_timings[-1]]},
            )
            _evidence_finish(report_timer)
            workflow_elapsed = {
                "stage": "workflow_elapsed",
                "started_at": total_started_at.isoformat(),
                "ended_at": datetime.now(UTC).isoformat(),
                "duration_ms": max(0.001, (time.perf_counter() - total_timer) * 1000.0),
                "status": "completed",
                "rows_in": int(upload.record_count),
                "rows_out": current_rows,
            }
            result["stage_timings"] = [
                *result["stage_timings"],
                *evidence_stage_timings[-2:],
                workflow_elapsed,
            ]
            log["stage_timings"] = list(result["stage_timings"])
            result["technical_report"]["stage_timings"] = list(result["stage_timings"])
            result["technical_report"]["deterministic_verification"] = verification
            (Path(experiment.artifact_dir) / "result.json").write_text(
                json.dumps(result, default=str, indent=2) + "\n",
                encoding="utf-8",
            )
            experiment.result = result
            db.commit()
            if workflow_run is not None:
                from app.services.lineage_service import (
                    create_model_asset,
                    create_model_version,
                )

                model_asset = db.scalar(
                    select(ModelAsset).where(
                        ModelAsset.workflow_id == workflow_run.workflow_id,
                        ModelAsset.slug == "client-lab-selected-model",
                    )
                )
                if model_asset is None:
                    model_asset = create_model_asset(
                        db,
                        workspace_id=upload.workspace_id,
                        workflow=workflow_run.workflow,
                        name="Client Lab Selected Model",
                        slug="client-lab-selected-model",
                    )
                selected_key = (result.get("selection") or {}).get(
                    "selected_candidate_id"
                ) or (result.get("best_single") or {}).get("candidate_id")
                selected_candidate = db.scalar(
                    select(ExperimentCandidate).where(
                        ExperimentCandidate.experiment_id == experiment.id,
                        ExperimentCandidate.candidate_key == selected_key,
                    )
                )
                if selected_candidate is None:
                    raise RuntimeError("selected candidate was not persisted")
                version_count = db.query(ModelVersion).filter(
                    ModelVersion.model_asset_id == model_asset.id
                ).count()
                create_model_version(
                    db,
                    model_asset=model_asset,
                    pipeline_run=experiment,
                    selected_candidate=selected_candidate,
                    version=f"v{version_count + 1}",
                )
            _mark(db, upload, status=COMPLETED, log=log, experiment_id=experiment.id)
            _emit_event(
                "terminal",
                "pipeline_terminal",
                "completed",
                {
                    "model_version_created": workflow_run is not None,
                    "candidate_count": len(experiment.candidates),
                },
            )
            _request_routine_advisory_verification()
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
