"""Admin-only reads for Labs custom-box uploads and their auto-train jobs."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ClientLabUpload
from app.domain.admin_client_uploads import AdminClientUploadDetail, AdminClientUploadSummary


def list_client_uploads(db: Session) -> list[AdminClientUploadSummary]:
    rows = db.scalars(select(ClientLabUpload).order_by(ClientLabUpload.created_at.desc()).limit(200)).all()
    return [AdminClientUploadSummary.model_validate(row) for row in rows]


def get_client_upload(db: Session, upload_id: UUID) -> AdminClientUploadDetail | None:
    row = db.get(ClientLabUpload, upload_id)
    if row is None:
        return None
    return AdminClientUploadDetail(
        id=row.id,
        workspace_id=row.workspace_id,
        category=row.category,
        original_filename=row.original_filename,
        kind=row.kind,
        record_count=row.record_count,
        has_named_fields=row.has_named_fields,
        pipeline_status=row.pipeline_status,
        experiment_id=row.experiment_id,
        created_at=row.created_at,
        stored_path=row.stored_path,
        fields_noticed=list(row.fields_noticed or []),
        pipeline_log=row.pipeline_log,
    )
