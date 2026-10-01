"""IngestionRun lifecycle for Project → DataSource → Dataset lineage."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings, is_production_env
from app.db.models import (
    Artifact, DataAccess, DataSource, Dataset, ExecutionRequest,
    IngestionPublicationEvent, IngestionRun, Project, User,
)
from app.domain.data_plane import INGESTION_RUN_STATUSES
from app.domain.errors import IdentityError, IngestionRunNotFoundError

_TERMINAL = frozenset({"completed", "failed"})
_PUBLICATION_NEXT = {
    "received": "quarantined",
    "quarantined": "scanned",
    "scanned": "classified",
    "classified": "publishable",
    "publishable": "published",
}
_REASON = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# ADR 0005: the MVP upload policy. An owner/member upload to its own workspace is
# published for internal deterministic training after structural validation.
# This attests file structure only (type, signature, encoding, schema); it is
# NOT a malware or content scan, and grants no LLM exposure or export.
INTERNAL_TRAINING_ATTESTATION = "structural_internal_training"
INTERNAL_TRAINING_REASONS = {
    "quarantined": "upload_received",
    "scanned": "structural_validation_passed",
    "classified": "internal_training_policy_applied",
    "publishable": "internal_training_policy_complete",
    "published": "internal_training_published",
}
# Conservative labels: unknown content is treated as most sensitive; the source
# says "policy" because nothing was classified. A person can append a manual
# policy revision later.
INTERNAL_TRAINING_LABELS = {
    "sensitivity_class": "restricted",
    "llm_exposure_policy": "deny",
    "retention_class": "standard",
    "residency_class": "home_cloud_only",
    "classification_source": "policy",
    "classification_confidence": 1.0,
}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _now() -> datetime:
    return datetime.now(UTC)


def _require_source(db: Session, workspace_id: UUID, data_source_id: UUID) -> DataSource:
    source = db.get(DataSource, data_source_id)
    if source is None or source.workspace_id != workspace_id:
        raise IdentityError("data source does not belong to this workspace", status_code=404)
    return source


def _require_project(db: Session, workspace_id: UUID, project_id: UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise IdentityError("project does not belong to this workspace", status_code=404)
    return project


def _require_access(
    db: Session,
    *,
    workspace_id: UUID,
    data_source_id: UUID,
    data_access_id: UUID | None,
) -> None:
    if data_access_id is None:
        return
    access = db.get(DataAccess, data_access_id)
    if access is None or access.workspace_id != workspace_id:
        raise IdentityError("data access does not belong to this workspace", status_code=404)
    if access.data_source_id != data_source_id:
        raise IdentityError("data access does not belong to this data source", status_code=400)


def _require_execution_request(
    db: Session, workspace_id: UUID, execution_request_id: UUID | None
) -> None:
    if execution_request_id is None:
        return
    request = db.get(ExecutionRequest, execution_request_id)
    if request is None or request.workspace_id != workspace_id:
        raise IdentityError(
            "execution request does not belong to this workspace", status_code=404
        )


def start_ingestion_run(
    db: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    data_source_id: UUID,
    status: str = "running",
    data_access_id: UUID | None = None,
    execution_request_id: UUID | None = None,
    artifact_id: UUID | None = None,
) -> IngestionRun:
    if status not in INGESTION_RUN_STATUSES:
        raise IdentityError(f"unsupported ingestion status: {status}", status_code=400)
    _require_project(db, workspace_id, project_id)
    _require_source(db, workspace_id, data_source_id)
    _require_access(
        db,
        workspace_id=workspace_id,
        data_source_id=data_source_id,
        data_access_id=data_access_id,
    )
    _require_execution_request(db, workspace_id, execution_request_id)
    if artifact_id is not None:
        artifact = db.get(Artifact, artifact_id)
        if artifact is None or artifact.workspace_id != workspace_id or artifact.artifact_type != "dataset":
            raise IdentityError("dataset artifact not found", status_code=404)
    now = _now()
    row = IngestionRun(
        workspace_id=workspace_id,
        project_id=project_id,
        data_source_id=data_source_id,
        data_access_id=data_access_id,
        execution_request_id=execution_request_id,
        artifact_id=artifact_id,
        status=status,
        started_at=now,
        rows_read=0,
        rows_written=0,
        bytes_read=0,
    )
    db.add(row)
    db.flush()
    return row


def transition_publication(
    db: Session,
    *,
    workspace_id: UUID,
    ingestion_run_id: UUID,
    to_state: str,
    content_digest: str,
    reason_code: str,
    actor_type: str,
    actor_user_id: UUID | None = None,
    policy_schema_version: int = 1,
    attestation: str | None = None,
) -> IngestionRun:
    """Advance exactly one audited state under a database row lock.

    The caller owns commit. Repeating the immediately preceding transition with
    identical evidence is idempotent; any other stale/invalid command is 409.
    """
    if not _DIGEST.fullmatch(content_digest) or not _REASON.fullmatch(reason_code):
        raise IdentityError("invalid publication evidence", status_code=400)
    if actor_type not in {"user", "system", "operator"} or (actor_type != "system") != (actor_user_id is not None):
        raise IdentityError("invalid publication actor", status_code=400)
    if policy_schema_version != 1:
        raise IdentityError("unsupported publication policy version", status_code=400)
    if attestation is not None and (
        attestation != INTERNAL_TRAINING_ATTESTATION
        or INTERNAL_TRAINING_REASONS.get(to_state) != reason_code
        or actor_type != "user"
    ):
        raise IdentityError("invalid publication attestation", status_code=400)
    if (
        to_state in {"scanned", "classified", "publishable", "published"}
        and attestation is None
        and is_production_env(get_settings())
    ):
        # Only the ADR 0005 internal-training policy may publish in production;
        # there is still no malware/content scanner attestation.
        raise IdentityError("production scan attestation is unavailable", status_code=503)
    run = db.scalar(
        select(IngestionRun)
        .where(IngestionRun.id == ingestion_run_id, IngestionRun.workspace_id == workspace_id)
        .with_for_update()
    )
    if run is None:
        raise IdentityError("ingestion run not found", status_code=404)
    if actor_user_id is not None:
        from app.services.authorization_service import can_write_workspace

        actor = db.get(User, actor_user_id)
        if actor is None or not can_write_workspace(db, actor, workspace_id):
            raise IdentityError("workspace write denied", status_code=403)
    previous = db.scalar(
        select(IngestionPublicationEvent)
        .where(IngestionPublicationEvent.ingestion_run_id == run.id)
        .order_by(IngestionPublicationEvent.version.desc())
        .limit(1)
    )
    if run.publication_state == to_state and previous is not None:
        if (previous.content_digest, previous.reason_code, previous.actor_type, previous.actor_user_id) == (
            content_digest, reason_code, actor_type, actor_user_id
        ):
            return run
        raise IdentityError("publication replay conflict", status_code=409)
    expected = _PUBLICATION_NEXT.get(run.publication_state)
    if to_state not in {expected, "rejected", "expired"} or run.publication_state in {"published", "rejected", "expired"}:
        raise IdentityError("invalid publication transition", status_code=409)
    artifact = db.get(Artifact, run.artifact_id) if run.artifact_id else None
    if artifact is None or artifact.workspace_id != workspace_id or artifact.content_digest != content_digest:
        raise IdentityError("publication artifact digest mismatch", status_code=409)
    if attestation is not None:
        # The internal-training attestation only covers a direct upload by the
        # same user; it can never publish connector, admin-import or other runs.
        source = db.get(DataSource, run.data_source_id) if run.data_source_id else None
        if (
            source is None
            or source.workspace_id != workspace_id
            or source.source_type != "upload"
            or artifact.created_by != actor_user_id
        ):
            raise IdentityError("attestation does not match an upload by this user", status_code=403)
    if run.publication_digest is not None and run.publication_digest != content_digest:
        raise IdentityError("publication object changed", status_code=409)
    if to_state in {"publishable", "published"}:
        dataset = db.scalar(select(Dataset).where(
            Dataset.ingestion_run_id == run.id,
            Dataset.workspace_id == workspace_id,
            Dataset.artifact_id == artifact.id,
        ))
        if dataset is None or dataset.content_digest != content_digest:
            raise IdentityError("publication dataset lineage incomplete", status_code=409)
        if to_state == "publishable":
            from app.services.dataset_column_service import resolve_dataset_policy

            if actor_user_id is None:
                raise IdentityError("classification review requires a user", status_code=403)
            policy = resolve_dataset_policy(db, actor=actor, workspace_id=workspace_id, dataset_id=dataset.id)
            if not policy.complete or policy.retention_class in {None, "unknown"} or policy.residency_class in {None, "unknown"}:
                raise IdentityError("dataset classification incomplete", status_code=409)
        if to_state == "published" and run.status != "completed":
            raise IdentityError("ingestion is not complete", status_code=409)
    next_version = run.publication_version + 1
    db.add(IngestionPublicationEvent(
        workspace_id=workspace_id,
        ingestion_run_id=run.id,
        version=next_version,
        from_state=run.publication_state,
        to_state=to_state,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        reason_code=reason_code,
        policy_schema_version=policy_schema_version,
        content_digest=content_digest,
    ))
    run.publication_state = to_state
    run.publication_version = next_version
    run.publication_digest = content_digest
    db.flush()
    return run


def publish_upload_for_internal_training(
    db: Session,
    *,
    workspace_id: UUID,
    ingestion_run_id: UUID,
    dataset_id: UUID,
    content_digest: str,
    actor: User,
) -> IngestionRun:
    """Publish a structurally validated own-workspace upload (ADR 0005).

    Applies the conservative internal-training policy labels to the dataset and
    every column, then advances the audited state machine to ``published``.
    The ingestion run must already be completed with its dataset lineage. The
    caller owns commit; any failure leaves nothing published.
    """
    from app.services.dataset_column_service import (
        publish_dataset_policy_defaults,
        set_dataset_column_policy,
    )
    from app.db.models import DatasetColumn, DatasetPolicyRevision

    current_revision = db.scalar(
        select(DatasetPolicyRevision.revision)
        .where(
            DatasetPolicyRevision.dataset_id == dataset_id,
            DatasetPolicyRevision.workspace_id == workspace_id,
        )
        .order_by(DatasetPolicyRevision.revision.desc())
        .limit(1)
    ) or 0
    publish_dataset_policy_defaults(
        db,
        actor=actor,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        expected_revision=current_revision,
        **INTERNAL_TRAINING_LABELS,
    )
    column_ids = db.scalars(
        select(DatasetColumn.id).where(
            DatasetColumn.dataset_id == dataset_id,
            DatasetColumn.workspace_id == workspace_id,
        )
    ).all()
    for column_id in column_ids:
        set_dataset_column_policy(
            db,
            workspace_id=workspace_id,
            column_id=column_id,
            model_use_policy="allow",  # deterministic training is the granted use
            **INTERNAL_TRAINING_LABELS,
        )
    run = None
    for state, reason in INTERNAL_TRAINING_REASONS.items():
        run = transition_publication(
            db,
            workspace_id=workspace_id,
            ingestion_run_id=ingestion_run_id,
            to_state=state,
            content_digest=content_digest,
            reason_code=reason,
            actor_type="user",
            actor_user_id=actor.id,
            attestation=INTERNAL_TRAINING_ATTESTATION,
        )
    assert run is not None
    return run


def require_published_artifact(db: Session, artifact: Artifact) -> IngestionRun | None:
    """Dataset objects without a verified run are never downloadable."""
    if artifact.artifact_type != "dataset":
        return None
    run = db.scalar(select(IngestionRun).where(
        IngestionRun.workspace_id == artifact.workspace_id,
        IngestionRun.artifact_id == artifact.id,
    ))
    event = db.scalar(select(IngestionPublicationEvent).where(
        IngestionPublicationEvent.ingestion_run_id == run.id,
        IngestionPublicationEvent.version == run.publication_version,
        IngestionPublicationEvent.to_state == "published",
        IngestionPublicationEvent.content_digest == artifact.content_digest,
    )) if run is not None else None
    dataset = db.scalar(select(Dataset.id).where(
        Dataset.workspace_id == artifact.workspace_id,
        Dataset.ingestion_run_id == run.id,
        Dataset.artifact_id == artifact.id,
        Dataset.content_digest == artifact.content_digest,
    )) if run is not None else None
    if (
        run is None or run.status != "completed"
        or run.publication_state != "published"
        or run.publication_digest != artifact.content_digest
        or event is None or dataset is None
    ):
        raise IdentityError("dataset is not published", status_code=409)
    return run


def complete_ingestion_run(
    db: Session,
    run: IngestionRun,
    *,
    rows_read: int,
    rows_written: int,
    bytes_read: int,
    schema_digest: str | None = None,
    content_digest: str | None = None,
) -> IngestionRun:
    if run.status in _TERMINAL:
        raise IdentityError("ingestion run already finished", status_code=409)
    run.status = "completed"
    run.completed_at = _now()
    run.rows_read = int(rows_read)
    run.rows_written = int(rows_written)
    run.bytes_read = int(bytes_read)
    run.schema_digest = schema_digest
    run.content_digest = content_digest
    run.error_code = None
    run.error_summary = None
    db.flush()
    return run


def fail_ingestion_run(
    db: Session,
    run: IngestionRun,
    *,
    error_code: str,
    error_summary: str,
) -> IngestionRun:
    if run.status in _TERMINAL:
        raise IdentityError("ingestion run already finished", status_code=409)
    run.status = "failed"
    run.completed_at = _now()
    run.error_code = error_code[:64]
    run.error_summary = error_summary[:1024]
    db.flush()
    return run


def get_ingestion_run(
    db: Session, *, workspace_id: UUID, ingestion_run_id: UUID
) -> IngestionRun:
    row = db.get(IngestionRun, ingestion_run_id)
    if row is None or row.workspace_id != workspace_id:
        raise IngestionRunNotFoundError("ingestion run not found")
    return row
