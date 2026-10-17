"""/v1 retrain on newest data (D5a, ADR 0012): start a retrain, the retrain policy, the status read.

Transport only (``retrain_service``). ``POST .../retrain`` is a signed-in person's act with an
ML-write role (Idempotency-Key, bound to the run; ``202`` + ``Location``). Service tokens are refused
(not in ``TOKEN_ROUTE_SCOPES``: a retrain spends compute to challenge the model in use and opens a
switch proposal; a token with ``experiments:write`` can still start its own runs with
``POST /v1/experiments``). ``PUT .../retrain-policy`` is a workspace owner's or administrator's act
with a reason (Idempotency-Key and ``If-Match`` required). Reads are workspace-scoped (another
tenant's project is 404); service tokens get no user ids and never the comparison figures.
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id, require_workspace_ml_execution, require_workspace_read
from app.api.v1 import _created, _key_scope, _keyed_command, _not_found, _principal_id
from app.api.v1_agent_views import is_agent
from app.api.v1_conventions import (
    COMMON_ERROR_STATUSES,
    ETAG_HEADER_DOC,
    REPLAY_HEADER_DOC,
    V1APIError,
    check_if_match,
    domain_error,
    error_responses,
    idempotency_binding,
    idempotency_key_header,
    if_match_header,
    representation_etag,
    set_etag,
    version_etag,
)
from app.api.v1_decisions import _keyed as keyed_decision
from app.db.models import Experiment, ProjectDecisionRecord, User
from app.db.session import get_db
from app.domain.errors import (
    DecisionRecordError,
    ExperimentRequestError,
    IdentityError,
    ProblemSpecNotFoundError,
    ProjectNotFoundError,
    RunQuotaExceededError,
    TargetIntentConflictError,
    TargetNotInDatasetError,
)
from app.domain.idempotency import RESOURCE_EXPERIMENT
from app.domain.retrain import RetrainPolicyRead, RetrainPolicyWrite, RetrainStartRequest, RetrainStatusRead
from app.services.retrain_service import policy_read, retrain_status, set_policy, start_retrain

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))

_START = "POST /v1/projects/{project_id}/retrain"
_SET_POLICY = "PUT /v1/projects/{project_id}/retrain-policy"


def _human_only(request: Request, what: str) -> None:
    if is_agent(request):
        raise V1APIError(403, "human_session_required", f"{what} is a person's act, not an agent's")


def _status(db: Session, request: Request, user: User, project_id: UUID) -> RetrainStatusRead:
    try:
        return retrain_status(db, actor=user, workspace_id=request_workspace_id(request), project_id=project_id,
                              agent=is_agent(request))
    except IdentityError as exc:
        raise domain_error(exc) from exc
    except ProjectNotFoundError as exc:
        raise _not_found(str(exc)) from exc


def _policy(db: Session, request: Request, user: User, project_id: UUID) -> RetrainPolicyRead:
    try:
        return policy_read(db, actor=user, workspace_id=request_workspace_id(request), project_id=project_id,
                           agent=is_agent(request))
    except IdentityError as exc:
        raise domain_error(exc) from exc
    except ProjectNotFoundError as exc:
        raise _not_found(str(exc)) from exc


@router.post(
    "/projects/{project_id}/retrain",
    response_model=RetrainStatusRead,
    status_code=202,
    responses={202: {"headers": {**ETAG_HEADER_DOC, **REPLAY_HEADER_DOC, "Location": {
        "description": "URL of the retrain run.", "schema": {"type": "string"}}}}, **error_responses(409, 429)},
)
def start_retrain_v1(
    project_id: UUID,
    payload: RetrainStartRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> RetrainStatusRead:
    """Retrain the model in use on a newer labelled file: a new root run with the same target and
    goal (metric and constraints), its own new final test. One retrain per project at a time
    (``409 retrain_in_progress``); the file must be uploaded into this project after the training
    file of the model in use (``409 dataset_not_newer``); plan limits apply (``429``). When it
    finishes, the worker compares it with the model in use and opens a switch proposal: nothing
    switches until a person accepts it. People with ML-write only (service tokens 403). Requires
    ``Idempotency-Key``."""

    _human_only(request, "starting a retrain")
    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(
        operation=_START, principal_id=_principal_id(request, user), header_key=idempotency_key,
        path_params={"project_id": project_id}, body=payload.model_dump(mode="json"), required=True,
    )

    def load(resource_id: Any) -> Experiment | None:
        row = db.get(Experiment, resource_id)
        return row if row is not None and row.workspace_id == workspace_id and row.project_id == project_id else None

    def execute(bind: Callable[[Any], None]) -> Experiment:
        return start_retrain(db, actor=user, workspace_id=workspace_id, project_id=project_id,
                             dataset_id=payload.dataset_id, before_commit=lambda shell: bind(shell.id)).experiment

    try:
        experiment, status, replayed = _keyed_command(
            db, _key_scope(request, user, _START), binding, resource_kind=RESOURCE_EXPERIMENT, load=load,
            execute=execute, status=202)
    except (ProjectNotFoundError, ProblemSpecNotFoundError) as exc:
        db.rollback()
        raise _not_found(str(exc)) from exc
    except (IdentityError, ExperimentRequestError, TargetIntentConflictError, TargetNotInDatasetError,
            RunQuotaExceededError) as exc:
        db.rollback()
        raise domain_error(exc) from exc
    body = _status(db, request, user, project_id)
    _created(response, status, replayed, etag=representation_etag(body), location=f"/v1/experiments/{experiment.id}")
    return body


@router.get(
    "/projects/{project_id}/retrain-policy",
    response_model=RetrainPolicyRead,
    responses={200: {"headers": ETAG_HEADER_DOC}},
)
def read_retrain_policy(
    project_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> RetrainPolicyRead:
    """The project's retrain policy: ``off`` (default), ``ask`` (an Inbox question "Retrain on
    newest data?" when the model in use drifts and a newer labelled file exists) or ``auto`` (the
    retrain starts by itself, acting as the person who chose it; it never switches the model).
    ``ETag`` is its version. Service tokens may read it (no user ids)."""

    body = _policy(db, request, user, project_id)
    set_etag(response, version_etag(body.version))
    return body


@router.put(
    "/projects/{project_id}/retrain-policy",
    response_model=RetrainPolicyRead,
    responses={200: {"headers": {**ETAG_HEADER_DOC, **REPLAY_HEADER_DOC}}, **error_responses(409, 412, 428)},
)
def set_retrain_policy(
    project_id: UUID,
    payload: RetrainPolicyWrite,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
    if_match: str | None = Depends(if_match_header),
) -> RetrainPolicyRead:
    """Set the retrain policy with your reason (``rationale``); writes an accepted
    ``retrain_policy_changed`` History entry. Workspace owners and administrators only (others
    ``403``; service tokens and agents ``403``). Requires ``Idempotency-Key`` and ``If-Match``
    (the policy's ETag; ``"0"`` when never set): a stale one is ``412``, none ``428``."""

    _human_only(request, "changing the retrain policy")
    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(
        operation=_SET_POLICY, principal_id=_principal_id(request, user), header_key=idempotency_key,
        path_params={"project_id": project_id}, body=payload.model_dump(mode="json"), required=True,
    )

    def execute(bind: Callable[[Any], None]) -> ProjectDecisionRecord:
        row = set_policy(db, actor=user, workspace_id=workspace_id, project_id=project_id, request=payload,
                         precondition=lambda version: check_if_match(if_match, version_etag(version), required=True))
        bind(row.id)
        db.commit()
        return row

    try:
        _row, status, replayed = keyed_decision(db, request, user, _SET_POLICY, binding, execute, status=200)
    except V1APIError:
        db.rollback()
        raise
    except ProjectNotFoundError as exc:
        db.rollback()
        raise _not_found(str(exc)) from exc
    except (IdentityError, DecisionRecordError) as exc:
        db.rollback()
        raise domain_error(exc) from exc
    body = _policy(db, request, user, project_id)
    _created(response, status, replayed, etag=version_etag(body.version), location=None)
    return body


@router.get("/projects/{project_id}/retrain-status", response_model=RetrainStatusRead)
def read_retrain_status(
    project_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> RetrainStatusRead:
    """What the system does on its own, for the Monitoring card: the policy, the state (``idle``,
    ``drift_found``, ``retraining``, ``ready_to_compare``, ``waiting_for_you``, ``switched``,
    ``kept``), the newest retrain, its comparison with the model in use (same kind of test, latest
    months; or why it is not comparable), the switch proposal and the real outcomes of the model in
    use beside it (not the same data). Service tokens get the state without the comparison figures."""

    return _status(db, request, user, project_id)
