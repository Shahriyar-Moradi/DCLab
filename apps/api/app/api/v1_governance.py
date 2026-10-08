"""/v1 governance console: effective policy, levels with R3 evidence links, spend, incidents, policy
proposals, kill switches and replay of recorded agent runs (P6.11-A; ADR 0009 §3, §4, §5.5; ADR 0008 §3).
Transport only: ``agents/governance`` owns every state change.

* ``GET /v1/governance``: workspace read plus ML-write, owner/admin or platform role (viewers 403). Other
  tenants' ids read as absent; no R3 report body, tenant aggregate, pseudonym or operator identity is ever
  returned (platform users read as ``platform_staff``). R3 verdicts show only while ``verify_platform_raise``
  could still rely on the run. A service token with ``read`` gets the SHAPED console: states, ids, counts and
  digests, no rationales / reasons / incident evidence / actor refs / proposed documents.
* ``POST /v1/governance/policy``: a person with ML-write proposes a full ``AiPolicyV1`` (caps enforced: it may
  only narrow the platform policy, ``422 policy_cap_violation``; at most 20 open proposals per workspace, 429).
  Nothing applies until ``POST /v1/governance/policy/{proposal_id}/accept`` by an owner/admin: another approver,
  unless the workspace has exactly one (the row is then flagged ``self_approved``). The accept body carries the
  ``policy_digest`` the approver reviewed (``409 policy_digest_mismatch``); a change of the R3 sharing consent
  (``consent_change``) needs ``acknowledge_consent_change`` (``409 consent_change_unacknowledged``); a proposal
  older than ``proposals.ttl_days`` is ``409 proposal_expired``. Open proposals show their document and diffs to
  owners/admins and the proposer (human session only). Platform staff never approve a customer's policy (403).
  Both need ``Idempotency-Key`` and a human principal (no service token); a replayed key re-checks the capability.
* ``POST /v1/governance/switches``: workspace kill switches. Off is always allowed for owners/admins and for
  platform admins; on needs an owner/admin and is ``409 switch_held_by_incident`` while an open incident holds
  the key. So a platform admin can switch a workspace key off and the workspace owner can switch it back on
  unless an incident holds it (platform hard stops use platform-scope keys, CLI only). The next gateway call
  sees the new state.
* ``POST /v1/agent-runs/{id}/replay``: human-only (CSRF + ``Idempotency-Key``): replays a recorded specialist /
  ops run through the harness and compares it with the record. It writes a ``replay_checked`` event, a mismatch
  opens one ``replay_mismatch`` incident (a run that failed with the same code replays as ``same_failure``, a
  record without its final digest as ``not_comparable``). Serialized per run, 5 per run and 20 per viewer per 10
  minutes (429), 30 s wall limit (504). Lead and assistant runs are ``409 not_replayable``. It runs in the
  request thread (a worker job is a follow-up).
* There is no HTTP route that raises a platform level: promotions go through ``r3_evaluation_service``.

**Rendering contract.** Rationales, reasons and incident evidence strings are plain text.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.governance import console
from app.agents.governance.policy import (
    GovernanceError,
    GovernanceNotPermitted,
    PolicyCapViolation,
    PolicyReview,
    accept_policy,
    propose_policy,
)
from app.agents.governance.switches import flip_off, re_enable
from app.agents.harness.replay import ReplayRefused, replay
from app.api.deps import request_service_token, request_workspace_id, require_workspace_read
from app.api.v1 import _created, _key_scope, _keyed_command, _principal_id
from app.api.v1_conventions import (
    COMMON_ERROR_STATUSES,
    REPLAY_HEADER_DOC,
    V1APIError,
    error_responses,
    idempotency_binding,
    idempotency_key_header,
)
from app.db.models import AgentEvent, AgentRun, AiPolicy, AiSwitch, User
from app.db.session import get_db
from app.domain.governance_console import (
    GovernanceRead,
    PolicyAcceptRequest,
    PolicyChangeRead,
    PolicyProposalRequest,
    ReplayRead,
    ReplayToolRead,
    SwitchChangeRequest,
    SwitchRead,
)
from app.services.authorization_service import (
    can_approve_ai_policy,
    can_execute_workspace_ml,
    can_read_platform,
    can_read_workspace,
)

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))

RESOURCE_POLICY = "ai_policy"
RESOURCE_SWITCH = "ai_switch"
_PROPOSE = "POST /v1/governance/policy"
_ACCEPT = "POST /v1/governance/policy/{proposal_id}/accept"
_SWITCH = "POST /v1/governance/switches"
_COMMAND_RESPONSES: dict[int | str, dict[str, Any]] = {200: {"headers": REPLAY_HEADER_DOC}, **error_responses(409, 428)}
PRIVATE_RUN_KINDS = frozenset({"assistant", "lead"})
_REPLAY = "POST /v1/agent-runs/{run_id}/replay"
RESOURCE_REPLAY = "agent_run_replay"
_REPLAY_REFUSALS = {"not_found": 404, "forbidden": 403, "run_not_finished": 409, "not_replayable": 409,
                    "runtime_unavailable": 409, "rate_limited": 429, "replay_timeout": 504}


def require_human_reader(request: Request, user: User = Depends(require_workspace_read)) -> User:
    """A person with workspace read: never a service token (an agent never changes governance)."""

    if request_service_token(request) is not None:
        raise HTTPException(status_code=403, detail={
            "code": "human_session_required", "message": "governance changes are made by people, not agents"})
    return user


def _error(exc: GovernanceError) -> V1APIError:
    details = {"violations": exc.violations} if isinstance(exc, PolicyCapViolation) else None
    return V1APIError(exc.status_code, exc.code, exc.detail or exc.code.replace("_", " "), details=details)


def _command(request: Request, response: Response, db: Session, user: User, *, operation: str, resource_kind: str,
             model: type, body: Any, path_params: dict[str, Any] | None, header_key: str | None,
             execute: Callable[[Callable[[Any], None]], Any], read: Callable[[Any], Any], location: str,
             allowed: Callable[[Session, User, UUID], bool]) -> Any:
    workspace_id = request_workspace_id(request)
    try:  # a replayed Idempotency-Key must not return a stored result to someone who may no longer act
        if not allowed(db, user, workspace_id):
            raise GovernanceNotPermitted(detail="not permitted to make this governance change")
    except GovernanceError as exc:
        raise _error(exc) from exc
    binding = idempotency_binding(operation=operation, principal_id=_principal_id(request, user), header_key=header_key,
                                  path_params=path_params, body=body, required=True)

    def load(resource_id: Any) -> Any:
        row = db.get(model, resource_id)
        return row if row is not None and row.workspace_id == workspace_id else None

    try:
        row, status, replayed = _keyed_command(db, _key_scope(request, user, operation), binding,
                                               resource_kind=resource_kind, load=load, execute=execute, status=200)
    except V1APIError:
        db.rollback()
        raise
    except GovernanceError as exc:
        db.rollback()
        raise _error(exc) from exc
    _created(response, status, replayed, etag=None, location=location)
    return read(row)


# --- reads ------------------------------------------------------------------------------------------------


@router.get("/governance", response_model=GovernanceRead)
def read_governance(request: Request, user: User = Depends(require_workspace_read),
                    db: Session = Depends(get_db)) -> GovernanceRead:
    """The effective AI policy (platform narrowed by the workspace), model allowlist, data classes, switches,
    decision-point levels with links to the stored R3 runs, spend vs budget, open incidents and recent changes
    of this workspace. Plain text throughout; no R3 report body or tenant evidence."""

    try:
        return console.console_read(db, actor=user, workspace_id=request_workspace_id(request),
                                    agent=request_service_token(request) is not None)
    except GovernanceError as exc:
        raise _error(exc) from exc


def _may_replay(db: Session, user: User, workspace_id: UUID) -> bool:
    return can_read_workspace(db, user, workspace_id) and (
        can_read_platform(db, user) or can_execute_workspace_ml(db, user, workspace_id))


def _replay_read(event: AgentEvent) -> ReplayRead:
    body = event.payload
    return ReplayRead(
        run_id=event.run_id, equal=bool(body.get("equal")), mismatches=[str(m) for m in body.get("mismatches", [])],
        tool_sequence=[ReplayToolRead(tool=str(t[0]), argument_digest=str(t[1])) for t in body.get("tool_sequence", [])],
        output_digest=body.get("output_digest"),
        incident_id=UUID(body["incident_id"]) if body.get("incident_id") else None,
        same_failure=bool(body.get("same_failure")), not_comparable=bool(body.get("not_comparable")))


@router.post("/agent-runs/{run_id}/replay", response_model=ReplayRead,
             responses={200: {"headers": REPLAY_HEADER_DOC}, **error_responses(409, 429, 504)})
def replay_agent_run(run_id: UUID, request: Request, response: Response, user: User = Depends(require_human_reader),
                     db: Session = Depends(get_db),
                     idempotency_key: str | None = Depends(idempotency_key_header)) -> ReplayRead:
    """Re-run a recorded specialist / ops run with the fake provider fed from its record (tools stubbed): the
    tool sequence, output digest and proposal payloads must equal the record. A mismatch opens one
    ``replay_mismatch`` incident (not when the run failed with the same code: ``same_failure``). Lead and
    assistant runs are ``409 not_replayable`` (typed refusal, never faked); a run still live is
    ``409 run_not_finished``. Human session only (CSRF); needs workspace read plus platform read or ML-write;
    requires ``Idempotency-Key`` (a replayed key returns the stored result)."""

    workspace_id = request_workspace_id(request)

    def execute(bind: Callable[[Any], None]) -> AgentEvent:
        run = db.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.workspace_id == workspace_id))
        private = run is not None and run.kind in PRIVATE_RUN_KINDS  # assistant threads / lead turns: their owner's
        if run is None or (private and run.created_by_user_id != user.id
                           and not can_approve_ai_policy(db, user, workspace_id)):
            raise V1APIError(404, "not_found", "agent run not found")
        if private:  # recorded without the turn input: not replayable yet (P6.10-B); typed refusal, never faked
            raise V1APIError(409, "not_replayable", "lead and assistant runs cannot be replayed yet")
        try:
            result = replay(db, workspace_id=workspace_id, run_id=run_id, actor=user)
        except ReplayRefused as exc:
            raise V1APIError(_REPLAY_REFUSALS.get(exc.code, 409), exc.code, exc.code.replace("_", " ")) from exc
        event = db.get(AgentEvent, result.event_id)
        bind(event.id)
        db.commit()
        return event

    return _command(request, response, db, user, operation=_REPLAY, resource_kind=RESOURCE_REPLAY, model=AgentEvent,
                    body={}, path_params={"run_id": run_id}, header_key=idempotency_key, execute=execute,
                    read=_replay_read, location=f"/v1/agent-runs/{run_id}", allowed=_may_replay)


# --- commands ---------------------------------------------------------------------------------------------


@router.post("/governance/policy", response_model=PolicyChangeRead, responses=_COMMAND_RESPONSES)
def propose_governance_policy(payload: PolicyProposalRequest, request: Request, response: Response,
                              user: User = Depends(require_human_reader), db: Session = Depends(get_db),
                              idempotency_key: str | None = Depends(idempotency_key_header)) -> PolicyChangeRead:
    """Propose a change of the workspace AI policy as a ``proposed`` ``ai_policies`` row (the decision record of
    ADR 0009 §2.5): the document is validated and checked against the platform caps (it may only narrow them)
    and applies only when an owner/admin accepts it. ML-write membership required; ``Idempotency-Key`` required."""

    workspace_id = request_workspace_id(request)

    def execute(bind: Callable[[Any], None]) -> AiPolicy:
        row = propose_policy(db, actor=user, workspace_id=workspace_id, policy=payload.policy,
                             rationale=payload.rationale)
        bind(row.id)
        db.commit()
        return row

    return _command(request, response, db, user, operation=_PROPOSE, resource_kind=RESOURCE_POLICY, model=AiPolicy,
                    body=payload.model_dump(mode="json"), path_params=None, header_key=idempotency_key,
                    execute=execute, read=lambda row: console.policy_change_read(db, row),
                    location="/v1/governance", allowed=lambda db_, u, ws: console.viewer_for(db_, u, ws).can_propose)


@router.post("/governance/policy/{proposal_id}/accept", response_model=PolicyChangeRead,
             responses=_COMMAND_RESPONSES)
def accept_governance_policy(proposal_id: UUID, payload: PolicyAcceptRequest, request: Request, response: Response,
                             user: User = Depends(require_human_reader), db: Session = Depends(get_db),
                             idempotency_key: str | None = Depends(idempotency_key_header)) -> PolicyChangeRead:
    """Accept an open policy proposal: only a workspace owner/admin (platform staff never), the proposal's base
    must still be the accepted head (409 otherwise), the body's ``policy_digest`` must be the proposal's and
    the caps are checked again. A proposer cannot accept their
    own proposal unless they are the workspace's only approver (then ``self_approved`` is recorded)."""

    workspace_id = request_workspace_id(request)

    def execute(bind: Callable[[Any], None]) -> AiPolicy:
        row = accept_policy(db, approver=user, workspace_id=workspace_id, proposal_id=proposal_id,
                            review=PolicyReview(payload.policy_digest, payload.acknowledge_consent_change))
        bind(row.id)
        db.commit()
        return row

    return _command(request, response, db, user, operation=_ACCEPT, resource_kind=RESOURCE_POLICY, model=AiPolicy,
                    body=payload.model_dump(mode="json"), path_params={"proposal_id": proposal_id},
                    header_key=idempotency_key, execute=execute, read=lambda row: console.policy_change_read(db, row),
                    location="/v1/governance", allowed=lambda db_, u, ws: console.viewer_for(db_, u, ws).can_approve)


