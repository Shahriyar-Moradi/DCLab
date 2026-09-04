from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, request_workspace_id
from app.db.models import User
from app.db.session import get_db
from app.domain.client_lab import ClientLabProblem, ClientLabQuotaRead, ClientLabRunRead, ClientLabUploadRead
from app.domain.errors import (
    OpenLabFileError,
    TrialDatasetColumnsError,
    TrialDatasetTooLargeError,
    TrialQuotaExceededError,
    UnknownLabCategoryError,
    UnknownLabProblemError,
)
from app.services import client_lab_service, client_lab_upload_service
from app.services.authorization_service import AuthorizationError
from app.services.workspace_capability_service import (
    PREDICTION_DOWNLOAD,
    require_modern_business_capability,
)

router = APIRouter(prefix="/labs", tags=["client-labs"])


@router.get("/problems", response_model=list[ClientLabProblem])
def list_problems() -> list[ClientLabProblem]:
    return client_lab_service.list_problems()


@router.get("/problems/{use_case}/quota", response_model=ClientLabQuotaRead)
def get_quota(
    use_case: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ClientLabQuotaRead:
    try:
        return client_lab_service.get_quota(
            db,
            user,
            use_case,
            workspace_id=request_workspace_id(request),
        )
    except UnknownLabProblemError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs", response_model=ClientLabRunRead)
async def create_run(
    request: Request,
    use_case: str = Form(...),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ClientLabRunRead:
    uploaded_bytes = await file.read() if file is not None else None
    try:
        row = client_lab_service.run_trial(
            db,
            user=user,
            use_case_name=use_case,
            uploaded_bytes=uploaded_bytes,
            workspace_id=request_workspace_id(request),
        )
    except UnknownLabProblemError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TrialQuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (TrialDatasetTooLargeError, TrialDatasetColumnsError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ClientLabRunRead.model_validate(row)


@router.post("/uploads", response_model=ClientLabUploadRead)
async def create_upload(
    request: Request,
    category: str = Form(...),
    file: UploadFile = File(...),
    target_column: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ClientLabUploadRead:
    """Accept a file and return dataset_id, run_id, and status=queued immediately.

    Training is enqueued after this response is built, so the client never waits
    on the ML job to learn the run identity.
    """
    try:
        return client_lab_upload_service.save_upload(
            db,
            user=user,
            category=category,
            filename=file.filename or "upload",
            upload_stream=file.file,
            target_column=target_column,
            workspace_id=request_workspace_id(request),
        )
    except UnknownLabCategoryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OpenLabFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/uploads", response_model=list[ClientLabUploadRead])
def list_uploads(
    request: Request,
    category: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ClientLabUploadRead]:
    try:
        return client_lab_upload_service.list_uploads(
            db,
            user,
            category,
            workspace_id=request_workspace_id(request),
        )
    except UnknownLabCategoryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/uploads/{upload_id}", response_model=ClientLabUploadRead)
def get_upload(
    upload_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ClientLabUploadRead:
    row = client_lab_upload_service.get_upload(
        db,
        user,
        upload_id,
        workspace_id=request_workspace_id(request),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="file not found")
    return row


@router.get("/uploads/{upload_id}/predictions.csv")
def download_upload_predictions(
    upload_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    workspace_id = request_workspace_id(request)
    try:
        require_modern_business_capability(
            db, user, workspace_id, PREDICTION_DOWNLOAD
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    payload = client_lab_upload_service.predictions_download(
        db,
        user,
        upload_id,
        workspace_id=workspace_id,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="predictions are not ready")
    filename, body = payload
    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in filename)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/runs", response_model=list[ClientLabRunRead])
def list_runs(
    request: Request,
    use_case: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ClientLabRunRead]:
    rows = client_lab_service.list_runs(
        db,
        user,
        use_case,
        workspace_id=request_workspace_id(request),
    )
    return [ClientLabRunRead.model_validate(row) for row in rows]


@router.get("/runs/{run_id}", response_model=ClientLabRunRead)
def get_run(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ClientLabRunRead:
    row = client_lab_service.get_run(
        db,
        user,
        run_id,
        workspace_id=request_workspace_id(request),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="trial run not found")
    return ClientLabRunRead.model_validate(row)
