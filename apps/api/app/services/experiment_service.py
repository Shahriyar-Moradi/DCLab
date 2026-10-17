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

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, exists, func, or_, select, tuple_
from sqlalchemy.orm import Session, aliased

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
    MlJobAttempt,
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
    ExperimentAttemptRead,
    ExperimentDetailRead,
    ExperimentWinner,
    FeatureContractColumnRead,
    FeatureContractRead,
    MODEL_VERSION_PAGE_DEFAULT,
    MODEL_VERSION_PAGE_MAX,
    ModelPackageCheckRead,
    ModelPackageVerificationRead,
    ModelVersionArtifactRef,
    ModelVersionLineage,
    ModelVersionResourceRead,
    ProjectModelVersionItem,
    ProjectModelVersionPage,
)
from app.domain.findings import ExperimentFindingsRead, findings_read
from app.domain.model_card import (
    FINAL_EVALUATION_WITHHELD,
    THRESHOLD_CAVEAT,
    TOP_DRIVERS,
    ModelCardBaseline,
    ModelCardConfusion,
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
from app.domain.pipeline_run_branch import EVIDENCE_MODE_SEALED, SEALED_FINAL_TEST_NOTE
from app.engine.evaluation.metrics import LOWER_IS_BETTER
from app.engine.modeling.objective import DEFAULT_THRESHOLD
from app.engine.serving.model_package import saved_format
from app.services.model_release_service import release_for_version
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


def _attempts(db: Session, workspace_id: UUID, job_id: UUID) -> list[ExperimentAttemptRead]:
    """D2a: the job's attempts (closed codes only), oldest first."""

    rows = db.scalars(
        select(MlJobAttempt)
        .where(MlJobAttempt.workspace_id == workspace_id, MlJobAttempt.ml_job_id == job_id)
        .order_by(MlJobAttempt.attempt.asc())
    ).all()
    return [
        ExperimentAttemptRead(
            attempt=row.attempt,
            started_at=row.started_at,
            ended_at=row.ended_at,
            duration_seconds=(
                round(max(0.0, (row.ended_at - row.started_at).total_seconds()), 3) if row.ended_at is not None else None
            ),
            outcome=row.outcome,
            failure_class=row.failure_class,
        )
        for row in rows
    ]


_RULE_STARTS = frozenset({"system", "schedule"})


def started_by(db: Session, workspace_id: UUID, experiments: list[Experiment],
               viewer_id: UUID | None) -> dict[UUID, str | None]:
    """D8a ``started_by`` of each run from existing records, in at most five workspace-scoped
    statements: a service token's execution request or improve loop (``connected_tool``) > an
    accepted AI proposal whose command started it (``assistant``) > an automatic retrain or a
    system / scheduled workflow run (``rules``) > the person behind the improve loop, workflow run
    or retrain (``you`` for ``viewer_id``, else ``person``). ``viewer_id`` is None for tokens/agents."""

    from app.db.models import AgentProposal, ImproveIteration, ImproveRun, ProjectDecisionRecord, RetrainRequest

    ids = [row.id for row in experiments]
    if not ids:
        return {}
    tokens = set(db.scalars(select(ExecutionRequest.pipeline_run_id).where(
        ExecutionRequest.workspace_id == workspace_id, ExecutionRequest.pipeline_run_id.in_(ids),
        ExecutionRequest.initiated_by_service_token_id.is_not(None))))
    loops = {exp: (token, user) for exp, token, user in db.execute(
        select(ImproveIteration.experiment_id, ImproveRun.created_by_service_token_id, ImproveRun.created_by_user_id)
        .join(ImproveRun, (ImproveRun.workspace_id == ImproveIteration.workspace_id)
              & (ImproveRun.id == ImproveIteration.improve_run_id))
        .where(ImproveIteration.workspace_id == workspace_id, ImproveIteration.experiment_id.in_(ids)))}
    pdr, executed = ProjectDecisionRecord, ProjectDecisionRecord.details["executed"]
    ai = set(db.scalars(select(executed["id"].astext).join(
        AgentProposal, (AgentProposal.workspace_id == pdr.workspace_id) & (AgentProposal.decision_record_id == pdr.id))
        .where(pdr.workspace_id == workspace_id, pdr.decision_type == "proposal_accepted",
               executed["kind"].astext == "experiment", executed["id"].astext.in_([str(i) for i in ids]))))
    retrains = {exp: (origin, user) for exp, origin, user in db.execute(
        select(RetrainRequest.experiment_id, RetrainRequest.origin, RetrainRequest.requested_by_user_id)
        .where(RetrainRequest.workspace_id == workspace_id, RetrainRequest.experiment_id.in_(ids)))}
    run_ids = [row.workflow_run_id for row in experiments if row.workflow_run_id is not None]
    runs = {run: (kind, user) for run, kind, user in db.execute(
        select(WorkflowRun.id, WorkflowRun.initiated_by_type, WorkflowRun.requested_by)
        .where(WorkflowRun.workspace_id == workspace_id, WorkflowRun.id.in_(run_ids)))} if run_ids else {}
    out: dict[UUID, str | None] = {}
    for row in experiments:
        loop, retrain = loops.get(row.id), retrains.get(row.id)
        kind, user = runs.get(row.workflow_run_id, (None, None))
        if row.id in tokens or (loop is not None and loop[0] is not None) or kind == "agent":
            out[row.id] = "connected_tool"
        elif str(row.id) in ai:
            out[row.id] = "assistant"
        elif (retrain is not None and retrain[0] == "auto") or kind in _RULE_STARTS:
            out[row.id] = "rules"
        else:
            person = loop[1] if loop is not None else user or (retrain[1] if retrain is not None else None)
            you = viewer_id is not None and person == viewer_id
            out[row.id] = None if person is None else "you" if you else "person"
    return out


def experiment_read(db: Session, *, actor: User, workspace_id: UUID, experiment_id: UUID,
                    agent: bool = False) -> ExperimentDetailRead:
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
    job = db.execute(
        select(MlJob.id, MlJob.cancel_requested_at)
        .where(MlJob.workspace_id == workspace_id, MlJob.pipeline_run_id == experiment.id)
        .order_by(MlJob.created_at.desc())
        .limit(1)
    ).first()
    cancel_requested_at = job.cancel_requested_at if job is not None else None
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
        started_by=started_by(db, workspace_id, [experiment], None if agent else actor.id)[experiment.id],
        evidence_mode=experiment.evidence_mode,
        evidence_note=SEALED_FINAL_TEST_NOTE if experiment.evidence_mode == EVIDENCE_MODE_SEALED else None,
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
        attempts=_attempts(db, workspace_id, job.id) if job is not None else [],
    )


