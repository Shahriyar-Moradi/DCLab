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


_TERMINAL_STAGE = "terminal"
_INTRA_STAGE_EVENT_PREFIXES = ("cv_fold_", "candidate_")


def _stage_status(raw: str | None) -> str:
    value = (raw or "completed").lower()
    if value in {"queued", "running", "completed", "failed", "skipped"}:
        return value
    if value in {"started"}:
        return "running"
    if value in {"fail", "error"}:
        return "failed"
    if value in {"skip", "not_applicable"}:
        return "skipped"
    return "completed"


def _stage_key(value: Any, *, fallback: str = "stage") -> str:
    return str(value or fallback)[:80]


def _stage_name(stage_key: str, raw: Any = None) -> str:
    name = str(raw or stage_key.replace("_", " "))
    return name[:256]


def _next_stage_sequence(db: Session, pipeline_run_id: UUID) -> int:
    last = db.scalar(
        select(func.max(PipelineStageRun.sequence)).where(
            PipelineStageRun.pipeline_run_id == pipeline_run_id
        )
    )
    return int(last or 0) + 1


def _lookup_stage_run(
    db: Session, pipeline_run_id: UUID, stage_key: str
) -> PipelineStageRun | None:
    return db.scalar(
        select(PipelineStageRun).where(
            PipelineStageRun.pipeline_run_id == pipeline_run_id,
            PipelineStageRun.stage_key == stage_key,
        )
    )


