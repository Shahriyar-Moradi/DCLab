"""Tenant-scoped read models for Pipeline Observatory APIs."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Experiment, LlmInvocation, MlRunEvent, WorkflowRun
from app.services.observability_service import pipeline_summary


def get_pipeline(
    db: Session, experiment_id: UUID, *, workspace_id: UUID | None
) -> Experiment | None:
    stmt = select(Experiment).where(
        Experiment.id == experiment_id,
        Experiment.workflow_run_id.is_not(None),
    )
    if workspace_id is not None:
        stmt = stmt.where(Experiment.workspace_id == workspace_id)
    return db.scalar(stmt)


def get_pipeline_summary(
    db: Session, experiment_id: UUID, *, workspace_id: UUID | None
) -> dict | None:
    pipeline = get_pipeline(db, experiment_id, workspace_id=workspace_id)
    return pipeline_summary(db, pipeline) if pipeline is not None else None


def list_pipeline_events(
    db: Session,
    experiment_id: UUID,
    *,
    workspace_id: UUID | None,
    after_sequence: int = 0,
) -> list[MlRunEvent] | None:
    if get_pipeline(db, experiment_id, workspace_id=workspace_id) is None:
        return None
    return list(
        db.scalars(
            select(MlRunEvent)
            .where(
                MlRunEvent.experiment_id == experiment_id,
                MlRunEvent.sequence > max(0, after_sequence),
            )
            .order_by(MlRunEvent.sequence)
        )
    )


def list_pipeline_llm_invocations(
    db: Session, experiment_id: UUID, *, workspace_id: UUID | None
) -> list[LlmInvocation] | None:
    if get_pipeline(db, experiment_id, workspace_id=workspace_id) is None:
        return None
    return list(
        db.scalars(
            select(LlmInvocation)
            .where(LlmInvocation.experiment_id == experiment_id)
            .order_by(LlmInvocation.started_at, LlmInvocation.id)
        )
    )


def get_llm_invocation(
    db: Session, invocation_id: UUID, *, workspace_id: UUID | None
) -> LlmInvocation | None:
    stmt = select(LlmInvocation).where(LlmInvocation.id == invocation_id)
    if workspace_id is not None:
        stmt = stmt.where(LlmInvocation.workspace_id == workspace_id)
    return db.scalar(stmt)


def list_workflow_run_pipelines(
    db: Session, workflow_run_id: UUID, *, workspace_id: UUID | None
) -> list[Experiment] | None:
    run_stmt = select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
    if workspace_id is not None:
        run_stmt = run_stmt.where(WorkflowRun.workspace_id == workspace_id)
    if db.scalar(run_stmt) is None:
        return None
    return list(
        db.scalars(
            select(Experiment)
            .where(Experiment.workflow_run_id == workflow_run_id)
            .order_by(Experiment.pipeline_index, Experiment.created_at)
        )
    )
