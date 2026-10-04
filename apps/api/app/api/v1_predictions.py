"""/v1 batch predictions (P4.9-A): score a dataset with a model version.

Transport only. ``POST /v1/model-versions/{id}/predictions`` records the request
and answers ``202`` + ``Location`` (Idempotency-Key required); the worker job
``models.batch_predict`` loads the model, never this process. Reads are
workspace-scoped (a foreign id is 404) and never carry object keys, buckets or
providers; the download streams the stored file through the same authorization.
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import request_service_token, request_workspace_id, require_workspace_ml_execution, require_workspace_read
from app.api.v1 import _created, _key_scope, _keyed_command, _not_found, _principal_id
from app.api.v1_conventions import (
    COMMON_ERROR_STATUSES,
    ETAG_HEADER_DOC,
    REPLAY_HEADER_DOC,
    domain_error,
    error_responses,
    idempotency_binding,
    idempotency_key_header,
    representation_etag,
    set_etag,
)
from app.db.models import BatchPrediction, User
from app.db.session import get_db
from app.domain.batch_predictions import BatchPredictionCreateRequest, BatchPredictionRead
from app.domain.errors import ArtifactNotFoundError, ExperimentRequestError, IdentityError, RunQuotaExceededError
from app.domain.idempotency import RESOURCE_BATCH_PREDICTION
from app.services.batch_prediction_service import (
    batch_prediction_download,
    batch_prediction_read,
    create_batch_prediction,
)

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))

_CREATE_PREDICTION = "POST /v1/model-versions/{model_version_id}/predictions"
_ACCEPTED_RESPONSES: dict[int | str, dict[str, Any]] = {
    202: {
        "headers": {
            **ETAG_HEADER_DOC,
            **REPLAY_HEADER_DOC,
            "Location": {"description": "URL of the prediction.", "schema": {"type": "string"}},
        }
    },
    **error_responses(409, 429),
}


def _read(db: Session, user: User, workspace_id: UUID, prediction_id: UUID) -> BatchPredictionRead:
    try:
        body = batch_prediction_read(db, actor=user, workspace_id=workspace_id, prediction_id=prediction_id)
    except IdentityError as exc:
        raise domain_error(exc) from exc
    if body is None:
        raise _not_found("prediction not found")
    return body


@router.post(
    "/model-versions/{model_version_id}/predictions",
    response_model=BatchPredictionRead,
    status_code=202,
    responses=_ACCEPTED_RESPONSES,
)
def create_prediction_v1(
    model_version_id: UUID,
    payload: BatchPredictionCreateRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> BatchPredictionRead:
    """Score a dataset of the model's project (``purpose=scoring`` uploads: no target)
    with this model version: the full fitted pipeline, the run's replayed preparation
    and its locked decision threshold. The worker checks the feature contract first:
    missing required columns fail (``feature_contract_failed``, names in
    ``contract_check``); extra columns and the target column are ignored. Requires
    ``Idempotency-Key``; ``202`` + ``Location``."""

    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(
        operation=_CREATE_PREDICTION, principal_id=_principal_id(request, user), header_key=idempotency_key,
        path_params={"model_version_id": model_version_id}, body=payload.model_dump(mode="json"), required=True,
    )
    token = request_service_token(request)

    def load(resource_id: Any) -> BatchPrediction | None:
        row = db.get(BatchPrediction, resource_id)
        return row if row is not None and row.workspace_id == workspace_id else None

    def execute(bind: Callable[[Any], None]) -> BatchPrediction:
        return create_batch_prediction(
            db, actor=user, workspace_id=workspace_id, model_version_id=model_version_id,
            dataset_id=payload.dataset_id, output_format=payload.output_format,
            before_commit=lambda row: bind(row.id),
            initiated_by_service_token_id=token.id if token is not None else None,
        )

    try:
        row, status, replayed = _keyed_command(
            db, _key_scope(request, user, _CREATE_PREDICTION), binding,
            resource_kind=RESOURCE_BATCH_PREDICTION, load=load, execute=execute, status=202,
        )
    except (IdentityError, ExperimentRequestError, RunQuotaExceededError) as exc:
        db.rollback()
        raise domain_error(exc) from exc
    body = _read(db, user, workspace_id, row.id)
    _created(response, status, replayed, etag=representation_etag(body), location=f"/v1/predictions/{body.id}")
    return body


@router.get(
    "/predictions/{prediction_id}",
    response_model=BatchPredictionRead,
    responses={200: {"headers": ETAG_HEADER_DOC}},
)
def read_prediction(
    prediction_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> BatchPredictionRead:
    """Status, row counts (``rows_out`` equals ``rows_in`` when completed), the
    feature-contract result, the decision threshold applied and the output file."""

    body = _read(db, user, request_workspace_id(request), prediction_id)
    set_etag(response, representation_etag(body))
    return body


@router.get(
    "/predictions/{prediction_id}/download",
    response_class=Response,
    responses={
        200: {
            "description": "The predictions file (CSV or Parquet) as an attachment.",
            "content": {"text/csv": {}, "application/vnd.apache.parquet": {}},
        },
        **error_responses(409),
    },
)
def download_prediction(
    prediction_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> Response:
    """The completed predictions file: one row per input row (``row_number`` 1 = the
    first data row, plus the entity column when the run had one). Binary:
    ``probability`` and ``label`` at the locked threshold; multiclass:
    ``probability_<class>`` and ``label``; regression: ``prediction``. Not completed:
    ``409 prediction_not_ready``."""

    try:
        found = batch_prediction_download(
            db, actor=user, workspace_id=request_workspace_id(request), prediction_id=prediction_id
        )
    except (IdentityError, ExperimentRequestError) as exc:
        raise domain_error(exc) from exc
    except ArtifactNotFoundError as exc:
        raise _not_found("prediction file not found") from exc
    if found is None:
        raise _not_found("prediction not found")
    payload, media_type, filename = found
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )
