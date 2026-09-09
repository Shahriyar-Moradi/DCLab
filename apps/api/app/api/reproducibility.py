"""Workspace-scoped and platform-wide reproducibility / artifact access."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.domain.errors import IdentityError
from app.domain.reproducibility import (
    ArtifactRead,
    CodeSnapshotRead,
    ReproducibilityRead,
    RuntimeEnvironmentRead,
    SignedArtifactUrlRead,
)
from app.services import reproducibility_service

workspace_router = APIRouter(prefix="/workspaces", tags=["reproducibility"])
admin_router = APIRouter(tags=["reproducibility"])


def _identity_http(exc: IdentityError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _artifact_read(row) -> ArtifactRead:
    return ArtifactRead.model_validate(row)


def _reproducibility_read(db: Session, model_version) -> ReproducibilityRead:
    artifacts = reproducibility_service.artifacts_for_model_version(db, model_version)
    return ReproducibilityRead(
        model_version_id=model_version.id,
        workspace_id=model_version.workspace_id,
        project_id=model_version.project_id,
        workflow_id=model_version.workflow_id,
        workflow_version_id=model_version.workflow_version_id,
        workflow_run_id=model_version.workflow_run_id,
        pipeline_id=model_version.pipeline_id,
        pipeline_version_id=model_version.pipeline_version_id,
        pipeline_run_id=model_version.pipeline_run_id,
        selected_candidate_id=model_version.selected_candidate_id,
        dataset_id=model_version.dataset_id,
        feature_set_version_id=model_version.feature_set_version_id,
        artifact_uri=model_version.artifact_uri,
        model_artifact_id=model_version.model_artifact_id,
        preprocessor_artifact_id=model_version.preprocessor_artifact_id,
        feature_manifest_artifact_id=model_version.feature_manifest_artifact_id,
        runtime_environment=(
            RuntimeEnvironmentRead.model_validate(model_version.runtime_environment)
            if model_version.runtime_environment is not None
            else None
        ),
        code_snapshot=(
            CodeSnapshotRead.model_validate(model_version.code_snapshot)
            if model_version.code_snapshot is not None
            else None
        ),
        artifacts=[_artifact_read(row) for row in artifacts],
    )


def _download_response(artifact, payload: bytes) -> Response:
    filename = artifact.object_key.rsplit("/", 1)[-1] or "artifact"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(
        content=payload,
        media_type=artifact.mime_type or "application/octet-stream",
        headers=headers,
    )


@workspace_router.get(
    "/{workspace_id}/model-versions/{model_version_id}/reproducibility",
    response_model=ReproducibilityRead,
)
def workspace_model_reproducibility(
    workspace_id: UUID,
    model_version_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReproducibilityRead:
    try:
        model_version = reproducibility_service.get_model_version_for_actor(
            db, user, model_version_id=model_version_id, workspace_id=workspace_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return _reproducibility_read(db, model_version)


@workspace_router.get(
    "/{workspace_id}/model-versions/{model_version_id}/artifacts",
    response_model=list[ArtifactRead],
)
def workspace_model_artifacts(
    workspace_id: UUID,
    model_version_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ArtifactRead]:
    try:
        model_version = reproducibility_service.get_model_version_for_actor(
            db, user, model_version_id=model_version_id, workspace_id=workspace_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return [
        _artifact_read(row)
        for row in reproducibility_service.artifacts_for_model_version(db, model_version)
    ]


@workspace_router.get(
    "/{workspace_id}/artifacts/{artifact_id}",
    response_model=ArtifactRead,
)
def workspace_artifact_metadata(
    workspace_id: UUID,
    artifact_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ArtifactRead:
    try:
        artifact = reproducibility_service.get_artifact_for_actor(
            db, user, artifact_id=artifact_id, workspace_id=workspace_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return _artifact_read(artifact)


@workspace_router.get(
    "/{workspace_id}/artifacts/{artifact_id}/download",
)
def workspace_artifact_download(
    workspace_id: UUID,
    artifact_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        artifact, payload = reproducibility_service.download_artifact_bytes(
            db, user, artifact_id=artifact_id, workspace_id=workspace_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return _download_response(artifact, payload)


@workspace_router.get(
    "/{workspace_id}/artifacts/{artifact_id}/signed-url",
    response_model=SignedArtifactUrlRead,
)
def workspace_artifact_signed_url(
    workspace_id: UUID,
    artifact_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SignedArtifactUrlRead:
    try:
        artifact, url, expires_in = reproducibility_service.signed_url_for_artifact(
            db, user, artifact_id=artifact_id, workspace_id=workspace_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return SignedArtifactUrlRead(artifact_id=artifact.id, url=url, expires_in=expires_in)


@admin_router.get(
    "/model-versions/{model_version_id}/reproducibility",
    response_model=ReproducibilityRead,
)
def admin_model_reproducibility(
    model_version_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReproducibilityRead:
    try:
        model_version = reproducibility_service.get_model_version_for_actor(
            db, user, model_version_id=model_version_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return _reproducibility_read(db, model_version)


@admin_router.get(
    "/model-versions/{model_version_id}/artifacts",
    response_model=list[ArtifactRead],
)
def admin_model_artifacts(
    model_version_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ArtifactRead]:
    try:
        model_version = reproducibility_service.get_model_version_for_actor(
            db, user, model_version_id=model_version_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return [
        _artifact_read(row)
        for row in reproducibility_service.artifacts_for_model_version(db, model_version)
    ]


@admin_router.get("/artifacts/{artifact_id}", response_model=ArtifactRead)
def admin_artifact_metadata(
    artifact_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ArtifactRead:
    try:
        artifact = reproducibility_service.get_artifact_for_actor(
            db, user, artifact_id=artifact_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return _artifact_read(artifact)


@admin_router.get("/artifacts/{artifact_id}/download")
def admin_artifact_download(
    artifact_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        artifact, payload = reproducibility_service.download_artifact_bytes(
            db, user, artifact_id=artifact_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return _download_response(artifact, payload)


@admin_router.get(
    "/artifacts/{artifact_id}/signed-url",
    response_model=SignedArtifactUrlRead,
)
def admin_artifact_signed_url(
    artifact_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SignedArtifactUrlRead:
    try:
        artifact, url, expires_in = reproducibility_service.signed_url_for_artifact(
            db, user, artifact_id=artifact_id
        )
    except IdentityError as exc:
        raise _identity_http(exc) from exc
    return SignedArtifactUrlRead(artifact_id=artifact.id, url=url, expires_in=expires_in)
