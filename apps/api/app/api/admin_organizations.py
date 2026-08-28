from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.admin_organization import OrganizationDetail, OrganizationSummary
from app.services.admin_organization_service import get_organization, list_organizations

router = APIRouter(prefix="/organizations", tags=["admin-organizations"])


@router.get("", response_model=list[OrganizationSummary])
def list_organizations_endpoint(db: Session = Depends(get_db)) -> list[OrganizationSummary]:
    return list_organizations(db)


@router.get("/{workspace_id}", response_model=OrganizationDetail)
def get_organization_endpoint(workspace_id: UUID, db: Session = Depends(get_db)) -> OrganizationDetail:
    org = get_organization(db, workspace_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    return org
