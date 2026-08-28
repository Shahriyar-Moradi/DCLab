from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.admin_monitoring import MonitoringOverview
from app.services.admin_monitoring_service import get_monitoring_overview

router = APIRouter(prefix="/monitoring", tags=["admin-monitoring"])


@router.get("", response_model=MonitoringOverview)
def get_monitoring_overview_endpoint(db: Session = Depends(get_db)) -> MonitoringOverview:
    return get_monitoring_overview(db)
