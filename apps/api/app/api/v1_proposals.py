"""/v1 proposal review: agent runs, proposals, accept / reject / revert, agent review requests
(P6.6-A; ADR 0009 §2.3, §6). Transport only: ``proposal_review_service`` owns every change.

* ``GET /v1/agent-runs[/{id}]`` and ``GET /v1/proposals[/{id}]``: workspace read; service tokens
  need ``read`` and get holdout-free output. Assistant threads are read through ``/v1/assistant``.
* ``POST /v1/proposals/{id}/accept|reject|revert``: **human principals only** (a signed-in session, or
  a person's own API bearer; a service token is refused: ``HUMAN_ONLY_ROUTES``, and again here as
  ``403 human_session_required``: an agent never accepts, rejects or reverts), CSRF-checked for
  cookies, ML-write (``403``), workspace-scoped (``404`` across tenants). Each needs
  ``Idempotency-Key``; the same key replays the stored proposal, a wrong state is ``409``
  (``proposal_not_open``, ``proposal_superseded``, ``proposal_expired``, ``proposal_not_applied``,
  ``not_revertible_in_place``), a ref move needs ``ref_versions`` (``428``).
* ``POST /v1/agent-reviews``: queue a Critic / Investigator / Planner run (ML-write; ``experiments:write``
  for tokens); the run only proposes.

**Rendering contract.** ``proposed_rationale`` and every free-text string inside ``payload`` /
``tool_arguments`` / ``rule_answer`` are plain text, written by a model or by dataset content: render
them with linkify off, never as markup or instructions (the label ``unverified agent rationale`` stays
next to them).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_user,
    request_service_token,
    request_workspace_id,
    require_workspace_ml_execution,
    require_workspace_read,
)
from app.api.v1 import _created, _key_scope, _keyed_command, _principal_id
from app.api.v1_agent_views import is_agent
from app.api.v1_conventions import (
    COMMON_ERROR_STATUSES,
    REPLAY_HEADER_DOC,
    V1APIError,
    domain_error,
    error_responses,
    idempotency_binding,
    idempotency_key_header,
)
from app.db.models import AgentProposal, AgentRun, User
from app.db.session import get_db
from app.domain.errors import (
    DecisionRecordError,
    ExperimentNotBranchableError,
    ExperimentNotFoundError,
    ExperimentRequestError,
    IdentityError,
    InvalidChangeSetError,
    InvalidCursorError,
    PlanRefusedError,
    ProjectNotFoundError,
    ProblemSpecNotFoundError,
    RunQuotaExceededError,
    TargetIntentConflictError,
    TargetNotInDatasetError,
)
from app.domain.proposal_reviews import (
    PAGE_DEFAULT,
    PAGE_MAX,
    AgentReviewRequest,
    AgentRunPage,
    AgentRunRead,
    ProposalDecisionRequest,
    ProposalPage,
    ProposalRead,
    ProposalStatus,
    ProposalType,
)
from app.services import proposal_review_service as prs

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))

RESOURCE_PROPOSAL = "agent_proposal"
RESOURCE_AGENT_RUN = "agent_run"
_ACCEPT = "POST /v1/proposals/{proposal_id}/accept"
_REJECT = "POST /v1/proposals/{proposal_id}/reject"
_REVERT = "POST /v1/proposals/{proposal_id}/revert"
_REVIEW = "POST /v1/agent-reviews"
_DECIDE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"headers": REPLAY_HEADER_DOC}, **error_responses(409, 428)}
_DOMAIN_ERRORS = (
    DecisionRecordError, ExperimentNotBranchableError, ExperimentRequestError, InvalidChangeSetError,
    PlanRefusedError, RunQuotaExceededError, TargetIntentConflictError, TargetNotInDatasetError, IdentityError)


def require_human_session(request: Request, db: Session = Depends(get_db)) -> User:
    """A person with ML-write: never a service token (an agent never decides)."""

    user = get_current_user(request, db)  # a service token is refused here (route not in TOKEN_ROUTE_SCOPES)
    if request_service_token(request) is not None:
        raise HTTPException(status_code=403, detail={
            "code": "human_session_required", "message": "proposals are decided by people, not agents"})
    return require_workspace_ml_execution(request, user, db)


def _error(exc: Exception) -> V1APIError:
    if isinstance(exc, prs.ProposalError):
        return V1APIError(exc.status, exc.code, exc.message, details=exc.details or None)
    if isinstance(exc, (ProjectNotFoundError, ProblemSpecNotFoundError, ExperimentNotFoundError)):
        return V1APIError(404, "not_found", "not found")
    if isinstance(exc, InvalidCursorError):
        return V1APIError(400, "invalid_cursor", str(exc))
    return domain_error(exc)


# --- reads -------------------------------------------------------------------------------------


@router.get("/agent-runs", response_model=AgentRunPage)
def list_agent_runs(
    request: Request,
    project_id: UUID | None = Query(None),
    agent_key: str | None = Query(None, max_length=64),
    status: str | None = Query(None, max_length=24),
    cursor: str | None = Query(None, max_length=256),
    limit: int = Query(PAGE_DEFAULT, ge=1, le=PAGE_MAX),
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> AgentRunPage:
    """Specialist and ops agent runs of the workspace, newest first (assistant threads are not listed)."""

    try:
        rows, next_cursor, size = prs.list_agent_runs(
            db, workspace_id=request_workspace_id(request), project_id=project_id, agent_key=agent_key,
            status=status, cursor=cursor, limit=limit)
    except InvalidCursorError as exc:
        raise _error(exc) from exc
    return AgentRunPage(items=[prs.agent_run_read(db, row) for row in rows], next_cursor=next_cursor, limit=size)


@router.get("/agent-runs/{run_id}", response_model=AgentRunRead)
def read_agent_run(run_id: UUID, request: Request, user: User = Depends(require_workspace_read),
                   db: Session = Depends(get_db)) -> AgentRunRead:
    try:
        return prs.agent_run_read(db, prs.get_agent_run(db, workspace_id=request_workspace_id(request), run_id=run_id))
    except prs.ProposalError as exc:
        raise _error(exc) from exc


@router.get("/proposals", response_model=ProposalPage)
def list_proposals(
    request: Request,
    project_id: UUID | None = Query(None),
    run_id: UUID | None = Query(None),
    level: int | None = Query(None, ge=0, le=3, description="Trust level at proposal time."),
    decision_point_key: str | None = Query(None, max_length=64),
    status: ProposalStatus | None = Query(None),
    proposal_type: ProposalType | None = Query(None),
    cursor: str | None = Query(None, max_length=256),
    limit: int = Query(PAGE_DEFAULT, ge=1, le=PAGE_MAX),
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> ProposalPage:
    """Proposals of the workspace, newest first. Service tokens get holdout-free payloads. Free text is
    plain text (see the module note)."""

    try:
        rows, next_cursor, size = prs.list_proposals(
            db, workspace_id=request_workspace_id(request), project_id=project_id, run_id=run_id, level=level,
            decision_point_key=decision_point_key, status=status, proposal_type=proposal_type, cursor=cursor,
            limit=limit, viewer=user, agent=is_agent(request))
    except InvalidCursorError as exc:
        raise _error(exc) from exc
    agent = is_agent(request)
    return ProposalPage(items=[prs.proposal_read(row, agent=agent) for row in rows], next_cursor=next_cursor,
                        limit=size)


@router.get("/proposals/{proposal_id}", response_model=ProposalRead)
def read_proposal(proposal_id: UUID, request: Request, user: User = Depends(require_workspace_read),
                  db: Session = Depends(get_db)) -> ProposalRead:
    try:
        row = prs.get_proposal(db, workspace_id=request_workspace_id(request), proposal_id=proposal_id, viewer=user,
                               agent=is_agent(request))
    except prs.ProposalError as exc:
        raise _error(exc) from exc
    return prs.proposal_read(row, agent=is_agent(request))


# --- decisions ---------------------------------------------------------------------------------


def _winner(db: Session, request: Request, user: User, operation: str, binding: Any,
            load: Callable[[Any], Any]) -> tuple[Any, int] | None:
    """A 409 may only mean a concurrent request with the same key won: answer its stored result."""

    from app.domain.errors import IdempotencyKeyReusedError
    from app.services import idempotency_service

    db.rollback()
    try:
        bound = idempotency_service.find_bound(db, _key_scope(request, user, operation), binding)
    except IdempotencyKeyReusedError:
        return None
    found = load(bound.resource_id) if bound is not None else None
    return (found, bound.response_status) if found is not None else None


def _decide(request: Request, response: Response, db: Session, user: User, proposal_id: UUID,
            payload: ProposalDecisionRequest, idempotency_key: str | None, operation: str,
            run: Callable[[Callable[[Any], None]], AgentProposal]) -> ProposalRead:
    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(operation=operation, principal_id=_principal_id(request, user),
                                  header_key=idempotency_key, path_params={"proposal_id": proposal_id},
                                  body=payload.model_dump(mode="json"), required=True)

    def load(resource_id: Any) -> AgentProposal | None:
        row = db.get(AgentProposal, resource_id)
        return row if row is not None and row.workspace_id == workspace_id else None

    try:
        row, status, replayed = _keyed_command(db, _key_scope(request, user, operation), binding,
                                               resource_kind=RESOURCE_PROPOSAL, load=load, execute=run, status=200)
    except V1APIError:
        db.rollback()
        raise
    except (prs.ProposalError, *_DOMAIN_ERRORS, ProjectNotFoundError, ProblemSpecNotFoundError,
            ExperimentNotFoundError) as exc:
        won = _winner(db, request, user, operation, binding, load) if (
            isinstance(exc, prs.ProposalError) and exc.status == 409) else (db.rollback() or None)
        if won is None:
            raise _error(exc) from exc
        (row, status), replayed = won, True
    _created(response, status, replayed, etag=None, location=f"/v1/proposals/{row.id}")
    return prs.proposal_read(row, agent=False)


@router.post("/proposals/{proposal_id}/accept", response_model=ProposalRead, responses=_DECIDE_RESPONSES)
def accept_proposal(proposal_id: UUID, payload: ProposalDecisionRequest, request: Request, response: Response,
                    user: User = Depends(require_human_session), db: Session = Depends(get_db),
                    idempotency_key: str | None = Depends(idempotency_key_header)) -> ProposalRead:
    """Accept an open proposal as the signed-in person: the normal command of its type runs as you, a
    ``proposal_accepted`` decision record is written, and the proposal becomes ``accepted`` (a plan or
    advice) or ``applied`` (a command ran). Human session only; ML-write; requires ``Idempotency-Key``."""

    def run(bind: Callable[[Any], None]) -> AgentProposal:
        return prs.accept(db, user=user, workspace_id=request_workspace_id(request), proposal_id=proposal_id,
                          rationale=payload.rationale, ref_versions=payload.ref_versions, bind=bind)

    return _decide(request, response, db, user, proposal_id, payload, idempotency_key, _ACCEPT, run)


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalRead, responses=_DECIDE_RESPONSES)
def reject_proposal(proposal_id: UUID, payload: ProposalDecisionRequest, request: Request, response: Response,
                    user: User = Depends(require_human_session), db: Session = Depends(get_db),
                    idempotency_key: str | None = Depends(idempotency_key_header)) -> ProposalRead:
    """Reject an open proposal (terminal) and record it. Human session only; ML-write."""

    def run(bind: Callable[[Any], None]) -> AgentProposal:
        return prs.reject(db, user=user, workspace_id=request_workspace_id(request), proposal_id=proposal_id,
                          rationale=payload.rationale, bind=bind)

    return _decide(request, response, db, user, proposal_id, payload, idempotency_key, _REJECT, run)


@router.post("/proposals/{proposal_id}/revert", response_model=ProposalRead, responses=_DECIDE_RESPONSES)
def revert_proposal(proposal_id: UUID, payload: ProposalDecisionRequest, request: Request, response: Response,
                    user: User = Depends(require_human_session), db: Session = Depends(get_db),
                    idempotency_key: str | None = Depends(idempotency_key_header)) -> ProposalRead:
    """Revert an applied L2 plan or an applied Jev review item: the rule value is restored by a branch of
    the experiment that used the AI value, under a ``proposal_reverted`` record that supersedes the record
    of the applied value. Anything without a recorded in-place revert is ``409 not_revertible_in_place``."""

    def run(bind: Callable[[Any], None]) -> AgentProposal:
        return prs.revert(db, user=user, workspace_id=request_workspace_id(request), proposal_id=proposal_id,
                          rationale=payload.rationale, bind=bind)

    return _decide(request, response, db, user, proposal_id, payload, idempotency_key, _REVERT, run)


# --- agent review requests -----------------------------------------------------------------------


@router.post("/agent-reviews", response_model=AgentRunRead, status_code=202,
             responses={202: {"headers": REPLAY_HEADER_DOC}, **error_responses(409)})
def request_agent_review(payload: AgentReviewRequest, request: Request, response: Response,
                         user: User = Depends(require_workspace_ml_execution), db: Session = Depends(get_db),
                         idempotency_key: str | None = Depends(idempotency_key_header)) -> AgentRunRead:
    """Queue a Critic (`experiment_id`), Investigator or Planner (`dataset_id`) run for a node you can read.
    The run only proposes: proposals appear under ``GET /v1/proposals?run_id=`` for a person to decide.
    Requires ``Idempotency-Key``; ``409 agent_unavailable`` when AI is off or the agent has no release."""

    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(operation=_REVIEW, principal_id=_principal_id(request, user),
                                  header_key=idempotency_key, body=payload.model_dump(mode="json"), required=True)
    token = request_service_token(request)

    def load(resource_id: Any) -> AgentRun | None:
        row = db.get(AgentRun, resource_id)
        return row if row is not None and row.workspace_id == workspace_id else None

    def execute(bind: Callable[[Any], None]) -> AgentRun:
        return prs.request_review(db, user=user, workspace_id=workspace_id, project_id=payload.project_id,
                                  agent=payload.agent, experiment_id=payload.experiment_id,
                                  dataset_id=payload.dataset_id, service_token_id=token.id if token else None,
                                  bind=bind)

    try:
        run, status, replayed = _keyed_command(db, _key_scope(request, user, _REVIEW), binding,
                                               resource_kind=RESOURCE_AGENT_RUN, load=load, execute=execute,
                                               status=202)
    except V1APIError:
        db.rollback()
        raise
    except (prs.ProposalError, ProjectNotFoundError, IdentityError) as exc:
        won = _winner(db, request, user, _REVIEW, binding, load) if (
            isinstance(exc, prs.ProposalError) and exc.status == 409) else (db.rollback() or None)
        if won is None:
            raise _error(exc) from exc
        (run, status), replayed = won, True
    _created(response, status, replayed, etag=None, location=f"/v1/agent-runs/{run.id}")
    return prs.agent_run_read(db, run)
