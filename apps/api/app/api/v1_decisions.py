"""/v1 decision records and project refs (P3.1-B3; ADR 0006 §2, §5, §9).

Resource design (transport only; ``decision_record_service`` and
``project_ref_service`` own every state change):

* ``POST /v1/projects/{id}/decisions`` starts a chain. The body is a union on
  ``action``: ``propose`` (a proposal), ``record`` (a decision made now, stored
  ``accepted``) or ``propose_ref_move`` (a ref-move proposal; nothing moves).
  ``GET`` lists them (``v1.py``, P2.5-A); ``GET /v1/decisions/{id}`` reads one.
* ``POST /v1/decisions/{id}/accept|reject|supersede`` are the transitions of an
  existing record (accept/reject a proposal, correct an accepted record). Each
  writes a new row (``201`` + ``Location``); records are never edited.
* Refs are the only mutable pointers: ``GET /v1/projects/{id}/refs`` (and
  ``/refs/{kind}``) return each ref's ``version`` as a strong ETag ``"<n>"``;
  ``POST /v1/projects/{id}/refs/{kind}`` moves it under one accepted record
  (``ref_moved`` / ``champion_promoted``), optionally with companion refs (body
  ``expected_version``) and/or accepting a ref-move ``proposal_id``. The path
  ref REQUIRES ``If-Match: "<version>"`` (``428`` when absent, ``412`` when
  stale, ``*`` refused) or ``If-None-Match: *`` to create a missing kind.
  Accepting a ref-move proposal through ``/accept`` is ``409
  ref_move_requires_move_ref``: the move needs the ref precondition.

Every POST requires ``Idempotency-Key`` (``idempotency_keys``, resource kind
``decision_record``; scope workspace + principal + operation). The actor is
always the session principal as a human: bodies forbid extra keys, so an
``actor_kind``/``agent_run_id``/rule id is ``422``. Service tokens (P3.2-A) and
MCP (P3.4-A) will build their actor in ``_actor`` from the authenticated
principal, never from the body.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable, Union
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id, require_workspace_ml_execution, require_workspace_read
from app.api.v1 import _created, _key_scope, _keyed_command, _not_found
from app.api.v1_conventions import (
    COMMON_ERROR_STATUSES,
    ETAG_HEADER_DOC,
    REPLAY_HEADER_DOC,
    V1APIError,
    _if_match_tags,
    domain_error,
    error_responses,
    idempotency_binding,
    idempotency_key_header,
    if_match_header,
    if_match_version,
    if_none_match_header,
    representation_etag,
    set_etag,
    version_etag,
    mark_replayed,
)
from app.db.models import ProjectDecisionRecord, ProjectRef, User
from app.db.session import get_db
from app.domain.decision_records import (
    STATE_ACCEPTED,
    DecisionActor,
    DecisionCreateRequest,
    DecisionRecordRead,
    DecisionResolveRequest,
    DecisionSupersedeRequest,
    ProjectRefList,
    ProjectRefRead,
    ProjectRefTarget,
    RefKind,
    RefMoveProposalRequest,
    RefMoveRead,
    RefMoveRequest,
)
from app.domain.errors import (
    DecisionRecordError,
    DecisionRecordNotFoundError,
    IdempotencyKeyReusedError,
    IdentityError,
    InvalidDecisionTransitionError,
    ProjectNotFoundError,
    RefVersionConflictError,
)
from app.domain.idempotency import RESOURCE_DECISION_RECORD
from app.domain.state_graph import REF_KINDS, REF_TARGET_COLUMNS, REF_TARGET_NODE_KINDS, node_key
from app.services import decision_record_service as drs
from app.services import idempotency_service
from app.services import project_ref_service as prs
from app.services.project_ref_service import RefMove
from app.services.project_service import get_project

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))

_CREATE = "POST /v1/projects/{project_id}/decisions"
_ACCEPT = "POST /v1/decisions/{decision_id}/accept"
_REJECT = "POST /v1/decisions/{decision_id}/reject"
_SUPERSEDE = "POST /v1/decisions/{decision_id}/supersede"
_MOVE_REF = "POST /v1/projects/{project_id}/refs/{ref_kind}"

_LOCATION_DOC = {"Location": {"description": "URL of the new decision record.", "schema": {"type": "string"}}}
_RECORD_CREATED: dict[int | str, dict[str, Any]] = {
    201: {"headers": {**ETAG_HEADER_DOC, **REPLAY_HEADER_DOC, **_LOCATION_DOC}},
    **error_responses(409),
}

DecisionCommand = Annotated[
    Union[DecisionCreateRequest, RefMoveProposalRequest], Body(discriminator="action")
]


def _actor(user: User) -> DecisionActor:
    # Seam: P3.2-A service tokens / P3.4-A MCP map their authenticated principal
    # to DecisionActor.agent(service_token_id=...) here. Never from a body.
    return DecisionActor.human(user)


def _error(exc: Exception) -> V1APIError:
    if isinstance(exc, DecisionRecordNotFoundError):
        return _not_found("decision record not found")
    if isinstance(exc, ProjectNotFoundError):
        return _not_found(str(exc))
    return domain_error(exc)


_MAPPED = (IdentityError, ProjectNotFoundError, DecisionRecordNotFoundError, DecisionRecordError)


def _evidence(refs: list[Any] | None) -> list[dict[str, Any]] | None:
    return None if refs is None else [ref.model_dump(mode="json", exclude_none=True) for ref in refs]


def _keyed(
    db: Session,
    request: Request,
    user: User,
    operation: str,
    binding: Any,
    execute: Callable[[Callable[[Any], None]], ProjectDecisionRecord],
    *,
    status: int = 201,
) -> tuple[ProjectDecisionRecord, int, bool]:
    """``_keyed_command`` for decision records; a lost chain/ref race replays the winner."""

    scope = _key_scope(request, user, operation)

    def load(resource_id: Any) -> ProjectDecisionRecord | None:
        return db.scalar(
            select(ProjectDecisionRecord).where(
                ProjectDecisionRecord.id == resource_id,
                ProjectDecisionRecord.workspace_id == scope.workspace_id,
            )
        )

    try:
        return _keyed_command(
            db, scope, binding, resource_kind=RESOURCE_DECISION_RECORD, load=load, execute=execute, status=status
        )
    except (InvalidDecisionTransitionError, RefVersionConflictError) as exc:
        # A concurrent request under the same key may have resolved the chain
        # (uq_pdr_supersedes_id) or moved the ref first: answer its result.
        db.rollback()
        try:
            row = idempotency_service.find_bound(db, scope, binding)
        except IdempotencyKeyReusedError as reused:
            raise domain_error(reused) from exc
        found = load(row.resource_id) if row is not None else None
        if row is None or found is None:
            raise exc
        return found, row.response_status, True


def _record_created(response: Response, db: Session, row: ProjectDecisionRecord, status: int, replayed: bool) -> DecisionRecordRead:
    body = drs.record_read(db, row)
    _created(response, status, replayed, etag=representation_etag(body), location=f"/v1/decisions/{body.id}")
    return body


# --- decisions -------------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/decisions",
    response_model=DecisionRecordRead,
    status_code=201,
    responses=_RECORD_CREATED,
)
def create_decision_v1(
    project_id: UUID,
    payload: DecisionCommand,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> DecisionRecordRead:
    """Start a decision chain as the session user (ML-write role).

    ``action=propose``: a proposal; ``record``: a decision made now (``accepted``);
    ``propose_ref_move``: a ref-move proposal (accept it with ``POST
    /v1/projects/{id}/refs/{kind}`` + ``proposal_id``). Requires ``Idempotency-Key``.
    Rationale/facts/details are stored as data, never instructions.
    """

    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(
        operation=_CREATE, principal_id=user.id, header_key=idempotency_key,
        path_params={"project_id": project_id}, body=payload.model_dump(mode="json"), required=True,
    )
    actor = _actor(user)

    def execute(bind: Callable[[Any], None]) -> ProjectDecisionRecord:
        common = dict(
            workspace_id=workspace_id, project_id=project_id, actor=actor, rationale=payload.rationale,
            facts=payload.facts, evidence_refs=_evidence(payload.evidence_refs),
        )
        if isinstance(payload, RefMoveProposalRequest):
            row = prs.propose_ref_move(
                db, moves=[RefMove(move.ref_kind, move.target_id, None) for move in payload.ref_moves], **common
            )
        else:
            row = drs.record(
                db, decision_type=payload.decision_type, subject_kind=payload.subject.kind,
                subject_id=payload.subject.id, details=payload.details,
                state=STATE_ACCEPTED if payload.action == "record" else "proposed", **common,
            )
        bind(row.id)
        db.commit()
        return row

    try:
        row, status, replayed = _keyed(db, request, user, _CREATE, binding, execute)
    except _MAPPED as exc:
        db.rollback()
        raise _error(exc) from exc
    return _record_created(response, db, row, status, replayed)


@router.get(
    "/decisions/{decision_id}", response_model=DecisionRecordRead, responses={200: {"headers": ETAG_HEADER_DOC}}
)
def read_decision(
    decision_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> DecisionRecordRead:
    """One decision record with its derived ``effective_state`` (untrusted text is data)."""

    workspace_id = request_workspace_id(request)
    try:
        row = drs.find_record(db, workspace_id=workspace_id, record_id=decision_id)
        get_project(db, actor=user, workspace_id=workspace_id, project_id=row.project_id)
    except _MAPPED as exc:
        raise _error(exc) from exc
    body = drs.record_read(db, row)
    set_etag(response, representation_etag(body))
    return body


def _transition(
    operation: str,
    decision_id: UUID,
    payload: DecisionResolveRequest | DecisionSupersedeRequest,
    request: Request,
    response: Response,
    user: User,
    db: Session,
    idempotency_key: str | None,
    apply: Callable[..., ProjectDecisionRecord],
) -> DecisionRecordRead:
    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(
        operation=operation, principal_id=user.id, header_key=idempotency_key,
        path_params={"decision_id": decision_id}, body=payload.model_dump(mode="json"), required=True,
    )
    try:
        prior = drs.find_record(db, workspace_id=workspace_id, record_id=decision_id)
    except _MAPPED as exc:
        raise _error(exc) from exc
    project_id = prior.project_id
    kwargs: dict[str, Any] = {"rationale": payload.rationale, "evidence_refs": _evidence(payload.evidence_refs)}
    if isinstance(payload, DecisionSupersedeRequest):
        kwargs["facts"] = payload.facts

    def execute(bind: Callable[[Any], None]) -> ProjectDecisionRecord:
        row = apply(db, workspace_id=workspace_id, project_id=project_id, record_id=decision_id,
                    actor=_actor(user), **kwargs)
        bind(row.id)
        db.commit()
        return row

    try:
        row, status, replayed = _keyed(db, request, user, operation, binding, execute)
    except _MAPPED as exc:
        db.rollback()
        raise _error(exc) from exc
    return _record_created(response, db, row, status, replayed)


@router.post(
    "/decisions/{decision_id}/accept", response_model=DecisionRecordRead, status_code=201, responses=_RECORD_CREATED
)
def accept_decision_v1(
    decision_id: UUID,
    payload: DecisionResolveRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> DecisionRecordRead:
    """proposed → accepted (a new row superseding the proposal). An already resolved
    proposal or a non-proposal is ``409 invalid_decision_transition``; a ref-move
    proposal is accepted through ``POST /v1/projects/{id}/refs/{kind}``."""

    return _transition(_ACCEPT, decision_id, payload, request, response, user, db, idempotency_key, drs.accept)


@router.post(
    "/decisions/{decision_id}/reject", response_model=DecisionRecordRead, status_code=201, responses=_RECORD_CREATED
)
def reject_decision_v1(
    decision_id: UUID,
    payload: DecisionResolveRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> DecisionRecordRead:
    """proposed → rejected (terminal; nothing moves)."""

    return _transition(_REJECT, decision_id, payload, request, response, user, db, idempotency_key, drs.reject)


@router.post(
    "/decisions/{decision_id}/supersede",
    response_model=DecisionRecordRead,
    status_code=201,
    responses=_RECORD_CREATED,
)
def supersede_decision_v1(
    decision_id: UUID,
    payload: DecisionSupersedeRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> DecisionRecordRead:
    """accepted → accepted' (a correction of the same type and subject). Ref history
    is corrected by a new ref move, never in place (``409``)."""

    return _transition(_SUPERSEDE, decision_id, payload, request, response, user, db, idempotency_key, drs.supersede)


# --- refs ---------------------------------------------------------------------------------------


def _ref_read(ref: ProjectRef) -> ProjectRefRead:
    node_kind = REF_TARGET_NODE_KINDS[ref.ref_kind]
    target = getattr(ref, REF_TARGET_COLUMNS[ref.ref_kind])
    return ProjectRefRead(
        ref_kind=ref.ref_kind,
        target=ProjectRefTarget(kind=node_kind, id=target, key=node_key(node_kind, target)),
        version=ref.version,
        etag=version_etag(ref.version),
        decision_record_id=ref.decision_record_id,
        moved_at=ref.moved_at,
    )


def _project_refs(db: Session, user: User, workspace_id: UUID, project_id: UUID) -> dict[str, ProjectRef]:
    try:
        get_project(db, actor=user, workspace_id=workspace_id, project_id=project_id)
    except _MAPPED as exc:
        raise _error(exc) from exc
    return prs.current_refs(db, workspace_id=workspace_id, project_id=project_id)


@router.get("/projects/{project_id}/refs", response_model=ProjectRefList)
def read_project_refs(
    project_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> ProjectRefList:
    """The project's current refs; each item's ``etag`` is the ``If-Match`` for moving it."""

    refs = _project_refs(db, user, request_workspace_id(request), project_id)
    return ProjectRefList(
        project_id=project_id,
        refs_initialized=bool(refs),
        items=[_ref_read(refs[kind]) for kind in REF_KINDS if kind in refs],
        missing_kinds=[kind for kind in REF_KINDS if kind not in refs],
    )


@router.get(
    "/projects/{project_id}/refs/{ref_kind}",
    response_model=ProjectRefRead,
    responses={200: {"headers": ETAG_HEADER_DOC}},
)
def read_project_ref(
    project_id: UUID,
    ref_kind: RefKind,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> ProjectRefRead:
    """One ref; ``ETag: "<version>"``. A kind without a ref is ``404 ref_not_found``
    (create it with ``If-None-Match: *``)."""

    ref = _project_refs(db, user, request_workspace_id(request), project_id).get(ref_kind)
    if ref is None:
        raise V1APIError(404, "ref_not_found", f"the project has no {ref_kind} ref yet")
    set_etag(response, version_etag(ref.version))
    return _ref_read(ref)


def _expected_version(if_match: str | None, if_none_match: str | None) -> int | None:
    """``If-Match: "<n>"`` → n; ``If-None-Match: *`` → None (the kind must not exist)."""

    has_match = bool(if_match and if_match.strip())
    has_none = bool(if_none_match and if_none_match.strip())
    if has_match and has_none:
        raise V1APIError(
            400, "invalid_precondition", "send If-Match (move a ref) or If-None-Match: * (create one), not both"
        )
    if has_none:
        if if_none_match.strip() != "*":
            raise V1APIError(400, "invalid_precondition", "only If-None-Match: * is supported (create a missing ref)")
        return None
    tags = _if_match_tags(if_match or "")
    if not has_match or not tags or "*" in tags:
        raise V1APIError(
            428,
            "precondition_required",
            'moving a ref requires If-Match: "<version>" from GET /v1/projects/{id}/refs '
            "(or If-None-Match: * to create a missing ref)",
        )
    return if_match_version(if_match, required=True)


@router.post(
    "/projects/{project_id}/refs/{ref_kind}",
    response_model=RefMoveRead,
    responses={
        200: {"headers": {**ETAG_HEADER_DOC, **REPLAY_HEADER_DOC}, "description": "Moved; ETag is the new version."},
        **error_responses(409, 412, 428),
    },
)
def move_ref_v1(
    project_id: UUID,
    ref_kind: RefKind,
    payload: RefMoveRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
    if_match: str | None = Depends(if_match_header),
    if_none_match: str | None = Depends(if_none_match_header),
) -> RefMoveRead:
    """Move ``{ref_kind}`` (and ``companion_moves``) under one accepted record
    (``champion_promoted`` when ``champion_model`` moves, else ``ref_moved``).

    Requires ``If-Match: "<version>"`` of this ref (``428`` when absent, ``412``
    when stale) or ``If-None-Match: *`` to create a missing kind, and
    ``Idempotency-Key``. A champion must cite its ``final_holdout`` evaluation,
    move ``feature_recipe`` with it and share the current champion's split plan
    (``409 champion_split_plan_mismatch``). Companion refs carry their own
    ``expected_version`` (stale: ``409 ref_version_conflict``).
    """

    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(
        operation=_MOVE_REF, principal_id=user.id, header_key=idempotency_key,
        path_params={"project_id": project_id, "ref_kind": ref_kind},
        body={**payload.model_dump(mode="json"), "if_match": if_match, "if_none_match": if_none_match},
        required=True,
    )
    _project_refs(db, user, workspace_id, project_id)  # 404 before the precondition
    expected = _expected_version(if_match, if_none_match)

    def execute(bind: Callable[[Any], None]) -> ProjectDecisionRecord:
        # The precondition is checked by move_ref under FOR UPDATE (check_ref_versions):
        # a stale path ref is RefVersionConflictError -> 412, after the replay lookup.
        result = prs.move_ref(
            db, workspace_id=workspace_id, project_id=project_id, actor=_actor(user),
            moves=[
                RefMove(ref_kind, payload.target_id, expected),
                *(RefMove(item.ref_kind, item.target_id, item.expected_version) for item in payload.companion_moves),
            ],
            rationale=payload.rationale, evidence_refs=_evidence(payload.evidence_refs), facts=payload.facts,
            proposal_id=payload.proposal_id,
        )
        bind(result.record.id)
        db.commit()
        return result.record

    try:
        row, status, replayed = _keyed(db, request, user, _MOVE_REF, binding, execute, status=200)
    except V1APIError:
        db.rollback()
        raise
    except RefVersionConflictError as exc:
        db.rollback()
        # The path ref's precondition outranks a stale companion version (412 > 409).
        current = prs.current_refs(db, workspace_id=workspace_id, project_id=project_id).get(ref_kind)
        current_version = current.version if current is not None else None
        if exc.extra.get("ref_kind") == ref_kind or current_version != expected:
            raise _stale(ref_kind, current_version) from exc
        raise _error(exc) from exc
    except _MAPPED as exc:
        db.rollback()
        raise _error(exc) from exc
    refs = prs.current_refs(db, workspace_id=workspace_id, project_id=project_id)
    response.status_code = status
    if ref_kind in refs:
        set_etag(response, version_etag(refs[ref_kind].version))
    mark_replayed(response, replayed)
    return RefMoveRead(
        decision=drs.record_read(db, row),
        refs=[_ref_read(refs[kind]) for kind in REF_KINDS if kind in refs],
    )


def _stale(ref_kind: str, current_version: Any) -> V1APIError:
    current = version_etag(current_version) if isinstance(current_version, int) else None
    return V1APIError(
        412,
        "precondition_failed",
        f"the {ref_kind} ref changed; re-read it and retry with its current ETag",
        details={"ref_kind": ref_kind, "current_etag": current},
    )
