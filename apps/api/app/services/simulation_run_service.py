"""Workspace-scoped SimulationRun persistence. Unowned rows are archive only."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Project, SimulationRun
from app.domain.errors import IdentityError, SimulationRunNotFoundError


def persist_simulation_run(
    db: Session,
    *,
    workspace_id: UUID,
    payload: dict,
    project_id: UUID | None = None,
) -> SimulationRun:
    if project_id is not None:
        project = db.get(Project, project_id)
        if project is None or project.workspace_id != workspace_id:
            raise IdentityError("project not found", status_code=404)
    row = SimulationRun(
        workspace_id=workspace_id,
        project_id=project_id,
        use_case=str(payload["use_case"]),
        model_version=str(payload["model_version"]),
        policy_version=str(payload["policy_version"]),
        fusion=str(payload["fusion"]),
        payload=payload,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_workspace_simulation_runs(db: Session, *, workspace_id: UUID) -> list[SimulationRun]:
    return list(
        db.scalars(
            select(SimulationRun)
            .where(SimulationRun.workspace_id == workspace_id)
            .order_by(SimulationRun.created_at.desc())
        )
    )


def get_workspace_simulation_run(
    db: Session, *, workspace_id: UUID, run_id: UUID
) -> SimulationRun:
    row = db.get(SimulationRun, run_id)
    if row is None or row.workspace_id != workspace_id:
        raise SimulationRunNotFoundError("simulation run not found")
    return row
