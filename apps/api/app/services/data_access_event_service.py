"""Append-only data-access audit events. Summaries only — never raw rows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import DataAccess, DataAccessEvent, ExecutionRequest, IngestionRun
from app.domain.errors import DataAccessEventSpecError, IdentityError
from app.domain.privacy_audit import (
    ALLOWED_COLUMN_SUMMARY_KEYS,
    ALLOWED_RESOURCE_SUMMARY_KEYS,
    DATA_ACCESS_EVENT_ACTOR_TYPES,
    DATA_ACCESS_EVENT_OPERATIONS,
    DATA_ACCESS_EVENT_PURPOSES,
    DATA_ACCESS_EVENT_STATUSES,
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_STARTED,
    FORBIDDEN_AUDIT_JSON_KEYS,
    SUMMARY_MAX_BYTES,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_object(value: dict[str, Any] | None, *, label: str) -> dict[str, Any]:
    payload = {} if value is None else value
    if type(payload) is not dict:
        raise DataAccessEventSpecError(f"{label} must be a JSON object")
    return payload


def _reject_forbidden_keys(payload: dict[str, Any], *, label: str) -> None:
    blocked = sorted(set(payload) & set(FORBIDDEN_AUDIT_JSON_KEYS))
    if blocked:
        raise DataAccessEventSpecError(
            f"{label} must not contain {', '.join(blocked)}"
        )


def _bounded_json(payload: dict[str, Any], *, label: str) -> dict[str, Any]:
    encoded = json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")
    if len(encoded) > SUMMARY_MAX_BYTES:
        raise DataAccessEventSpecError(f"{label} exceeds {SUMMARY_MAX_BYTES} bytes")
    return payload


def bound_resource_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    summary = _as_object(payload, label="resource_summary")
    _reject_forbidden_keys(summary, label="resource_summary")
    unknown = sorted(set(summary) - ALLOWED_RESOURCE_SUMMARY_KEYS)
    if unknown:
        raise DataAccessEventSpecError(
            f"resource_summary has unsupported keys: {', '.join(unknown)}"
        )
    return _bounded_json(summary, label="resource_summary")


def bound_column_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    summary = _as_object(payload, label="column_summary")
    _reject_forbidden_keys(summary, label="column_summary")
    unknown = sorted(set(summary) - ALLOWED_COLUMN_SUMMARY_KEYS)
    if unknown:
        raise DataAccessEventSpecError(
            f"column_summary has unsupported keys: {', '.join(unknown)}"
        )
    names = summary.get("column_names")
    if names is not None:
        if not isinstance(names, list) or any(not isinstance(item, str) for item in names):
            raise DataAccessEventSpecError("column_summary.column_names must be strings")
        summary = {
            **summary,
            "column_names": [str(name)[:256] for name in names[:256]],
        }
    return _bounded_json(summary, label="column_summary")


def append_data_access_event(
    db: Session,
    *,
    workspace_id: UUID,
    data_access_id: UUID,
    actor_type: str,
    purpose: str,
    operation: str,
    status: str = EVENT_COMPLETED,
    actor_user_id: UUID | None = None,
    execution_request_id: UUID | None = None,
    ingestion_run_id: UUID | None = None,
    resource_summary: dict[str, Any] | None = None,
    column_summary: dict[str, Any] | None = None,
    rows_read: int | None = None,
    bytes_read: int | None = None,
    failure_code: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> DataAccessEvent:
    """Insert one audit row. Updates and deletes are refused by PostgreSQL."""

    if actor_type not in DATA_ACCESS_EVENT_ACTOR_TYPES:
        raise DataAccessEventSpecError(f"unsupported actor_type: {actor_type}")
    if purpose not in DATA_ACCESS_EVENT_PURPOSES:
        raise DataAccessEventSpecError(f"unsupported purpose: {purpose}")
    if operation not in DATA_ACCESS_EVENT_OPERATIONS:
        raise DataAccessEventSpecError(f"unsupported operation: {operation}")
    if status not in DATA_ACCESS_EVENT_STATUSES:
        raise DataAccessEventSpecError(f"unsupported status: {status}")
    access = db.get(DataAccess, data_access_id)
    if access is None or access.workspace_id != workspace_id:
        raise IdentityError("data access does not belong to this workspace", status_code=404)
    if execution_request_id is not None:
        request = db.get(ExecutionRequest, execution_request_id)
        if request is None or request.workspace_id != workspace_id:
            raise IdentityError(
                "execution request does not belong to this workspace", status_code=404
            )
    if ingestion_run_id is not None:
        run = db.get(IngestionRun, ingestion_run_id)
        if run is None or run.workspace_id != workspace_id:
            raise IdentityError(
                "ingestion run does not belong to this workspace", status_code=404
            )
    now = _now()
    started = started_at or now
    if status == EVENT_STARTED:
        finished = None
        code = None
    else:
        finished = completed_at or now
        code = (failure_code or "").strip()[:64] or None
        if status == EVENT_FAILED and not code:
            raise DataAccessEventSpecError("failed events require failure_code")
        if status == EVENT_COMPLETED:
            code = None
    row = DataAccessEvent(
        workspace_id=workspace_id,
        data_access_id=data_access_id,
        execution_request_id=execution_request_id,
        ingestion_run_id=ingestion_run_id,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        purpose=purpose,
        operation=operation,
        resource_summary=bound_resource_summary(resource_summary),
        column_summary=bound_column_summary(column_summary),
        rows_read=rows_read,
        bytes_read=bytes_read,
        status=status,
        started_at=started,
        completed_at=finished,
        failure_code=code,
    )
    db.add(row)
    db.flush()
    return row
