"""Persist protocol-neutral execution requests. Workers stay on MlJob."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ClientLabUpload, ExecutionRequest, User
from app.domain.errors import IdentityError
from app.domain.execution_requests import (
    ALLOWED_REQUEST_SPEC_KEYS,
    FORBIDDEN_REQUEST_PAYLOAD_KEYS,
    OPERATION_MODEL_BUILD,
    REQUEST_ACCEPTED,
    REQUEST_SPEC_MAX_BYTES,
    RESULT_SUMMARY_MAX_BYTES,
    SOURCE_LEGACY_LABS,
)
from app.services.authorization_service import can_read_workspace

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


def _legacy_labs_request_spec(upload: ClientLabUpload) -> dict[str, Any]:
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

    key = (idempotency_key or "").strip() or None
    if key is not None:
        existing = db.scalar(
            select(ExecutionRequest).where(
                ExecutionRequest.workspace_id == workspace_id,
                ExecutionRequest.idempotency_key == key,
            )
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
        request_spec=bound_request_spec(request_spec),
        workflow_run_id=workflow_run_id,
        pipeline_run_id=pipeline_run_id,
    )
    db.add(row)
    db.flush()
    return row


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
        request_spec=_legacy_labs_request_spec(upload),
        workflow_run_id=workflow_run_id,
        pipeline_run_id=pipeline_run_id,
        status=REQUEST_ACCEPTED,
    )
