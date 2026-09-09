"""Persist canonical visualization metadata. Does not render charts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    Experiment,
    ExperimentCandidate,
    ModelEvaluation,
    PipelineStageRun,
    Project,
    Visualization,
)
from app.domain.errors import IdentityError, VisualizationSpecError
from app.domain.visualizations import (
    FORBIDDEN_SPEC_KEYS,
    SPEC_MAX_ARRAY_LENGTH,
    SPEC_MAX_BYTES,
    SPEC_VERSION_DEFAULT,
    VISUALIZATION_TYPES,
)

_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RENDERER_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_SPEC_VERSION_RE = re.compile(r"^[a-zA-Z0-9._-]{1,32}$")


def _as_object(value: dict[str, Any] | None) -> dict[str, Any]:
    payload = {} if value is None else value
    if type(payload) is not dict:
        raise VisualizationSpecError("spec must be a JSON object")
    return payload


def _reject_forbidden_keys(payload: dict[str, Any]) -> None:
    blocked = sorted(set(payload) & set(FORBIDDEN_SPEC_KEYS))
    if blocked:
        raise VisualizationSpecError(f"spec must not contain {', '.join(blocked)}")


def _reject_large_arrays(payload: Any, *, path: str = "spec") -> None:
    if isinstance(payload, list):
        if len(payload) > SPEC_MAX_ARRAY_LENGTH:
            raise VisualizationSpecError(
                f"{path} array exceeds {SPEC_MAX_ARRAY_LENGTH} items; "
                "store series in an Artifact"
            )
        for index, item in enumerate(payload):
            _reject_large_arrays(item, path=f"{path}[{index}]")
        return
    if isinstance(payload, dict):
        for key, item in payload.items():
            _reject_large_arrays(item, path=f"{path}.{key}")


def bound_visualization_spec(payload: dict[str, Any] | None) -> dict[str, Any]:
    spec = _as_object(payload)
    _reject_forbidden_keys(spec)
    _reject_large_arrays(spec)
    encoded = json.dumps(spec, default=str, separators=(",", ":")).encode("utf-8")
    if len(encoded) > SPEC_MAX_BYTES:
        raise VisualizationSpecError(f"spec exceeds {SPEC_MAX_BYTES} bytes")
    return spec


def visualization_content_digest(
    *,
    visualization_type: str,
    spec_version: str,
    renderer_hint: str | None,
    spec: dict[str, Any],
    data_artifact_id: UUID | None,
    image_artifact_id: UUID | None,
) -> str:
    payload = {
        "visualization_type": visualization_type,
        "spec_version": spec_version,
        "renderer_hint": renderer_hint,
        "spec": spec,
        "data_artifact_id": str(data_artifact_id) if data_artifact_id else None,
        "image_artifact_id": str(image_artifact_id) if image_artifact_id else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_run(db: Session, workspace_id: UUID, pipeline_run_id: UUID) -> Experiment:
    run = db.get(Experiment, pipeline_run_id)
    if run is None or run.workspace_id != workspace_id:
        raise IdentityError(
            "pipeline run does not belong to this workspace", status_code=404
        )
    return run


def _require_project(
    db: Session, workspace_id: UUID, project_id: UUID | None
) -> UUID | None:
    if project_id is None:
        return None
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise IdentityError("project does not belong to this workspace", status_code=404)
    return project_id


def _require_stage(
    db: Session,
    workspace_id: UUID,
    pipeline_run_id: UUID,
    stage_id: UUID | None,
) -> UUID | None:
    if stage_id is None:
        return None
    stage = db.get(PipelineStageRun, stage_id)
    if (
        stage is None
        or stage.workspace_id != workspace_id
        or stage.pipeline_run_id != pipeline_run_id
    ):
        raise IdentityError(
            "pipeline stage run does not belong to this pipeline run",
            status_code=404,
        )
    return stage_id


def _require_candidate(
    db: Session,
    workspace_id: UUID,
    pipeline_run_id: UUID,
    candidate_id: UUID | None,
) -> UUID | None:
    if candidate_id is None:
        return None
    candidate = db.get(ExperimentCandidate, candidate_id)
    if (
        candidate is None
        or candidate.workspace_id != workspace_id
        or candidate.experiment_id != pipeline_run_id
    ):
        raise IdentityError(
            "candidate does not belong to this pipeline run", status_code=404
        )
    return candidate_id


def _require_evaluation(
    db: Session,
    workspace_id: UUID,
    candidate_id: UUID | None,
    evaluation_id: UUID | None,
) -> UUID | None:
    if evaluation_id is None:
        return None
    evaluation = db.get(ModelEvaluation, evaluation_id)
    if evaluation is None or evaluation.workspace_id != workspace_id:
        raise IdentityError(
            "model evaluation does not belong to this workspace", status_code=404
        )
    if candidate_id is not None and evaluation.candidate_id not in {None, candidate_id}:
        raise IdentityError(
            "model evaluation does not belong to this candidate", status_code=404
        )
    return evaluation_id


def _require_artifact(
    db: Session, workspace_id: UUID, artifact_id: UUID | None, *, label: str
) -> UUID | None:
    if artifact_id is None:
        return None
    artifact = db.get(Artifact, artifact_id)
    if artifact is None or artifact.workspace_id != workspace_id:
        raise IdentityError(
            f"{label} does not belong to this workspace", status_code=404
        )
    return artifact_id


def persist_visualization(
    db: Session,
    *,
    workspace_id: UUID,
    pipeline_run_id: UUID,
    visualization_type: str,
    spec: dict[str, Any] | None = None,
    project_id: UUID | None = None,
    pipeline_stage_run_id: UUID | None = None,
    candidate_id: UUID | None = None,
    model_evaluation_id: UUID | None = None,
    spec_version: str = SPEC_VERSION_DEFAULT,
    renderer_hint: str | None = None,
    data_artifact_id: UUID | None = None,
    image_artifact_id: UUID | None = None,
) -> Visualization:
    """Insert one immutable visualization description. Does not plot."""

    if visualization_type not in VISUALIZATION_TYPES:
        raise VisualizationSpecError(
            f"unsupported visualization_type: {visualization_type}"
        )
    if _TYPE_RE.fullmatch(visualization_type) is None:
        raise VisualizationSpecError(
            f"invalid visualization_type {visualization_type!r}"
        )
    if _SPEC_VERSION_RE.fullmatch(spec_version) is None:
        raise VisualizationSpecError(f"invalid spec_version {spec_version!r}")
    hint = (renderer_hint or "").strip() or None
    if hint is not None and _RENDERER_RE.fullmatch(hint) is None:
        raise VisualizationSpecError(f"invalid renderer_hint {hint!r}")

    run = _require_run(db, workspace_id, pipeline_run_id)
    project_id = _require_project(db, workspace_id, project_id or run.project_id)
    pipeline_stage_run_id = _require_stage(
        db, workspace_id, pipeline_run_id, pipeline_stage_run_id
    )
    candidate_id = _require_candidate(
        db, workspace_id, pipeline_run_id, candidate_id
    )
    model_evaluation_id = _require_evaluation(
        db, workspace_id, candidate_id, model_evaluation_id
    )
    data_artifact_id = _require_artifact(
        db, workspace_id, data_artifact_id, label="data_artifact_id"
    )
    image_artifact_id = _require_artifact(
        db, workspace_id, image_artifact_id, label="image_artifact_id"
    )
    bound_spec = bound_visualization_spec(spec)
    digest = visualization_content_digest(
        visualization_type=visualization_type,
        spec_version=spec_version,
        renderer_hint=hint,
        spec=bound_spec,
        data_artifact_id=data_artifact_id,
        image_artifact_id=image_artifact_id,
    )
    row = Visualization(
        workspace_id=workspace_id,
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        pipeline_stage_run_id=pipeline_stage_run_id,
        candidate_id=candidate_id,
        model_evaluation_id=model_evaluation_id,
        visualization_type=visualization_type,
        spec_version=spec_version,
        renderer_hint=hint,
        spec=bound_spec,
        data_artifact_id=data_artifact_id,
        image_artifact_id=image_artifact_id,
        content_digest=digest,
    )
    db.add(row)
    db.flush()
    return row


def list_visualizations_for_pipeline_run(
    db: Session,
    *,
    workspace_id: UUID,
    pipeline_run_id: UUID,
) -> list[Visualization]:
    run = db.get(Experiment, pipeline_run_id)
    if run is None or run.workspace_id != workspace_id:
        raise IdentityError("not found", status_code=404)
    return list(
        db.scalars(
            select(Visualization)
            .where(
                Visualization.workspace_id == workspace_id,
                Visualization.pipeline_run_id == pipeline_run_id,
            )
            .order_by(Visualization.created_at, Visualization.id)
        )
    )