def experiment_findings(
    db: Session, *, actor: User, workspace_id: UUID, experiment_id: UUID, agent: bool = False
) -> ExperimentFindingsRead:
    """The run's trust checks (five since P4.10-A, fifteen since P5.1-A), stored before its
    evidence lock; runs that predate them read ``investigated: false``. ``agent``: the
    agent-consumer view (holdout-scoped evidence stripped, fixed messages for the checks that
    compare training and test rows)."""

    _require_read(db, actor, workspace_id)
    experiment, _status = _load(db, workspace_id, experiment_id)
    return findings_read(experiment.id, (experiment.result or {}).get("investigation"), agent=agent)


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
    agent: bool = False,
) -> ExperimentPage:
    """Experiments of the workspace, newest first; the cursor is bound to the filters. ``agent``:
    a service token or agent reads (``started_by`` never says ``you``)."""

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
    starters = started_by(db, workspace_id, [row for row, _state in page], None if agent else actor.id)
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
                evidence_mode=row.evidence_mode,
                intent=_intent(row),
                started_by=starters[row.id],
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
        list(db.scalars(select(Artifact).where(Artifact.workspace_id == workspace_id, Artifact.id.in_(artifact_ids))))
        if artifact_ids
        else []
    )
    model_artifact = next((item for item in artifacts if artifact_ids[item.id] == "model"), None)
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
        saved_format=saved_format(model_artifact.extra_metadata) if model_artifact is not None else None,
        release=release_for_version(db, workspace_id=workspace_id, model_version=row) if ref_kinds else None,
        package_verification=package_verification_read(model_artifact),
    )


