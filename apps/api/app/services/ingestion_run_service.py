"""IngestionRun lifecycle for Project → DataSource → Dataset lineage."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import DataSource, IngestionRun, Project
from app.domain.data_plane import INGESTION_RUN_STATUSES
from app.domain.errors import IdentityError, IngestionRunNotFoundError

_TERMINAL = frozenset({"completed", "failed"})


def _now() -> datetime:
    return datetime.now(UTC)


def _require_source(db: Session, workspace_id: UUID, data_source_id: UUID) -> DataSource:
    source = db.get(DataSource, data_source_id)
    if source is None or source.workspace_id != workspace_id:
        raise IdentityError("data source does not belong to this workspace", status_code=404)
    return source


def _require_project(db: Session, workspace_id: UUID, project_id: UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise IdentityError("project does not belong to this workspace", status_code=404)
    return project


def start_ingestion_run(
    db: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    data_source_id: UUID,
    status: str = "running",
) -> IngestionRun:
    if status not in INGESTION_RUN_STATUSES:
        raise IdentityError(f"unsupported ingestion status: {status}", status_code=400)
    _require_project(db, workspace_id, project_id)
    _require_source(db, workspace_id, data_source_id)
    now = _now()
    row = IngestionRun(
        workspace_id=workspace_id,
        project_id=project_id,
        data_source_id=data_source_id,
        status=status,
        started_at=now,
        rows_read=0,
        rows_written=0,
        bytes_read=0,
    )
    db.add(row)
    db.flush()
    return row


def complete_ingestion_run(
    db: Session,
    run: IngestionRun,
    *,
    rows_read: int,
    rows_written: int,
    bytes_read: int,
    schema_digest: str | None = None,
    content_digest: str | None = None,
) -> IngestionRun:
    if run.status in _TERMINAL:
        raise IdentityError("ingestion run already finished", status_code=409)
    run.status = "completed"
    run.completed_at = _now()
    run.rows_read = int(rows_read)
    run.rows_written = int(rows_written)
    run.bytes_read = int(bytes_read)
    run.schema_digest = schema_digest
    run.content_digest = content_digest
    run.error_code = None
    run.error_summary = None
    db.flush()
    return run


def fail_ingestion_run(
    db: Session,
    run: IngestionRun,
    *,
    error_code: str,
    error_summary: str,
) -> IngestionRun:
    if run.status in _TERMINAL:
        raise IdentityError("ingestion run already finished", status_code=409)
    run.status = "failed"
    run.completed_at = _now()
    run.error_code = error_code[:64]
    run.error_summary = error_summary[:1024]
    db.flush()
    return run


def get_ingestion_run(
    db: Session, *, workspace_id: UUID, ingestion_run_id: UUID
) -> IngestionRun:
    row = db.get(IngestionRun, ingestion_run_id)
    if row is None or row.workspace_id != workspace_id:
        raise IngestionRunNotFoundError("ingestion run not found")
    return row
