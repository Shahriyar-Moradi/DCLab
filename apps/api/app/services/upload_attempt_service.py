"""D3a item 12: the upload log (ADR 0005 amendment): one metadata row per upload attempt.

Written for every attempt that reaches a project on the two upload paths (``POST /v1/datasets``
and the legacy Labs upload): accepted (in the upload's own transaction, with the dataset it
created) or rejected (after the upload's rollback, in its own transaction; best effort, so the
refusal the uploader gets never changes). Never file content or row values: the file name
(capped, control characters removed), size, outcome, a closed reason code and who uploaded.
Rows are append-only; ``purge_expired_upload_attempts`` (worker tick) deletes rows older than
90 days through the retention guard (ADR 0009 §2.2 pattern), one workspace per transaction.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.orm import Session

from app.db.models import Project, UploadAttempt, User
from app.db.retention_guard import allow_retention
from app.domain.data_goal import (
    UPLOAD_ATTEMPT_FILE_NAME_MAX,
    UPLOAD_ATTEMPT_PAGE_MAX,
    UPLOAD_ATTEMPT_RETENTION_DAYS,
    UPLOAD_REASON_CODES,
    UPLOAD_REASON_TEXT,
    UploadAttemptPage,
    UploadAttemptRead,
)
from app.domain.data_plane import LABS_PROJECT_SLUG
from app.domain.errors import InvalidCursorError, QuotaExceededError
from app.domain.usage import QUOTA_STORAGE_BYTES
from app.domain.workspace_identity import creator_read
from app.services.authorization_service import can_read_workspace
from app.services.cursor_codec import open_cursor, sign_cursor

logger = logging.getLogger(__name__)

PURGE_TICK_SECONDS = 3600.0
PURGE_MAX_WORKSPACES = 20
PURGE_BATCH = 1000


def sanitize_file_name(name: Any) -> str:
    """The last path segment, control characters as spaces, whitespace collapsed, capped."""

    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = " ".join("".join(ch if ch.isprintable() else " " for ch in base).split())
    return (cleaned or "upload")[:UPLOAD_ATTEMPT_FILE_NAME_MAX]


def reason_for(exc: BaseException) -> str | None:
    """The closed reason of a refused upload (the structural check's code, or the storage limit);
    None for a refusal that is not about the file (e.g. a run limit): not logged as an upload."""

    if isinstance(exc, QuotaExceededError):
        return "storage_limit" if exc.key == QUOTA_STORAGE_BYTES else None
    code = getattr(exc.__cause__, "code", None)
    return code if code in UPLOAD_REASON_CODES else "unreadable_file"


def labs_project_id(db: Session, workspace_id: UUID) -> UUID | None:
    """The workspace's existing Labs project (looked up, never created)."""

    return db.scalar(select(Project.id).where(Project.workspace_id == workspace_id, Project.slug == LABS_PROJECT_SLUG))


def _row(*, workspace_id: UUID, project_id: UUID | None, user: User, service_token_id: UUID | None,
         file_name: Any, size_bytes: int | None, source: str, **outcome: Any) -> UploadAttempt:
    return UploadAttempt(
        workspace_id=workspace_id, project_id=project_id, actor_user_id=user.id,
        actor_kind="connected_tool" if service_token_id is not None else "person", service_token_id=service_token_id,
        file_name=sanitize_file_name(file_name), size_bytes=size_bytes if size_bytes and size_bytes > 0 else None,
        source=source, **outcome,
    )


def record_accepted(db: Session, *, workspace_id: UUID, project_id: UUID, user: User,
                    service_token_id: UUID | None, file_name: Any, size_bytes: int | None, dataset_id: UUID,
                    source: str) -> None:
    """Inside the upload's transaction (the caller commits; a rollback drops it with the dataset)."""

    db.add(_row(workspace_id=workspace_id, project_id=project_id, user=user, service_token_id=service_token_id,
                file_name=file_name, size_bytes=size_bytes, source=source, outcome="accepted", dataset_id=dataset_id))
    db.flush()


def record_rejected(db: Session, *, workspace_id: UUID, project_id: UUID | None, user: User,
                    service_token_id: UUID | None, file_name: Any, size_bytes: int | None, reason_code: str | None,
                    source: str) -> None:
    """After the upload rolled back: its own commit. Kept only for a reader of the workspace and a
    project of the workspace (else nothing is written; nothing either for ``reason_code`` None).
    Never raises."""

    if reason_code is None:
        return
    try:
        if not can_read_workspace(db, user, workspace_id) or (project_id is not None and db.scalar(
                select(Project.id).where(Project.id == project_id, Project.workspace_id == workspace_id)) is None):
            db.rollback()
            return
        db.add(_row(workspace_id=workspace_id, project_id=project_id, user=user, service_token_id=service_token_id,
                    file_name=file_name, size_bytes=size_bytes, source=source, outcome="rejected",
                    reason_code=reason_code if reason_code in UPLOAD_REASON_CODES else "unreadable_file"))
        db.commit()
    except Exception as exc:  # noqa: BLE001 - the log never changes the refusal
        db.rollback()
        logger.warning("upload attempt not recorded: %s", type(exc).__name__)


def list_upload_attempts(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID,
                         viewer_id: UUID | None, cursor: str | None, limit: int) -> UploadAttemptPage:
    """Newest first. Raises ``IdentityError`` / ``ProjectNotFoundError`` / ``InvalidCursorError``."""

    from app.services.project_service import get_project

    project = get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    limit = max(1, min(int(limit), UPLOAD_ATTEMPT_PAGE_MAX))
    scope = f"upload-attempts:{workspace_id}:{project.id}"
    query = select(UploadAttempt).where(UploadAttempt.workspace_id == workspace_id,
                                        UploadAttempt.project_id == project.id)
    if cursor:
        message = "cursor is not a cursor of this project's upload log"
        try:
            stamp, row_id = open_cursor(cursor, scope, message=message)
            position = (datetime.fromisoformat(stamp), UUID(row_id))
        except (ValueError, TypeError) as exc:
            raise InvalidCursorError(message) from exc
        query = query.where(tuple_(UploadAttempt.created_at, UploadAttempt.id) < position)
    rows = list(db.scalars(query.order_by(UploadAttempt.created_at.desc(), UploadAttempt.id.desc()).limit(limit + 1)))
    more, rows = len(rows) > limit, rows[:limit]
    return UploadAttemptPage(
        project_id=project.id,
        items=[UploadAttemptRead(
            id=row.id, created_at=row.created_at, file_name=row.file_name, size_bytes=row.size_bytes,
            outcome=row.outcome, reason_code=row.reason_code, reason=UPLOAD_REASON_TEXT.get(row.reason_code or ""),
            uploaded_by=creator_read(user_id=row.actor_user_id, service_token_id=row.service_token_id,
                                     viewer_id=viewer_id),
            dataset_id=row.dataset_id, source=row.source,
        ) for row in rows],
        next_cursor=sign_cursor(scope, [rows[-1].created_at.isoformat(), str(rows[-1].id)]) if more else None,
    )


def purge_expired_upload_attempts(db: Session, *, max_workspaces: int = PURGE_MAX_WORKSPACES,
                                  batch: int = PURGE_BATCH) -> int:
    """Delete rows older than the retention horizon: bounded (workspaces x batch per call),
    idempotent, one transaction per workspace under ``allow_retention``. Returns rows deleted."""

    horizon = timedelta(days=UPLOAD_ATTEMPT_RETENTION_DAYS)
    expired = UploadAttempt.created_at < func.now() - horizon
    workspaces = list(db.scalars(select(UploadAttempt.workspace_id).where(expired).distinct().limit(max_workspaces)))
    db.rollback()
    deleted = 0
    for workspace_id in workspaces:
        try:
            allow_retention(db, workspace_id, horizon_days=UPLOAD_ATTEMPT_RETENTION_DAYS)
            oldest = (select(UploadAttempt.id).where(UploadAttempt.workspace_id == workspace_id, expired)
                      .order_by(UploadAttempt.created_at).limit(batch))
            result = db.execute(delete(UploadAttempt).where(UploadAttempt.workspace_id == workspace_id,
                                                            UploadAttempt.id.in_(oldest.scalar_subquery()))
                                .execution_options(synchronize_session=False))
            db.commit()
            deleted += int(result.rowcount or 0)
        except Exception as exc:  # noqa: BLE001 - one workspace never stops the others
            db.rollback()
            logger.warning("upload attempt purge failed for one workspace: %s", type(exc).__name__)
    return deleted