def _summaries_from_mapping(payload: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    data = payload or {}
    return (
        {key: data[key] for key in ("rows_in",) if key in data},
        {key: data[key] for key in ("rows_out",) if key in data},
    )


def _apply_summaries(row: PipelineStageRun, payload: dict[str, Any] | None) -> None:
    incoming_in, incoming_out = _summaries_from_mapping(payload)
    if incoming_in:
        row.input_summary = {**dict(row.input_summary or {}), **incoming_in}
    if incoming_out:
        row.output_summary = {**dict(row.output_summary or {}), **incoming_out}


def start_pipeline_stage_run(
    db: Session,
    pipeline_run: Experiment,
    *,
    stage_key: str,
    started_at: datetime | None = None,
    payload: dict[str, Any] | None = None,
    stage_type: str = "execution",
    name: str | None = None,
) -> PipelineStageRun | None:
    """Create or reuse the live stage row. Same stage_key keeps the same row."""

    key = _stage_key(stage_key)
    if not key or key == _TERMINAL_STAGE:
        return None
    started = started_at or _now()
    row = _lookup_stage_run(db, pipeline_run.id, key)
    if row is None:
        row = PipelineStageRun(
            workspace_id=pipeline_run.workspace_id,
            project_id=pipeline_run.project_id,
            pipeline_run_id=pipeline_run.id,
            stage_key=key,
            stage_type=str(stage_type or "execution")[:64],
            sequence=_next_stage_sequence(db, pipeline_run.id),
            name=_stage_name(key, name),
            status="running",
            started_at=started,
            completed_at=None,
            duration_ms=None,
            input_summary={},
            output_summary={},
        )
        db.add(row)
    elif row.status == "running":
        if name:
            row.name = _stage_name(key, name)
    else:
        row.status = "running"
        row.started_at = started
        row.completed_at = None
        row.duration_ms = None
        row.failure_code = None
        row.failure_reason = None
        if name:
            row.name = _stage_name(key, name)
    _apply_summaries(row, payload)
    db.flush()
    return row


def complete_pipeline_stage_run(
    db: Session,
    pipeline_run: Experiment,
    *,
    stage_key: str,
    status: str = "completed",
    completed_at: datetime | None = None,
    duration_ms: float | None = None,
    payload: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    stage_type: str = "execution",
    name: str | None = None,
) -> PipelineStageRun | None:
    key = _stage_key(stage_key)
    if not key or key == _TERMINAL_STAGE:
        return None
    ended = completed_at or _now()
    resolved = _stage_status(status)
    if resolved not in {"completed", "skipped"}:
        resolved = "completed"
    row = _lookup_stage_run(db, pipeline_run.id, key)
    if row is None:
        row = start_pipeline_stage_run(
            db,
            pipeline_run,
            stage_key=key,
            started_at=started_at,
            payload=payload,
            stage_type=stage_type,
            name=name,
        )
        if row is None:
            return None
    row.status = resolved
    row.completed_at = ended
    if duration_ms is not None:
        row.duration_ms = float(duration_ms)
    elif row.started_at is not None:
        row.duration_ms = max(0.0, (ended - row.started_at).total_seconds() * 1000.0)
    if started_at is not None and row.started_at is None:
        row.started_at = started_at
    _apply_summaries(row, payload)
    db.flush()
    return row


def fail_pipeline_stage_run(
    db: Session,
    pipeline_run: Experiment,
    *,
    stage_key: str,
    reason: str | None = None,
    failure_code: str | None = None,
    completed_at: datetime | None = None,
    duration_ms: float | None = None,
    payload: dict[str, Any] | None = None,
    started_at: datetime | None = None,
) -> PipelineStageRun | None:
    key = _stage_key(stage_key)
    if not key or key == _TERMINAL_STAGE:
        return None
    ended = completed_at or _now()
    row = _lookup_stage_run(db, pipeline_run.id, key)
    if row is None:
        row = start_pipeline_stage_run(
            db,
            pipeline_run,
            stage_key=key,
            started_at=started_at,
            payload=payload,
        )
        if row is None:
            return None
    row.status = "failed"
    row.completed_at = ended
    if duration_ms is not None:
        row.duration_ms = float(duration_ms)
    elif row.started_at is not None:
        row.duration_ms = max(0.0, (ended - row.started_at).total_seconds() * 1000.0)
    data = payload or {}
    code = failure_code or data.get("failure_code")
    detail = reason or data.get("failure_reason") or data.get("error") or data.get("reason")
    row.failure_code = str(code)[:64] if code else row.failure_code
    row.failure_reason = str(detail)[:2048] if detail else row.failure_reason
    _apply_summaries(row, payload)
    db.flush()
    return row


def fail_open_pipeline_stage_runs(
    db: Session,
    pipeline_run: Experiment,
    *,
    reason: str | None = None,
    failure_code: str | None = None,
    payload: dict[str, Any] | None = None,
) -> list[PipelineStageRun]:
    rows = list(
        db.scalars(
            select(PipelineStageRun).where(
                PipelineStageRun.pipeline_run_id == pipeline_run.id,
                PipelineStageRun.status == "running",
            )
        )
    )
    failed: list[PipelineStageRun] = []
    for row in rows:
        updated = fail_pipeline_stage_run(
            db,
            pipeline_run,
            stage_key=row.stage_key,
            reason=reason,
            failure_code=failure_code,
            payload=payload,
        )
        if updated is not None:
            failed.append(updated)
    return failed


def complete_open_pipeline_stage_runs(
    db: Session,
    pipeline_run: Experiment,
    *,
    payload: dict[str, Any] | None = None,
) -> list[PipelineStageRun]:
    rows = list(
        db.scalars(
            select(PipelineStageRun).where(
                PipelineStageRun.pipeline_run_id == pipeline_run.id,
                PipelineStageRun.status == "running",
            )
        )
    )
    completed: list[PipelineStageRun] = []
    for row in rows:
        updated = complete_pipeline_stage_run(
            db,
            pipeline_run,
            stage_key=row.stage_key,
            payload=payload,
        )
        if updated is not None:
            completed.append(updated)
    return completed


def apply_live_stage_from_event(
    db: Session,
    pipeline_run: Experiment,
    *,
    stage: str,
    event_type: str,
    status: str,
    payload: dict[str, Any] | None = None,
    duration_ms: float | None = None,
) -> PipelineStageRun | None:
    """Keep PipelineStageRun in lockstep with an append-only MlRunEvent."""

    key = _stage_key(stage)
    event_name = str(event_type or "")
    resolved = _stage_status(status)
    data = payload or {}
    if key == _TERMINAL_STAGE or event_name == "pipeline_terminal":
        if resolved == "failed" or str(status).lower() == "failed":
            fail_open_pipeline_stage_runs(
                db,
                pipeline_run,
                reason=str(data.get("reason") or "") or None,
                failure_code=str(data.get("failure_code") or "") or None,
                payload=data,
            )
        elif resolved in {"completed", "skipped"}:
            complete_open_pipeline_stage_runs(db, pipeline_run, payload=data)
        return None
    intra = event_name.startswith(_INTRA_STAGE_EVENT_PREFIXES)
    if resolved == "running" or event_name.endswith("_started") or event_name == "operation_started":
        return start_pipeline_stage_run(
            db,
            pipeline_run,
            stage_key=key,
            payload=data,
        )
    if resolved == "failed" or event_name.endswith("_failed"):
        if intra:
            return start_pipeline_stage_run(
                db,
                pipeline_run,
                stage_key=key,
                payload=data,
            )
        return fail_pipeline_stage_run(
            db,
            pipeline_run,
            stage_key=key,
            reason=str(data.get("reason") or data.get("error") or "") or None,
            failure_code=str(data.get("failure_code") or "") or None,
            duration_ms=duration_ms,
            payload=data,
        )
    if intra:
        return start_pipeline_stage_run(
            db,
            pipeline_run,
            stage_key=key,
            payload=data,
        )
    if resolved in {"completed", "skipped"} or event_name in {
        "operation_completed",
    } or event_name.endswith("_completed"):
        return complete_pipeline_stage_run(
            db,
            pipeline_run,
            stage_key=key,
            status=resolved if resolved in {"completed", "skipped"} else "completed",
            duration_ms=duration_ms,
            payload=data,
        )
    return start_pipeline_stage_run(db, pipeline_run, stage_key=key, payload=data)


def _row_from_timing(
    db: Session,
    pipeline_run: Experiment,
    timing: dict[str, Any],
    *,
    sequence: int | None = None,
) -> PipelineStageRun | None:
    stage_key = _stage_key(timing.get("stage") or timing.get("stage_key"))
    if not stage_key or stage_key == _TERMINAL_STAGE:
        return None
    status = _stage_status(str(timing.get("status") or "completed"))
    started = _parse_time(timing.get("started_at"))
    completed = _parse_time(timing.get("ended_at") or timing.get("completed_at"))
    duration = timing.get("duration_ms")
    if status == "running":
        return start_pipeline_stage_run(
            db,
            pipeline_run,
            stage_key=stage_key,
            started_at=started,
            payload=timing,
            stage_type=str(timing.get("stage_type") or "execution"),
            name=timing.get("name"),
        )
    if status == "failed":
        return fail_pipeline_stage_run(
            db,
            pipeline_run,
            stage_key=stage_key,
            reason=str(timing.get("failure_reason") or timing.get("error") or "") or None,
            failure_code=str(timing["failure_code"]) if timing.get("failure_code") else None,
            completed_at=completed,
            duration_ms=float(duration) if duration is not None else None,
            payload=timing,
            started_at=started,
        )
    row = complete_pipeline_stage_run(
        db,
        pipeline_run,
        stage_key=stage_key,
        status=status,
        completed_at=completed,
        duration_ms=float(duration) if duration is not None else None,
        payload=timing,
        started_at=started,
        stage_type=str(timing.get("stage_type") or "execution"),
        name=timing.get("name"),
    )
    if row is not None and sequence is not None and row.sequence != sequence:
        # Recovery rebuild assigns explicit sequences; live upsert keeps first sequence.
        pass
    return row


def reconcile_pipeline_stage_runs(
    db: Session,
    pipeline_run: Experiment,
    timings: list[dict[str, Any]],
) -> list[PipelineStageRun]:
    """Backfill missing stage facts from timing records. Does not delete live rows."""

    rows: list[PipelineStageRun] = []
    seen: set[str] = set()
    for timing in timings:
        if not isinstance(timing, dict):
            continue
        key = _stage_key(timing.get("stage") or timing.get("stage_key"))
        if not key or key in seen:
            continue
        seen.add(key)
        row = _row_from_timing(db, pipeline_run, timing)
        if row is not None:
            rows.append(row)
    return rows


def replace_pipeline_stage_runs(
    db: Session,
    pipeline_run: Experiment,
    timings: list[dict[str, Any]],
) -> list[PipelineStageRun]:
    """Recovery-only rebuild. Normal execution must update live rows in place."""

    db.query(PipelineStageRun).filter(
        PipelineStageRun.pipeline_run_id == pipeline_run.id
    ).delete(synchronize_session=False)
    db.flush()
    rows: list[PipelineStageRun] = []
    for sequence, timing in enumerate(timings, start=1):
        if not isinstance(timing, dict):
            continue
        row = _row_from_timing(db, pipeline_run, timing, sequence=sequence)
        if row is None:
            continue
        row.sequence = sequence
        rows.append(row)
    if rows:
        db.flush()
    return rows
