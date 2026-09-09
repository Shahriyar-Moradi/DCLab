"""Persist Artifact registry rows. Bytes go to ObjectStorage, not PostgreSQL."""

from __future__ import annotations

import mimetypes
import re
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Artifact, Experiment, Project, Workspace
from app.domain.data_plane import ARTIFACT_TYPES
from app.domain.errors import ArtifactNotFoundError, IdentityError
from app.storage.base import ObjectPutResult, ObjectStorage
from app.storage.factory import get_object_storage

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(filename: str) -> str:
    stem = (filename or "blob").split("/")[-1].split("\\")[-1]
    cleaned = _SAFE_NAME.sub("-", stem).strip(".-") or "blob"
    return cleaned[:200]


def artifact_object_key(workspace_id: UUID, artifact_id: UUID, filename: str) -> str:
    return f"workspaces/{workspace_id}/artifacts/{artifact_id}/{_safe_filename(filename)}"


def _require_workspace(db: Session, workspace_id: UUID) -> None:
    if db.get(Workspace, workspace_id) is None:
        raise IdentityError("workspace not found", status_code=404)


def _require_project(db: Session, workspace_id: UUID, project_id: UUID | None) -> None:
    if project_id is None:
        return
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise IdentityError("project does not belong to this workspace", status_code=404)


def _require_pipeline_run(
    db: Session, workspace_id: UUID, pipeline_run_id: UUID | None
) -> None:
    if pipeline_run_id is None:
        return
    run = db.get(Experiment, pipeline_run_id)
    if run is None or run.workspace_id != workspace_id:
        raise IdentityError(
            "pipeline run does not belong to this workspace", status_code=404
        )


def store_artifact(
    db: Session,
    *,
    workspace_id: UUID,
    artifact_type: str,
    filename: str,
    data: bytes | BinaryIO,
    project_id: UUID | None = None,
    pipeline_run_id: UUID | None = None,
    created_by: UUID | None = None,
    mime_type: str | None = None,
    extra_metadata: dict | None = None,
    artifact_id: UUID | None = None,
    storage: ObjectStorage | None = None,
) -> Artifact:
    """Write bytes once to object storage and register the Artifact row."""

    if artifact_type not in ARTIFACT_TYPES:
        raise IdentityError(f"unsupported artifact_type: {artifact_type}", status_code=400)
    _require_workspace(db, workspace_id)
    _require_project(db, workspace_id, project_id)
    _require_pipeline_run(db, workspace_id, pipeline_run_id)
    backend = storage or get_object_storage()
    row_id = artifact_id or uuid4()
    key = artifact_object_key(workspace_id, row_id, filename)
    guessed = mime_type or mimetypes.guess_type(filename)[0]
    put = backend.put(key, data, content_type=guessed)
    return record_artifact(
        db,
        artifact_id=row_id,
        workspace_id=workspace_id,
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        artifact_type=artifact_type,
        put=put,
        mime_type=put.content_type or guessed,
        created_by=created_by,
        extra_metadata=extra_metadata,
    )


def record_artifact(
    db: Session,
    *,
    artifact_id: UUID,
    workspace_id: UUID,
    artifact_type: str,
    put: ObjectPutResult,
    project_id: UUID | None = None,
    pipeline_run_id: UUID | None = None,
    created_by: UUID | None = None,
    mime_type: str | None = None,
    extra_metadata: dict | None = None,
) -> Artifact:
    """Register metadata for bytes that are already in object storage."""

    if artifact_type not in ARTIFACT_TYPES:
        raise IdentityError(f"unsupported artifact_type: {artifact_type}", status_code=400)
    artifact = Artifact(
        id=artifact_id,
        workspace_id=workspace_id,
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        artifact_type=artifact_type,
        provider=put.provider,
        bucket=put.bucket,
        object_key=put.key,
        content_digest=put.content_digest,
        mime_type=mime_type or put.content_type,
        size_bytes=put.size_bytes,
        extra_metadata=dict(extra_metadata or {}),
        created_by=created_by,
    )
    db.add(artifact)
    db.flush()
    return artifact


def get_artifact(
    db: Session, *, workspace_id: UUID, artifact_id: UUID
) -> Artifact:
    row = db.get(Artifact, artifact_id)
    if row is None or row.workspace_id != workspace_id:
        raise ArtifactNotFoundError("artifact not found")
    return row


def read_artifact_bytes(
    db: Session,
    *,
    workspace_id: UUID,
    artifact_id: UUID,
    storage: ObjectStorage | None = None,
) -> bytes:
    artifact = get_artifact(db, workspace_id=workspace_id, artifact_id=artifact_id)
    backend = storage or get_object_storage()
    return backend.get(artifact.object_key)


def list_artifacts(
    db: Session, *, workspace_id: UUID, project_id: UUID | None = None
) -> list[Artifact]:
    _require_workspace(db, workspace_id)
    stmt = select(Artifact).where(Artifact.workspace_id == workspace_id)
    if project_id is not None:
        stmt = stmt.where(Artifact.project_id == project_id)
    stmt = stmt.order_by(Artifact.created_at.desc(), Artifact.id)
    return list(db.scalars(stmt))
