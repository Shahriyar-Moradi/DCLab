"""/v1 monitoring (P7.4-A): weekly windows of the model in use, drift, and outcomes when linked.

Transport only. Reads are workspace-scoped (another tenant's project is 404) and readable by
service tokens with ``read``; they carry aggregates and plain sentences, never rows, object
keys or holdout figures. Linking a file of real outcomes is a person's act (ML-write role,
``Idempotency-Key``); service tokens and agents get ``403``. The worker computes windows;
nothing here retrains, switches a model or acts.
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id, require_workspace_ml_execution, require_workspace_read
from app.api.v1 import _created, _key_scope, _keyed_command, _not_found, _principal_id
from app.api.v1_agent_views import is_agent
from app.api.v1_conventions import (
    COMMON_ERROR_STATUSES,
    ETAG_HEADER_DOC,
    REPLAY_HEADER_DOC,
    V1APIError,
    domain_error,
    error_responses,
    idempotency_binding,
    idempotency_key_header,
    representation_etag,
)
from app.db.models import MonitoringOutcomeSet, User
from app.db.session import get_db
from app.domain.errors import DecisionRecordError, ExperimentRequestError, IdentityError, ProjectNotFoundError
from app.domain.monitoring import (
    MonitoringOverviewRead,
    MonitoringWindowRead,
    OutcomeSetCreateRequest,
    OutcomeSetList,
    OutcomeSetRead,
)
from app.services import monitoring_service as ms

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))

_LINK_OUTCOMES = "POST /v1/projects/{project_id}/monitoring/outcomes"
_NO_STORE = {"Cache-Control": "private, no-store"}


def _call(fn: Callable[[], Any], missing: str) -> Any:
    try:
        body = fn()
    except ProjectNotFoundError as exc:
        raise _not_found("project not found") from exc
    except (IdentityError, ExperimentRequestError) as exc:
        raise domain_error(exc) from exc
    if body is None:
        raise _not_found(missing)
    return body


@router.get("/projects/{project_id}/monitoring", response_model=MonitoringOverviewRead)
def read_monitoring(
    project_id: UUID,
    request: Request,
    response: Response,
    model_version_id: UUID | None = Query(default=None, description="Another version of this project (default: the model in use)."),
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> MonitoringOverviewRead:
    """The model in use: latest window status (``ok`` / ``watch`` / ``alert`` / ``not_enough_data``)
    and the last 12 weekly windows (Monday 00:00 UTC), newest first, with the code-owned
    thresholds. An ``alert`` window carries one plain finding. Drift means the inputs look
    different from training, not that the model is wrong; performance appears only when outcomes
    are linked. ``no_model_in_use`` / ``no_windows`` before there is anything to show."""

    response.headers.update(_NO_STORE)
    return _call(lambda: ms.monitoring_overview(db, actor=user, workspace_id=request_workspace_id(request),
                                                project_id=project_id, model_version_id=model_version_id),
                 "project not found")


@router.get("/projects/{project_id}/monitoring/windows/{window_start}", response_model=MonitoringWindowRead)
def read_monitoring_window(
    project_id: UUID,
    request: Request,
    response: Response,
    window_start: str = Path(max_length=40, description="The window's Monday, YYYY-MM-DD (UTC)."),
    model_version_id: UUID | None = Query(default=None, description="Another version of this project (default: the model in use)."),
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> MonitoringWindowRead:
    """One window (highest revision): PSI per feature for the top 20 drifting features with a plain
    sentence each (built from numbers, never a cause), score drift and the flagged share vs
    cross-validation, and outcomes (coverage, labelled rows, metrics on joined rows) when linked.
    A date that is not a window's Monday, or a week without a window, is 404."""

    response.headers.update(_NO_STORE)
    return _call(lambda: ms.monitoring_window(db, actor=user, workspace_id=request_workspace_id(request),
                                              project_id=project_id, window_start=window_start,
                                              model_version_id=model_version_id),
                 "monitoring window not found")


@router.get("/projects/{project_id}/monitoring/outcomes", response_model=OutcomeSetList)
def read_monitoring_outcomes(
    project_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> OutcomeSetList:
    """Linked files of real outcomes, newest first (at most 50). Column and file names are
    untrusted text; service tokens get ``linked_by_user_id`` null."""

    response.headers.update(_NO_STORE)
    return _call(lambda: ms.list_outcome_sets(db, actor=user, workspace_id=request_workspace_id(request),
                                              project_id=project_id, agent=is_agent(request)),
                 "project not found")


@router.post(
    "/projects/{project_id}/monitoring/outcomes",
    response_model=OutcomeSetRead,
    status_code=201,
    responses={
        201: {"headers": {**ETAG_HEADER_DOC, **REPLAY_HEADER_DOC,
                          "Location": {"description": "URL of the linked outcome files.", "schema": {"type": "string"}}}},
        **error_responses(409, 429),
    },
)
def link_monitoring_outcomes(
    project_id: UUID,
    payload: OutcomeSetCreateRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> OutcomeSetRead:
    """Link an uploaded file of real outcomes (``POST /v1/datasets``, this project) by its key
    column (the same values as the scored rows' entity column) and outcome column; the model in
    use's recent weeks are recomputed by the worker. Writes an accepted
    ``monitoring_outcomes_linked`` History entry. Outcomes are real results of scored rows, never
    the final test set, and never choose or retrain a model. People with ML-write only: a service
    token gets ``403 service_token_not_permitted``, any agent ``403 human_session_required``. ``409
    outcomes_from_training_data`` (a file that trained a model here), ``422
    key_column_unknown|outcome_column_unknown|columns_must_differ``, ``409 monitoring_disabled``,
    ``409 already_linked`` (the same file and columns again; an ``Idempotency-Key`` replay still
    replays), ``429 rate_limited`` (more than 10 links per project in an hour).
    Keys must be unique on both sides when joined, else the window says ``key_not_unique``.
    Requires ``Idempotency-Key``."""

    if is_agent(request):
        raise V1APIError(403, "human_session_required", "outcomes are linked by people, not agents")
    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(
        operation=_LINK_OUTCOMES, principal_id=_principal_id(request, user), header_key=idempotency_key,
        path_params={"project_id": project_id}, body=payload.model_dump(mode="json"), required=True,
    )

    def load(resource_id: Any) -> MonitoringOutcomeSet | None:
        row = db.get(MonitoringOutcomeSet, resource_id)
        return row if row is not None and row.workspace_id == workspace_id else None

    def execute(bind: Callable[[Any], None]) -> MonitoringOutcomeSet:
        return ms.link_outcomes(db, actor=user, workspace_id=workspace_id, project_id=project_id, request=payload,
                                before_commit=lambda row: bind(row.id))

    try:
        row, status, replayed = _keyed_command(
            db, _key_scope(request, user, _LINK_OUTCOMES), binding,
            resource_kind="monitoring_outcome_set", load=load, execute=execute, status=201,
        )
    except ProjectNotFoundError as exc:
        db.rollback()
        raise _not_found("project not found") from exc
    except (IdentityError, ExperimentRequestError, DecisionRecordError) as exc:
        db.rollback()
        raise domain_error(exc) from exc
    body = ms.outcome_set_read(row, None, agent=False)
    _created(response, status, replayed, etag=representation_etag(body),
             location=f"/v1/projects/{project_id}/monitoring/outcomes")
    return body
