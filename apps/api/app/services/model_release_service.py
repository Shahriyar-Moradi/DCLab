"""Model releases (P7.2-A): the approval record behind "model in use".

``project_ref_service.move_ref`` is the only writer: when a person moves ``champion_model``
it checks the package first (``releasable_package``) and inserts the release inside the
savepoint that writes the accepted record and moves the ref (``insert_release``), so a
release never exists without its decision and ref move, nor the reverse. Reads are
workspace-scoped; agent readers (service tokens, the catalog) get no approving person's id
and no rationale that names the final holdout.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.tools.shaping import holdout_free_text
from app.db.models import Artifact, ModelRelease, ModelVersion, ProjectDecisionRecord, ProjectRef, User
from app.domain.errors import InvalidDecisionRecordError
from app.domain.model_releases import (
    LEGACY_MODEL_QUARANTINED,
    MODEL_PACKAGE_MISSING,
    ModelReleaseList,
    ModelReleaseRead,
    ModelReleaseSummary,
)
from app.domain.state_graph import HEX_SHA256_PATTERN

RELEASE_LIST_MAX = 200


def _artifact(db: Session, workspace_id: UUID, artifact_id: UUID | None) -> Artifact | None:
    if artifact_id is None:
        return None
    return db.scalar(select(Artifact).where(Artifact.id == artifact_id, Artifact.workspace_id == workspace_id))


def releasable_package(db: Session, model: ModelVersion) -> tuple[str, str]:
    """(package manifest digest, saved format) of a version a person may put in use.

    The API never loads a model (the worker does, P7.1-A), so this checks what the
    release pins: a stored model artifact and its ``feature_manifest`` artifact, and a
    saved format the server may score with (a quarantined legacy pickle only where
    ``LEGACY_PICKLE_MODELS_ENABLED`` allows it). Raises ``InvalidDecisionRecordError``.
    """

    from app.config import get_settings, legacy_pickle_models_allowed
    from app.engine.serving.model_package import saved_format

    stored = _artifact(db, model.workspace_id, model.model_artifact_id)
    manifest = _artifact(db, model.workspace_id, model.feature_manifest_artifact_id)
    digest = manifest.content_digest if manifest is not None else None
    # The release pins a lowercase SHA-256 (ck_model_releases_digest): anything else is no package.
    if (stored is None or stored.artifact_type != "model" or not isinstance(digest, str)
            or re.fullmatch(HEX_SHA256_PATTERN, digest) is None):
        raise InvalidDecisionRecordError(
            MODEL_PACKAGE_MISSING, "this model has no stored model package, so it cannot be approved for scoring"
        )
    fmt = saved_format(stored.extra_metadata)
    if fmt != "package_v1" and not legacy_pickle_models_allowed(get_settings()):
        raise InvalidDecisionRecordError(
            LEGACY_MODEL_QUARANTINED,
            "this model was saved in the old pickle format, which this server does not score with; "
            "train it again to put it in use",
        )
    return digest, fmt


def latest_release(db: Session, *, workspace_id: UUID, project_id: UUID) -> ModelRelease | None:
    """The head of the project's approval chain (the release no other release follows)."""

    successor = select(ModelRelease.previous_release_id).where(
        ModelRelease.workspace_id == workspace_id,
        ModelRelease.project_id == project_id,
        ModelRelease.previous_release_id.is_not(None),
    )
    return db.scalar(
        select(ModelRelease)
        .where(
            ModelRelease.workspace_id == workspace_id,
            ModelRelease.project_id == project_id,
            ModelRelease.id.not_in(successor),
        )
        .limit(1)
    )


def insert_release(
    db: Session,
    *,
    record: ProjectDecisionRecord,
    model: ModelVersion,
    approved_by_user_id: UUID,
    package: tuple[str, str],
) -> ModelRelease:
    """Inside ``move_ref``'s savepoint, after ``record`` is flushed. A concurrent approval of
    the same project collides on the chain's unique indexes (the caller maps it)."""

    previous = latest_release(db, workspace_id=record.workspace_id, project_id=record.project_id)
    row = ModelRelease(
        workspace_id=record.workspace_id,
        project_id=record.project_id,
        model_version_id=model.id,
        previous_release_id=previous.id if previous is not None else None,
        decision_record_id=record.id,
        approved_by_user_id=approved_by_user_id,
        rationale=record.rationale,
        package_manifest_digest=package[0],
        saved_format=package[1],
    )
    db.add(row)
    db.flush()
    return row


def current_release(db: Session, *, workspace_id: UUID, project_id: UUID | None) -> ModelRelease | None:
    """The approval of the model in use: the chain head, only while the ``champion_model``
    ref still points at its version (a bootstrapped model in use has none)."""

    if project_id is None:
        return None
    champion = db.scalar(
        select(ProjectRef.model_version_id).where(
            ProjectRef.workspace_id == workspace_id,
            ProjectRef.project_id == project_id,
            ProjectRef.ref_kind == "champion_model",
        )
    )
    head = latest_release(db, workspace_id=workspace_id, project_id=project_id)
    if champion is None or head is None or head.model_version_id != champion:
        return None
    return head


def release_summary(row: ModelRelease | None, *, current_id: UUID | None) -> ModelReleaseSummary | None:
    if row is None:
        return None
    return ModelReleaseSummary(
        id=row.id, model_version_id=row.model_version_id, previous_release_id=row.previous_release_id,
        decision_record_id=row.decision_record_id, approved_at=row.created_at, saved_format=row.saved_format,
        package_manifest_digest=row.package_manifest_digest, is_current=row.id == current_id,
    )


def release_for_version(db: Session, *, workspace_id: UUID, model_version: ModelVersion) -> ModelReleaseSummary | None:
    """The current approval when this version is the model in use, else None."""

    current = current_release(db, workspace_id=workspace_id, project_id=model_version.project_id)
    if current is None or current.model_version_id != model_version.id:
        return None
    return release_summary(current, current_id=current.id)


def list_releases(
    db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, agent: bool
) -> ModelReleaseList:
    """The project's approvals, newest first (``created_at`` is ``clock_timestamp()``; ties by id).
    ``agent=True`` (service tokens, the catalog) drops the approving person's id and withholds a
    rationale that names the final holdout. Raises ``IdentityError`` / ``ProjectNotFoundError``."""

    from app.services.project_service import get_project

    get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    rows = list(
        db.execute(
            select(ModelRelease, ModelVersion.version)
            .join(
                ModelVersion,
                (ModelVersion.id == ModelRelease.model_version_id) & (ModelVersion.workspace_id == workspace_id),
            )
            .where(ModelRelease.workspace_id == workspace_id, ModelRelease.project_id == project_id)
            .order_by(ModelRelease.created_at.desc(), ModelRelease.id.desc())
            .limit(RELEASE_LIST_MAX)
        ).all()
    )
    current = current_release(db, workspace_id=workspace_id, project_id=project_id)
    current_id = current.id if current is not None else None
    items = [
        ModelReleaseRead(
            **release_summary(row, current_id=current_id).model_dump(),
            model_version=version,
            approved_by_user_id=None if agent else row.approved_by_user_id,
            rationale=holdout_free_text(row.rationale) if agent else row.rationale,
        )
        for row, version in rows
    ]
    return ModelReleaseList(project_id=project_id, current_release_id=current_id, items=items)
