"""Persist protocol-neutral execution requests. Workers stay on MlJob."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    ClientLabUpload,
    Dataset,
    ExecutionRequest,
    MlJob,
    User,
    WorkflowRun,
)
from app.domain.errors import (
    ExecutionNotWaitingError,
    IdentityError,
    TargetIntentConflictError,
    TargetNotInDatasetError,
)
from app.domain.execution_requests import (
    ALLOWED_REQUEST_SPEC_KEYS,
    EXECUTION_RESUMED,
    FORBIDDEN_REQUEST_PAYLOAD_KEYS,
    OPERATION_MODEL_BUILD,
    REQUEST_ACCEPTED,
    REQUEST_NEEDS_INPUT,
    REQUEST_RUNNING,
    REQUEST_SPEC_MAX_BYTES,
    RESULT_SUMMARY_MAX_BYTES,
    SOURCE_LEGACY_LABS,
    TARGET_CONFIRMATION_REQUIRED,
    TARGET_CONFIRMED,
    UQ_EXECUTION_REQUESTS_WORKSPACE_IDEMPOTENCY_KEY,
)
from app.domain.lab_run_stages import ANALYZING
from app.domain.ml_jobs import JOB_RUNNING
from app.services.authorization_service import can_execute_workspace_ml, can_read_workspace

LEGACY_LABS_UPLOAD_IDEMPOTENCY_PREFIX = "legacy_labs_upload:"


class ExecutionRequestSpecError(ValueError):
    """Request payload is too large, uses a forbidden key, or is not an object."""


def _as_object(value: dict[str, Any] | None, *, label: str) -> dict[str, Any]:
    payload = {} if value is None else value
    if type(payload) is not dict:
        raise ExecutionRequestSpecError(f"{label} must be a JSON object")
    return payload


def _reject_forbidden_keys(payload: dict[str, Any], *, label: str) -> None:
    blocked = sorted(set(payload) & set(FORBIDDEN_REQUEST_PAYLOAD_KEYS))
    if blocked:
        raise ExecutionRequestSpecError(
            f"{label} must not contain {', '.join(blocked)}"
        )


def _bounded_json(payload: dict[str, Any], *, limit: int, label: str) -> dict[str, Any]:
    encoded = json.dumps(payload, default=str, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > limit:
        raise ExecutionRequestSpecError(f"{label} exceeds {limit} bytes")
    return payload


def bound_request_spec(payload: dict[str, Any] | None) -> dict[str, Any]:
    spec = _as_object(payload, label="request_spec")
    _reject_forbidden_keys(spec, label="request_spec")
    unknown = sorted(set(spec) - ALLOWED_REQUEST_SPEC_KEYS)
    if unknown:
        raise ExecutionRequestSpecError(
            f"request_spec has unsupported keys: {', '.join(unknown)}"
        )
    return _bounded_json(spec, limit=REQUEST_SPEC_MAX_BYTES, label="request_spec")


def bound_result_summary(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    summary = _as_object(payload, label="result_summary")
    _reject_forbidden_keys(summary, label="result_summary")
    return _bounded_json(summary, limit=RESULT_SUMMARY_MAX_BYTES, label="result_summary")


def _legacy_labs_idempotency_key(upload_id: UUID) -> str:
    return f"{LEGACY_LABS_UPLOAD_IDEMPOTENCY_PREFIX}{upload_id}"


def _execution_request_by_idempotency(
    db: Session, *, workspace_id: UUID, key: str
) -> ExecutionRequest | None:
    return db.scalar(
        select(ExecutionRequest).where(
            ExecutionRequest.workspace_id == workspace_id,
            ExecutionRequest.idempotency_key == key,
        )
    )


def _is_idempotency_key_conflict(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    diag = getattr(orig, "diag", None)
    name = getattr(diag, "constraint_name", None) if diag is not None else None
    if name == UQ_EXECUTION_REQUESTS_WORKSPACE_IDEMPOTENCY_KEY:
        return True
    pgcode = getattr(orig, "pgcode", None)
    return (
        pgcode == "23505"
        and UQ_EXECUTION_REQUESTS_WORKSPACE_IDEMPOTENCY_KEY in str(orig)
    )


def _legacy_labs_request_spec(
    upload: ClientLabUpload, *, problem_spec_id: UUID | None = None
) -> dict[str, Any]:
    fields = [str(name)[:256] for name in list(upload.fields_noticed or [])[:64]]
    spec: dict[str, Any] = {
        "upload_id": str(upload.id),
        "filename": (upload.original_filename or "")[:512],
        "category": upload.category,
        "kind": upload.kind,
        "record_count": int(upload.record_count or 0),
        "column_count": len(fields),
        "fields_noticed": fields,
    }
    if upload.dataset_id is not None:
        spec["dataset_id"] = str(upload.dataset_id)
    if upload.artifact_id is not None:
        spec["artifact_id"] = str(upload.artifact_id)
    target = (upload.explicit_target_column or "").strip()
    if target:
        spec["target_column"] = target[:256]
    if problem_spec_id is not None:
        spec["problem_spec_id"] = str(problem_spec_id)
    return bound_request_spec(spec)


def create_execution_request(
    db: Session,
    *,
    workspace_id: UUID,
    operation: str,
    source_surface: str,
    project_id: UUID | None = None,
    requested_by_user_id: UUID | None = None,
    idempotency_key: str | None = None,
    external_request_id: str | None = None,
    parent_request_id: UUID | None = None,
    request_spec: dict[str, Any] | None = None,
    workflow_run_id: UUID | None = None,
    pipeline_run_id: UUID | None = None,
    status: str = REQUEST_ACCEPTED,
) -> ExecutionRequest:
    """Insert a control-plane request. Duplicate idempotency keys return the row."""

    from app.services.target_intent_service import validate_request_spec_target_intent

    bound = bound_request_spec(request_spec)
    validate_request_spec_target_intent(
        db,
        workspace_id=workspace_id,
        request_spec=bound,
        project_id=project_id,
    )
    key = (idempotency_key or "").strip() or None
    if key is not None:
        existing = _execution_request_by_idempotency(
            db, workspace_id=workspace_id, key=key
        )
        if existing is not None:
            return existing
    row = ExecutionRequest(
        workspace_id=workspace_id,
        project_id=project_id,
        operation=operation,
        source_surface=source_surface,
        requested_by_user_id=requested_by_user_id,
        idempotency_key=key,
        external_request_id=(external_request_id or "").strip() or None,
        parent_request_id=parent_request_id,
        status=status,
        request_spec=bound,
        workflow_run_id=workflow_run_id,
        pipeline_run_id=pipeline_run_id,
    )
    if key is None:
        db.add(row)
        db.flush()
        return row
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
            return row
    except IntegrityError as exc:
        if not _is_idempotency_key_conflict(exc):
            raise
        existing = _execution_request_by_idempotency(
            db, workspace_id=workspace_id, key=key
        )
        if existing is None:
            raise
        return existing


def get_execution_request(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    request_id: UUID,
) -> ExecutionRequest:
    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not found", status_code=404)
    row = db.get(ExecutionRequest, request_id)
    if row is None or row.workspace_id != workspace_id:
        raise IdentityError("not found", status_code=404)
    return row


def attach_legacy_labs_model_build_request(
    db: Session,
    *,
    upload: ClientLabUpload,
    actor: User,
    project_id: UUID | None,
    workflow_run_id: UUID,
    pipeline_run_id: UUID,
    problem_spec_id: UUID | None = None,
) -> ExecutionRequest:
    """Link Labs auto-train intent without replacing ClientLabUpload routes."""

    return create_execution_request(
        db,
        workspace_id=upload.workspace_id,
        project_id=project_id,
        operation=OPERATION_MODEL_BUILD,
        source_surface=SOURCE_LEGACY_LABS,
        requested_by_user_id=actor.id,
        idempotency_key=_legacy_labs_idempotency_key(upload.id),
        request_spec=_legacy_labs_request_spec(upload, problem_spec_id=problem_spec_id),
        workflow_run_id=workflow_run_id,
        pipeline_run_id=pipeline_run_id,
        status=REQUEST_ACCEPTED,
    )


def execution_request_for_upload(
    db: Session, upload: ClientLabUpload
) -> ExecutionRequest | None:
    if upload.experiment_id is not None:
        by_pipeline = db.scalar(
            select(ExecutionRequest).where(
                ExecutionRequest.workspace_id == upload.workspace_id,
                ExecutionRequest.pipeline_run_id == upload.experiment_id,
            )
        )
        if by_pipeline is not None:
            return by_pipeline
    workflow_run = db.scalar(
        select(WorkflowRun).where(WorkflowRun.source_upload_id == upload.id)
    )
    if workflow_run is None:
        return None
    return db.scalar(
        select(ExecutionRequest).where(
            ExecutionRequest.workspace_id == upload.workspace_id,
            ExecutionRequest.workflow_run_id == workflow_run.id,
        )
    )


def upload_for_execution_request(
    db: Session, request: ExecutionRequest
) -> ClientLabUpload | None:
    spec = request.request_spec if isinstance(request.request_spec, dict) else {}
    raw_upload = spec.get("upload_id")
    if raw_upload:
        try:
            upload_id = UUID(str(raw_upload))
        except ValueError:
            upload_id = None
        if upload_id is not None:
            row = db.get(ClientLabUpload, upload_id)
            if row is not None and row.workspace_id == request.workspace_id:
                return row
    if request.workflow_run_id is not None:
        workflow_run = db.get(WorkflowRun, request.workflow_run_id)
        if workflow_run is not None and workflow_run.source_upload_id is not None:
            row = db.get(ClientLabUpload, workflow_run.source_upload_id)
            if row is not None and row.workspace_id == request.workspace_id:
                return row
    return None


def _job_for_request(
    db: Session, request: ExecutionRequest, upload: ClientLabUpload | None
) -> MlJob | None:
    job = db.scalar(
        select(MlJob).where(MlJob.execution_request_id == request.id)
    )
    if job is not None:
        return job
    if upload is not None:
        return db.scalar(select(MlJob).where(MlJob.upload_id == upload.id))
    return None


def _touch_started(request: ExecutionRequest, now: datetime) -> None:
    if request.started_at is None:
        request.started_at = now
    request.completed_at = None
    request.failure_code = None
    request.failure_summary = None


def _emit_execution_event(
    db: Session,
    *,
    upload: ClientLabUpload | None,
    request: ExecutionRequest,
    event_type: str,
    status: str,
    payload: dict[str, Any],
) -> None:
    from app.services.observability_service import (
        append_ml_run_event,
        pipeline_context_for_upload,
    )

    context = None
    if upload is not None:
        context = pipeline_context_for_upload(db, upload.id)
    if context is None:
        return
    _upload, workflow_run, pipeline = context
    append_ml_run_event(
        db,
        workspace_id=request.workspace_id,
        workflow_run_id=workflow_run.id,
        experiment_id=pipeline.id,
        stage="target_task",
        event_type=event_type,
        status=status,
        payload=payload,
        commit=False,
    )


def mark_execution_needs_input(
    db: Session,
    *,
    upload: ClientLabUpload,
    waiting: dict[str, Any],
    source: str,
) -> ExecutionRequest | None:
    """Park the control-plane request in needs_input. Does not fail the run."""

    from app.services.target_intent_service import strip_raw_dataset_values

    request = execution_request_for_upload(db, upload)
    now = datetime.now(UTC)
    summary = bound_result_summary(strip_raw_dataset_values(dict(waiting)))
    if request is not None:
        request.status = REQUEST_NEEDS_INPUT
        request.result_summary = summary
        _touch_started(request, now)
        _emit_execution_event(
            db,
            upload=upload,
            request=request,
            event_type=TARGET_CONFIRMATION_REQUIRED,
            status=REQUEST_NEEDS_INPUT,
            payload={
                **dict(summary or {}),
                "source": source,
                "execution_request_id": str(request.id),
                "workflow_run_id": str(request.workflow_run_id)
                if request.workflow_run_id
                else None,
                "pipeline_run_id": str(request.pipeline_run_id)
                if request.pipeline_run_id
                else None,
            },
        )
    return request


def _resume_auto_train_job(
    db: Session,
    *,
    request: ExecutionRequest,
    upload: ClientLabUpload | None,
    job_was_running: bool,
) -> MlJob | None:
    from app.services.auto_train_service import enqueue_auto_train
    from app.services.ml_job_service import requeue_job

    job = _job_for_request(db, request, upload)
    if job is None:
        return None
    if job.status == JOB_RUNNING or job_was_running:
        return job
    requeue_job(db, job)
    if upload is not None:
        enqueue_auto_train(upload.id)
    return job


def confirm_execution_target(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    request_id: UUID,
    target_column: str,
) -> ExecutionRequest:
    """Supply the missing target and resume the original execution once."""

    from app.services.problem_spec_service import populate_unlocked_problem_spec_target
    from app.services.target_intent_service import (
        TARGET_SOURCE_USER,
        column_names_from_dataset_id,
        confirmed_target_from_request,
        dataset_schema_column_names,
        load_workspace_problem_spec,
        normalize_target_name,
        validate_declared_target_intent,
    )

    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not found", status_code=404)
    if not can_execute_workspace_ml(db, actor, workspace_id):
        raise IdentityError("not found", status_code=404)

    selected = normalize_target_name(target_column)
    if selected is None:
        raise TargetNotInDatasetError("")

    request = db.scalar(
        select(ExecutionRequest)
        .where(ExecutionRequest.id == request_id)
        .with_for_update()
    )
    if request is None or request.workspace_id != workspace_id:
        raise IdentityError("not found", status_code=404)

    already = confirmed_target_from_request(request)
    if request.status != REQUEST_NEEDS_INPUT:
        if already is not None and already == selected:
            return request
        if already is not None and already != selected:
            raise TargetIntentConflictError(
                problem_spec_target=already,
                requested_target=selected,
            )
        raise ExecutionNotWaitingError(request.status)

    upload = upload_for_execution_request(db, request)
    dataset_id = upload.dataset_id if upload is not None else None
    if dataset_id is None:
        spec = request.request_spec if isinstance(request.request_spec, dict) else {}
        raw_dataset = spec.get("dataset_id")
        if raw_dataset:
            try:
                dataset_id = UUID(str(raw_dataset))
            except ValueError:
                dataset_id = None
    dataset = db.get(Dataset, dataset_id) if dataset_id is not None else None
    if dataset is not None and dataset.workspace_id != workspace_id:
        raise TargetNotInDatasetError(selected)
    schema_names = column_names_from_dataset_id(db, dataset_id) if dataset_id else []
    if not schema_names and dataset is not None:
        schema_names = dataset_schema_column_names(dataset)
    if not schema_names and upload is not None:
        schema_names = [str(name) for name in list(upload.fields_noticed or []) if str(name).strip()]

    spec_id = None
    spec_payload = request.request_spec if isinstance(request.request_spec, dict) else {}
    raw_spec = spec_payload.get("problem_spec_id")
    if raw_spec:
        try:
            spec_id = UUID(str(raw_spec))
        except ValueError:
            spec_id = None
    workflow_run = (
        db.get(WorkflowRun, request.workflow_run_id)
        if request.workflow_run_id is not None
        else None
    )
    if spec_id is None and workflow_run is not None:
        spec_id = workflow_run.problem_spec_id
    problem_spec = load_workspace_problem_spec(
        db,
        workspace_id=workspace_id,
        problem_spec_id=spec_id,
        project_id=request.project_id,
    )
    validate_declared_target_intent(
        schema_names=schema_names,
        problem_spec=problem_spec,
        requested_target=selected,
    )
    if schema_names and selected not in schema_names:
        raise TargetNotInDatasetError(selected)

    if problem_spec is not None:
        populate_unlocked_problem_spec_target(db, problem_spec, selected)

    now = datetime.now(UTC)
    spec = dict(spec_payload)
    spec["target_column"] = selected
    request.request_spec = bound_request_spec(spec)
    request.status = REQUEST_RUNNING
    request.result_summary = bound_result_summary(
        {
            "code": TARGET_CONFIRMED,
            "target_column": selected,
            "confirmed_target": selected,
            "source": TARGET_SOURCE_USER,
            "confirmed_by": str(actor.id),
            "confirmed_at": now.isoformat(),
        }
    )
    _touch_started(request, now)

    if upload is not None:
        upload.explicit_target_column = selected
    if workflow_run is not None:
        workflow_run.explicit_target = selected
        workflow_run.failure_reason = None
        if workflow_run.status not in {"completed", "failed", "skipped"}:
            workflow_run.status = "running"

    job = _job_for_request(db, request, upload)
    job_was_running = job is not None and job.status == JOB_RUNNING
    if upload is not None:
        from app.services.auto_train_service import _mark

        log = dict(upload.pipeline_log or {})
        confirmation = log.get("target_confirmation")
        if not isinstance(confirmation, dict):
            confirmation = {}
        log["target_confirmation"] = {
            **confirmation,
            "code": TARGET_CONFIRMED,
            "target_column": selected,
            "source": TARGET_SOURCE_USER,
            "confirmed_by": str(actor.id),
        }
        if not job_was_running:
            _mark(db, upload, status=ANALYZING, log=log)
            db.refresh(request)
            db.refresh(upload)
        else:
            upload.pipeline_log = log
            db.flush()

    _emit_execution_event(
        db,
        upload=upload,
        request=request,
        event_type=TARGET_CONFIRMED,
        status="completed",
        payload={
            "target_column": selected,
            "source": TARGET_SOURCE_USER,
            "confirmed_by": str(actor.id),
            "execution_request_id": str(request.id),
            "workflow_run_id": str(request.workflow_run_id)
            if request.workflow_run_id
            else None,
            "pipeline_run_id": str(request.pipeline_run_id)
            if request.pipeline_run_id
            else None,
        },
    )
    resumed = _resume_auto_train_job(
        db, request=request, upload=upload, job_was_running=job_was_running
    )
    _emit_execution_event(
        db,
        upload=upload,
        request=request,
        event_type=EXECUTION_RESUMED,
        status=REQUEST_RUNNING,
        payload={
            "source": TARGET_SOURCE_USER,
            "target_column": selected,
            "execution_request_id": str(request.id),
            "ml_job_id": str(resumed.id) if resumed is not None else None,
            "workflow_run_id": str(request.workflow_run_id)
            if request.workflow_run_id
            else None,
            "pipeline_run_id": str(request.pipeline_run_id)
            if request.pipeline_run_id
            else None,
        },
    )
    db.commit()
    db.refresh(request)
    return request


def confirm_upload_target(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    upload_id: UUID,
    target_column: str,
) -> ClientLabUpload:
    upload = db.get(ClientLabUpload, upload_id)
    if upload is None or upload.workspace_id != workspace_id:
        raise IdentityError("not found", status_code=404)
    request = execution_request_for_upload(db, upload)
    if request is None:
        raise ExecutionNotWaitingError(upload.pipeline_status or "unknown")
    confirm_execution_target(
        db,
        actor=actor,
        workspace_id=workspace_id,
        request_id=request.id,
        target_column=target_column,
    )
    db.refresh(upload)
    return upload
