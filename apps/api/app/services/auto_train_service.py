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

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ClientLabUpload,
    Dataset,
    Experiment,
    Project,
    WorkflowRun,
)
from app.domain.errors import (
    InvalidChangeSetError,
    ScientificEvidenceLockedError,
    SplitPlanLineageError,
)
from app.domain.lab_run_stages import (
    CANCELLED,
    COMPLETED,
    FAILED,
    INGESTING,
    QUEUED,
    SKIPPED,
)
from app.engine.data.loaders import load_table
from app.engine.lab.schema_inference import MIN_TRAIN_ROWS
from app.engine.modeling.objective import Objective, parse_objective

# ``profile_frame`` and ``materialize_client_upload`` are used by the stages
# through this module (``service_module()``) so patches on it keep applying.
from app.engine.schema.profiler import profile_frame  # noqa: F401
from app.engine.types import SearchConfig
from app.engine.validation.split_assignment import SplitAssignmentMismatchError
from app.services.auto_train.branch import load_branch_run
from app.services.auto_train.cleaning import StructuralCleaningInput, run_structural_cleaning
from app.services.auto_train.column_roles import ColumnRolesInput, run_column_roles
from app.services.auto_train.context import RunContext, StageHalt
from app.services.auto_train.decisions import TrainOnlyDecisionsInput, run_train_only_decisions
from app.services.auto_train.finalize import FinalizeInput, run_finalize
from app.services.auto_train.holdout import HoldoutLockInput, run_holdout_lock
from app.services.auto_train.load_profile import LoadProfileInput, run_load_profile
from app.services.auto_train.persistence import PersistenceInput, run_persistence
from app.services.auto_train.preprocessing import PreprocessingSetupInput, run_preprocessing_setup
from app.services.auto_train.split_plan import SplitPlanInput, run_split_plan
from app.services.auto_train.target import TargetResolutionInput, run_target_resolution
from app.services.auto_train.training import TrainingInput, run_training
from app.services.dataset_materialization import materialize_client_upload  # noqa: F401
from app.services.observability_service import PipelineRunObserver
from app.services.target_intent_service import load_workspace_problem_spec

logger = logging.getLogger(__name__)

# Gate from the plan: only spreadsheet/json/table_file with named columns and
# enough rows attempt auto-train. Raw logs and headerless files stay skipped.
SIMPLE_KINDS = {"spreadsheet", "json", "table_file"}


def is_simple_tabular(upload: ClientLabUpload) -> bool:
    return upload.kind in SIMPLE_KINDS and upload.has_named_fields and upload.record_count >= MIN_TRAIN_ROWS


def _resolve_auto_train_project_id(
    db: Session,
    upload: ClientLabUpload,
    workflow_run: WorkflowRun | None,
) -> UUID | None:
    if workflow_run is not None and workflow_run.project_id is not None:
        return workflow_run.project_id
    if upload.dataset_id is not None:
        source = db.get(Dataset, upload.dataset_id)
        if source is not None and source.project_id is not None:
            return source.project_id
    from app.domain.data_plane import LABS_PROJECT_SLUG

    labs = db.scalar(
        select(Project).where(
            Project.workspace_id == upload.workspace_id,
            Project.slug == LABS_PROJECT_SLUG,
        )
    )
    return labs.id if labs is not None else None


def _load_upload_frame(stored_path: str) -> pd.DataFrame:
    """Load a materialized local path. Canonical bytes come from ObjectStorage."""
    return load_table(stored_path)


def _run_objective(
    db: Session, upload: ClientLabUpload, workflow_run: WorkflowRun | None, task_type: str
) -> Objective | None:
    """The linked ProblemSpec's objective (primary metric, constraints, costs), if any."""
    if workflow_run is None:
        return None
    spec = load_workspace_problem_spec(
        db,
        workspace_id=upload.workspace_id,
        problem_spec_id=workflow_run.problem_spec_id,
        project_id=workflow_run.project_id,
    )
    if spec is None:
        return None
    objective = parse_objective(
        task_type, primary_metric=spec.primary_metric, constraints=spec.constraints or {}
    )
    return None if objective.is_empty else objective


