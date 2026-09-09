"""Canonical workspace, project, and problem-spec routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.domain.errors import IdentityError, ProblemSpecNotFoundError, ProjectNotFoundError
from app.domain.workspace_identity import (
    ProblemSpecCreateRequest,
    ProblemSpecRead,
    ProjectCreateRequest,
    ProjectRead,
    WorkspaceCreateRequest,
    WorkspaceMemberCreateRequest,
    WorkspaceMembershipRead,
    WorkspaceRead,
)
from app.services import problem_spec_service, project_service, workspace_service

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _identity_http(exc: IdentityError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/personal", response_model=WorkspaceRead)
def create_personal_workspace_endpoint(
    payload: WorkspaceCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceRead:
    try:
        workspace = workspace_service.create_personal_workspace(
            db, owner=user, name=payload.name, slug=payload.slug
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    db.commit()
    db.refresh(workspace)
    return workspace_service.workspace_to_read(db, workspace)


@router.post("/business", response_model=WorkspaceRead)
def create_business_workspace_endpoint(
    payload: WorkspaceCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceRead:
    try:
        workspace = workspace_service.create_business_workspace(
            db, owner=user, name=payload.name, slug=payload.slug
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    db.commit()
    db.refresh(workspace)
    return workspace_service.workspace_to_read(db, workspace)


@router.post("/{workspace_id}/members", response_model=WorkspaceMembershipRead)
def add_workspace_member_endpoint(
    workspace_id: UUID,
    payload: WorkspaceMemberCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceMembershipRead:
    try:
        membership = workspace_service.add_workspace_member(
            db,
            actor=user,
            workspace_id=workspace_id,
            email=payload.email,
            password=payload.password,
            role=payload.role,
            full_name=payload.full_name,
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    db.commit()
    db.refresh(membership)
    return WorkspaceMembershipRead.model_validate(membership)


@router.get("/{workspace_id}/projects", response_model=list[ProjectRead])
def list_projects_endpoint(
    workspace_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectRead]:
    try:
        projects = project_service.list_projects(db, actor=user, workspace_id=workspace_id)
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return [ProjectRead.model_validate(row) for row in projects]


@router.post("/{workspace_id}/projects", response_model=ProjectRead)
def create_project_endpoint(
    workspace_id: UUID,
    payload: ProjectCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectRead:
    try:
        project = project_service.create_project(
            db,
            actor=user,
            workspace_id=workspace_id,
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    db.commit()
    db.refresh(project)
    return ProjectRead.model_validate(project)


@router.get("/{workspace_id}/projects/{project_id}", response_model=ProjectRead)
def get_project_endpoint(
    workspace_id: UUID,
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectRead:
    try:
        project = project_service.get_project(
            db, actor=user, workspace_id=workspace_id, project_id=project_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectRead.model_validate(project)


@router.get(
    "/{workspace_id}/projects/{project_id}/problem-specs",
    response_model=list[ProblemSpecRead],
)
def list_problem_specs_endpoint(
    workspace_id: UUID,
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProblemSpecRead]:
    try:
        specs = problem_spec_service.list_problem_specs(
            db, actor=user, workspace_id=workspace_id, project_id=project_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [ProblemSpecRead.model_validate(row) for row in specs]


@router.post(
    "/{workspace_id}/projects/{project_id}/problem-specs",
    response_model=ProblemSpecRead,
)
def create_problem_spec_endpoint(
    workspace_id: UUID,
    project_id: UUID,
    payload: ProblemSpecCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProblemSpecRead:
    try:
        spec = problem_spec_service.create_problem_spec(
            db,
            actor=user,
            workspace_id=workspace_id,
            project_id=project_id,
            task_type=payload.task_type,
            business_objective=payload.business_objective,
            target_column=payload.target_column,
            prediction_unit=payload.prediction_unit,
            prediction_time_column=payload.prediction_time_column,
            prediction_horizon=payload.prediction_horizon,
            primary_metric=payload.primary_metric,
            constraints=payload.constraints,
            success_criteria=payload.success_criteria,
            status=payload.status,
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    db.refresh(spec)
    return ProblemSpecRead.model_validate(spec)


@router.get(
    "/{workspace_id}/projects/{project_id}/problem-specs/{spec_id}",
    response_model=ProblemSpecRead,
)
def get_problem_spec_endpoint(
    workspace_id: UUID,
    project_id: UUID,
    spec_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProblemSpecRead:
    try:
        spec = problem_spec_service.get_problem_spec(
            db,
            actor=user,
            workspace_id=workspace_id,
            project_id=project_id,
            spec_id=spec_id,
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    except ProblemSpecNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProblemSpecRead.model_validate(spec)
