"""Workspace-scoped Business administration over shared explorer services."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_business_administration
from app.db.models import ClientLabUpload, User
from app.db.session import get_db
from app.domain.ml_verification import VerificationAttemptResponse
from app.domain.platform_explorer import (
    BusinessWorkspaceDetailRead,
    BusinessWorkspaceSummaryRead,
    BusinessModelDetailRead,
    BusinessWorkflowRunDetailRead,
    DomainDetailRead,
    PipelineMonitorRead,
    WorkflowDetailRead,
)
from app.services import business_explorer_service, client_lab_upload_service
from app.services.authorization_service import (
    AuthorizationError,
    can_read_workspace,
    can_write_workspace,
)
from app.services.pipeline_audit_service import (
    RunNotFoundError,
    RunReportNotReadyError,
    request_pipeline_verification,
)
from app.services.workspace_capability_service import (
    DEEP_AUDIT,
    OPENAI_PIPELINE_AUDIT,
    PIPELINE_MONITOR,
    PREDICTION_DOWNLOAD,
    require_capability,
)

router = APIRouter(
    prefix="/business",
    tags=["business-administration"],
    dependencies=[Depends(require_business_administration)],
)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="business record not found")


def _required(value):
    if value is None:
        raise _not_found()
    return value


def _require_read(db: Session, user: User, workspace_id: UUID) -> None:
    if not can_read_workspace(db, user, workspace_id):
        raise _not_found()


def _require_write(db: Session, user: User, workspace_id: UUID) -> None:
    _require_read(db, user, workspace_id)
    if not can_write_workspace(db, user, workspace_id):
        raise HTTPException(
            status_code=403,
            detail="workspace write access requires business_admin or dclab_admin",
        )


def _capability(db: Session, user: User, workspace_id: UUID, key: str) -> None:
    try:
        require_capability(db, user, workspace_id, key)
    except AuthorizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/workspaces", response_model=list[BusinessWorkspaceSummaryRead])
def workspaces(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return business_explorer_service.list_workspaces(db, user)


@router.get(
    "/workspaces/{workspace_id}", response_model=BusinessWorkspaceDetailRead
)
def business(
    workspace_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _required(business_explorer_service.get_business(db, user, workspace_id))


@router.get(
    "/workspaces/{workspace_id}/domains/{domain_id}",
    response_model=DomainDetailRead,
)
def domain(
    workspace_id: UUID,
    domain_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _required(
        business_explorer_service.get_domain(db, user, workspace_id, domain_id)
    )


@router.get(
    "/workspaces/{workspace_id}/workflows/{workflow_id}",
    response_model=WorkflowDetailRead,
)
def workflow(
    workspace_id: UUID,
    workflow_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _required(
        business_explorer_service.get_workflow(db, user, workspace_id, workflow_id)
    )


@router.get(
    "/workspaces/{workspace_id}/workflow-runs/{run_id}",
    response_model=BusinessWorkflowRunDetailRead,
)
def workflow_run(
    workspace_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _required(
        business_explorer_service.get_workflow_run(db, user, workspace_id, run_id)
    )


@router.get(
    "/workspaces/{workspace_id}/models/{model_id}", response_model=BusinessModelDetailRead
)
def model(
    workspace_id: UUID,
    model_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _required(
        business_explorer_service.get_model(db, user, workspace_id, model_id)
    )


@router.get(
    "/workspaces/{workspace_id}/pipeline-runs/{experiment_id}/monitor",
    response_model=PipelineMonitorRead,
)
def pipeline_monitor(
    workspace_id: UUID,
    experiment_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_read(db, user, workspace_id)
    value = business_explorer_service.get_pipeline_monitor(
        db, user, workspace_id, experiment_id
    )
    if value is None:
        raise _not_found()
    _capability(db, user, workspace_id, PIPELINE_MONITOR)
    return value


@router.get(
    "/workspaces/{workspace_id}/client-uploads/{upload_id}/predictions.csv"
)
def download_predictions(
    workspace_id: UUID,
    upload_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    _require_read(db, user, workspace_id)
    exists = db.scalar(
        select(ClientLabUpload.id).where(
            ClientLabUpload.workspace_id == workspace_id,
            or_(ClientLabUpload.id == upload_id, ClientLabUpload.run_id == upload_id),
        )
    )
    if exists is None:
        raise _not_found()
    _capability(db, user, workspace_id, PREDICTION_DOWNLOAD)
    payload = client_lab_upload_service.predictions_download(
        db, user, upload_id, workspace_id=workspace_id
    )
    if payload is None:
        raise _not_found()
    filename, body = payload
    safe_name = "".join(
        ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in filename
    )
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.post(
    "/workspaces/{workspace_id}/lab-runs/{run_id}/verification/deep",
    response_model=VerificationAttemptResponse,
)
def deep_audit(
    workspace_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_write(db, user, workspace_id)
    upload = db.scalar(
        select(ClientLabUpload).where(
            ClientLabUpload.workspace_id == workspace_id,
            or_(ClientLabUpload.id == run_id, ClientLabUpload.run_id == run_id),
        )
    )
    if upload is None:
        raise _not_found()
    _capability(db, user, workspace_id, OPENAI_PIPELINE_AUDIT)
    _capability(db, user, workspace_id, DEEP_AUDIT)
    try:
        attempt = request_pipeline_verification(db, upload.id, deep=True)
    except (RunNotFoundError, RunReportNotReadyError) as exc:
        raise HTTPException(status_code=409, detail="ML run report is not ready") from exc
    return VerificationAttemptResponse.model_validate(attempt)
