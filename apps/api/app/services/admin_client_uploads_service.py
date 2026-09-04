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
from app.services.ml_run_docx import render_ml_run_report_docx
from app.services.pipeline_audit_service import (
    RunNotFoundError,
    RunReportNotReadyError,
    canonical_report_for_run,
)


def list_client_uploads(db: Session) -> list[AdminClientUploadSummary]:
    rows = db.scalars(select(ClientLabUpload).order_by(ClientLabUpload.created_at.desc()).limit(200)).all()
    return [
        AdminClientUploadSummary.model_validate(row).model_copy(
            update={"workflow_run_id": row.workflow_runs[0].id if row.workflow_runs else None}
        )
        for row in rows
    ]


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
        workflow_run_id=experiment.workflow_run_id if experiment is not None else None,
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


def technical_report_download(db: Session, upload_id: UUID) -> tuple[str, bytes] | None:
    """Return the admin DOCX generated only from the persisted canonical report."""
    try:
        report = canonical_report_for_run(db, upload_id)
    except (RunNotFoundError, RunReportNotReadyError):
        return None
    return "DCLab ML Run Report.docx", render_ml_run_report_docx(report)
