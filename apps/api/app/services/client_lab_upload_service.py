"""Capability 1 of Client Labs open ingest: take any usual data file, no schema.

Capability 2 (understand / structure the file) is deferred — see
docs/LABS_DATA_UNDERSTANDING.md.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.db.models import ClientLabUpload, Dataset, DatasetAsset, User
from app.domain.client_lab import ClientLabUploadRead
from app.domain.errors import OpenLabFileError, UnknownLabCategoryError
from app.domain.lab_run_stages import (
    IN_PROGRESS_STAGES,
    PROCESSING_HEADLINE,
    client_error_message,
    client_stage,
    headline,
    lifecycle_status,
    milestone_for,
    public_pipeline_status,
    steps_for,
)
from app.engine.lab.open_ingest import OpenIngestError, OpenIngestPreview, preview_upload_path
from app.services.auto_train_service import enqueue_auto_train
from app.services.client_upload_insights import insights_for_upload, outcome_for_upload, predictions_csv_text
from app.services.lab_service import seed_dogfood
from app.translation.models import InsightCategory

SAVED_MESSAGE = (
    "We saved your file. Turning unstructured files into a usable table is not available yet."
)
LOOKING_MESSAGE = PROCESSING_HEADLINE
READY_MESSAGE = "We've looked at your file."


def _progress(row: ClientLabUpload) -> str:
    """Map the admin-only pipeline status to a three-word client progress label."""
    if row.pipeline_status in IN_PROGRESS_STAGES:
        return "looking"
    if row.pipeline_status == "completed":
        return "ready"
    return "saved"


def _parse_category(value: str) -> InsightCategory:
    try:
        return InsightCategory(value)
    except ValueError as exc:
        raise UnknownLabCategoryError(f"{value!r} is not a Labs category") from exc


def _persist_upload_dataset(
    db: Session,
    dest: Path,
    filename: str,
    content_digest: str,
    preview: OpenIngestPreview,
    *,
    workspace_id: UUID,
    requested_by: UUID,
) -> Dataset:
    """Persist the physical upload even when it is not yet training-ready."""

    env = seed_dogfood(db)
    from app.services.lineage_service import slugify

    name = (Path(filename).stem or "upload")[:128]
    asset = DatasetAsset(
        workspace_id=workspace_id,
        name=name,
        slug=f"{slugify(name)}-{uuid4().hex[:8]}",
        created_by=requested_by,
    )
    db.add(asset)
    db.flush()
    fields = list(preview.fields_noticed or [])
    dataset = Dataset(
        workspace_id=workspace_id,
        dataset_asset_id=asset.id,
        environment_id=env.id,
        name=name,
        source_type=(dest.suffix.lower().lstrip(".") or preview.kind)[:32],
        location=str(dest),
        version="v1",
        content_digest=content_digest,
        schema_json={
            "columns": [
                {"name": field, "dtype": "unknown", "semantic": "unknown"}
                for field in fields
            ],
            "row_count": preview.record_count,
            "column_count": len(fields),
        },
        row_count=preview.record_count,
        column_count=len(fields),
    )
    db.add(dataset)
    db.flush()
    return dataset


def _to_read(
    db: Session, row: ClientLabUpload, *, include_predictions: bool = False
) -> ClientLabUploadRead:
    view = insights_for_upload(db, row)
    insights = list(view.insights)
    progress = _progress(row)
    outcome = outcome_for_upload(db, row, include_predictions=include_predictions)
    coarse = row.client_status or lifecycle_status(row.pipeline_status)
    if coarse in {"queued", "processing"}:
        message = headline(row.pipeline_status) or PROCESSING_HEADLINE
    elif coarse == "failed":
        log = row.pipeline_log if isinstance(row.pipeline_log, dict) else {}
        reason = log.get("reason")
        message = client_error_message(reason if isinstance(reason, str) else None)
    elif outcome is not None:
        message = outcome.title
    else:
        message = view.status
    return ClientLabUploadRead(
        id=row.id,
        run_id=row.run_id,
        dataset_id=row.dataset_id,
        status=coarse,
        stage=client_stage(row.pipeline_status),
        headline=headline(row.pipeline_status),
        milestone=milestone_for(row.pipeline_status),
        steps=steps_for(row.pipeline_status),
        category=InsightCategory(row.category),
        filename=row.original_filename,
        kind=row.kind,
        record_count=row.record_count,
        fields_noticed=list(row.fields_noticed or []),
        has_named_fields=row.has_named_fields,
        structured=bool(insights) or outcome is not None,
        progress=progress,
        message=message,
        pipeline_status=public_pipeline_status(row.pipeline_status),
        insights=insights,
        outcome=outcome,
        created_at=row.created_at,
    )


def save_upload(
    db: Session,
    *,
    user: User,
    category: str,
    filename: str,
    data: bytes | None = None,
    upload_stream: BinaryIO | None = None,
    target_column: str | None = None,
    workspace_id: UUID,
) -> ClientLabUploadRead:
    parsed_category = _parse_category(category)
    if data is None and upload_stream is None:
        raise ValueError("data or upload_stream is required")
    if data is not None and upload_stream is not None:
        raise ValueError("provide data or upload_stream, not both")

    dest_dir = REPO_ROOT / "data" / "uploads" / "client_labs" / str(workspace_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = ""
    if "." in (filename or ""):
        suffix = "." + filename.rsplit(".", 1)[-1].lower()[:12]
    dest = dest_dir / f"{uuid4().hex}{suffix}"
    digest = hashlib.sha256()
    try:
        with dest.open("wb") as destination:
            if upload_stream is not None:
                while chunk := upload_stream.read(1024 * 1024):
                    destination.write(chunk)
                    digest.update(chunk)
            else:
                assert data is not None
                destination.write(data)
                digest.update(data)
        preview = preview_upload_path(filename, dest)
    except OpenIngestError as exc:
        dest.unlink(missing_ok=True)
        raise OpenLabFileError(str(exc)) from exc
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    dataset = _persist_upload_dataset(
        db,
        dest,
        filename or "upload",
        digest.hexdigest(),
        preview,
        workspace_id=workspace_id,
        requested_by=user.id,
    )

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
        explicit_target_column=(target_column or "").strip() or None,
        pipeline_status="queued",
        client_status="queued",
        dataset_id=dataset.id,
    )
    db.add(row)
    db.flush()
    from app.services.lineage_service import (
        create_pipeline_run,
        create_workflow_run,
        get_or_create_labs_workflow,
    )

    workflow = get_or_create_labs_workflow(
        db, workspace_id=workspace_id, actor=user
    )
    workflow_run = create_workflow_run(
        db,
        workspace_id=workspace_id,
        workflow=workflow,
        requester=user,
        trigger_type="upload",
        source_type=preview.kind,
        source_upload=row,
        explicit_target=(target_column or "").strip() or None,
        inputs=[(dataset, "reference")],
    )
    pipeline_run = create_pipeline_run(
        db,
        workflow_run=workflow_run,
        environment=dataset.environment,
        dataset=dataset,
        task=None,
        pipeline_name="open_ingest_deterministic_ml",
        pipeline_index=0,
        pipeline_purpose="training_and_scoring",
        input_role=None,
        commit=False,
    )
    row.experiment_id = pipeline_run.id
    db.commit()
    db.refresh(row)
    # Build the client payload while the row is still queued. The background job
    # must not start until this snapshot exists — otherwise POST can return a
    # later pipeline_status.
    payload = _to_read(db, row)
    enqueue_auto_train(row.id)
    return payload


def list_uploads(
    db: Session,
    user: User,
    category: str | None = None,
    *,
    workspace_id: UUID,
) -> list[ClientLabUploadRead]:
    stmt = select(ClientLabUpload).where(
        ClientLabUpload.workspace_id == workspace_id
    )
    if category:
        parsed = _parse_category(category)
        stmt = stmt.where(ClientLabUpload.category == parsed.value)
    stmt = stmt.order_by(ClientLabUpload.created_at.desc()).limit(20)
    return [_to_read(db, row) for row in db.scalars(stmt)]


def _upload_for_workspace(
    db: Session,
    user: User,
    upload_id: UUID,
    *,
    workspace_id: UUID,
) -> ClientLabUpload | None:
    row = db.get(ClientLabUpload, upload_id)
    if row is None:
        row = db.scalars(select(ClientLabUpload).where(ClientLabUpload.run_id == upload_id)).first()
    if row is None or row.workspace_id != workspace_id:
        return None
    return row


def get_upload(
    db: Session,
    user: User,
    upload_id: UUID,
    *,
    workspace_id: UUID,
) -> ClientLabUploadRead | None:
    row = _upload_for_workspace(
        db, user, upload_id, workspace_id=workspace_id
    )
    if row is None:
        return None
    return _to_read(db, row, include_predictions=True)


def predictions_download(
    db: Session,
    user: User,
    upload_id: UUID,
    *,
    workspace_id: UUID,
) -> tuple[str, str] | None:
    """Filename and CSV body for the completed run's predictions, or None."""
    row = _upload_for_workspace(
        db, user, upload_id, workspace_id=workspace_id
    )
    if row is None:
        return None
    outcome = outcome_for_upload(db, row, include_predictions=True)
    if outcome is None or not outcome.predictions:
        return None
    stem = Path(row.original_filename or "predictions").stem or "predictions"
    filename = f"{stem}-predictions.csv"
    return filename, predictions_csv_text(outcome)