_PACKAGE_NOTE = (
    "Checked once by the worker when it saved the model, before the final test and on training rows only; "
    "every scoring checks the file's digests again before using it."
)


def package_verification_read(model_artifact: Artifact | None) -> ModelPackageVerificationRead | None:
    """V7 B4: the build-time checks the worker stored with the model artifact (no model is loaded here)."""

    from app.engine.serving.model_package import VERIFICATION_FORMAT

    if model_artifact is None:
        return None
    if saved_format(model_artifact.extra_metadata) != "package_v1":
        return ModelPackageVerificationRead(
            status="not_applicable", note="This model is not saved as a model package, so the file checks do not apply.")
    stored = (model_artifact.extra_metadata or {}).get("verification")
    parity = stored.get("parity") if isinstance(stored, dict) else None
    if not isinstance(stored, dict) or stored.get("format") != VERIFICATION_FORMAT or not isinstance(parity, dict):
        return ModelPackageVerificationRead(
            status="not_recorded",
            note="This model was saved before the file checks were recorded; every scoring still checks its digests.")
    rows = int(parity["rows"]) if isinstance(parity.get("rows"), int) else None
    same = parity.get("passed") is True
    try:
        checked_at = datetime.fromisoformat(str(stored.get("checked_at")))
    except ValueError:
        checked_at = None
    return ModelPackageVerificationRead(
        status="verified", checked_at=checked_at, rows_compared=rows, row_source="training_partition",
        comparison="exact", tolerance=_num(parity.get("tolerance")), max_abs_difference=_num(parity.get("max_abs_difference")),
        note=_PACKAGE_NOTE,
        checks=[
            ModelPackageCheckRead(check="opens", passed=stored.get("opens") is True,
                                  text="The saved file opens with the worker's safe loader (no pickle)."),
            ModelPackageCheckRead(check="digests", passed=stored.get("digests") is True,
                                  text="Every part of the file matches the SHA-256 digest recorded when it was saved."),
            ModelPackageCheckRead(
                check="same_predictions", passed=same,
                text=f"{rows if rows is not None else 'Sample'} training rows score "
                     f"{'exactly the same' if same else 'differently'} with the saved file as with the trained model."),
        ],
    )


# --- project model versions (GET /v1/projects/{id}/model-versions, V7 B2) -------------------


def _at_locked_threshold(task_type: str | None, result: dict[str, Any]) -> dict[str, float]:
    """Binary: threshold-dependent CV metrics as fold means at the locked threshold (empty when the
    threshold is the default 0.5, where the CV aggregate already holds them)."""

    threshold = dict(result.get("decision_threshold") or {})
    locked = _num(threshold.get("value"))
    out: dict[str, float] = {}
    if task_type == "binary" and locked is not None and locked != DEFAULT_THRESHOLD:
        rows = [row for row in threshold.get("per_fold") or [] if isinstance(row, dict)]
        for key in _THRESHOLD_DEPENDENT:
            values = [_num(row.get(key)) for row in rows]
            if values and None not in values:
                out[key] = sum(values) / len(values)  # type: ignore[arg-type]
    return out


