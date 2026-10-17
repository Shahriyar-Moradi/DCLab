"""/v1 improve loops (P5.4-A3, ADR 0007 §7, §8, §10): start, read, list per project, cancel, accept.

Transport only (``app.services.improve.service``). ``POST /v1/improve-runs`` (``experiments:write``;
``Idempotency-Key`` bound to the loop, principal-scoped; reserved ``improve:`` / ``improve-final:``
keys refused) answers ``202`` + ``Location``; a service token's budget is clamped to the defaults,
its starts are counted (5 per hour) and it may not take the split plan past half its comparison
bound. ``POST .../cancel`` (``If-Match``) is open to tokens only for a queued or running loop.
``POST .../accept`` is a person's act (``HUMAN_ONLY_ROUTES``, ``Idempotency-Key`` and ``If-Match``).
Reads are workspace-scoped (another tenant's loop is 404); tokens get no person ids and no key
naming the holdout.
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import request_service_token, request_workspace_id, require_workspace_ml_execution, require_workspace_read
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
)
from app.db.models import ImproveRun, User
from app.db.session import get_db
from app.domain.errors import (
    ExperimentNotBranchableError,
    ExperimentNotFoundError,
    IdentityError,
    ImproveRunError,
    InvalidChangeSetError,
    ProjectNotFoundError,
    RunQuotaExceededError,
)
from app.domain.idempotency import RESOURCE_IMPROVE_RUN
from app.domain.improve import (
    IMPROVE_PAGE_DEFAULT,
    IMPROVE_PAGE_MAX,
    RUN_FINALIZING,
    ImproveAcceptRequest,
    ImproveRunPage,
    ImproveRunRead,
    ImproveStartRequest,
)
from app.services.authorization_service import can_execute_workspace_ml
from app.services import idempotency_service
from app.services.idempotency_service import IdempotencyKeyReusedError
from app.services.improve import service as improve

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))

_START = "POST /v1/improve-runs"
_ACCEPT = "POST /v1/improve-runs/{improve_run_id}/accept"
_REFUSALS = (ImproveRunError, ExperimentNotFoundError, ProjectNotFoundError, IdentityError,
             ExperimentNotBranchableError, InvalidChangeSetError, RunQuotaExceededError)


def _token_id(request: Request) -> UUID | None:
    token = request_service_token(request)
    return token.id if token is not None else None


def _error(exc: Exception) -> V1APIError:
    if isinstance(exc, (ExperimentNotFoundError, ProjectNotFoundError)) or getattr(exc, "status_code", None) == 404:
        return _not_found("not found")
    return domain_error(exc)


def _view(db: Session, request: Request, user: User, run: ImproveRun) -> ImproveRunRead:
    return improve.run_read(run, improve.iterations(db, run), agent=is_agent(request),
                            can_act=can_execute_workspace_ml(db, user, run.workspace_id), token_id=_token_id(request))


def _read(db: Session, request: Request, user: User, run_id: UUID) -> ImproveRunRead:
    try:
        return improve.improve_run_read(db, actor=user, workspace_id=request_workspace_id(request), run_id=run_id,
                                        agent=is_agent(request), token_id=_token_id(request))
    except _REFUSALS as exc:
        raise _error(exc) from exc


def _if_match(db: Session, request: Request, user: User, if_match: str | None) -> Callable[[ImproveRun], None]:
    """``If-Match`` against the loop as this principal reads it, checked under the row lock."""

    def check(run: ImproveRun) -> None:
        check_if_match(if_match, representation_etag(_view(db, request, user, run)), required=True)

    return check


def _loader(db: Session, workspace_id: UUID) -> Callable[[Any], ImproveRun | None]:
    def load(resource_id: Any) -> ImproveRun | None:
        row = db.get(ImproveRun, resource_id)
        return row if row is not None and row.workspace_id == workspace_id else None

    return load


def _keyed(db: Session, request: Request, user: User, operation: str, binding: Any,
           execute: Callable[[Callable[[Any], None]], ImproveRun], status: int) -> tuple[ImproveRun, int, bool]:
    """``_keyed_command``, plus one more replay when the command lost a race to a concurrent request
    with the same key (its 409 / 412 came from the state that request just committed)."""

    workspace_id = request_workspace_id(request)
    scope = _key_scope(request, user, operation)
    try:
        return _keyed_command(db, scope, binding, resource_kind=RESOURCE_IMPROVE_RUN, load=_loader(db, workspace_id),
                              execute=execute, status=status)
    except (V1APIError, ImproveRunError) as exc:
        if getattr(exc, "status_code", None) not in (409, 412) or not binding.keyed:
            raise
        db.rollback()
        try:
            row = idempotency_service.find_bound(db, scope, binding)
        except IdempotencyKeyReusedError:
            raise exc from None
        resource = _loader(db, workspace_id)(row.resource_id) if row is not None else None
        if resource is None:
            raise
        return resource, row.response_status, True


@router.post(
    "/improve-runs",
    response_model=ImproveRunRead,
    status_code=202,
    responses={202: {"headers": {**ETAG_HEADER_DOC, **REPLAY_HEADER_DOC, "Location": {
        "description": "URL of the improve loop.", "schema": {"type": "string"}}}}, **error_responses(409, 429)},
)
def start_improve_run_v1(
    payload: ImproveStartRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> ImproveRunRead:
    """Start an improve loop on a completed run: a goal (``plain`` primary metric, or ``objective``
    with constraints) and a budget; each try is a sealed branch on the same folds (the final test set
    is not used), judged per fold against noise. ``409`` with the reason when the run cannot start a
    loop (``nothing_to_try``, ``start_truncated``, ``improve_run_live``, ``split_plan_exhausted``,
    ``fresh_split_unavailable`` ...), ``422`` for a goal the loop cannot judge, ``429`` for the
    workspace's live loops or a service token's starts per hour. Service tokens: the budget is only
    lowered, never raised; ``fresh_split`` is ``403``. Requires ``Idempotency-Key``."""

    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(
        operation=_START, principal_id=_principal_id(request, user), header_key=idempotency_key,
        body=payload.model_dump(mode="json"), required=True,
    )

    def execute(bind: Callable[[Any], None]) -> ImproveRun:
        return improve.start_improve_run(
            db, actor=user, workspace_id=workspace_id, start_experiment_id=payload.start.from_experiment_id,
            goal=payload.goal, budget=payload.budget, service_token_id=_token_id(request),
            fresh_split=payload.start.fresh_split, before_commit=lambda run: bind(run.id))

    try:
        run, status, replayed = _keyed(db, request, user, _START, binding, execute, 202)
    except _REFUSALS as exc:
        db.rollback()
        raise _error(exc) from exc
    body = _read(db, request, user, run.id)
    _created(response, status, replayed, etag=representation_etag(body), location=f"/v1/improve-runs/{run.id}")
    return body


@router.get("/improve-runs/{improve_run_id}", response_model=ImproveRunRead,
            responses={200: {"headers": ETAG_HEADER_DOC}})
def read_improve_run(
    improve_run_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> ImproveRunRead:
    """One loop: status, outcome and stop reason, goal and budget, spend, every try with its
    per-fold cross-validation verdict, and what you may do now (``actions``). Never a final-test
    value. ``ETag`` is the ``If-Match`` of cancel and accept. Service tokens get no person ids."""

    body = _read(db, request, user, improve_run_id)
    set_etag(response, representation_etag(body))
    return body


@router.get("/projects/{project_id}/improve-runs", response_model=ImproveRunPage)
def list_project_improve_runs(
    project_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
    limit: int = Query(IMPROVE_PAGE_DEFAULT, ge=1, le=IMPROVE_PAGE_MAX),
) -> ImproveRunPage:
    """The project's improve loops, newest first."""

    try:
        return improve.list_improve_runs(db, actor=user, workspace_id=request_workspace_id(request),
                                         project_id=project_id, agent=is_agent(request), limit=limit,
                                         token_id=_token_id(request))
    except _REFUSALS as exc:
        raise _error(exc) from exc


