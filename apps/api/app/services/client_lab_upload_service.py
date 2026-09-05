"""Capability 1 of Client Labs open ingest: take any usual data file, no schema.

Capability 2 (understand / structure the file) is deferred — see
docs/LABS_DATA_UNDERSTANDING.md.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

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
from app.engine.data.loaders import infer_schema, load_table
from app.engine.lab.open_ingest import OpenIngestError, OpenIngestPreview, preview_upload_path
from app.services.artifact_service import artifact_object_key, record_artifact
from app.services.auto_train_service import enqueue_auto_train
from app.services.client_upload_insights import insights_for_upload, outcome_for_upload, predictions_csv_text
from app.services.data_source_service import create_data_source
from app.services.dataset_column_service import persist_dataset_columns, schema_digest_from_columns
from app.services.ingestion_run_service import complete_ingestion_run, start_ingestion_run
from app.services.lab_service import seed_dogfood
from app.services.project_service import get_or_create_labs_project, get_project
from app.storage.factory import get_object_storage
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


def _load_preview_frame(dest: Path, kind: str):
    if kind not in {"spreadsheet", "json", "table_file"}:
        return None
    if dest.suffix.lower() not in {".csv", ".parquet", ".pq"}:
        return None
    try:
        return load_table(dest)
    except Exception:
        return None


def _persist_upload_dataset(
    db: Session,
    dest: Path,
    filename: str,
    content_digest: str,
    preview: OpenIngestPreview,
    *,
    workspace_id: UUID,
    requested_by: UUID,
    project_id: UUID,
    ingestion_run_id: UUID,
    artifact_id: UUID,
    size_bytes: int,
) -> Dataset:
    """Persist the physical upload even when it is not yet training-ready."""

    env = seed_dogfood(db)
    from app.services.lineage_service import slugify

    name = (Path(filename).stem or "upload")[:128]
    asset = DatasetAsset(
        workspace_id=workspace_id,
        project_id=project_id,
        name=name,
        slug=f"{slugify(name)}-{uuid4().hex[:8]}",
        created_by=requested_by,
    )
    db.add(asset)
    db.flush()
    fields = list(preview.fields_noticed or [])
    frame = _load_preview_frame(dest, preview.kind)
    schema = infer_schema(frame) if frame is not None else {
        "columns": [
            {"name": field, "dtype": "unknown", "semantic": "unknown"}
            for field in fields
        ],
        "row_count": preview.record_count,
        "column_count": len(fields),
    }
    schema_digest = schema_digest_from_columns(list(schema.get("columns") or []))
    dataset = Dataset(
        workspace_id=workspace_id,
        dataset_asset_id=asset.id,
        environment_id=env.id,
        project_id=project_id,
        ingestion_run_id=ingestion_run_id,
        artifact_id=artifact_id,
        name=name,
        source_type=(dest.suffix.lower().lstrip(".") or preview.kind)[:32],
        location=str(dest),
        version="v1",
        content_digest=content_digest,
        schema_digest=schema_digest,
        size_bytes=size_bytes,
        schema_json=schema,
        row_count=preview.record_count,
        column_count=len(schema.get("columns") or fields),
    )
    db.add(dataset)
    db.flush()
    persist_dataset_columns(
        db,
        workspace_id=workspace_id,
        dataset_id=dataset.id,
        schema=schema,
        frame=frame,
    )
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
    project_id: UUID | None = None,
    problem_spec_id: UUID | None = None,
) -> ClientLabUploadRead:
    parsed_category = _parse_category(category)
    if data is None and upload_stream is None:
        raise ValueError("data or upload_stream is required")
    if data is not None and upload_stream is not None:
        raise ValueError("provide data or upload_stream, not both")

    storage = get_object_storage()
    artifact_id = uuid4()
    filename = filename or "upload"
    object_key = artifact_object_key(workspace_id, artifact_id, filename)
    payload = data if data is not None else upload_stream
    assert payload is not None
    mime_type = mimetypes.guess_type(filename)[0]
    try:
        put = storage.put(object_key, payload, content_type=mime_type)
        local = storage.local_path(put.key)
        if local is None:
            storage.delete(put.key)
            raise OpenLabFileError("Labs uploads require local object storage")
        dest = Path(local)
        preview = preview_upload_path(filename, dest)
    except OpenIngestError as exc:
        storage.delete(object_key)
        raise OpenLabFileError(str(exc)) from exc
    except OpenLabFileError:
        raise
    except Exception:
        storage.delete(object_key)
        raise

    if project_id is not None:
        project = get_project(
            db, actor=user, workspace_id=workspace_id, project_id=project_id
        )
    else:
        project = get_or_create_labs_project(
            db, workspace_id=workspace_id, actor=user
        )
    problem_spec_id_resolved: UUID | None = None
    if problem_spec_id is not None:
        from app.services.problem_spec_service import get_problem_spec

        spec = get_problem_spec(
            db,
            actor=user,
            workspace_id=workspace_id,
            project_id=project.id,
            spec_id=problem_spec_id,
        )
        problem_spec_id_resolved = spec.id
    artifact = record_artifact(
        db,
        artifact_id=artifact_id,
        workspace_id=workspace_id,
        project_id=project.id,
        artifact_type="dataset",
        put=put,
        mime_type=put.content_type or mime_type,
        created_by=user.id,
        extra_metadata={"original_filename": filename, "kind": preview.kind},
    )
    source = create_data_source(
        db,
        workspace_id=workspace_id,
        project_id=project.id,
        name=filename,
        source_type="upload",
        provider=put.provider,
        created_by=user.id,
        configuration={
            "original_filename": filename,
            "kind": preview.kind,
            "object_key": put.key,
            "artifact_id": str(artifact.id),
        },
    )
    ingestion = start_ingestion_run(
        db,
        workspace_id=workspace_id,
        project_id=project.id,
        data_source_id=source.id,
        status="running",
    )

    dataset = _persist_upload_dataset(
        db,
        dest,
        filename,
        put.content_digest,
        preview,
        workspace_id=workspace_id,
        requested_by=user.id,
        project_id=project.id,
        ingestion_run_id=ingestion.id,
        artifact_id=artifact.id,
        size_bytes=put.size_bytes,
    )
    complete_ingestion_run(
        db,
        ingestion,
        rows_read=preview.record_count,
        rows_written=preview.record_count,
        bytes_read=put.size_bytes,
        schema_digest=dataset.schema_digest,
        content_digest=put.content_digest,
    )

    row = ClientLabUpload(
        workspace_id=workspace_id,
        requested_by=user.id,
        category=parsed_category.value,
        original_filename=filename,
        stored_path=str(dest),
        kind=preview.kind,
        record_count=preview.record_count,
        fields_noticed=preview.fields_noticed,
        has_named_fields=preview.has_named_fields,
        explicit_target_column=(target_column or "").strip() or None,
        pipeline_status="queued",
        client_status="queued",
        dataset_id=dataset.id,
        artifact_id=artifact.id,
        data_source_id=source.id,
        ingestion_run_id=ingestion.id,
    )
    db.add(row)
    db.flush()
    from app.services.lineage_service import (
        create_pipeline_run,
        create_workflow_run,
        get_or_create_labs_workflow,
    )

    workflow = get_or_create_labs_workflow(
        db, workspace_id=workspace_id, actor=user, project=project
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
        problem_spec_id=problem_spec_id_resolved,
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
    from app.services.ml_job_service import create_auto_train_job

    create_auto_train_job(
        db,
        workspace_id=workspace_id,
        project_id=project.id,
        upload_id=row.id,
    )
    db.commit()
    db.refresh(row)
    # Build the client payload while the row is still queued. Training starts
    # only when a worker claims the persisted ml_jobs row.
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
