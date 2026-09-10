from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id, require_workspace_read
from app.db.session import get_db
from app.domain.admin_model_registry import ClientTrialAuditDetail, RegisteredModel
from app.services.admin_model_registry_service import get_client_trial_audit, list_registered_models

router = APIRouter(prefix="/models", tags=["admin-model-registry"])


@router.get("", response_model=list[RegisteredModel])
def list_models_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(require_workspace_read),
) -> list[RegisteredModel]:
    return list_registered_models(db, request_workspace_id(request))


@router.get("/client-trials/{audit_id}", response_model=ClientTrialAuditDetail)
def get_client_trial_audit_endpoint(audit_id: UUID, db: Session = Depends(get_db)) -> ClientTrialAuditDetail:
    audit = get_client_trial_audit(db, audit_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="client trial audit not found")
    return audit
