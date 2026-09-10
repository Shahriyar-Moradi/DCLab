"""Stable /v1 application boundary.

Transport-neutral HTTP resources over existing query and application services.
Legacy /app, /admin, /business, and /workspaces routes remain adapters.
This module does not add MCP, CLI, WebSocket, or SSE.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_user,
    parse_requested_workspace_id,
    request_workspace_id,
    require_workspace_ml_execution,
    require_workspace_read,
)
from app.db.models import User
from app.db.session import get_db
from app.domain.application_api import (
    EventPage,
    ExecutionRequestCreate,
    ExecutionRequestRead,
    ExecutionTargetConfirmation,
    PrincipalRead,
    VisualizationRead,
)
from app.domain.errors import (
    ExecutionNotWaitingError,
    IdentityError,
    ProjectNotFoundError,
    TargetIntentConflictError,
    TargetNotInDatasetError,
)
from app.domain.execution_requests import EXECUTION_OPERATIONS, SOURCE_API
from app.domain.model_build import PipelineModelBuildRead
from app.domain.observability import MlRunEventRead
from app.domain.reproducibility import ArtifactRead
from app.domain.technical_explorer import DatasetListItem
from app.domain.workspace_identity import ProjectRead, WorkspaceRead
from app.services.artifact_service import list_artifacts
from app.services.execution_request_service import (
    ExecutionRequestSpecError,
    confirm_execution_target,
    create_execution_request,
    get_execution_request,
)
from app.services.model_build_reproduction_service import load_model_build_experiment
from app.services.model_build_service import get_pipeline_model_build
from app.services.observatory_query_service import list_run_events
from app.services.project_service import get_project, list_projects
from app.services.technical_explorer_service import get_dataset, list_datasets
from app.services.visualization_service import list_visualizations_for_pipeline_run
from app.services.workspace_service import list_workspaces_for_actor
from app.services.workspace_selection_service import principal_read

router = APIRouter(prefix="/v1", tags=["v1"])

_EVENT_PAGE_MAX = 200
_EVENT_PAGE_DEFAULT = 100


def _identity_http(exc: IdentityError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


def _cursor_sequence(cursor: str | None) -> int:
    if cursor is None or cursor.strip() == "":
        return 0
    try:
        value = int(cursor.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="cursor must be a sequence integer"
        ) from exc
    if value < 0:
        raise HTTPException(status_code=400, detail="cursor must be a sequence integer")
    return value


def _principal(db, user, request) -> PrincipalRead:
    parse_requested_workspace_id(request)
    return principal_read(db, user, request)


@router.get("/me", response_model=PrincipalRead)
def read_principal(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PrincipalRead:
    return _principal(db, user, request)


@router.get("/workspaces", response_model=list[WorkspaceRead])
def read_workspaces(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WorkspaceRead]:
    return list_workspaces_for_actor(db, user)


@router.get("/projects", response_model=list[ProjectRead])
def read_projects(
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> list[ProjectRead]:
    workspace_id = request_workspace_id(request)
    try:
        rows = list_projects(db, actor=user, workspace_id=workspace_id)
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return [ProjectRead.model_validate(row) for row in rows]


@router.get("/projects/{project_id}", response_model=ProjectRead)
def read_project(
    project_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> ProjectRead:
    workspace_id = request_workspace_id(request)
    try:
        project = get_project(
            db, actor=user, workspace_id=workspace_id, project_id=project_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectRead.model_validate(project)


@router.get("/datasets", response_model=list[DatasetListItem])
def read_datasets(
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
    limit: int | None = Query(None, ge=1, le=200),
) -> list[DatasetListItem]:
    workspace_id = request_workspace_id(request)
    try:
        return list_datasets(db, user, workspace_id=workspace_id, limit=limit)
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@router.get("/datasets/{dataset_id}", response_model=DatasetListItem)
def read_dataset(
    dataset_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> DatasetListItem:
    workspace_id = request_workspace_id(request)
    try:
        row = get_dataset(db, user, dataset_id, workspace_id=workspace_id)
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    if row is None:
        raise _not_found()
    return row


@router.post(
    "/execution-requests",
    response_model=ExecutionRequestRead,
    status_code=202,
)
def submit_execution_request(
    payload: ExecutionRequestCreate,
    request: Request,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
) -> ExecutionRequestRead:
    workspace_id = request_workspace_id(request)
    if payload.operation not in EXECUTION_OPERATIONS:
        raise HTTPException(status_code=400, detail="unsupported operation")
    if payload.project_id is not None:
        try:
            get_project(
                db,
                actor=user,
                workspace_id=workspace_id,
                project_id=payload.project_id,
            )
        except IdentityError as exc:
            raise _identity_http(exc) from exc
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    if payload.parent_request_id is not None:
        try:
            get_execution_request(
                db,
                actor=user,
                workspace_id=workspace_id,
                request_id=payload.parent_request_id,
            )
        except IdentityError as exc:
            raise _identity_http(exc) from exc
    try:
        row = create_execution_request(
            db,
            workspace_id=workspace_id,
            operation=payload.operation,
            source_surface=SOURCE_API,
            project_id=payload.project_id,
            requested_by_user_id=user.id,
            idempotency_key=payload.idempotency_key,
            external_request_id=payload.external_request_id,
            parent_request_id=payload.parent_request_id,
            request_spec=payload.request_spec,
        )
    except ExecutionRequestSpecError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TargetNotInDatasetError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_detail()) from exc
    except TargetIntentConflictError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_detail()) from exc
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    db.commit()
    db.refresh(row)
    return ExecutionRequestRead.model_validate(row)


@router.post(
    "/execution-requests/{request_id}/target-confirmation",
    response_model=ExecutionRequestRead,
)
def confirm_execution_request_target(
    request_id: UUID,
    payload: ExecutionTargetConfirmation,
    request: Request,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
) -> ExecutionRequestRead:
    workspace_id = request_workspace_id(request)
    try:
        row = confirm_execution_target(
            db,
            actor=user,
            workspace_id=workspace_id,
            request_id=request_id,
            target_column=payload.target_column,
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    except ExecutionNotWaitingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_detail()) from exc
    except TargetNotInDatasetError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_detail()) from exc
    except TargetIntentConflictError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_detail()) from exc
    except ExecutionRequestSpecError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ExecutionRequestRead.model_validate(row)


@router.get("/execution-requests/{request_id}", response_model=ExecutionRequestRead)
def read_execution_request(
    request_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> ExecutionRequestRead:
    workspace_id = request_workspace_id(request)
    try:
        row = get_execution_request(
            db, actor=user, workspace_id=workspace_id, request_id=request_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return ExecutionRequestRead.model_validate(row)


def _require_model_build(
    db: Session, user: User, workspace_id: UUID, pipeline_run_id: UUID
):
    try:
        experiment = load_model_build_experiment(
            db, user, workspace_id, pipeline_run_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    if experiment is None:
        raise _not_found()
    return experiment


@router.get(
    "/model-builds/{pipeline_run_id}",
    response_model=PipelineModelBuildRead,
)
def read_model_build(
    pipeline_run_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> PipelineModelBuildRead:
    workspace_id = request_workspace_id(request)
    try:
        body = get_pipeline_model_build(db, user, workspace_id, pipeline_run_id)
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    if body is None:
        raise _not_found()
    return body


@router.get(
    "/model-builds/{pipeline_run_id}/events",
    response_model=EventPage,
)
def read_model_build_events(
    pipeline_run_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
    cursor: str | None = Query(None),
    limit: int = Query(_EVENT_PAGE_DEFAULT, ge=1, le=_EVENT_PAGE_MAX),
) -> EventPage:
    workspace_id = request_workspace_id(request)
    _require_model_build(db, user, workspace_id, pipeline_run_id)
    after_sequence = _cursor_sequence(cursor)
    rows = list_run_events(
        db,
        pipeline_run_id,
        after_sequence=after_sequence,
        limit=limit + 1,
    )
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        next_cursor = str(page[-1].sequence)
    return EventPage(
        items=[MlRunEventRead.model_validate(row) for row in page],
        next_cursor=next_cursor,
    )


@router.get(
    "/model-builds/{pipeline_run_id}/visualizations",
    response_model=list[VisualizationRead],
)
def read_model_build_visualizations(
    pipeline_run_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> list[VisualizationRead]:
    workspace_id = request_workspace_id(request)
    _require_model_build(db, user, workspace_id, pipeline_run_id)
    try:
        rows = list_visualizations_for_pipeline_run(
            db, workspace_id=workspace_id, pipeline_run_id=pipeline_run_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return [VisualizationRead.model_validate(row) for row in rows]


@router.get(
    "/model-builds/{pipeline_run_id}/artifacts",
    response_model=list[ArtifactRead],
)
def read_model_build_artifacts(
    pipeline_run_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> list[ArtifactRead]:
    workspace_id = request_workspace_id(request)
    _require_model_build(db, user, workspace_id, pipeline_run_id)
    rows = list_artifacts(
        db, workspace_id=workspace_id, pipeline_run_id=pipeline_run_id
    )
    return [ArtifactRead.model_validate(row) for row in rows]
