"""WorkflowVersion, Pipeline, PipelineVersion, and PipelineStageRun services.

Experiment remains the physical PipelineRun. These helpers attach canonical IDs
without replacing the training engine.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Experiment,
    MlWorkflow,
    Pipeline,
    PipelineStageRun,
    PipelineVersion,
    Project,
    User,
    WorkflowVersion,
)
from app.domain.execution_plane import PIPELINE_STATUSES
from app.domain.errors import (
    PipelineDefinitionNotFoundError,
    PipelineVersionNotFoundError,
    WorkflowVersionNotFoundError,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:128] or "asset"


def content_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _lineage_error(message: str):
    from app.services.lineage_service import LineageError

    return LineageError(message)


def _require_project(db: Session, workspace_id: UUID, project_id: UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise _lineage_error("project does not belong to this workspace")
    return project


def workflow_definition_snapshot(workflow: MlWorkflow) -> dict[str, Any]:
    return {
        "business_objective": workflow.business_objective,
        "config": dict(workflow.config or {}),
        "description": workflow.description,
        "name": workflow.name,
        "slug": workflow.slug,
        "status": workflow.status,
    }


def create_workflow_version(
    db: Session,
    *,
    workflow: MlWorkflow,
    actor: User,
    definition: dict[str, Any] | None = None,
    lock: bool = False,
) -> WorkflowVersion:
    if workflow.project_id is None:
        raise _lineage_error("workflow is missing a project")
    _require_project(db, workflow.workspace_id, workflow.project_id)
    snapshot = dict(definition or workflow_definition_snapshot(workflow))
    current = db.scalar(
        select(func.max(WorkflowVersion.version)).where(
            WorkflowVersion.workflow_id == workflow.id
        )
    )
    row = WorkflowVersion(
        workspace_id=workflow.workspace_id,
        project_id=workflow.project_id,
        workflow_id=workflow.id,
        version=int(current or 0) + 1,
        definition=snapshot,
        content_digest=content_digest(snapshot),
        created_by=actor.id,
        locked_at=_now() if lock else None,
    )
    db.add(row)
    db.flush()
    return row


def lock_workflow_version(db: Session, version: WorkflowVersion) -> WorkflowVersion:
    if version.locked_at is not None:
        return version
    version.locked_at = _now()
    db.flush()
    return version


def _actor_for_version(db: Session, workflow: MlWorkflow, actor: User | None) -> User:
    if actor is not None:
        return actor
    if workflow.created_by is not None:
        user = db.get(User, workflow.created_by)
        if user is not None:
            return user
    raise _lineage_error("creating a workflow version requires an actor")


def get_or_create_current_workflow_version(
    db: Session, *, workflow: MlWorkflow, actor: User | None
) -> WorkflowVersion:
    existing = db.scalar(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow.id)
        .order_by(WorkflowVersion.version.desc())
        .limit(1)
    )
    if existing is not None:
        if existing.locked_at is None:
            lock_workflow_version(db, existing)
        return existing
    return create_workflow_version(
        db,
        workflow=workflow,
        actor=_actor_for_version(db, workflow, actor),
        lock=True,
    )


def get_workflow_version(
    db: Session, *, workspace_id: UUID, workflow_version_id: UUID
) -> WorkflowVersion:
    row = db.get(WorkflowVersion, workflow_version_id)
    if row is None or row.workspace_id != workspace_id:
        raise WorkflowVersionNotFoundError("workflow version not found")
    return row


def create_pipeline(
    db: Session,
    *,
    workflow: MlWorkflow,
    name: str,
    slug: str | None = None,
    purpose: str = "training",
    status: str = "active",
    actor: User | None = None,
) -> Pipeline:
    if workflow.project_id is None:
        raise _lineage_error("workflow is missing a project")
    _require_project(db, workflow.workspace_id, workflow.project_id)
    if status not in PIPELINE_STATUSES:
        raise _lineage_error(f"unsupported pipeline status: {status}")
    row = Pipeline(
        workspace_id=workflow.workspace_id,
        project_id=workflow.project_id,
        workflow_id=workflow.id,
        name=name,
        slug=_slugify(slug or name),
        purpose=purpose,
        status=status,
        created_by=actor.id if actor is not None else workflow.created_by,
    )
    db.add(row)
    db.flush()
    return row


def get_or_create_pipeline(
    db: Session,
    *,
    workflow: MlWorkflow,
    name: str,
    slug: str,
    purpose: str,
    actor: User | None = None,
) -> Pipeline:
    existing = db.scalar(
        select(Pipeline).where(
            Pipeline.workflow_id == workflow.id,
            Pipeline.slug == _slugify(slug),
        )
    )
    if existing is not None:
        if existing.workspace_id != workflow.workspace_id:
            raise _lineage_error("pipeline belongs to another workspace")
        return existing
    return create_pipeline(
        db,
        workflow=workflow,
        name=name,
        slug=slug,
        purpose=purpose,
        actor=actor,
    )


def create_pipeline_version(
    db: Session,
    *,
    pipeline: Pipeline,
    workflow_version: WorkflowVersion,
    graph_definition: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    lock: bool = False,
) -> PipelineVersion:
    if pipeline.workspace_id != workflow_version.workspace_id:
        raise _lineage_error("pipeline version crosses workspaces")
    if pipeline.project_id != workflow_version.project_id:
        raise _lineage_error("pipeline version crosses projects")
    if pipeline.workflow_id != workflow_version.workflow_id:
        raise _lineage_error("pipeline is not part of this workflow version")
    graph = dict(graph_definition or {})
    cfg = dict(config or {})
    digest = content_digest({"config": cfg, "graph_definition": graph})
    current = db.scalar(
        select(func.max(PipelineVersion.version)).where(
            PipelineVersion.pipeline_id == pipeline.id
        )
    )
    row = PipelineVersion(
        workspace_id=pipeline.workspace_id,
        project_id=pipeline.project_id,
        pipeline_id=pipeline.id,
        workflow_version_id=workflow_version.id,
        version=int(current or 0) + 1,
        graph_definition=graph,
        config=cfg,
        content_digest=digest,
        locked_at=_now() if lock else None,
    )
    db.add(row)
    db.flush()
    return row


def lock_pipeline_version(db: Session, version: PipelineVersion) -> PipelineVersion:
    if version.locked_at is not None:
        return version
    version.locked_at = _now()
    db.flush()
    return version


def get_or_create_current_pipeline_version(
    db: Session,
    *,
    pipeline: Pipeline,
    workflow_version: WorkflowVersion,
    graph_definition: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> PipelineVersion:
    existing = db.scalar(
        select(PipelineVersion)
        .where(
            PipelineVersion.pipeline_id == pipeline.id,
            PipelineVersion.workflow_version_id == workflow_version.id,
        )
        .order_by(PipelineVersion.version.desc())
        .limit(1)
    )
    if existing is not None:
        if existing.locked_at is None:
            lock_pipeline_version(db, existing)
        return existing
    return create_pipeline_version(
        db,
        pipeline=pipeline,
        workflow_version=workflow_version,
        graph_definition=graph_definition,
        config=config,
        lock=True,
    )


def get_pipeline(db: Session, *, workspace_id: UUID, pipeline_id: UUID) -> Pipeline:
    row = db.get(Pipeline, pipeline_id)
    if row is None or row.workspace_id != workspace_id:
        raise PipelineDefinitionNotFoundError("pipeline not found")
    return row


def get_pipeline_version(
    db: Session, *, workspace_id: UUID, pipeline_version_id: UUID
) -> PipelineVersion:
    row = db.get(PipelineVersion, pipeline_version_id)
    if row is None or row.workspace_id != workspace_id:
        raise PipelineVersionNotFoundError("pipeline version not found")
    return row


def next_pipeline_run_number(db: Session, pipeline_id: UUID) -> int:
    current = db.scalar(
        select(func.max(Experiment.run_number)).where(
            Experiment.pipeline_id == pipeline_id
        )
    )
    return int(current or 0) + 1


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _stage_status(raw: str | None) -> str:
    value = (raw or "completed").lower()
    if value in {"queued", "running", "completed", "failed", "skipped"}:
        return value
    if value in {"fail", "error"}:
        return "failed"
    if value in {"skip", "not_applicable"}:
        return "skipped"
    return "completed"


def replace_pipeline_stage_runs(
    db: Session,
    pipeline_run: Experiment,
    timings: list[dict[str, Any]],
) -> list[PipelineStageRun]:
    """Replace queryable stage state from timing records. Events stay append-only."""

    db.query(PipelineStageRun).filter(
        PipelineStageRun.pipeline_run_id == pipeline_run.id
    ).delete(synchronize_session=False)
    db.flush()
    rows: list[PipelineStageRun] = []
    for sequence, timing in enumerate(timings, start=1):
        if not isinstance(timing, dict):
            continue
        stage_key = str(timing.get("stage") or timing.get("stage_key") or f"stage_{sequence}")[:80]
        status = _stage_status(str(timing.get("status") or "completed"))
        started = _parse_time(timing.get("started_at"))
        completed = _parse_time(timing.get("ended_at") or timing.get("completed_at"))
        duration = timing.get("duration_ms")
        row = PipelineStageRun(
            workspace_id=pipeline_run.workspace_id,
            project_id=pipeline_run.project_id,
            pipeline_run_id=pipeline_run.id,
            stage_key=stage_key,
            stage_type=str(timing.get("stage_type") or "execution")[:64],
            sequence=sequence,
            name=str(timing.get("name") or stage_key.replace("_", " "))[:256],
            status=status,
            started_at=started,
            completed_at=completed,
            duration_ms=float(duration) if duration is not None else None,
            input_summary={
                key: timing[key]
                for key in ("rows_in",)
                if key in timing
            },
            output_summary={
                key: timing[key]
                for key in ("rows_out",)
                if key in timing
            },
            failure_code=(str(timing["failure_code"])[:64] if timing.get("failure_code") else None),
            failure_reason=(
                str(timing.get("failure_reason") or timing.get("error") or "")[:2048] or None
                if status == "failed"
                else None
            ),
        )
        db.add(row)
        rows.append(row)
    if rows:
        db.flush()
    return rows
