from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.platform_explorer import (
    BusinessDetailRead,
    BusinessSummaryRead,
    DomainDetailRead,
    ModelDetailRead,
    PipelineMonitorRead,
    WorkflowDetailRead,
    WorkflowRunDetailRead,
)
from app.services import platform_explorer_service as service

router = APIRouter(tags=["platform-explorer"])


def _required(value):
    if value is None:
        raise HTTPException(status_code=404, detail="platform hierarchy record not found")
    return value


@router.get("/businesses", response_model=list[BusinessSummaryRead])
def businesses(db: Session = Depends(get_db)):
    return service.list_businesses(db)


@router.get("/businesses/{workspace_id}", response_model=BusinessDetailRead)
def business(workspace_id: UUID, db: Session = Depends(get_db)):
    return _required(service.get_business(db, workspace_id))


@router.get(
    "/businesses/{workspace_id}/domains/{domain_id}", response_model=DomainDetailRead
)
def domain(workspace_id: UUID, domain_id: UUID, db: Session = Depends(get_db)):
    return _required(service.get_domain(db, workspace_id, domain_id))


@router.get(
    "/businesses/{workspace_id}/workflows/{workflow_id}",
    response_model=WorkflowDetailRead,
)
def workflow(workspace_id: UUID, workflow_id: UUID, db: Session = Depends(get_db)):
    return _required(service.get_workflow(db, workspace_id, workflow_id))


@router.get(
    "/businesses/{workspace_id}/workflow-runs/{run_id}",
    response_model=WorkflowRunDetailRead,
)
def workflow_run(workspace_id: UUID, run_id: UUID, db: Session = Depends(get_db)):
    return _required(service.get_workflow_run(db, workspace_id, run_id))


@router.get(
    "/businesses/{workspace_id}/models/{model_id}", response_model=ModelDetailRead
)
def model(workspace_id: UUID, model_id: UUID, db: Session = Depends(get_db)):
    return _required(service.get_model(db, workspace_id, model_id))


@router.get(
    "/pipeline-runs/{experiment_id}/monitor", response_model=PipelineMonitorRead
)
def pipeline_monitor(experiment_id: UUID, db: Session = Depends(get_db)):
    return _required(service.get_pipeline_monitor(db, experiment_id))
