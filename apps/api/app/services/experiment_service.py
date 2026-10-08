"""/v1 experiment resources (P3.1-B2): root runs, reads, cancellation.

Root runs reuse the one training path: the same run envelope the Labs upload
(``client_lab_upload_service.save_upload``) and branches
(``experiment_branch_service.branch_experiment``) create — a ``ClientLabUpload``
row on the already published dataset (no new bytes, no new Dataset), a Labs
``WorkflowRun``, the shell ``Experiment`` with ``source_dataset_id``, an
``ExecutionRequest`` and a queued ``labs.auto_train`` ``MlJob``. The worker keys
the run by that upload row, exactly as for Labs and branches.

Cancellation is cooperative (``ml_job_service.request_job_cancellation``): a
queued job is cancelled at once; a running job is flagged and the worker stops
at its next stage or candidate checkpoint.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, tuple_
from sqlalchemy.orm import Session

from app.config import get_settings, publication_enforced
from app.db.models import (
    Artifact,
    ClientLabUpload,
    Dataset,
    ExecutionRequest,
    Experiment,
    ExperimentCandidate,
    IngestionRun,
    LlmInvocation,
    MlJob,
    ModelSelectionDecision,
    ModelVersion,
    ProblemSpec,
    ProjectRef,
    SplitPlan,
    User,
    WorkflowRun,
)
from app.domain.errors import (
    ExperimentComparisonError,
    ExperimentNotCancellableError,
    ExperimentNotFoundError,
    ExperimentRequestError,
    IdentityError,
    InvalidCursorError,
)
from app.domain.data_plane import DATASET_PURPOSE_SCORING
from app.domain.execution_requests import OPERATION_MODEL_BUILD, SOURCE_API
from app.domain.experiment_resources import (
    EXPERIMENT_INTENT_READ_MAX_CHARS,
    EXPERIMENT_PAGE_MAX,
    ExperimentLineage,
    ExperimentListItem,
    ExperimentMetrics,
    ExperimentPage,
    ExperimentDetailRead,
    ExperimentWinner,
    ModelVersionArtifactRef,
    ModelVersionLineage,
    ModelVersionResourceRead,
)
from app.domain.findings import ExperimentFindingsRead, findings_read
from app.domain.model_card import (
    FINAL_EVALUATION_WITHHELD,
    THRESHOLD_CAVEAT,
    TOP_DRIVERS,
    ModelCardBaseline,
    ModelCardConstraint,
    ModelCardCrossValidation,
    ModelCardData,
    ModelCardDriver,
    ModelCardDrivers,
    ModelCardFinalEvaluation,
    ModelCardLlm,
    ModelCardMetricInWords,
    ModelCardObjective,
    ModelCardRead,
    ModelCardRisk,
    ModelCardRisks,
    ModelCardSplit,
    ModelCardTarget,
    drivers_text,
    fmt,
    metric_in_words_text,
    metric_label,
    render_markdown,
)
from app.domain.ml_jobs import JOB_CANCELLED, JOB_COMPLETED, JOB_FAILED, JOB_QUEUED, JOB_RUNNING
from app.engine.evaluation.metrics import LOWER_IS_BETTER
from app.engine.modeling.objective import DEFAULT_THRESHOLD
from app.engine.lab.open_ingest import _from_columns as preview_from_columns
from app.services.audience_projection import public_diagnostic, public_failure
from app.services.authorization_service import can_perform_ml_write, can_read_workspace
from app.services.cursor_codec import open_cursor, scope_digest, sign_cursor
from app.services.experiment_branch_service import branch_comparison, sanitize_intent, winner_evidence
from app.services.graph_service import untrusted_text
from app.services.project_service import get_project

ROOT_RUN_CATEGORY = "Custom"


# --- public status (one SQL expression for reads and the list filter) ---------------------


def _latest_job(column: Any) -> Any:
    return (
        select(column)
        .where(MlJob.pipeline_run_id == Experiment.id, MlJob.workspace_id == Experiment.workspace_id)
        .order_by(MlJob.created_at.desc())
        .limit(1)
        .correlate(Experiment)
        .scalar_subquery()
    )


_JOB_STATUS = _latest_job(MlJob.status)
_JOB_CANCEL = _latest_job(MlJob.cancel_requested_at)
_EXP_STATUS = func.upper(Experiment.status)

# Precedence: a completed experiment is completed; a cancelled job wins over the
# experiment row; a live job (a retry included) wins over an earlier failure; a
# finished job whose experiment is not terminal is waiting for input.
STATUS_SQL = case(
    (_EXP_STATUS == "COMPLETED", "completed"),
    (or_(_EXP_STATUS == "CANCELLED", _JOB_STATUS == JOB_CANCELLED), "cancelled"),
    (and_(_JOB_STATUS == JOB_RUNNING, _JOB_CANCEL.is_not(None)), "cancelling"),
    (_JOB_STATUS == JOB_RUNNING, "running"),
    (_JOB_STATUS == JOB_QUEUED, "queued"),
    (_EXP_STATUS == "FAILED", "failed"),
    (_EXP_STATUS == "SKIPPED", "skipped"),
    (_JOB_STATUS == JOB_FAILED, "failed"),
    (_JOB_STATUS == JOB_COMPLETED, "needs_input"),
    (_EXP_STATUS.in_(("CREATED", "QUEUED")), "queued"),
    else_="running",
)


def _require_read(db: Session, actor: User, workspace_id: UUID) -> None:
    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)


def _load(db: Session, workspace_id: UUID, experiment_id: UUID) -> tuple[Experiment, str]:
    row = db.execute(
        select(Experiment, STATUS_SQL).where(
            Experiment.id == experiment_id, Experiment.workspace_id == workspace_id
        )
    ).first()
    if row is None:
        raise ExperimentNotFoundError("experiment not found")
    return row[0], str(row[1])


# --- root run ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RootRunResult:
    experiment: Experiment
    execution_request: ExecutionRequest
    upload: ClientLabUpload
    ml_job: MlJob


def uploaded_dataset(db: Session, workspace_id: UUID, project_id: UUID | None, dataset_id: UUID) -> Dataset:
    """A published dataset uploaded with ``POST /v1/datasets`` into this project: the
    only input a run (or, P4.9-A, a batch prediction) accepts."""

    dataset = db.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.workspace_id == workspace_id)
    )
    if dataset is None:
        raise ExperimentRequestError("dataset_not_found", "dataset not found", status_code=404)
    if dataset.project_id != project_id:
        raise ExperimentRequestError("dataset_not_in_project", "the dataset belongs to another project")
    artifact = db.get(Artifact, dataset.artifact_id) if dataset.artifact_id is not None else None
    if artifact is None or artifact.workspace_id != workspace_id or artifact.artifact_type != "dataset":
        # Prepared (derived) tables and legacy rows are never a run's source.
        raise ExperimentRequestError(
            "dataset_not_uploaded", "only a dataset uploaded with POST /v1/datasets can be used", status_code=409
        )
    if publication_enforced(get_settings()):
        from app.services.ingestion_run_service import require_published_artifact

        try:
            require_published_artifact(db, artifact)
        except IdentityError as exc:
            raise ExperimentRequestError(
                "dataset_not_published", "the dataset is not published", status_code=409
            ) from exc
    return dataset


def _trainable_source(db: Session, workspace_id: UUID, project_id: UUID, dataset_id: UUID) -> Dataset:
    dataset = uploaded_dataset(db, workspace_id, project_id, dataset_id)
    if dataset.purpose == DATASET_PURPOSE_SCORING:
        raise ExperimentRequestError(
            "dataset_purpose_scoring",
            "a scoring dataset (purpose=scoring) has no target and cannot start a run",
            status_code=409,
        )
    return dataset


def start_root_experiment(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    project_id: UUID,
    dataset_id: UUID,
    problem_spec_id: UUID | None = None,
    target_column: str | None = None,
    intent: str | None = None,
    plan: UUID | None = None,
    before_commit: Callable[[Experiment], None] | None = None,
    initiated_by_service_token_id: UUID | None = None,
) -> RootRunResult:
    """Queue a root auto-train run on a published dataset. Commits.

    ``before_commit(shell)`` runs inside the transaction (the /v1 key binding).
    Raises ``ProjectNotFoundError`` / ``ProblemSpecNotFoundError`` (404),
    ``ExperimentRequestError`` (404/409/422) and the target-intent errors.
    """

    from app.services.auto_train_service import enqueue_auto_train, is_simple_tabular
    from app.services.execution_request_service import _legacy_labs_request_spec, create_execution_request
    from app.services.lineage_service import create_pipeline_run, create_workflow_run, get_or_create_labs_workflow
    from app.services.ml_job_service import create_auto_train_job, ensure_run_capacity
    from app.services.problem_spec_service import get_problem_spec
    from app.services.target_intent_service import (
        dataset_schema_column_names,
        normalize_target_name,
        validate_declared_target_intent,
    )

    project = get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    if not can_perform_ml_write(db, actor, workspace_id):
        raise IdentityError("starting a run requires an ML-write workspace role", status_code=403)
    dataset = _trainable_source(db, workspace_id, project.id, dataset_id)
    ensure_run_capacity(db, workspace_id)
    spec = None
    if problem_spec_id is not None:
        spec = get_problem_spec(
            db, actor=actor, workspace_id=workspace_id, project_id=project.id, spec_id=problem_spec_id
        )
    target = normalize_target_name(target_column)
    columns = dataset_schema_column_names(dataset)
    validate_declared_target_intent(schema_names=columns, problem_spec=spec, requested_target=target)
    text = sanitize_intent(intent) if intent is not None and intent.strip() else None
    artifact = db.get(Artifact, dataset.artifact_id)
    ingestion = db.get(IngestionRun, dataset.ingestion_run_id) if dataset.ingestion_run_id else None
    # The upload's own preview, re-derived from the stored schema (same rule).
    preview = preview_from_columns(
        kind=str((artifact.extra_metadata or {}).get("kind") or "table_file"),
        record_count=int(dataset.row_count or 0),
        columns=columns,
    )
    upload = ClientLabUpload(
        workspace_id=workspace_id,
        requested_by=actor.id,
        category=ROOT_RUN_CATEGORY,
        original_filename=str((artifact.extra_metadata or {}).get("original_filename") or dataset.name)[:512],
        stored_path=dataset.location,
        kind=preview.kind,
        record_count=preview.record_count,
        fields_noticed=preview.fields_noticed,
        has_named_fields=preview.has_named_fields,
        explicit_target_column=target,
        pipeline_status="queued",
        client_status="queued",
        dataset_id=dataset.id,
        artifact_id=artifact.id,
        data_source_id=ingestion.data_source_id if ingestion is not None else None,
        ingestion_run_id=dataset.ingestion_run_id,
    )
    if not is_simple_tabular(upload):
        raise ExperimentRequestError(
            "dataset_not_trainable", "the dataset is not a named-column table with enough rows"
        )
    db.add(upload)
    db.flush()
    workflow = get_or_create_labs_workflow(db, workspace_id=workspace_id, actor=actor, project=project)
    workflow_run = create_workflow_run(
        db,
        workspace_id=workspace_id,
        workflow=workflow,
        requester=actor,
        trigger_type="manual",
        source_type=upload.kind,
        source_upload=upload,
        explicit_target=target,
        inputs=[(dataset, "reference")],
        problem_spec_id=spec.id if spec is not None else None,
    )
    shell = create_pipeline_run(
        db,
        workflow_run=workflow_run,
        environment=dataset.environment,
        dataset=dataset,
        task=None,
        pipeline_name="open_ingest_deterministic_ml",
        pipeline_purpose="training_and_scoring",
        input_role=None,
        commit=False,
        source_dataset_id=dataset.id,
        intent=text,
    )
    upload.experiment_id = shell.id
    plan_id, plan_refusal = (None, None)
    if plan is not None:  # P6.9-A: never an error; a refused plan makes the run rule-only
        from app.services.run_plan_service import plan_for_request

        plan_id, plan_refusal = plan_for_request(db, workspace_id=workspace_id, project_id=project.id, plan_id=plan)
    request_spec = _legacy_labs_request_spec(upload, problem_spec_id=spec.id if spec is not None else None)
    if plan_refusal is not None:
        request_spec["plan_refusal"] = plan_refusal
    request = create_execution_request(
        db,
        workspace_id=workspace_id,
        project_id=project.id,
        operation=OPERATION_MODEL_BUILD,
        source_surface=SOURCE_API,
        requested_by_user_id=actor.id,
        initiated_by_service_token_id=initiated_by_service_token_id,
        request_spec=request_spec,
        workflow_run_id=workflow_run.id,
        pipeline_run_id=shell.id,
    )
    if plan_id is not None:
        _bind_plan(db, request, plan_id)
    job = create_auto_train_job(
        db,
        workspace_id=workspace_id,
        project_id=project.id,
        upload_id=upload.id,
        execution_request_id=request.id,
        workflow_run_id=workflow_run.id,
        pipeline_run_id=shell.id,
    )
    if before_commit is not None:
        before_commit(shell)
    db.commit()
    enqueue_auto_train(upload.id)
    return RootRunResult(shell, request, upload, job)


def _bind_plan(db: Session, request: Any, plan_id: UUID) -> None:
    """Consume the plan (unique: single use). Two runs naming the same plan at once: the
    loser's request records ``plan_already_used`` and its run is rule-only (never an error)."""

    from sqlalchemy.exc import IntegrityError

    from app.services.decision_record_service import unique_violation
    from app.services.execution_request_service import bound_request_spec

    db.flush()  # everything else is written before the savepoint, so only the binding can roll back
    try:
        with db.begin_nested():
            request.plan_proposal_id = plan_id
            db.flush()
    except IntegrityError as exc:  # the single-use index, or the plan's FK (deleted meanwhile)
        used = unique_violation(exc) == "uq_execution_requests_plan_proposal"
        db.refresh(request)
        request.request_spec = bound_request_spec({**dict(request.request_spec or {}), "plan_refusal": {
            "code": "plan_already_used" if used else "plan_not_found", "proposal_id": str(plan_id)}})
        db.flush()


