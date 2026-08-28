"""Capability 1 of Client Labs open ingest: take any usual data file, no schema.

Capability 2 (understand / structure the file) is deferred — see
docs/LABS_DATA_UNDERSTANDING.md.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.db.models import DEFAULT_WORKSPACE_ID, ClientLabUpload, User
from app.domain.client_lab import ClientLabUploadRead
from app.domain.errors import OpenLabFileError, UnknownLabCategoryError
from app.engine.lab.open_ingest import OpenIngestError, preview_upload
from app.services.auto_train_service import enqueue_auto_train
from app.translation.models import InsightCategory

RECEIVED_MESSAGE = (
    "We saved your file. Turning unstructured files into a usable table is not available yet."
)


def _workspace_id_for(user: User) -> UUID:
    return user.workspace_id or DEFAULT_WORKSPACE_ID


def _parse_category(value: str) -> InsightCategory:
    try:
        return InsightCategory(value)
    except ValueError as exc:
        raise UnknownLabCategoryError(f"{value!r} is not a Labs category") from exc


def _to_read(row: ClientLabUpload) -> ClientLabUploadRead:
    return ClientLabUploadRead(
        id=row.id,
        category=InsightCategory(row.category),
        filename=row.original_filename,
        kind=row.kind,
        record_count=row.record_count,
        fields_noticed=list(row.fields_noticed or []),
        has_named_fields=row.has_named_fields,
        structured=False,
        message=RECEIVED_MESSAGE,
        created_at=row.created_at,
    )


def save_upload(
    db: Session,
    *,
    user: User,
    category: str,
    filename: str,
    data: bytes,
) -> ClientLabUploadRead:
    parsed_category = _parse_category(category)
    try:
        preview = preview_upload(filename, data)
    except OpenIngestError as exc:
        raise OpenLabFileError(str(exc)) from exc

    workspace_id = _workspace_id_for(user)
    dest_dir = REPO_ROOT / "data" / "uploads" / "client_labs" / str(workspace_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = ""
    if "." in (filename or ""):
        suffix = "." + filename.rsplit(".", 1)[-1].lower()[:12]
    dest = dest_dir / f"{uuid4().hex}{suffix}"
    dest.write_bytes(data)

    row = ClientLabUpload(
        workspace_id=workspace_id,
        requested_by=user.id,
        category=parsed_category.value,
        original_filename=filename or "upload",
        stored_path=str(dest),
        kind=preview.kind,
        record_count=preview.record_count,
        fields_noticed=preview.fields_noticed,
        has_named_fields=preview.has_named_fields,
        pipeline_status="queued",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    # Simple-case auto-train (admin-only) runs behind the client response so the
    # upload stays fast — see docs/LABS_DATA_UNDERSTANDING.md.
    enqueue_auto_train(row.id)
    return _to_read(row)


def list_uploads(db: Session, user: User, category: str | None = None) -> list[ClientLabUploadRead]:
    stmt = select(ClientLabUpload).where(ClientLabUpload.workspace_id == _workspace_id_for(user))
    if category:
        parsed = _parse_category(category)
        stmt = stmt.where(ClientLabUpload.category == parsed.value)
    stmt = stmt.order_by(ClientLabUpload.created_at.desc()).limit(20)
    return [_to_read(row) for row in db.scalars(stmt)]