@router.post(
    "/improve-runs/{improve_run_id}/cancel",
    response_model=ImproveRunRead,
    responses={200: {"headers": ETAG_HEADER_DOC, "description": "Cancelled (now or earlier)."},
               202: {"headers": ETAG_HEADER_DOC, "description": "Finalizing: the final run is asked to stop."},
               **error_responses(409, 412, 428)},
)
def cancel_improve_run_v1(
    improve_run_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
    if_match: str | None = Depends(if_match_header),
) -> ImproveRunRead:
    """Stop a loop: its live try is cancelled and the best so far is kept. People may cancel any
    live loop; a service token only a queued or running loop it started (``403 token_cannot_cancel``). During
    finalizing the final run is asked to stop (``202``): after its final test the look still counts.
    Replay-safe by state (a finished loop answers ``200`` without a precondition). Requires
    ``If-Match`` (the loop's ``ETag``)."""

    del idempotency_key  # validated by the dependency; the state machine dedupes
    try:
        run = improve.cancel_improve_run(db, actor=user, workspace_id=request_workspace_id(request),
                                         run_id=improve_run_id, service_token_id=_token_id(request),
                                         precondition=_if_match(db, request, user, if_match))
    except V1APIError:
        db.rollback()
        raise
    except _REFUSALS as exc:
        db.rollback()
        raise _error(exc) from exc
    body = _read(db, request, user, run.id)
    response.status_code = 202 if body.status == RUN_FINALIZING else 200
    set_etag(response, representation_etag(body))
    return body


