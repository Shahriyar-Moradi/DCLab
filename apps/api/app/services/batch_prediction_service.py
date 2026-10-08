"""Batch predictions (P4.9-A): create, read and download (API); run (worker only).

Create records intent in one transaction: the ``batch_predictions`` row, its
``ExecutionRequest`` (``model_batch_predict``) and a ``models.batch_predict``
``ml_jobs`` row, dispatched exactly like auto-train (production: a worker claims
the row). The API process never loads a model: ``run_batch_prediction_job`` is
reached only through the worker handler and imports the scoring engine lazily.
Every query is workspace-scoped; a foreign model version or dataset is "not found".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    BatchPrediction,
    Dataset,
    ExecutionRequest,
    Experiment,
    ExperimentCandidate,
    MlJob,
    ModelVersion,
    User,
)
from app.domain.batch_predictions import (
    DATASET_UNAVAILABLE,
    PREDICTION_COMPLETED,
    PREDICTION_FAILED,
    PREDICTION_QUEUED,
    PREDICTION_RUNNING,
    SCORING_FAILED,
    TERMINAL_PREDICTION_STATUSES,
    UNSUPPORTED_MODEL,
    BatchPredictionOutput,
    BatchPredictionRead,
)
from app.domain.data_plane import PREDICTIONS_TYPE
from app.domain.errors import BatchPredictionError, IdentityError
from app.domain.execution_requests import (
    OPERATION_MODEL_BATCH_PREDICT,
    REQUEST_COMPLETED,
    REQUEST_FAILED,
    REQUEST_RUNNING,
    SOURCE_API,
)
from app.domain.ml_jobs import (
    HANDLER_MODELS_BATCH_PREDICT,
    HANDLER_VERSION_MODELS_BATCH_PREDICT,
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_TYPE_BATCH_PREDICT,
)
from app.services.artifact_service import get_artifact, read_artifact_bytes, store_artifact
from app.services.authorization_service import can_perform_ml_write, can_read_workspace
from app.storage._hashing import sha256_bytes

logger = logging.getLogger(__name__)

_RETRIES_EXHAUSTED = "scoring failed; create a new prediction to retry"


def _now() -> datetime:
    return datetime.now(UTC)


def _row(db: Session, workspace_id: UUID, prediction_id: UUID, *, lock: bool = False) -> BatchPrediction | None:
    query = select(BatchPrediction).where(
        BatchPrediction.id == prediction_id, BatchPrediction.workspace_id == workspace_id
    )
    return db.scalar(query.with_for_update() if lock else query)


def _scoped(db: Session, model: Any, workspace_id: UUID, row_id: UUID | None) -> Any:
    if row_id is None:
        return None
    return db.scalar(select(model).where(model.id == row_id, model.workspace_id == workspace_id))


# --- create (API) --------------------------------------------------------------------


def create_batch_prediction(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    model_version_id: UUID,
    dataset_id: UUID,
    output_format: str = "csv",
    before_commit: Callable[[BatchPrediction], None] | None = None,
    initiated_by_service_token_id: UUID | None = None,
) -> BatchPrediction:
    """Queue one scoring of ``dataset_id`` by ``model_version_id``. Commits.

    Raises ``IdentityError`` (403), ``BatchPredictionError`` / ``ExperimentRequestError``
    (404/409/422) and ``RunQuotaExceededError`` (429).
    """

    from app.services.execution_request_service import create_execution_request
    from app.services.experiment_service import uploaded_dataset
    from app.services.job_dispatcher import get_job_dispatcher
    from app.services.ml_job_service import create_ml_job, ensure_run_capacity

    if not can_perform_ml_write(db, actor, workspace_id):
        raise IdentityError("scoring requires an ML-write workspace role", status_code=403)
    model_version = _scoped(db, ModelVersion, workspace_id, model_version_id)
    if model_version is None:
        raise BatchPredictionError("not_found", "model version not found", status_code=404)
    experiment = _scoped(db, Experiment, workspace_id, model_version.pipeline_run_id)
    if model_version.model_artifact_id is None or experiment is None or experiment.scientific_evidence_locked_at is None:
        raise BatchPredictionError(
            "model_not_scoreable", "this model version has no locked, stored model to score with", status_code=409
        )
    dataset = uploaded_dataset(db, workspace_id, model_version.project_id, dataset_id)
    # ADR 0008 §2b: refuse the file the model was trained on, by id and by identical content digest. Limit:
    # a shuffled, re-encoded or column-extended copy has another digest and is NOT caught here; matching
    # scored rows against the training rows is a worker-side follow-up. This is not a holdout guarantee.
    source = _scoped(db, Dataset, workspace_id, experiment.source_dataset_id) if experiment.source_dataset_id else None
    if source is not None and (dataset.id == source.id or (
            dataset.content_digest and dataset.content_digest == source.content_digest)):
        raise BatchPredictionError(
            "training_dataset_not_scoreable", "score new data, not the dataset this model was trained on",
            status_code=409)
    ensure_run_capacity(db, workspace_id)  # one shared limit for training and scoring jobs
    prediction_id = uuid4()
    request = create_execution_request(
        db,
        workspace_id=workspace_id,
        project_id=model_version.project_id,
        operation=OPERATION_MODEL_BATCH_PREDICT,
        source_surface=SOURCE_API,
        requested_by_user_id=actor.id,
        initiated_by_service_token_id=initiated_by_service_token_id,
        request_spec={
            "dataset_id": str(dataset.id),
            "model_version_id": str(model_version.id),
            "output_format": output_format,
        },
    )
    job = create_ml_job(
        db,
        workspace_id=workspace_id,
        project_id=model_version.project_id,
        execution_request_id=request.id,
        job_type=JOB_TYPE_BATCH_PREDICT,
        handler_key=HANDLER_MODELS_BATCH_PREDICT,
        handler_version=HANDLER_VERSION_MODELS_BATCH_PREDICT,
        target_id=prediction_id,
        payload={"batch_prediction_id": str(prediction_id)},
    )
    row = BatchPrediction(
        id=prediction_id,
        workspace_id=workspace_id,
        project_id=model_version.project_id,
        model_version_id=model_version.id,
        input_dataset_id=dataset.id,
        execution_request_id=request.id,
        ml_job_id=job.id,
        requested_by_user_id=actor.id,
        initiated_by_service_token_id=initiated_by_service_token_id,
        status=PREDICTION_QUEUED,
        output_format=output_format,
    )
    db.add(row)
    db.flush()
    if before_commit is not None:
        before_commit(row)
    db.commit()
    get_job_dispatcher().dispatch(prediction_id, job.id)
    return row


# --- read / download (API) -----------------------------------------------------------


def _require_read(db: Session, actor: User, workspace_id: UUID) -> None:
    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)


def batch_prediction_read(
    db: Session, *, actor: User, workspace_id: UUID, prediction_id: UUID
) -> BatchPredictionRead | None:
    """This workspace's prediction (None when unknown or another tenant's). Output
    artifacts are id + digest only, never object keys, buckets or providers."""

    _require_read(db, actor, workspace_id)
    row = _row(db, workspace_id, prediction_id)
    if row is None:
        return None
    artifact = _scoped(db, Artifact, workspace_id, row.output_artifact_id)
    output = None
    if artifact is not None:
        output = BatchPredictionOutput(
            artifact_id=artifact.id,
            content_digest=artifact.content_digest,
            size_bytes=artifact.size_bytes,
            mime_type=artifact.mime_type,
            download_path=f"/v1/predictions/{row.id}/download",
        )
    return BatchPredictionRead(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        model_version_id=row.model_version_id,
        model_release_id=row.model_release_id,
        input_dataset_id=row.input_dataset_id,
        execution_request_id=row.execution_request_id,
        status=row.status,
        output_format=row.output_format,
        rows_in=row.rows_in,
        rows_out=row.rows_out,
        decision_threshold=row.decision_threshold,
        contract_check=row.contract_check,
        output=output,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def batch_prediction_download(
    db: Session, *, actor: User, workspace_id: UUID, prediction_id: UUID
) -> tuple[bytes, str, str] | None:
    """(bytes, mime type, filename) of a completed prediction; None when unknown."""

    _require_read(db, actor, workspace_id)
    row = _row(db, workspace_id, prediction_id)
    if row is None:
        return None
    if row.status != PREDICTION_COMPLETED or row.output_artifact_id is None:
        raise BatchPredictionError(
            "prediction_not_ready", f"the prediction is {row.status}; its file exists once it completes",
            status_code=409,
        )
    artifact = get_artifact(db, workspace_id=workspace_id, artifact_id=row.output_artifact_id)
    payload = read_artifact_bytes(db, workspace_id=workspace_id, artifact_id=artifact.id)
    if sha256_bytes(payload) != artifact.content_digest:
        raise BatchPredictionError("prediction_output_changed", "the stored predictions file changed", status_code=409)
    return payload, artifact.mime_type or "application/octet-stream", f"predictions-{row.id}.{row.output_format}"


# --- worker --------------------------------------------------------------------------


def _request_for(db: Session, row: BatchPrediction) -> ExecutionRequest | None:
    return _scoped(db, ExecutionRequest, row.workspace_id, row.execution_request_id)


def _mark_failed(db: Session, row: BatchPrediction, code: str, message: str, contract: dict | None = None) -> None:
    now = _now()
    row.status = PREDICTION_FAILED
    row.error_code = code
    row.error_message = message[:1024]
    row.contract_check = contract
    row.started_at = row.started_at or now
    row.completed_at = now
    request = _request_for(db, row)
    if request is not None:
        request.status = REQUEST_FAILED
        request.failure_code = code[:64]
        request.failure_summary = message[:2048]
        request.completed_at = now
    db.flush()


def _score(db: Session, row: BatchPrediction, on_heartbeat: Callable[[], None] | None) -> tuple[Any, Any, int]:
    """(ScoredFrame, ScoringSpec, rows_in) for this row. Raises ``ScoringError``."""

    from app.engine.data.loaders import load_table
    from app.engine.serving.batch_scoring import ScoringError, load_pipeline, score_frame, scoring_spec
    from app.services.dataset_materialization import materialize_dataset

    workspace_id = row.workspace_id
    model_version = _scoped(db, ModelVersion, workspace_id, row.model_version_id)
    experiment = model_version and _scoped(db, Experiment, workspace_id, model_version.pipeline_run_id)
    candidate = model_version and _scoped(db, ExperimentCandidate, workspace_id, model_version.selected_candidate_id)
    artifact = model_version and _scoped(db, Artifact, workspace_id, model_version.model_artifact_id)
    message = "this model version's stored pipeline cannot be used for batch scoring"
    if experiment is None or candidate is None or artifact is None or artifact.artifact_type != "model":
        raise ScoringError(UNSUPPORTED_MODEL, message)
    result = experiment.result if isinstance(experiment.result, dict) else {}
    # Same winner rule as persistence (auto_train/persistence.py) when it created the version.
    selected = (result.get("selection") or {}).get("selected_candidate_id") or (
        result.get("best_single") or {}
    ).get("candidate_id")
    if experiment.scientific_evidence_locked_at is None or selected != candidate.candidate_key:
        raise ScoringError(UNSUPPORTED_MODEL, message)  # the evidence must describe this model
    payload = read_artifact_bytes(db, workspace_id=workspace_id, artifact_id=artifact.id)
    if sha256_bytes(payload) != artifact.content_digest:
        raise ScoringError(UNSUPPORTED_MODEL, "the stored model does not match its recorded digest")
    pipeline = load_pipeline(payload)
    spec = scoring_spec(result, pipeline)
    # Re-check at run time: the dataset may have been unpublished or removed since it was queued.
    from app.services.experiment_service import ExperimentRequestError, uploaded_dataset

    try:
        dataset = uploaded_dataset(db, workspace_id, row.project_id, row.input_dataset_id)
    except ExperimentRequestError as exc:
        raise ScoringError(DATASET_UNAVAILABLE, "the input dataset is no longer available for scoring") from exc
    with materialize_dataset(dataset, db=db) as path:
        try:
            frame = load_table(path)
        except ValueError as exc:  # parser / decoding errors: deterministic, never retried
            raise ScoringError(SCORING_FAILED, "the input file could not be read as a table") from exc
    if on_heartbeat is not None:
        on_heartbeat()
    return score_frame(frame, spec, pipeline, on_progress=on_heartbeat), spec, int(len(frame))


def run_batch_prediction_job(db: Session, job: MlJob, *, on_heartbeat: Callable[[], None] | None = None) -> None:
    """Worker handler body. Deterministic failures (feature contract, unreplayable
    transform, unusable model) are recorded on the row and returned; anything else
    raises so ``execute_job`` retries it (``sync_prediction_with_job`` follows)."""

    from app.engine.serving.batch_scoring import ScoringError, serialize_predictions

    row = _row(db, job.workspace_id, job.target_id, lock=True)
    if row is None or row.status in TERMINAL_PREDICTION_STATUSES:
        db.rollback()
        return
    now = _now()
    row.status = PREDICTION_RUNNING
    row.started_at = row.started_at or now
    request = _request_for(db, row)
    if request is not None:
        request.status = REQUEST_RUNNING
        request.started_at = request.started_at or now
    db.commit()
    try:
        scored, spec, rows_in = _score(db, row, on_heartbeat)
        data, mime_type, extension = serialize_predictions(scored.predictions, row.output_format)
    except ScoringError as exc:
        db.rollback()
        logger.info("batch prediction %s failed: %s", row.id, exc.code)
        _mark_failed(db, row, exc.code, exc.public_message, exc.contract)
        db.commit()
        return
    artifact = store_artifact(
        db,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        artifact_type=PREDICTIONS_TYPE,
        filename=f"predictions-{row.id}.{extension}",
        data=data,
        mime_type=mime_type,
        created_by=row.requested_by_user_id,
        extra_metadata={"role": "batch_predictions", "batch_prediction_id": str(row.id)},
    )
    now = _now()
    row.status = PREDICTION_COMPLETED
    row.rows_in = rows_in
    row.rows_out = int(len(scored.predictions))
    row.contract_check = scored.contract
    row.decision_threshold = spec.decision_threshold
    row.output_artifact_id = artifact.id
    row.completed_at = now
    request = _request_for(db, row)
    if request is not None:
        request.status = REQUEST_COMPLETED
        request.completed_at = now
        request.result_summary = {
            "batch_prediction_id": str(row.id),
            "rows_in": row.rows_in,
            "rows_out": row.rows_out,
        }
    db.commit()


def sync_prediction_with_job(db: Session, job: MlJob) -> None:
    """After a job failure/retry/cancel outside the handler: a requeued job's row is
    ``queued`` again; a failed or cancelled one is ``failed``. Flushes only."""

    row = _row(db, job.workspace_id, job.target_id)
    if row is None or row.status in TERMINAL_PREDICTION_STATUSES:
        return
    if job.status in {JOB_FAILED, JOB_CANCELLED}:
        _mark_failed(db, row, SCORING_FAILED, _RETRIES_EXHAUSTED)
    elif job.status == JOB_QUEUED:
        row.status = PREDICTION_QUEUED
        db.flush()


def prediction_failure_code(db: Session, job: MlJob) -> str | None:
    """None when the handler finished the row (or it is gone); else why the job fails."""

    row = _row(db, job.workspace_id, job.target_id)
    if row is None or row.status == PREDICTION_COMPLETED:
        return None
    if row.status == PREDICTION_FAILED:
        return row.error_code or SCORING_FAILED
    _mark_failed(db, row, SCORING_FAILED, _RETRIES_EXHAUSTED)
    return SCORING_FAILED