def _search_config(
    *, holdout_plan=None, development_plan=None, objective=None, branch_overrides=None
) -> SearchConfig:
    return SearchConfig(
        strategy="open_ingest",
        max_candidates=8,
        max_feature_group_combinations=1,
        max_ensemble_size=1,
        # Honoured by the runner since P1.4-A1: later candidates are skipped (and
        # reported) once exceeded, never before one learned model has trained.
        max_training_seconds=600.0,
        # One nested-CV-tuned variant of the strongest family (P1.4-C), bounded
        # by this trial count and the time budget above; skipped without optuna.
        max_hyperparameter_trials=12,
        n_robustness_folds=5,
        min_metric=0.0,
        retain_min=1,
        retain_max=1,
        seed=42,
        holdout_plan=None if holdout_plan is None else holdout_plan.to_dict(),
        model_development_plan=None if development_plan is None else development_plan.to_dict(),
        objective=None if objective is None else objective.to_dict(),
        branch_overrides=branch_overrides,
    )


HEARTBEAT_PROGRESS_EVENTS = frozenset(
    {
        "candidate_started",
        "candidate_completed",
        "candidate_failed",
        "cv_fold_started",
        "cv_fold_completed",
    }
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
        if status in _TERMINAL:
            workflow_run.status = status
            workflow_run.completed_at = datetime.now(UTC)
            workflow_run.failure_reason = (
                str(merged.get("reason"))[:2048]
                if status != COMPLETED and merged.get("reason")
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
    if experiment is not None and status in _TERMINAL:
        experiment.status = status.upper()
        if experiment.ended_at is None:
            experiment.ended_at = datetime.now(UTC)
        experiment.failure_reason = (
            str(merged.get("reason"))[:2048]
            if status != COMPLETED and merged.get("reason")
            else None
        )
    db.commit()


_TERMINAL = frozenset({COMPLETED, FAILED, SKIPPED, CANCELLED})
RUN_CANCELLED_REASON = "cancelled by request"


def record_run_cancelled(db: Session, upload_id: UUID, *, cancelled_by: UUID | None = None) -> None:
    """Terminal state of a cancelled run (P3.1-B2). Commits.

    The upload, its WorkflowRun and shell Experiment become ``cancelled``; open
    stage runs are closed by the terminal event. Called before the persistence
    stage only (no checkpoint follows it), so a cancelled run never has locked
    evidence, a selection decision or a ModelVersion.
    """

    upload = db.get(ClientLabUpload, upload_id)
    if upload is None:
        return
    experiment = db.get(Experiment, upload.experiment_id) if upload.experiment_id is not None else None
    if experiment is not None and experiment.scientific_evidence_locked_at is not None:
        return  # locked evidence is never relabelled
    if experiment is not None and isinstance(experiment.result, dict):
        # A selection checkpoint of an interrupted run is not evidence.
        experiment.result = {**experiment.result, "status": "CANCELLED"}
    stage = str(upload.pipeline_status or QUEUED)
    actor = str(cancelled_by) if cancelled_by is not None else None
    _mark(
        db,
        upload,
        status=CANCELLED,
        log={"reason": RUN_CANCELLED_REASON, "cancelled_at_stage": stage, "cancelled_by_user_id": actor},
    )
    observer = PipelineRunObserver.for_upload(db, upload_id)
    if observer is not None:
        observer.emit(
            "terminal",
            "pipeline_terminal",
            "failed",  # closes the open stage runs; failure_code says why
            {
                "reason": RUN_CANCELLED_REASON,
                "failure_code": "cancelled",
                "failed_at": stage,
                "cancelled_by_user_id": actor,
            },
        )


def run_auto_train_job(
    db: Session,
    upload_id: UUID,
    *,
    on_heartbeat: Callable[[], None] | None = None,
) -> None:
    """Runs synchronously against the given session.

    Production callers are durable workers that claimed an ``ml_jobs`` row.
    The API request only persists that row and returns.

    Thin orchestrator over the typed stages in ``app.services.auto_train``; a
    stage that already recorded the outcome (failure / needs-input) raises
    ``StageHalt``.
    """
    total_started_at = datetime.now(UTC)
    total_timer = time.perf_counter()
    upload = db.get(ClientLabUpload, upload_id)
    if upload is None:
        return
    observer = PipelineRunObserver.for_upload(db, upload_id)
    ctx = RunContext(
        db,
        upload_id,
        upload,
        observer=observer,
        on_heartbeat=on_heartbeat,
        total_started_at=total_started_at,
        total_timer=total_timer,
    )

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
        ctx.emit_event("terminal", "pipeline_terminal", "skipped", {"reason": reason})
        return

    ctx.stage(INGESTING)
    try:
        ctx.branch = load_branch_run(db, upload)
        loaded = run_load_profile(ctx, LoadProfileInput(upload=upload))
        resolved = run_target_resolution(
            ctx,
            TargetResolutionInput(
                upload=upload,
                frame=loaded.frame,
                columns=loaded.columns,
                profile=loaded.profile,
                quality=loaded.quality,
            ),
        )
        cleaned = run_structural_cleaning(
            ctx,
            StructuralCleaningInput(
                coerced=resolved.coerced,
                columns=loaded.columns,
                target=resolved.target,
                target_evidence=resolved.target_evidence,
                profile=loaded.profile,
            ),
        )
        holdout = run_holdout_lock(
            ctx,
            HoldoutLockInput(
                frame=cleaned.frame,
                target=resolved.target,
                target_evidence=resolved.target_evidence,
                profile=loaded.profile,
                cleaning_log=cleaned.cleaning_log,
                upload=upload,
                workflow_run=resolved.workflow_run,
            ),
        )
        decisions = run_train_only_decisions(
            ctx,
            TrainOnlyDecisionsInput(
                upload=upload,
                frame=cleaned.frame,
                locked_train=holdout.locked_train,
                locked_split=holdout.locked_split,
                feature_columns=cleaned.feature_columns,
                target=resolved.target,
                target_evidence=resolved.target_evidence,
                profile=loaded.profile,
                cleaning_log=cleaned.cleaning_log,
                holdout_plan=holdout.holdout_plan,
                run_objective=resolved.run_objective,
            ),
        )
        split_plan = run_split_plan(
            ctx,
            SplitPlanInput(
                upload=upload,
                source_dataset=holdout.source_dataset,
                stored_holdout=holdout.stored_holdout,
                locked_train=holdout.locked_train,
                locked_split=holdout.locked_split,
                target=resolved.target,
                target_evidence=resolved.target_evidence,
                profile=loaded.profile,
                cleaning_log=decisions.cleaning_log,
                holdout_plan=holdout.holdout_plan,
                validation_plan=decisions.validation_plan,
                development_plan=decisions.development_plan,
            ),
        )
        roles = run_column_roles(
            ctx,
            ColumnRolesInput(
                upload=upload,
                frame=decisions.frame,
                locked_train=decisions.locked_train,
                locked_split=holdout.locked_split,
                feature_columns=cleaned.feature_columns,
                kept_columns=decisions.kept_columns,
                modeled_kept_columns=decisions.modeled_kept_columns,
                allowed_predictors=decisions.allowed_predictors,
                target=resolved.target,
                target_evidence=resolved.target_evidence,
                profile=loaded.profile,
                cleaning_log=decisions.cleaning_log,
                missing_plan=decisions.missing_plan,
                holdout_plan=split_plan.holdout_plan,
                development_plan=split_plan.development_plan,
                run_objective=resolved.run_objective,
            ),
        )
        prepared = run_preprocessing_setup(
            ctx,
            PreprocessingSetupInput(
                upload=upload,
                workflow_run=resolved.workflow_run,
                frame=roles.frame,
                target=resolved.target,
                feature_columns=cleaned.feature_columns,
                fe_transformations=roles.fe_transformations,
                missing_plan=decisions.missing_plan,
                identifier_cols=roles.identifier_cols,
                final_roles=roles.final_roles,
                leakage_excluded=decisions.leakage_excluded,
                development_plan=split_plan.development_plan,
                validation_plan=split_plan.validation_plan,
                metric_plan=decisions.metric_plan,
                entity_column=roles.entity_column,
                modeled_cols=roles.modeled_cols,
                num_cols=roles.num_cols,
                cat_cols=roles.cat_cols,
                search=roles.search,
                split_plan_id=split_plan.split_plan_id,
            ),
        )
        trained = run_training(
            ctx,
            TrainingInput(
                experiment=prepared.experiment,
                task_type=resolved.target.task_type,
                profile=loaded.profile,
                cleaning_log=decisions.cleaning_log,
                holdout_plan=split_plan.holdout_plan,
                development_plan=split_plan.development_plan,
                feature_report=prepared.feature_report,
                fe_transformations=roles.fe_transformations,
                num_cols=roles.num_cols,
                cat_cols=roles.cat_cols,
                combos=roles.combos,
                locked_split=holdout.locked_split,
                outer_fold_assignment=(
                    None
                    if split_plan.assignment is None
                    else dict(split_plan.assignment.train_folds)
                ),
                holdout_partition=split_plan.holdout_partition,
            ),
        )
        persisted = run_persistence(
            ctx,
            PersistenceInput(
                experiment=trained.experiment,
                result=trained.result,
                reuse_locked_run=trained.reuse_locked_run,
                boost_family_used=trained.boost_family_used,
                workflow_run=prepared.workflow_run,
                frame=roles.frame,
                profile=loaded.profile,
                quality=loaded.quality,
                cleaning_log=decisions.cleaning_log,
                target_evidence=resolved.target_evidence,
                feature_report=prepared.feature_report,
                fe_transformations=roles.fe_transformations,
                num_cols=roles.num_cols,
                cat_cols=roles.cat_cols,
                modeled_cols=roles.modeled_cols,
                initial_roles=roles.initial_roles,
                final_roles=roles.final_roles,
                transformed_datetime=roles.transformed_datetime,
                identifier_cols=roles.identifier_cols,
                column_role_evidence=roles.column_role_evidence,
                entity_column=roles.entity_column,
                missing_plan=decisions.missing_plan,
                holdout_plan=split_plan.holdout_plan,
                development_plan=split_plan.development_plan,
                split_assignment=split_plan.assignment,
            ),
        )
        run_finalize(
            ctx,
            FinalizeInput(
                upload=persisted.upload,
                experiment=persisted.experiment,
                workflow_run=prepared.workflow_run,
                result=persisted.result,
                log=persisted.log,
            ),
        )
    except StageHalt:
        return
    except (SplitAssignmentMismatchError, SplitPlanLineageError, InvalidChangeSetError) as exc:
        # A run whose rows or lineage disagree with its SplitPlan (or a branch
        # change that cannot apply to this run) fails closed.
        logger.exception("auto-train split plan check failed for upload %s", upload_id)
        db.rollback()
        ctx.fail(str(exc))
    except ScientificEvidenceLockedError as exc:
        logger.exception("auto-train hit locked pipeline run for upload %s", upload_id)
        db.rollback()
        ctx.fail(str(exc), experiment_id=exc.pipeline_run_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("auto-train job failed for upload %s", upload_id)
        db.rollback()
        ctx.fail(f"unexpected error: {exc}")


def enqueue_auto_train(upload_id: UUID) -> None:
    """Dispatch an already-persisted auto-train job.

    Production default is a no-op: a worker claims the ``ml_jobs`` row.
    ``inline`` / ``thread`` dispatchers are explicit local adapters only.
    """

    from app.services.job_dispatcher import get_job_dispatcher

    get_job_dispatcher().dispatch(upload_id)