@router.post("/governance/switches", response_model=SwitchRead, responses=_COMMAND_RESPONSES)
def change_governance_switch(payload: SwitchChangeRequest, request: Request, response: Response,
                             user: User = Depends(require_human_reader), db: Session = Depends(get_db),
                             idempotency_key: str | None = Depends(idempotency_key_header)) -> SwitchRead:
    """Flip a workspace kill switch (append-only, audited with actor, reason and time). ``off`` always succeeds
    for owners/admins and platform staff and stops the NEXT gateway call; ``on`` needs an owner/admin and is
    ``409 switch_held_by_incident`` while an open incident holds the key."""

    workspace_id = request_workspace_id(request)

    def execute(bind: Callable[[Any], None]) -> AiSwitch:
        if payload.state == "off":
            row = flip_off(db, workspace_id=workspace_id, switch_key=payload.switch_key, reason=payload.reason,
                           actor=user)
        else:
            row = re_enable(db, workspace_id=workspace_id, switch_key=payload.switch_key, reason=payload.reason,
                            actor=user)
        bind(row.id)
        db.commit()
        return row

    return _command(request, response, db, user, operation=_SWITCH, resource_kind=RESOURCE_SWITCH, model=AiSwitch,
                    body=payload.model_dump(mode="json"), path_params=None, header_key=idempotency_key,
                    execute=execute, read=lambda row: console.switch_read(db, row), location="/v1/governance",
                    allowed=lambda db_, u, ws: (console.viewer_for(db_, u, ws).can_switch_off if payload.state == "off"
                                                else console.viewer_for(db_, u, ws).can_approve))
