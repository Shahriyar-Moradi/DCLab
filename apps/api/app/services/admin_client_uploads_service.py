"""Admin-only reads for Labs custom-box uploads and their auto-train jobs."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ClientLabUpload, Dataset, Experiment, LabDecisionRecord
from app.domain.admin_client_uploads import (
    AdminClientUploadDetail,
    AdminClientUploadSummary,
    AdminLabDecisionRecord,
)
from app.services.admin_ml_run import build_ml_run, predictions_csv_text


def list_client_uploads(db: Session) -> list[AdminClientUploadSummary]:
    rows = db.scalars(select(ClientLabUpload).order_by(ClientLabUpload.created_at.desc()).limit(200)).all()
    return [AdminClientUploadSummary.model_validate(row) for row in rows]


def get_client_upload(db: Session, upload_id: UUID) -> AdminClientUploadDetail | None:
    row = db.get(ClientLabUpload, upload_id)
    if row is None:
        return None
    records = db.scalars(
        select(LabDecisionRecord)
        .where(LabDecisionRecord.upload_id == row.id)
        .order_by(LabDecisionRecord.column)
    ).all()
    experiment = db.get(Experiment, row.experiment_id) if row.experiment_id else None
    dataset = None
    if experiment is not None:
        dataset = db.get(Dataset, experiment.dataset_id)
    elif row.dataset_id:
        dataset = db.get(Dataset, row.dataset_id)
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
        decision_records=[AdminLabDecisionRecord.model_validate(item) for item in records],
        ml_run=build_ml_run(row, experiment, dataset),
    )


def predictions_download(db: Session, upload_id: UUID) -> tuple[str, str] | None:
    """Filename and CSV body for the generated holdout predictions, or None."""
    row = db.get(ClientLabUpload, upload_id)
    if row is None or row.experiment_id is None:
        return None
    experiment = db.get(Experiment, row.experiment_id)
    if experiment is None:
        return None
    body = predictions_csv_text(experiment)
    if not body:
        return None
    stem = Path(row.original_filename or "predictions").stem or "predictions"
    return f"{stem}-test-predictions.csv", body
