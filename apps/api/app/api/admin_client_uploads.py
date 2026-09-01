from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.admin_client_uploads import AdminClientUploadDetail, AdminClientUploadSummary
from app.services.admin_client_uploads_service import (
    get_client_upload,
    list_client_uploads,
    predictions_download,
    technical_report_download,
)

router = APIRouter(prefix="/client-uploads", tags=["admin-client-uploads"])


@router.get("", response_model=list[AdminClientUploadSummary])
def list_client_uploads_endpoint(db: Session = Depends(get_db)) -> list[AdminClientUploadSummary]:
    return list_client_uploads(db)


@router.get("/{upload_id}", response_model=AdminClientUploadDetail)
def get_client_upload_endpoint(upload_id: UUID, db: Session = Depends(get_db)) -> AdminClientUploadDetail:
    row = get_client_upload(db, upload_id)
    if row is None:
        raise HTTPException(status_code=404, detail="client upload not found")
    return row


@router.get("/{upload_id}/predictions.csv")
def download_client_upload_predictions(upload_id: UUID, db: Session = Depends(get_db)) -> Response:
    payload = predictions_download(db, upload_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="predictions are not ready")
    filename, body = payload
    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in filename)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/{upload_id}/report.docx")
def download_client_upload_report(upload_id: UUID, db: Session = Depends(get_db)) -> Response:
    payload = technical_report_download(db, upload_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="technical report is not ready")
    filename, body = payload
    return Response(
        content=body,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
