"""Workspace-scoped Project records. A Project is not a Workflow."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Project, User, Workspace
from app.domain.errors import IdentityError, ProjectNotFoundError
from app.services.authorization_service import can_perform_ml_write, can_read_workspace
from app.services.workspace_service import slugify


def _unique_project_slug(
    db: Session, workspace_id: UUID, name: str, requested: str | None
) -> str:
    base = slugify(requested or name, fallback="project")
    candidate = base
    while db.scalar(
        select(Project.id).where(
            Project.workspace_id == workspace_id, Project.slug == candidate
        )
    ) is not None:
        suffix = uuid4().hex[:8]
        candidate = f"{base[: 64 - len(suffix) - 1]}-{suffix}"
    return candidate[:64]


def create_project(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    name: str,
    slug: str | None = None,
    description: str = "",
) -> Project:
    if db.get(Workspace, workspace_id) is None:
        raise IdentityError("workspace not found", status_code=404)
    if not can_perform_ml_write(db, actor, workspace_id):
        raise IdentityError(
            "creating a project requires an ML-write workspace role",
            status_code=403,
        )
    project = Project(
        workspace_id=workspace_id,
        name=name.strip(),
        slug=_unique_project_slug(db, workspace_id, name, slug),
        description=description,
        status="active",
        created_by=actor.id,
    )
    db.add(project)
    db.flush()
    return project


def list_projects(db: Session, *, actor: User, workspace_id: UUID) -> list[Project]:
    if db.get(Workspace, workspace_id) is None:
        raise IdentityError("workspace not found", status_code=404)
    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)
    return list(
        db.scalars(
            select(Project)
            .where(Project.workspace_id == workspace_id)
            .order_by(Project.created_at.desc(), Project.id)
        )
    )


def get_or_create_labs_project(
    db: Session, *, workspace_id: UUID, actor: User
) -> Project:
    """Stable Project for Client Labs uploads. Customers do not pick it in the UI."""

    from app.domain.data_plane import LABS_PROJECT_NAME, LABS_PROJECT_SLUG

    existing = db.scalar(
        select(Project).where(
            Project.workspace_id == workspace_id,
            Project.slug == LABS_PROJECT_SLUG,
        )
    )
    if existing is not None:
        return existing
    return create_project(
        db,
        actor=actor,
        workspace_id=workspace_id,
        name=LABS_PROJECT_NAME,
        slug=LABS_PROJECT_SLUG,
        description="Client Labs uploads and datasets.",
    )


def get_project(
    db: Session, *, actor: User, workspace_id: UUID, project_id: UUID
) -> Project:
    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise ProjectNotFoundError("project not found")
    return project
