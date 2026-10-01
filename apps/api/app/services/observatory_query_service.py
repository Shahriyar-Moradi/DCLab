"""Tenant-scoped read models for Pipeline Observatory APIs."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Experiment, LlmInvocation, MlRunEvent, WorkflowRun
from app.services.observability_service import pipeline_summary


def get_pipeline(
    db: Session, experiment_id: UUID, *, workspace_id: UUID
) -> Experiment | None:
    stmt = select(Experiment).where(
        Experiment.id == experiment_id,
        Experiment.workflow_run_id.is_not(None),
    )
    stmt = stmt.where(Experiment.workspace_id == workspace_id)
    return db.scalar(stmt)


def get_pipeline_summary(
    db: Session, experiment_id: UUID, *, workspace_id: UUID
) -> dict | None:
    pipeline = get_pipeline(db, experiment_id, workspace_id=workspace_id)
    return pipeline_summary(db, pipeline) if pipeline is not None else None


def list_pipeline_events(
    db: Session,
    experiment_id: UUID,
    *,
    workspace_id: UUID,
    after_sequence: int = 0,
    limit: int | None = None,
) -> list[MlRunEvent] | None:
    if get_pipeline(db, experiment_id, workspace_id=workspace_id) is None:
        return None
    return list_run_events(
        db, experiment_id, workspace_id=workspace_id,
        after_sequence=after_sequence, limit=limit
    )


def list_run_events(
    db: Session,
    experiment_id: UUID,
    *,
    workspace_id: UUID,
    after_sequence: int = 0,
    limit: int | None = None,
) -> list[MlRunEvent]:
    stmt = (
        select(MlRunEvent)
        .where(
            MlRunEvent.experiment_id == experiment_id,
            MlRunEvent.workspace_id == workspace_id,
            MlRunEvent.sequence > max(0, after_sequence),
        )
        .order_by(MlRunEvent.sequence)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt))


def list_pipeline_llm_invocations(
    db: Session,
    experiment_id: UUID,
    *,
    workspace_id: UUID,
    include_semantic: bool = True,
    include_audit: bool = True,
) -> list[LlmInvocation] | None:
    if get_pipeline(db, experiment_id, workspace_id=workspace_id) is None:
        return None
    stmt = select(LlmInvocation).where(
        LlmInvocation.experiment_id == experiment_id,
        LlmInvocation.workspace_id == workspace_id,
    )
    if not include_semantic:
        stmt = stmt.where(~LlmInvocation.purpose.like("semantic_%"))
    if not include_audit:
        stmt = stmt.where(~LlmInvocation.purpose.like("pipeline_audit_%"))
    return list(
        db.scalars(
            stmt.order_by(LlmInvocation.started_at, LlmInvocation.id)
        )
    )


def get_llm_invocation(
    db: Session,
    invocation_id: UUID,
    *,
    workspace_id: UUID,
    allowed_purposes: frozenset[str] | None = None,
) -> LlmInvocation | None:
    stmt = select(LlmInvocation).where(
        LlmInvocation.id == invocation_id,
        LlmInvocation.workspace_id == workspace_id,
    )
    if allowed_purposes is not None:
        stmt = stmt.where(LlmInvocation.purpose.in_(allowed_purposes))
    return db.scalar(stmt)


def list_workflow_run_pipelines(
    db: Session, workflow_run_id: UUID, *, workspace_id: UUID
) -> list[Experiment] | None:
    run_stmt = select(WorkflowRun).where(
        WorkflowRun.id == workflow_run_id,
        WorkflowRun.workspace_id == workspace_id,
    )
    if db.scalar(run_stmt) is None:
        return None
    return list(
        db.scalars(
            select(Experiment)
            .where(
                Experiment.workflow_run_id == workflow_run_id,
                Experiment.workspace_id == workspace_id,
            )
            .order_by(Experiment.pipeline_index, Experiment.created_at)
        )
    )