def list_project_model_versions(
    db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, cursor: str | None = None,
    limit: int = MODEL_VERSION_PAGE_DEFAULT,
) -> ProjectModelVersionPage:
    """The project's model versions, newest first, with each locked winner's CV metrics (never a
    final-test value) and where it stands: in use, waiting for approval (an open proposed
    ``champion_promoted`` record), approved earlier, or not in use. A fixed number of statements
    per page. Raises ``IdentityError`` / ``ProjectNotFoundError`` / ``InvalidCursorError``."""

    from app.db.models import EvaluationMetric, ModelEvaluation, ModelRelease, ProjectDecisionRecord
    from app.domain.decision_records import STATE_PROPOSED
    from app.services.model_release_service import current_release

    get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    limit = max(1, min(int(limit), MODEL_VERSION_PAGE_MAX))
    scope = f"model-versions:{workspace_id}:{project_id}"
    stmt = select(ModelVersion).where(ModelVersion.workspace_id == workspace_id, ModelVersion.project_id == project_id)
    if cursor is not None and cursor.strip():
        message = "cursor is not a model version list cursor for this project"
        try:
            stamp, last_id = open_cursor(cursor, scope, message=message)
            created_at, row_id = datetime.fromisoformat(stamp), UUID(last_id)
            if created_at.tzinfo is None:
                raise ValueError(message)
        except (InvalidCursorError, ValueError, TypeError) as exc:
            raise InvalidCursorError(message) from exc
        stmt = stmt.where(tuple_(ModelVersion.created_at, ModelVersion.id) < tuple_(created_at, row_id))
    rows = list(db.scalars(stmt.order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc()).limit(limit + 1)))
    page = rows[:limit]
    next_cursor = sign_cursor(scope, [page[-1].created_at.isoformat(), str(page[-1].id)]) if len(rows) > limit else None
    ids, run_ids = [row.id for row in page], [row.pipeline_run_id for row in page]
    experiments = {row.id: row for row in db.scalars(
        select(Experiment).where(Experiment.workspace_id == workspace_id, Experiment.id.in_(run_ids)))} if page else {}
    selections = {run: (candidate, metric) for run, candidate, metric in db.execute(
        select(ModelSelectionDecision.pipeline_run_id, ModelSelectionDecision.selected_candidate_id,
               ModelSelectionDecision.selection_metric)
        .where(ModelSelectionDecision.workspace_id == workspace_id, ModelSelectionDecision.pipeline_run_id.in_(run_ids)))}
    candidates = {row.id: row for row in db.scalars(select(ExperimentCandidate).where(
        ExperimentCandidate.workspace_id == workspace_id,
        ExperimentCandidate.id.in_([row.selected_candidate_id for row in page])))} if page else {}
    # Same gate as the model-version read: metrics only once the run's evidence is locked for this winner.
    locked = {
        row.id for row in page
        if (run := experiments.get(row.pipeline_run_id)) is not None and run.scientific_evidence_locked_at is not None
        and (selections.get(run.id) or (None,))[0] == row.selected_candidate_id
    }
    cv: dict[UUID, dict[str, float]] = {}
    winners = [row.selected_candidate_id for row in page if row.id in locked]
    if winners:
        for candidate_id, name, value in db.execute(
            select(ModelEvaluation.candidate_id, EvaluationMetric.metric_name, EvaluationMetric.metric_value)
            .join(EvaluationMetric, EvaluationMetric.model_evaluation_id == ModelEvaluation.id)
            .where(ModelEvaluation.workspace_id == workspace_id, ModelEvaluation.candidate_id.in_(winners),
                   ModelEvaluation.evaluation_scope == "cv_aggregate")
        ):
            cv.setdefault(candidate_id, {})[str(name)] = float(value)
    champion = db.scalar(select(ProjectRef.model_version_id).where(
        ProjectRef.workspace_id == workspace_id, ProjectRef.project_id == project_id,
        ProjectRef.ref_kind == "champion_model"))
    current = current_release(db, workspace_id=workspace_id, project_id=project_id)
    approvals = dict(db.execute(
        select(ModelRelease.model_version_id, func.max(ModelRelease.created_at))
        .where(ModelRelease.workspace_id == workspace_id, ModelRelease.project_id == project_id,
               ModelRelease.model_version_id.in_(ids))
        .group_by(ModelRelease.model_version_id)).all()) if page else {}
    pdr, successor = ProjectDecisionRecord, aliased(ProjectDecisionRecord)
    pending: dict[UUID, UUID] = {}
    for version_id, decision_id in (db.execute(
        select(pdr.model_version_id, pdr.id)
        .where(pdr.workspace_id == workspace_id, pdr.project_id == project_id,
               pdr.decision_type == "champion_promoted", pdr.state == STATE_PROPOSED, pdr.model_version_id.in_(ids),
               ~exists().where(successor.supersedes_id == pdr.id, successor.workspace_id == pdr.workspace_id))
        .order_by(pdr.event_at.desc(), pdr.id.desc())) if page else []):
        pending.setdefault(version_id, decision_id)
    stored = {row.id: row for row in db.scalars(select(Artifact).where(
        Artifact.workspace_id == workspace_id,
        Artifact.id.in_([row.model_artifact_id for row in page if row.model_artifact_id])))} if page else {}
    items = []
    for row in page:
        run, candidate = experiments.get(row.pipeline_run_id), candidates.get(row.selected_candidate_id)
        result = dict(run.result or {}) if run is not None and row.id in locked else {}
        in_use = champion == row.id
        status = ("in_use" if in_use else "waiting_for_approval" if row.id in pending
                  else "approved_earlier" if row.id in approvals else "not_in_use")
        artifact = stored.get(row.model_artifact_id) if row.model_artifact_id else None
        items.append(ProjectModelVersionItem(
            id=row.id, version=row.version, created_at=row.created_at, experiment_id=row.pipeline_run_id,
            candidate_key=candidate.candidate_key if candidate is not None else None,
            family=(candidate.model_family or None) if candidate is not None else None,
            algorithm=(candidate.algorithm or None) if candidate is not None else None,
            split_plan_id=run.split_plan_id if run is not None else None,
            saved_format=saved_format(artifact.extra_metadata) if artifact is not None else None,
            selection_metric=(selections.get(row.pipeline_run_id) or (None, None))[1] if row.id in locked else None,
            cv=cv.get(row.selected_candidate_id, {}),
            cv_at_locked_threshold=_at_locked_threshold(dict(result.get("task") or {}).get("task_type"), result),
            decision_threshold=_num(dict(result.get("decision_threshold") or {}).get("value")),
            status=status, in_use=in_use,
            approved_for_scoring=in_use and current is not None and current.model_version_id == row.id,
            waiting_for_approval=row.id in pending, pending_decision_id=pending.get(row.id),
            last_approved_at=approvals.get(row.id),
        ))
    return ProjectModelVersionPage(project_id=project_id, model_in_use_id=champion, items=items,
                                   next_cursor=next_cursor, limit=limit)


