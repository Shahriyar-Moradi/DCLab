from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id, require_admin, require_workspace_read
from app.db.models import User
from app.db.session import get_db
from app.domain.admin_monitoring import MonitoringOverview
from app.services.admin_monitoring_service import get_monitoring_overview
from app.services.legacy_admin_access_audit import record_legacy_admin_access

router = APIRouter(prefix="/monitoring", tags=["admin-monitoring"])


@router.get("", response_model=MonitoringOverview)
def get_monitoring_overview_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(require_workspace_read),
    user: User = Depends(require_admin),
) -> MonitoringOverview:
    workspace_id = request_workspace_id(request)
    record_legacy_admin_access(
        request, action="monitoring_list", actor_id=user.id, workspace_id=workspace_id
    )
    return get_monitoring_overview(db, workspace_id)