# --- reads ---------------------------------------------------------------------------


def _intent(row: Experiment) -> str | None:
    return untrusted_text(row.intent or row.branch_reason, EXPERIMENT_INTENT_READ_MAX_CHARS)


def _metrics(db: Session, experiment: Experiment) -> ExperimentMetrics | None:
    try:
        winner = winner_evidence(db, experiment)
    except ExperimentComparisonError:
        return None
    baseline = (experiment.result or {}).get("baseline_comparison")
    return ExperimentMetrics(
        **winner,
        baseline_comparison=public_diagnostic(baseline) if isinstance(baseline, dict) else None,
    )


def experiment_read(db: Session, *, actor: User, workspace_id: UUID, experiment_id: UUID) -> ExperimentDetailRead:
    _require_read(db, actor, workspace_id)
    experiment, status = _load(db, workspace_id, experiment_id)
    workflow_run = (
        db.get(WorkflowRun, experiment.workflow_run_id) if experiment.workflow_run_id is not None else None
    )
    if workflow_run is not None and workflow_run.workspace_id != workspace_id:
        workflow_run = None
    request_id = db.scalar(
        select(ExecutionRequest.id)
        .where(ExecutionRequest.workspace_id == workspace_id, ExecutionRequest.pipeline_run_id == experiment.id)
        .order_by(ExecutionRequest.created_at.desc())
        .limit(1)
    )
    cancel_requested_at = db.scalar(
        select(MlJob.cancel_requested_at)
        .where(MlJob.workspace_id == workspace_id, MlJob.pipeline_run_id == experiment.id)
        .order_by(MlJob.created_at.desc())
        .limit(1)
    )
    task = dict((experiment.result or {}).get("task") or {})
    completed = status == "completed"
    diff = None
    if completed and experiment.parent_pipeline_run_id is not None:
        diff = public_diagnostic(branch_comparison(db, experiment))
    return ExperimentDetailRead(
        id=experiment.id,
        workspace_id=experiment.workspace_id,
        project_id=experiment.project_id,
        status=status,
        created_at=experiment.created_at,
        started_at=experiment.started_at,
        ended_at=experiment.ended_at,
        cancel_requested_at=cancel_requested_at,
        failure_reason=public_failure(experiment.failure_reason) if status == "failed" else None,
        task_type=(workflow_run.task_type if workflow_run is not None else None) or task.get("task_type"),
        target_column=untrusted_text(
            (workflow_run.resolved_target or workflow_run.explicit_target) if workflow_run is not None else None,
            256,
        ),
        intent=_intent(experiment),
        lineage=ExperimentLineage(
            parent_experiment_id=experiment.parent_pipeline_run_id,
            split_plan_id=experiment.split_plan_id,
            source_dataset_id=experiment.source_dataset_id,
            prepared_dataset_id=experiment.dataset_id if experiment.dataset_id != experiment.source_dataset_id else None,
            problem_spec_id=workflow_run.problem_spec_id if workflow_run is not None else None,
            workflow_run_id=experiment.workflow_run_id,
            execution_request_id=request_id,
        ),
        change_set=public_diagnostic(experiment.change_set) if experiment.change_set is not None else None,
        metrics=_metrics(db, experiment) if completed else None,
        diff_vs_parent=diff,
    )