@router.post(
    "/improve-runs/{improve_run_id}/accept",
    response_model=ImproveRunRead,
    responses={200: {"headers": {**ETAG_HEADER_DOC, **REPLAY_HEADER_DOC}, "description": "Kept the current model."},
               202: {"headers": {**ETAG_HEADER_DOC, **REPLAY_HEADER_DOC},
                     "description": "Accepted: the one final run is queued."},
               **error_responses(409, 412, 428, 429)},
)
def accept_improve_run_v1(
    improve_run_id: UUID,
    payload: ImproveAcceptRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
    if_match: str | None = Depends(if_match_header),
) -> ImproveRunRead:
    """A person accepts one try that beat the noise (``iteration``): it is re-run once with its
    selection pinned and that final run scores the final test set once (``202``); or keeps the
    current model (``keep_current``: nothing is scored, ``200``). Decided on cross-validation only.
    People with ML-write only (service tokens and agents ``403``). Requires ``Idempotency-Key`` and
    ``If-Match`` (the loop's ``ETag``); ``429`` while the workspace already runs its live loops."""

    if is_agent(request):
        raise V1APIError(403, "human_session_required", "accepting an improve try is a person's act")
    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(
        operation=_ACCEPT, principal_id=_principal_id(request, user), header_key=idempotency_key,
        path_params={"improve_run_id": improve_run_id}, body=payload.model_dump(mode="json"), required=True,
    )

    def execute(bind: Callable[[Any], None]) -> ImproveRun:
        return improve.accept_improve_run(
            db, actor=user, workspace_id=workspace_id, run_id=improve_run_id, iteration_seq=payload.iteration,
            keep_current=payload.keep_current, rationale=payload.rationale,
            precondition=_if_match(db, request, user, if_match), before_commit=lambda run: bind(run.id))

    try:
        run, status, replayed = _keyed(db, request, user, _ACCEPT, binding, execute,
                                       200 if payload.keep_current else 202)
    except V1APIError:
        db.rollback()
        raise
    except _REFUSALS as exc:
        db.rollback()
        raise _error(exc) from exc
    body = _read(db, request, user, run.id)
    _created(response, status, replayed, etag=representation_etag(body), location=None)
    return body