# --- feature contract (GET /v1/model-versions/{id}/feature-contract, V7 B3) ----------------

CONTRACT_NAME_MAX = 1024


def model_feature_contract(
    db: Session, *, actor: User, workspace_id: UUID, model_version_id: UUID
) -> FeatureContractRead | None:
    """The input columns a scoring file must have (names, roles, training dtypes, date flag),
    read from the version's stored feature manifest (JSON, capped, digest-checked; no model is
    loaded). Allowed values, date formats and every other value-bearing field are left out.
    None when the version is unknown or another tenant's; 409 without a readable manifest."""

    from app.engine.serving.model_package import MANIFEST_MAX_BYTES
    from app.services.artifact_service import ArtifactTooLargeError, read_artifact_bytes
    from app.storage._hashing import sha256_bytes

    _require_read(db, actor, workspace_id)
    row = db.scalar(
        select(ModelVersion).where(ModelVersion.id == model_version_id, ModelVersion.workspace_id == workspace_id))
    if row is None:
        return None
    unavailable = ExperimentRequestError(
        "feature_contract_unavailable", "this model version has no readable feature manifest", status_code=409)
    artifact = db.scalar(select(Artifact).where(
        Artifact.id == row.feature_manifest_artifact_id, Artifact.workspace_id == workspace_id,
    )) if row.feature_manifest_artifact_id is not None else None
    if artifact is None or int(artifact.size_bytes or 0) > MANIFEST_MAX_BYTES:
        raise unavailable
    try:
        data = read_artifact_bytes(db, workspace_id=workspace_id, artifact_id=artifact.id, max_bytes=MANIFEST_MAX_BYTES)
        manifest = json.loads(data.decode("utf-8")) if sha256_bytes(data) == artifact.content_digest else None
    except (ArtifactTooLargeError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise unavailable from exc
    if not isinstance(manifest, dict):
        raise unavailable
    contract = manifest.get("feature_contract")
    if isinstance(contract, dict) and isinstance(contract.get("columns"), list):
        columns = [
            FeatureContractColumnRead(
                name=str(item["name"])[:CONTRACT_NAME_MAX],
                role=item.get("role") if item.get("role") in ("numeric", "categorical") else None,
                training_dtype=str(item["training_dtype"])[:64] if item.get("training_dtype") is not None else None,
                required=item.get("required") is not False,
                is_date="date_format_recorded" in item,
            )
            for item in contract["columns"] if isinstance(item, dict) and item.get("name") is not None
        ]
        target = contract.get("target_column")
        return FeatureContractRead(
            model_version_id=row.id, status="available", contract_format=str(contract.get("format") or "")[:64] or None,
            column_count=len(columns), columns=columns,
            target_column=str(target)[:CONTRACT_NAME_MAX] if target is not None else None,
            unknown_columns=str(contract.get("unknown_columns") or "")[:64] or None,
            unseen_categories=str(contract.get("unseen_categories") or "")[:64] or None,
            contract_digest=str(contract.get("digest") or "")[:64] or None, manifest_digest=artifact.content_digest,
        )
    names = [str(name)[:CONTRACT_NAME_MAX] for name in manifest.get("features") or [] if name is not None] \
        if isinstance(manifest.get("features"), list) else []
    return FeatureContractRead(
        model_version_id=row.id, status="names_only", column_count=len(names),
        columns=[FeatureContractColumnRead(name=name) for name in names], manifest_digest=artifact.content_digest,
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
    at_locked = _at_locked_threshold(task_type, result)
    note = None
    if task_type == "binary" and locked is not None and locked != DEFAULT_THRESHOLD:
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


def _final_confusion(result: dict[str, Any], winner_key: str | None, task_type: str | None) -> ModelCardConfusion | None:
    """V7 B1: the 2x2 counts of the run's single final evaluation (binary, at the locked threshold),
    as the run recorded them for the locked winner; None when absent or inconsistent."""

    stored = result.get("final_test_evaluation")
    if task_type != "binary" or not winner_key or not isinstance(stored, dict) or stored.get("candidate_id") != winner_key:
        return None
    matrix = (stored.get("metrics") or {}).get("confusion_matrix") if isinstance(stored.get("metrics"), dict) else None
    if not isinstance(matrix, dict) or not all(
        isinstance(matrix.get(key), int) and not isinstance(matrix.get(key), bool) and matrix[key] >= 0
        for key in ("tp", "fp", "fn", "tn")
    ):
        return None
    counts = {key: int(matrix[key]) for key in ("tp", "fp", "fn", "tn")}
    total = sum(counts.values())
    if stored.get("test_row_count") is not None and stored.get("test_row_count") != total:
        return None
    return ModelCardConfusion(**counts, rows=total)


def model_card_read(
    db: Session, *, actor: User, workspace_id: UUID, model_version_id: UUID, include_final_evaluation: bool = False
) -> ModelCardRead | None:
    """One-page card of a model version (None when unknown or another tenant's).

    Only once the run's evidence is locked and its selection is this version (else 409).
    Everything that says how good the model is comes from CV/out-of-fold evidence; the
    only final-holdout read is ``winner_evidence``'s single locked-winner evaluation,
    which fills ``final_evaluation`` alone and only with ``include_final_evaluation``
    (fail-closed: callers that are not session humans get it withheld), together with that
    same evaluation's binary counts from the run's ``final_test_evaluation`` record (V7 B1).
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
            confusion=_final_confusion(result, candidate.candidate_key if candidate is not None else None, task_type),
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