def experiment_findings(
    db: Session, *, actor: User, workspace_id: UUID, experiment_id: UUID
) -> ExperimentFindingsRead:
    """The run's five trust checks (P4.10-A), stored before its evidence lock; runs
    that predate them read ``investigated: false``."""

    _require_read(db, actor, workspace_id)
    experiment, _status = _load(db, workspace_id, experiment_id)
    return findings_read(experiment.id, (experiment.result or {}).get("investigation"))


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo is not None else value.replace(tzinfo=UTC)


def list_experiments(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    project_id: UUID | None = None,
    status: str | None = None,
    parent_id: UUID | None = None,
    split_plan_id: UUID | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    has_change_set: bool | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ExperimentPage:
    """Experiments of the workspace, newest first; the cursor is bound to the filters."""

    _require_read(db, actor, workspace_id)
    limit = max(1, min(int(limit), EXPERIMENT_PAGE_MAX))
    after, before = _aware(created_after), _aware(created_before)
    if after is not None and before is not None and after > before:
        raise ExperimentRequestError("invalid_query", "created_after must not be later than created_before", status_code=400)
    stmt = select(Experiment, STATUS_SQL).where(Experiment.workspace_id == workspace_id)
    if project_id is not None:
        get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
        stmt = stmt.where(Experiment.project_id == project_id)
    if status is not None:
        stmt = stmt.where(STATUS_SQL == status)
    if parent_id is not None:
        stmt = stmt.where(Experiment.parent_pipeline_run_id == parent_id)
    if split_plan_id is not None:
        stmt = stmt.where(Experiment.split_plan_id == split_plan_id)
    if after is not None:
        stmt = stmt.where(Experiment.created_at >= after)
    if before is not None:
        stmt = stmt.where(Experiment.created_at < before)
    if has_change_set is not None:
        stmt = stmt.where(Experiment.change_set.is_not(None) if has_change_set else Experiment.change_set.is_(None))
    filters = {
        "project_id": project_id, "status": status, "parent_id": parent_id, "split_plan_id": split_plan_id,
        "created_after": after.isoformat() if after else None,
        "created_before": before.isoformat() if before else None, "has_change_set": has_change_set,
    }
    scope = f"experiments:{workspace_id}:{scope_digest(filters)}"
    if cursor is not None and cursor.strip():
        message = "cursor is not an experiment list cursor for this filter set"
        try:
            stamp, last_id = open_cursor(cursor, scope, message=message)
            created_at, row_id = datetime.fromisoformat(stamp), UUID(last_id)
            if created_at.tzinfo is None:
                raise ValueError(message)
        except (InvalidCursorError, ValueError, TypeError) as exc:
            raise InvalidCursorError(message) from exc
        stmt = stmt.where(tuple_(Experiment.created_at, Experiment.id) < tuple_(created_at, row_id))
    rows = db.execute(stmt.order_by(Experiment.created_at.desc(), Experiment.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1][0]
        next_cursor = sign_cursor(scope, [last.created_at.isoformat(), str(last.id)])
    return ExperimentPage(
        items=[
            ExperimentListItem(
                id=row.id,
                project_id=row.project_id,
                status=str(state),
                created_at=row.created_at,
                started_at=row.started_at,
                ended_at=row.ended_at,
                parent_experiment_id=row.parent_pipeline_run_id,
                split_plan_id=row.split_plan_id,
                source_dataset_id=row.source_dataset_id,
                has_change_set=row.change_set is not None,
                intent=_intent(row),
            )
            for row, state in page
        ],
        next_cursor=next_cursor,
        limit=limit,
    )


def experiments_for_compare(
    db: Session, *, actor: User, workspace_id: UUID, experiment_ids: list[UUID]
) -> list[Experiment]:
    """The requested experiments, in request order; any missing or foreign id is 404."""

    _require_read(db, actor, workspace_id)
    rows = {
        row.id: row
        for row in db.scalars(
            select(Experiment).where(Experiment.workspace_id == workspace_id, Experiment.id.in_(experiment_ids))
        )
    }
    if len(rows) != len(experiment_ids):
        raise ExperimentNotFoundError("experiment not found")
    return [rows[item] for item in experiment_ids]


# --- model versions (GET /v1/model-versions/{id}, P3.1-B3) ----------------------------------

_MODEL_ARTIFACT_ROLES = (
    ("model", "model_artifact_id"),
    ("preprocessor", "preprocessor_artifact_id"),
    ("feature_manifest", "feature_manifest_artifact_id"),
)


def model_version_read(
    db: Session, *, actor: User, workspace_id: UUID, model_version_id: UUID
) -> ModelVersionResourceRead | None:
    """One model version of this workspace (None when unknown or another tenant's).

    Metrics are the locked winner's ``evaluation_metrics`` (CV aggregate and the
    single final-holdout evaluation at the locked threshold); artifacts are ids +
    digests only — never ``artifact_uri``, buckets or object keys.
    """

    _require_read(db, actor, workspace_id)
    row = db.scalar(
        select(ModelVersion).where(ModelVersion.id == model_version_id, ModelVersion.workspace_id == workspace_id)
    )
    if row is None:
        return None
    experiment = db.scalar(
        select(Experiment).where(Experiment.id == row.pipeline_run_id, Experiment.workspace_id == workspace_id)
    )
    candidate = db.scalar(
        select(ExperimentCandidate).where(
            ExperimentCandidate.id == row.selected_candidate_id, ExperimentCandidate.workspace_id == workspace_id
        )
    )
    workflow_run = db.scalar(
        select(WorkflowRun).where(WorkflowRun.id == row.workflow_run_id, WorkflowRun.workspace_id == workspace_id)
    )
    metrics = None
    if experiment is not None:
        selected = db.scalar(
            select(ModelSelectionDecision.selected_candidate_id).where(
                ModelSelectionDecision.pipeline_run_id == experiment.id,
                ModelSelectionDecision.workspace_id == workspace_id,
            )
        )
        # Same gate as the experiment read: metrics only once evidence is locked.
        if selected == row.selected_candidate_id and experiment.scientific_evidence_locked_at is not None:
            metrics = ExperimentWinner(**winner_evidence(db, experiment))
    ref_kinds = sorted(
        db.scalars(
            select(ProjectRef.ref_kind).where(
                ProjectRef.workspace_id == workspace_id, ProjectRef.model_version_id == row.id
            )
        )
    )
    artifact_ids = {getattr(row, column): role for role, column in _MODEL_ARTIFACT_ROLES if getattr(row, column)}
    artifacts = (
        db.scalars(select(Artifact).where(Artifact.workspace_id == workspace_id, Artifact.id.in_(artifact_ids)))
        if artifact_ids
        else []
    )
    return ModelVersionResourceRead(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        version=row.version,
        created_at=row.created_at,
        content_digest=row.content_digest,
        family=candidate.model_family or None if candidate is not None else None,
        algorithm=candidate.algorithm or None if candidate is not None else None,
        candidate_key=candidate.candidate_key if candidate is not None else None,
        lineage=ModelVersionLineage(
            experiment_id=row.pipeline_run_id,
            candidate_id=row.selected_candidate_id,
            split_plan_id=experiment.split_plan_id if experiment is not None else None,
            source_dataset_id=experiment.source_dataset_id if experiment is not None else None,
            prepared_dataset_id=row.dataset_id,
            problem_spec_id=workflow_run.problem_spec_id if workflow_run is not None else None,
            feature_recipe_id=row.feature_set_version_id,
        ),
        metrics=metrics,
        is_champion="champion_model" in ref_kinds,
        ref_kinds=[kind for kind in ref_kinds if kind == "champion_model"],
        artifacts=sorted(
            (
                ModelVersionArtifactRef(
                    role=artifact_ids[item.id],
                    id=item.id,
                    artifact_type=item.artifact_type,
                    content_digest=item.content_digest,
                    size_bytes=item.size_bytes,
                    mime_type=item.mime_type,
                )
                for item in artifacts
            ),
            key=lambda ref: ref.role,
        ),
    )


# --- model card (GET /v1/model-versions/{id}/card, P4.11-A) ---------------------------------


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _natural(metric: str | None, oriented: Any) -> float | None:
    """Scores in ``baseline_comparison`` are oriented larger-is-better; undo that for display."""

    value = _num(oriented)
    return None if value is None else (-value if metric in LOWER_IS_BETTER else value)


_THRESHOLD_DEPENDENT = ("precision", "recall", "specificity", "f1", "accuracy", "balanced_accuracy")


def _card_metric_in_words(task_type: str | None, result: dict[str, Any], cv: dict[str, float]) -> ModelCardMetricInWords:
    """Numbers and basis only; the sentence is built from them (``metric_in_words_text``)."""

    if task_type == "binary":
        threshold = dict(result.get("decision_threshold") or {})
        pooled = threshold.get("oof_at_threshold") if isinstance(threshold.get("oof_at_threshold"), dict) else {}
        counts = {key: _num(pooled.get(key)) for key in ("tp", "fp", "fn")}
        if None not in counts.values():
            basis = (
                "most_recent_validation_fold_at_locked_threshold" if threshold.get("oof_folds") == "last_fold"
                else "pooled_out_of_fold_at_locked_threshold"
            )
            numbers = {key: float(value) for key, value in counts.items() if value is not None}
            for key in ("precision", "recall"):
                if _num(pooled.get(key)) is not None:
                    numbers[key] = float(pooled[key])
            tuned = threshold.get("source") not in {None, "default"}
            return ModelCardMetricInWords(text="", basis=basis, numbers=numbers, caveat=THRESHOLD_CAVEAT if tuned else None)
        # Runs that predate the pooled rates: the CV aggregate at the default threshold.
        numbers = {key: cv[key] for key in ("precision", "recall") if key in cv}
        return ModelCardMetricInWords(text="", basis="cross_validation_aggregate_at_default_threshold", numbers=numbers)
    keys = ("mae", "rmse", "r2") if task_type == "regression" else ("balanced_accuracy", "macro_f1")
    return ModelCardMetricInWords(text="", basis="cross_validation_aggregate", numbers={k: cv[k] for k in keys if k in cv})


def _card_cv(task_type: str | None, result: dict[str, Any], cv: dict[str, float], metric: str | None,
             best: dict[str, Any], strategy: str | None) -> ModelCardCrossValidation:
    threshold = dict(result.get("decision_threshold") or {})
    locked = _num(threshold.get("value"))
    at_locked: dict[str, float] = {}
    note = None
    if task_type == "binary" and locked is not None and locked != DEFAULT_THRESHOLD:
        rows = [row for row in threshold.get("per_fold") or [] if isinstance(row, dict)]
        for key in _THRESHOLD_DEPENDENT:
            values = [_num(row.get(key)) for row in rows]
            if values and None not in values:
                at_locked[key] = sum(values) / len(values)  # type: ignore[arg-type]
        note = (
            "Threshold-dependent cross-validation metrics (precision, recall, specificity, F1, accuracy, "
            f"balanced accuracy) in the fold aggregate are at the default {fmt(DEFAULT_THRESHOLD)} threshold; the "
            f"locked decision threshold is {fmt(locked)}."
        )
        if threshold.get("source") not in {None, "default"}:
            note = f"{note} {THRESHOLD_CAVEAT}"
    return ModelCardCrossValidation(
        metric=metric,
        mean=cv.get(str(metric)),
        std=_num((best.get("cv_std") or {}).get(str(metric))),
        folds=best.get("n_folds") or best.get("actual_folds"),
        strategy=strategy,
        metrics=cv,
        at_locked_threshold=at_locked,
        threshold_note=note,
    )


def _card_baseline(result: dict[str, Any]) -> ModelCardBaseline:
    stored = result.get("baseline_comparison")
    if not isinstance(stored, dict):
        return ModelCardBaseline(available=False, text="No dummy baseline was recorded for this run.")
    metric = stored.get("metric")
    baseline, winner = _natural(metric, stored.get("baseline_cv_score")), _natural(metric, stored.get("winner_cv_score"))
    beats, clear = stored.get("beats_baseline"), stored.get("clear_margin")
    verdict = (
        "beats it by more than its own fold-to-fold spread" if beats and clear
        else "beats it, but by less than its own fold-to-fold spread" if beats
        else "does not beat it"
    )
    return ModelCardBaseline(
        available=True,
        metric=metric,
        baseline_candidate_id=stored.get("baseline_candidate_id"),
        baseline_score=baseline,
        winner_score=winner,
        margin=_num(stored.get("margin")),
        beats_baseline=beats if isinstance(beats, bool) else None,
        clear_margin=clear if isinstance(clear, bool) else None,
        text=f"A dummy model that ignores every column scores {metric_label(metric)} {fmt(baseline)} in "
        f"cross-validation; this model scores {fmt(winner)} and {verdict}.",
    )


def _card_drivers(result: dict[str, Any]) -> ModelCardDrivers:
    stored = result.get("feature_importance")
    if not isinstance(stored, dict):
        drivers = ModelCardDrivers(status="not_computed", text="")
        return drivers.model_copy(update={"text": drivers_text(drivers)})
    status = stored.get("status") if stored.get("status") in {"computed", "skipped", "not_applicable"} else "skipped"
    rows = [row for row in stored.get("features") or [] if isinstance(row, dict) and row.get("column")]
    features = [
        ModelCardDriver(
            rank=index + 1,
            column=str(untrusted_text(str(row["column"]), 256)),
            importance_mean=_num(row.get("importance_mean")),
            importance_std=_num(row.get("importance_std")),
            importance_se=_num(row.get("importance_se")),
            distinguishable=row.get("distinguishable") if isinstance(row.get("distinguishable"), bool) else None,
        )
        for index, row in enumerate(rows[:TOP_DRIVERS])
    ] if status == "computed" else []
    significance = stored.get("significance") if isinstance(stored.get("significance"), dict) else {}
    drivers = ModelCardDrivers(
        status=status,
        columns_tested=significance.get("columns_tested"),
        critical_value=_num(significance.get("critical_value")),
        # Over every evaluated column, not only the top ones shown.
        clear_drivers=[
            str(untrusted_text(str(column), 256)) for column in stored.get("clear_drivers") or [] if column
        ] if status == "computed" else [],
        method=stored.get("method"),
        scoring=stored.get("scoring"),
        n_repeats=stored.get("n_repeats"),
        folds=stored.get("folds"),
        reason=untrusted_text(stored.get("reason"), 300),
        features=features,
        text="",
    )
    return drivers.model_copy(update={"text": drivers_text(drivers)})


def _card_risks(experiment: Experiment) -> ModelCardRisks:
    findings = findings_read(experiment.id, (experiment.result or {}).get("investigation"))
    items = [
        # Messages name dataset columns (user data): same projection as other untrusted text.
        ModelCardRisk(check=item.check, status=item.status, severity=item.severity,
                      message=untrusted_text(item.message, 1200) or "")
        for item in findings.checks
        if item.status in {"warning", "fail"}
    ]
    if not findings.investigated:
        text = "Trust checks were not run for this run."
    elif items:
        text = f"{len(items)} of {len(findings.checks)} trust checks raised a warning or failure:"
    else:
        skipped = findings.summary.not_evaluated
        text = f"No trust check raised a warning or failure ({skipped} could not be evaluated)."
    return ModelCardRisks(investigated=findings.investigated, items=items, text=text)


def model_card_read(
    db: Session, *, actor: User, workspace_id: UUID, model_version_id: UUID, include_final_evaluation: bool = False
) -> ModelCardRead | None:
    """One-page card of a model version (None when unknown or another tenant's).

    Only once the run's evidence is locked and its selection is this version (else 409).
    Everything that says how good the model is comes from CV/out-of-fold evidence; the
    only final-holdout read is ``winner_evidence``'s single locked-winner evaluation,
    which fills ``final_evaluation`` alone and only with ``include_final_evaluation``
    (fail-closed: callers that are not session humans get it withheld).
    ``ModelVersion.metrics`` and candidate ``test_metrics`` are never read.
    """

    _require_read(db, actor, workspace_id)
    row = db.scalar(
        select(ModelVersion).where(ModelVersion.id == model_version_id, ModelVersion.workspace_id == workspace_id)
    )
    if row is None:
        return None
    experiment = db.scalar(
        select(Experiment).where(Experiment.id == row.pipeline_run_id, Experiment.workspace_id == workspace_id)
    )
    selected = experiment is not None and db.scalar(
        select(ModelSelectionDecision.selected_candidate_id).where(
            ModelSelectionDecision.pipeline_run_id == experiment.id,
            ModelSelectionDecision.workspace_id == workspace_id,
        )
    )
    if experiment is None or experiment.scientific_evidence_locked_at is None or selected != row.selected_candidate_id:
        raise ExperimentRequestError(
            "model_card_unavailable", "the model version's run has no locked evidence for it", status_code=409
        )
    result = dict(experiment.result or {})
    evidence = winner_evidence(db, experiment)
    cv = dict(evidence.get("cv") or {})
    candidate = db.scalar(
        select(ExperimentCandidate).where(
            ExperimentCandidate.id == row.selected_candidate_id, ExperimentCandidate.workspace_id == workspace_id
        )
    )
    workflow_run = db.scalar(
        select(WorkflowRun).where(WorkflowRun.id == row.workflow_run_id, WorkflowRun.workspace_id == workspace_id)
    )
    spec = (
        db.scalar(select(ProblemSpec).where(
            ProblemSpec.id == workflow_run.problem_spec_id, ProblemSpec.workspace_id == workspace_id))
        if workflow_run is not None and workflow_run.problem_spec_id is not None
        else None
    )
    upload = (
        db.scalar(select(ClientLabUpload).where(
            ClientLabUpload.id == workflow_run.source_upload_id, ClientLabUpload.workspace_id == workspace_id))
        if workflow_run is not None and workflow_run.source_upload_id is not None
        else None
    )
    task = dict(result.get("task") or {})
    task_type = task.get("task_type") or (workflow_run.task_type if workflow_run is not None else None)
    target_column = untrusted_text(task.get("target"), 256)
    target_log = ((upload.pipeline_log or {}).get("target") or {}) if upload is not None else {}
    positive = untrusted_text(str((target_log.get("evidence") or {}).get("positive_label") or ""), 128)
    prediction_unit = untrusted_text(spec.prediction_unit if spec is not None else None, 128)
    metric_plan = dict(result.get("metric_plan") or {})
    objective = dict(result.get("objective") or {})
    threshold = dict(result.get("decision_threshold") or {})
    best = dict(result.get("best_single") or {})  # CV fields only (cv_std, features, folds)
    selection_metric = evidence.get("selection_metric") or metric_plan.get("primary_metric")
    validation_plan = dict(result.get("validation_plan") or {})

    dataset = (
        db.scalar(select(Dataset).where(Dataset.id == experiment.source_dataset_id, Dataset.workspace_id == workspace_id))
        if experiment.source_dataset_id is not None
        else None
    )
    plan = (
        db.scalar(select(SplitPlan).where(SplitPlan.id == experiment.split_plan_id, SplitPlan.workspace_id == workspace_id))
        if experiment.split_plan_id is not None
        else None
    )
    holdout_plan = dict(result.get("holdout_plan") or {})
    split = ModelCardSplit(
        split_plan_id=plan.id if plan is not None else None,
        evaluation_split_strategy=plan.holdout_strategy if plan is not None else holdout_plan.get("strategy"),
        evaluation_fraction=plan.holdout_test_size if plan is not None else _num(holdout_plan.get("test_size")),
        validation_strategy=plan.validation_strategy if plan is not None else validation_plan.get("strategy"),
        validation_folds=plan.validation_folds if plan is not None else best.get("n_folds"),
        train_rows=plan.train_row_count if plan is not None else best.get("n_train_rows"),
        evaluation_rows=plan.holdout_row_count if plan is not None else None,
        stratified=plan.stratified if plan is not None else holdout_plan.get("stratified"),
        group_column=untrusted_text(plan.group_column if plan is not None else holdout_plan.get("group_column"), 256),
        time_column=untrusted_text(plan.time_column if plan is not None else holdout_plan.get("time_column"), 256),
    )
    llm_filter = [LlmInvocation.experiment_id == experiment.id]
    if experiment.workflow_run_id is not None:
        llm_filter.append(LlmInvocation.workflow_run_id == experiment.workflow_run_id)
    purposes = sorted(
        db.scalars(
            select(LlmInvocation.purpose)
            .where(LlmInvocation.workspace_id == workspace_id, LlmInvocation.llm_used.is_(True), or_(*llm_filter))
            .distinct()
        )
    )
    holdout = dict(evidence.get("holdout") or {}) if include_final_evaluation else {}
    final = (
        ModelCardFinalEvaluation(status="withheld", note=FINAL_EVALUATION_WITHHELD)
        if not include_final_evaluation
        else ModelCardFinalEvaluation(
            status="reported",
            metric=selection_metric,
            value=holdout.get(str(selection_metric)),
            metrics=holdout,
            decision_threshold=_num(evidence.get("decision_threshold")),
        )
        if holdout
        else ModelCardFinalEvaluation(status="missing", note="No final evaluation was recorded for this run.")
    )
    card = ModelCardRead(
        model_version_id=row.id,
        version=row.version,
        experiment_id=experiment.id,
        project_id=row.project_id,
        candidate_key=candidate.candidate_key if candidate is not None else None,
        family=(candidate.model_family or None) if candidate is not None else None,
        algorithm=(candidate.algorithm or None) if candidate is not None else None,
        created_at=row.created_at,
        content_digest=row.content_digest,
        target=ModelCardTarget(
            column=target_column,
            task_type=task_type,
            positive_label=positive if task_type == "binary" else None,
            positive_label_note=(
                None if task_type != "binary" or positive
                else "the class coded 1 (a yes/true-style or 1 label in the uploaded target)"
            ),
            class_labels=[str(untrusted_text(str(label), 128)) for label in result.get("class_labels") or []],
            prediction_unit=prediction_unit,
        ),
        objective=ModelCardObjective(
            primary_metric=selection_metric,
            # ProblemSpec constraints / branch metric_override.reason: agent-writable text.
            primary_metric_reason=untrusted_text(
                objective.get("primary_metric_reason") or metric_plan.get("reason") or None, 512
            ),
            business_objective=untrusted_text(spec.business_objective if spec is not None else None, 1000),
            constraints=[
                ModelCardConstraint(
                    metric=str(item.get("metric")),
                    op=str(item.get("op")),
                    value=float(item.get("value")),
                    cv_value=_num(item.get("oof_value")),
                    cv_satisfied=item.get("oof_satisfied") if isinstance(item.get("oof_satisfied"), bool) else None,
                )
                for item in threshold.get("constraints") or []
                if isinstance(item, dict) and _num(item.get("value")) is not None
            ],
            decision_threshold=_num(threshold.get("value")),
            decision_threshold_source=threshold.get("source"),
        ),
        metric_in_words=_card_metric_in_words(task_type, result, cv),
        cv=_card_cv(task_type, result, cv, selection_metric, best, validation_plan.get("strategy")),
        baseline=_card_baseline(result),
        drivers=_card_drivers(result),
        risks=_card_risks(experiment),
        data=ModelCardData(
            source_dataset_id=experiment.source_dataset_id,
            name=untrusted_text(dataset.name, 128) if dataset is not None else None,
            row_count=dataset.row_count if dataset is not None else None,
            column_count=dataset.column_count if dataset is not None else None,
            content_digest=dataset.content_digest if dataset is not None else None,
            modeled_feature_count=len(best.get("features") or []) or None,
        ),
        split=split,
        llm=ModelCardLlm(used=bool(purposes), purposes=purposes),
        final_evaluation=final,
    )
    words = card.metric_in_words.model_copy(update={
        "text": metric_in_words_text(task_type, card.metric_in_words, card.target,
                                     threshold=card.objective.decision_threshold),
    })
    card = card.model_copy(update={"metric_in_words": words})
    return card.model_copy(update={"markdown": render_markdown(card)})


# --- cancellation ----------------------------------------------------------------------


def cancel_experiment(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    experiment_id: UUID,
    precondition: Callable[[], None] | None = None,
) -> str:
    """Cancel a queued run now or request a running run to stop. Commits.

    Idempotent: a cancelled run answers ``cancelled``, a pending request
    ``cancelling`` (``precondition`` — the If-Match check — runs only before a
    real transition). Anything else is ``ExperimentNotCancellableError`` (409).
    """

    from app.services.auto_train_service import record_run_cancelled
    from app.services.ml_job_service import request_job_cancellation

    if not can_perform_ml_write(db, actor, workspace_id):
        raise IdentityError("cancelling a run requires an ML-write workspace role", status_code=403)
    experiment, status = _load(db, workspace_id, experiment_id)
    job = db.scalar(
        select(MlJob)
        .where(MlJob.workspace_id == workspace_id, MlJob.pipeline_run_id == experiment.id)
        .order_by(MlJob.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if job is not None and job.status == JOB_CANCELLED:
        db.rollback()
        return "cancelled"
    if job is not None and job.status == JOB_RUNNING and job.cancel_requested_at is not None:
        db.rollback()
        return "cancelling"
    if (
        job is None
        or job.status not in {JOB_QUEUED, JOB_RUNNING}
        or experiment.scientific_evidence_locked_at is not None
        or str(experiment.status).upper() in {"COMPLETED", "CANCELLED"}
    ):
        db.rollback()
        raise ExperimentNotCancellableError(status, f"a {status} run cannot be cancelled")
    if precondition is not None:
        precondition()
    outcome = request_job_cancellation(db, job, actor_user_id=actor.id)
    if outcome == JOB_CANCELLED and job.upload_id is not None:
        record_run_cancelled(db, job.upload_id, cancelled_by=actor.id)  # commits job + run state together
    db.commit()
    return outcome
