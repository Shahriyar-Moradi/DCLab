from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.admin_client_uploads import AdminClientUploadDetail, AdminClientUploadSummary
from app.services.admin_client_uploads_service import get_client_upload, list_client_uploads

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
