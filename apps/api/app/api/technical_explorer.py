"""Platform and workspace technical explorer routes over the same query services."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_platform_read
from app.db.models import User
from app.db.session import get_db
from app.domain.errors import IdentityError
from app.domain.technical_explorer import (
    DatasetListItem,
    ModelCandidateDetailRead,
    ModelVersionDetailRead,
    ModelVersionListItem,
    PipelineRunDetailRead,
    PipelineRunListItem,
    ProjectDetailRead,
    ProjectListItem,
    WorkflowDetailRead,
    WorkflowListItem,
    WorkspaceListItem,
)
from app.services.technical_explorer_service import (
    list_datasets,
    list_workspaces,
    model_candidate_detail_query,
    model_version_detail_query,
    pipeline_run_detail_query,
    project_detail_query,
    workflow_detail_query,
)

workspace_router = APIRouter(prefix="/workspaces", tags=["technical-explorer"])
admin_router = APIRouter(prefix="/explorer", tags=["technical-explorer"])


def _identity_http(exc: IdentityError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _required(value):
    if value is None:
        raise HTTPException(status_code=404, detail="not found")
    return value


def _limit(limit: int | None) -> int | None:
    return limit


@workspace_router.get(
    "/{workspace_id}/explorer/projects",
    response_model=list[ProjectListItem],
)
def workspace_projects(
    workspace_id: UUID,
    limit: int | None = Query(None, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectListItem]:
    try:
        return project_detail_query.list(
            db, user, workspace_id=workspace_id, limit=_limit(limit)
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@workspace_router.get(
    "/{workspace_id}/explorer/projects/{project_id}",
    response_model=ProjectDetailRead,
)
def workspace_project(
    workspace_id: UUID,
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectDetailRead:
    try:
        return _required(
            project_detail_query.get(
                db, user, project_id, workspace_id=workspace_id
            )
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@workspace_router.get(
    "/{workspace_id}/explorer/workflows",
    response_model=list[WorkflowListItem],
)
def workspace_workflows(
    workspace_id: UUID,
    limit: int | None = Query(None, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WorkflowListItem]:
    try:
        return workflow_detail_query.list(
            db, user, workspace_id=workspace_id, limit=_limit(limit)
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@workspace_router.get(
    "/{workspace_id}/explorer/workflows/{workflow_id}",
    response_model=WorkflowDetailRead,
)
def workspace_workflow(
    workspace_id: UUID,
    workflow_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkflowDetailRead:
    try:
        return _required(
            workflow_detail_query.get(
                db, user, workflow_id, workspace_id=workspace_id
            )
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@workspace_router.get(
    "/{workspace_id}/explorer/pipeline-runs",
    response_model=list[PipelineRunListItem],
)
def workspace_pipeline_runs(
    workspace_id: UUID,
    limit: int | None = Query(None, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PipelineRunListItem]:
    try:
        return pipeline_run_detail_query.list(
            db, user, workspace_id=workspace_id, limit=_limit(limit)
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@workspace_router.get(
    "/{workspace_id}/explorer/pipeline-runs/{pipeline_run_id}",
    response_model=PipelineRunDetailRead,
)
def workspace_pipeline_run(
    workspace_id: UUID,
    pipeline_run_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PipelineRunDetailRead:
    try:
        return _required(
            pipeline_run_detail_query.get(
                db, user, pipeline_run_id, workspace_id=workspace_id
            )
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@workspace_router.get(
    "/{workspace_id}/explorer/candidates/{candidate_id}",
    response_model=ModelCandidateDetailRead,
)
def workspace_candidate(
    workspace_id: UUID,
    candidate_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ModelCandidateDetailRead:
    try:
        return _required(
            model_candidate_detail_query.get(
                db, user, candidate_id, workspace_id=workspace_id
            )
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@workspace_router.get(
    "/{workspace_id}/explorer/model-versions",
    response_model=list[ModelVersionListItem],
)
def workspace_model_versions(
    workspace_id: UUID,
    limit: int | None = Query(None, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ModelVersionListItem]:
    try:
        return model_version_detail_query.list(
            db, user, workspace_id=workspace_id, limit=_limit(limit)
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@workspace_router.get(
    "/{workspace_id}/explorer/model-versions/{model_version_id}",
    response_model=ModelVersionDetailRead,
)
def workspace_model_version(
    workspace_id: UUID,
    model_version_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ModelVersionDetailRead:
    try:
        return _required(
            model_version_detail_query.get(
                db, user, model_version_id, workspace_id=workspace_id
            )
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@workspace_router.get(
    "/{workspace_id}/explorer/datasets",
    response_model=list[DatasetListItem],
)
def workspace_datasets(
    workspace_id: UUID,
    limit: int | None = Query(None, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DatasetListItem]:
    try:
        return list_datasets(db, user, workspace_id=workspace_id, limit=_limit(limit))
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@admin_router.get("/workspaces", response_model=list[WorkspaceListItem])
def admin_workspaces(
    limit: int | None = Query(None, ge=1, le=200),
    user: User = Depends(require_platform_read),
    db: Session = Depends(get_db),
) -> list[WorkspaceListItem]:
    try:
        return list_workspaces(db, user, limit=_limit(limit))
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@admin_router.get("/projects", response_model=list[ProjectListItem])
def admin_projects(
    workspace_id: UUID | None = Query(None),
    limit: int | None = Query(None, ge=1, le=200),
    user: User = Depends(require_platform_read),
    db: Session = Depends(get_db),
) -> list[ProjectListItem]:
    try:
        return project_detail_query.list(
            db, user, workspace_id=workspace_id, limit=_limit(limit)
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@admin_router.get("/projects/{project_id}", response_model=ProjectDetailRead)
def admin_project(
    project_id: UUID,
    user: User = Depends(require_platform_read),
    db: Session = Depends(get_db),
) -> ProjectDetailRead:
    try:
        return _required(
            project_detail_query.get(db, user, project_id, workspace_id=None)
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@admin_router.get("/workflows", response_model=list[WorkflowListItem])
def admin_workflows(
    workspace_id: UUID | None = Query(None),
    limit: int | None = Query(None, ge=1, le=200),
    user: User = Depends(require_platform_read),
    db: Session = Depends(get_db),
) -> list[WorkflowListItem]:
    try:
        return workflow_detail_query.list(
            db, user, workspace_id=workspace_id, limit=_limit(limit)
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@admin_router.get("/workflows/{workflow_id}", response_model=WorkflowDetailRead)
def admin_workflow(
    workflow_id: UUID,
    user: User = Depends(require_platform_read),
    db: Session = Depends(get_db),
) -> WorkflowDetailRead:
    try:
        return _required(
            workflow_detail_query.get(db, user, workflow_id, workspace_id=None)
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@admin_router.get("/pipeline-runs", response_model=list[PipelineRunListItem])
def admin_pipeline_runs(
    workspace_id: UUID | None = Query(None),
    limit: int | None = Query(None, ge=1, le=200),
    user: User = Depends(require_platform_read),
    db: Session = Depends(get_db),
) -> list[PipelineRunListItem]:
    try:
        return pipeline_run_detail_query.list(
            db, user, workspace_id=workspace_id, limit=_limit(limit)
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@admin_router.get(
    "/pipeline-runs/{pipeline_run_id}",
    response_model=PipelineRunDetailRead,
)
def admin_pipeline_run(
    pipeline_run_id: UUID,
    user: User = Depends(require_platform_read),
    db: Session = Depends(get_db),
) -> PipelineRunDetailRead:
    try:
        return _required(
            pipeline_run_detail_query.get(
                db, user, pipeline_run_id, workspace_id=None
            )
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@admin_router.get(
    "/candidates/{candidate_id}",
    response_model=ModelCandidateDetailRead,
)
def admin_candidate(
    candidate_id: UUID,
    user: User = Depends(require_platform_read),
    db: Session = Depends(get_db),
) -> ModelCandidateDetailRead:
    try:
        return _required(
            model_candidate_detail_query.get(
                db, user, candidate_id, workspace_id=None
            )
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@admin_router.get("/model-versions", response_model=list[ModelVersionListItem])
def admin_model_versions(
    workspace_id: UUID | None = Query(None),
    limit: int | None = Query(None, ge=1, le=200),
    user: User = Depends(require_platform_read),
    db: Session = Depends(get_db),
) -> list[ModelVersionListItem]:
    try:
        return model_version_detail_query.list(
            db, user, workspace_id=workspace_id, limit=_limit(limit)
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@admin_router.get(
    "/model-versions/{model_version_id}",
    response_model=ModelVersionDetailRead,
)
def admin_model_version(
    model_version_id: UUID,
    user: User = Depends(require_platform_read),
    db: Session = Depends(get_db),
) -> ModelVersionDetailRead:
    try:
        return _required(
            model_version_detail_query.get(
                db, user, model_version_id, workspace_id=None
            )
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc


@admin_router.get("/datasets", response_model=list[DatasetListItem])
def admin_datasets(
    workspace_id: UUID | None = Query(None),
    limit: int | None = Query(None, ge=1, le=200),
    user: User = Depends(require_platform_read),
    db: Session = Depends(get_db),
) -> list[DatasetListItem]:
    try:
        return list_datasets(db, user, workspace_id=workspace_id, limit=_limit(limit))
    except IdentityError as exc:
        raise _identity_http(exc) from exc
