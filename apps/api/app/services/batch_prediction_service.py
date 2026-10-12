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

from pydantic import ValidationError
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
    ModelRelease,
    ModelVersion,
    ScoringScheduleRun,
    User,
)
from app.domain.batch_predictions import (
    DATASET_UNAVAILABLE,
    PREDICTION_COMPLETED,
    PREDICTION_FAILED,
    PREDICTION_QUEUED,
    PREDICTION_RUNNING,
    REASONS_NOT_RECORDED,
    REASONS_TURNED_OFF,
    RELEASE_MISMATCH,
    SCHEDULE_OWNER_NOT_ALLOWED,
    SCORING_FAILED,
    TERMINAL_PREDICTION_STATUSES,
    UNSUPPORTED_MODEL,
    BatchPredictionOutput,
    BatchPredictionRead,
    BatchPredictionReasons,
)
from app.domain.data_plane import PREDICTIONS_TYPE
from app.domain.errors import BatchPredictionError, IdentityError
from app.domain.execution_requests import (
    OPERATION_MODEL_BATCH_PREDICT,
    REQUEST_COMPLETED,
    REQUEST_FAILED,
    REQUEST_RUNNING,
    SOURCE_API,
    SOURCE_SYSTEM,
)
from app.domain.ml_jobs import (
    HANDLER_MODELS_BATCH_PREDICT,
    HANDLER_VERSION_MODELS_BATCH_PREDICT,
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_TYPE_BATCH_PREDICT,
)
from app.services.artifact_service import ArtifactTooLargeError, get_artifact, read_artifact_bytes, store_artifact
from app.services.authorization_service import can_perform_ml_write, can_read_workspace
from app.services.model_release_service import current_release, release_summary
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
    scheduled_run_id: UUID | None = None,
) -> BatchPrediction:
    """Queue one scoring of ``dataset_id`` by ``model_version_id``. Commits.

    ``scheduled_run_id`` (``scoring_schedule_service`` only) marks a weekly scheduled scoring:
    it needs the model in use approved for scoring (409 ``release_required`` otherwise, never an
    unapproved model) and records ``source_surface=system`` and the run id on the request.
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
    # P7.2-A: scoring with the model in use carries its approval; any other model scores
    # as before with no release (approval is never claimed for it).
    release = current_release(db, workspace_id=workspace_id, project_id=model_version.project_id)
    if scheduled_run_id is not None and (release is None or release.model_version_id != model_version.id):
        raise BatchPredictionError(
            "release_required", "scheduled scoring needs the model in use, approved for scoring", status_code=409)
    prediction_id = uuid4()
    request = create_execution_request(
        db,
        workspace_id=workspace_id,
        project_id=model_version.project_id,
        operation=OPERATION_MODEL_BATCH_PREDICT,
        source_surface=SOURCE_API if scheduled_run_id is None else SOURCE_SYSTEM,
        requested_by_user_id=actor.id,
        initiated_by_service_token_id=initiated_by_service_token_id,
        request_spec={
            "dataset_id": str(dataset.id),
            "model_version_id": str(model_version.id),
            "output_format": output_format,
            **({} if scheduled_run_id is None else {"scoring_schedule_run_id": str(scheduled_run_id)}),
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
        model_release_id=release.id if release is not None and release.model_version_id == model_version.id else None,
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


def _reasons(db: Session, row: BatchPrediction) -> BatchPredictionReasons | None:
    """P7.7-A: what the predictions file holds about per-row reasons (None until completed);
    the worker records it in the request's result summary."""

    if row.status != PREDICTION_COMPLETED:
        return None
    request = _request_for(db, row)
    summary = request.result_summary if request is not None and isinstance(request.result_summary, dict) else {}
    if isinstance(summary.get("reasons"), dict):
        try:
            return BatchPredictionReasons.model_validate(summary["reasons"])
        except ValidationError:
            logger.warning("batch prediction %s has an unreadable reasons record", row.id)
    return BatchPredictionReasons(available=False, unavailable_reason=REASONS_NOT_RECORDED)


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
    release = _scoped(db, ModelRelease, workspace_id, row.model_release_id)
    current = current_release(db, workspace_id=workspace_id, project_id=row.project_id) if release else None
    slot = db.scalar(select(ScoringScheduleRun.planned_for).where(
        ScoringScheduleRun.workspace_id == workspace_id, ScoringScheduleRun.batch_prediction_id == row.id))
    return BatchPredictionRead(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        model_version_id=row.model_version_id,
        model_release_id=row.model_release_id,
        release=release_summary(release, current_id=current.id if current is not None else None),
        input_dataset_id=row.input_dataset_id,
        execution_request_id=row.execution_request_id,
        status=row.status,
        output_format=row.output_format,
        rows_in=row.rows_in,
        rows_out=row.rows_out,
        decision_threshold=row.decision_threshold,
        contract_check=row.contract_check,
        output=output,
        reasons=_reasons(db, row),
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        scheduled=slot is not None,
        scheduled_for=slot.astimezone(UTC) if slot is not None else None,
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


def _recheck_scheduled(db: Session, row: BatchPrediction) -> None:
    """A weekly scheduled scoring (it has a ``scoring_schedule_runs`` row) runs only while its
    approval is still the model in use and the person it acts as can still score; otherwise it
    fails before anything is loaded. Scoring by hand keeps the checks above only."""

    from app.engine.serving.batch_scoring import ScoringError

    scheduled = db.scalar(select(ScoringScheduleRun.id).where(
        ScoringScheduleRun.workspace_id == row.workspace_id, ScoringScheduleRun.batch_prediction_id == row.id))
    if scheduled is None:
        return
    current = current_release(db, workspace_id=row.workspace_id, project_id=row.project_id)
    if row.model_release_id is None or current is None or current.id != row.model_release_id:
        raise ScoringError(RELEASE_MISMATCH, "the model in use changed before this scheduled run started")
    person = db.get(User, row.requested_by_user_id) if row.requested_by_user_id is not None else None
    if person is None or not person.is_active or not can_perform_ml_write(db, person, row.workspace_id):
        raise ScoringError(SCHEDULE_OWNER_NOT_ALLOWED, "the person who set up this schedule can no longer score")


def _score(db: Session, row: BatchPrediction, on_heartbeat: Callable[[], None] | None) -> tuple[Any, Any, int, str]:
    """(ScoredFrame, ScoringSpec, rows_in, saved format) for this row. Raises ``ScoringError``."""

    from app.engine.data.loaders import load_table
    from app.engine.serving.batch_scoring import ScoringError, score_frame, scoring_spec
    from app.engine.serving.model_package import (
        PACKAGE_MAX_BYTES,
        ModelPackageError,
        check_manifest_contract,
        saved_format,
    )
    from app.engine.serving.package_loader import load_model  # worker only (P7.1-A)
    from app.services.dataset_materialization import materialize_dataset

    workspace_id = row.workspace_id
    model_version = _scoped(db, ModelVersion, workspace_id, row.model_version_id)
    experiment = model_version and _scoped(db, Experiment, workspace_id, model_version.pipeline_run_id)
    candidate = model_version and _scoped(db, ExperimentCandidate, workspace_id, model_version.selected_candidate_id)
    artifact = model_version and _scoped(db, Artifact, workspace_id, model_version.model_artifact_id)
    message = "this model version's stored pipeline cannot be used for batch scoring"
    manifest = model_version and _scoped(db, Artifact, workspace_id, model_version.feature_manifest_artifact_id)
    release = None
    mismatch = "the stored model no longer matches the version approved for scoring"
    if row.model_release_id is not None:
        # P7.2-A, no TOCTOU: the approval this scoring was queued with must still name this
        # version and pin exactly the package manifest AND the saved format (the loader branches
        # on the artifact's package format) the worker is about to load.
        release = _scoped(db, ModelRelease, workspace_id, row.model_release_id)
        if (model_version is None or release is None or release.model_version_id != model_version.id
                or manifest is None or manifest.content_digest != release.package_manifest_digest
                or artifact is None or saved_format(artifact.extra_metadata) != release.saved_format):
            raise ScoringError(RELEASE_MISMATCH, mismatch)
    _recheck_scheduled(db, row)
    if experiment is None or candidate is None or artifact is None or artifact.artifact_type != "model":
        raise ScoringError(UNSUPPORTED_MODEL, message)
    result = experiment.result if isinstance(experiment.result, dict) else {}
    # Same winner rule as persistence (auto_train/persistence.py) when it created the version.
    selected = (result.get("selection") or {}).get("selected_candidate_id") or (
        result.get("best_single") or {}
    ).get("candidate_id")
    if experiment.scientific_evidence_locked_at is None or selected != candidate.candidate_key:
        raise ScoringError(UNSUPPORTED_MODEL, message)  # the evidence must describe this model
    if not 0 < int(artifact.size_bytes or 0) <= PACKAGE_MAX_BYTES:  # before reading the object
        raise ScoringError(UNSUPPORTED_MODEL, message)
    try:  # streamed with a byte cap: the stored object is never read past the limit
        payload = read_artifact_bytes(db, workspace_id=workspace_id, artifact_id=artifact.id, max_bytes=PACKAGE_MAX_BYTES)
    except ArtifactTooLargeError as exc:
        raise ScoringError(UNSUPPORTED_MODEL, message) from exc
    # P7.1-A: the loader checks the bytes against the artifact digest, a package against the
    # manifest digest the version references; a pre-package pickle is quarantined.
    loaded = load_model(
        payload,
        expected_digest=artifact.content_digest,
        package_format=(artifact.extra_metadata or {}).get("package_format"),
        manifest_digest=manifest.content_digest if manifest else None,
    )
    if release is not None and loaded.saved_format != release.saved_format:
        raise ScoringError(RELEASE_MISMATCH, mismatch)  # what was loaded is not what was approved
    if loaded.saved_format != "package_v1":
        logger.warning(
            "batch prediction %s loaded model version %s (workspace %s) from the QUARANTINED legacy pickle "
            "format because LEGACY_PICKLE_MODELS_ENABLED allows it; train the model again to replace it",
            row.id, model_version.id, workspace_id,
        )
    spec = scoring_spec(result, loaded.pipeline)
    if loaded.manifest is not None:
        try:
            check_manifest_contract(loaded.manifest, spec)
        except ModelPackageError as exc:
            raise ScoringError(UNSUPPORTED_MODEL, "the stored model does not match its recorded feature contract") from exc
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
    from app.config import get_settings

    # P7.7-A: per-row reasons are computed after the scores, from the same loaded model and rows.
    settings = get_settings()
    scored = score_frame(frame, spec, loaded.pipeline, on_progress=on_heartbeat,
                         reasons=settings.batch_reasons_enabled,
                         reasons_deadline_seconds=settings.batch_reasons_deadline_seconds)
    return scored, spec, int(len(frame)), loaded.saved_format


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
        scored, spec, rows_in, saved_format = _score(db, row, on_heartbeat)
        data, mime_type, extension = serialize_predictions(scored.predictions, row.output_format)
    except ScoringError as exc:
        db.rollback()
        from app.engine.serving.model_package import failure_chain

        log = logger.warning if exc.code == UNSUPPORTED_MODEL else logger.info  # server-side cause chain
        log("batch prediction %s failed: %s (%s)", row.id, exc.code, failure_chain(exc.__cause__))
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
        from app.engine.explain import unavailable_summary

        reasons = scored.reasons or unavailable_summary(REASONS_TURNED_OFF)
        request.status = REQUEST_COMPLETED
        request.completed_at = now
        request.result_summary = {
            "batch_prediction_id": str(row.id),
            "rows_in": row.rows_in,
            "rows_out": row.rows_out,
            "model_format": saved_format,
            "reasons_method": reasons["method"] or "unavailable",  # P7.7-A
            "reasons": reasons,
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

