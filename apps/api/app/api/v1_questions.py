"""/v1 business-view questions (D7a): the data team asks the workspace's business users; they reply.

Transport only (``business_question_service``). Every route is for a signed-in session: any
``Authorization`` header (a service token or an API bearer) is ``403 session_required``, so no agent,
MCP tool or SDK reaches it (``HUMAN_ONLY_ROUTES``). Asking needs ML-write; replying is for the
workspace's business users and its data team; closing is the asker's or an owner's / administrator's
act with ``If-Match: "<version>"``. Another tenant's project or question is 404. Text is untrusted plain
text; reads carry no user ids or emails (``person`` with ``is_you``).
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id, require_session_workspace_member
from app.api.v1 import _created, _key_scope, _keyed_command, _not_found, _principal_id
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
    set_etag,
    version_etag,
)
from app.db.models import User
from app.db.session import get_db
from app.domain import business_questions as bq
from app.domain.errors import IdentityError, ProjectNotFoundError
from app.domain.idempotency import RESOURCE_BUSINESS_QUESTION, RESOURCE_BUSINESS_QUESTION_REPLY
from app.services import business_question_service as svc

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))

_NO_STORE = "private, no-store"  # untrusted text people wrote: never cached
_ASK = "POST /v1/projects/{project_id}/questions"
_REPLY = "POST /v1/questions/{question_id}/replies"
_CLOSE = "POST /v1/questions/{question_id}/close"
_WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    201: {"headers": {**ETAG_HEADER_DOC, **REPLAY_HEADER_DOC}}, **error_responses(409, 412, 428)}


def _error(exc: Exception) -> V1APIError:
    if isinstance(exc, svc.QuestionError):
        return V1APIError(exc.status_code, exc.code, exc.message)
    if isinstance(exc, ProjectNotFoundError):
        return _not_found(str(exc))
    return domain_error(exc)


def _detail(db: Session, request: Request, user: User, question_id: UUID, response: Response) -> bq.QuestionDetailRead:
    try:
        body = svc.question_detail(db, actor=user, workspace_id=request_workspace_id(request), question_id=question_id)
    except (svc.QuestionError, IdentityError) as exc:
        raise _error(exc) from exc
    set_etag(response, version_etag(body.version))
    response.headers["Cache-Control"] = _NO_STORE
    return body


def _write(db: Session, request: Request, user: User, operation: str, binding: Any, *, resource_kind: str,
           load: Callable[[Any], Any], execute: Callable[[Callable[[Any], None]], Any],
           status: int) -> tuple[UUID, int, bool]:
    try:
        return _keyed_command(db, _key_scope(request, user, operation), binding, resource_kind=resource_kind,
                              load=load, execute=execute, status=status)
    except V1APIError:
        db.rollback()
        raise
    except (svc.QuestionError, IdentityError, ProjectNotFoundError) as exc:
        db.rollback()
        raise _error(exc) from exc


def _respond(db: Session, request: Request, user: User, written: tuple[UUID, int, bool],
             response: Response) -> bq.QuestionDetailRead:
    question_id, code, replayed = written
    body = _detail(db, request, user, question_id, response)
    _created(response, code, replayed, etag=version_etag(body.version), location=f"/v1/questions/{question_id}")
    return body


_STATUS_Q = Query(None, description="Only questions in this status.")
_MINE_Q = Query(False, description="Only questions you asked or replied to.")
_LIMIT_Q = Query(bq.LIST_DEFAULT, ge=1, le=bq.LIST_MAX)


def _list(db: Session, request: Request, response: Response, user: User, project_id: UUID | None,
          status: str | None, mine: bool, limit: int) -> bq.QuestionList:
    response.headers["Cache-Control"] = _NO_STORE
    try:
        return svc.list_questions(db, actor=user, workspace_id=request_workspace_id(request), project_id=project_id,
                                  status=status, mine=mine, limit=limit)
    except (IdentityError, ProjectNotFoundError) as exc:
        raise _error(exc) from exc


@router.get("/questions", response_model=bq.QuestionList)
def read_workspace_questions(
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
    status: bq.QuestionStatus | None = _STATUS_Q,
    mine: bool = _MINE_Q,
    limit: int = _LIMIT_Q,
) -> bq.QuestionList:
    """The Business view: questions from the data team to this workspace's business users, all
    projects, open first, then answered, then closed. Any workspace reader; signed-in sessions only.
    `text` is untrusted plain text; people appear as `person` (`is_you` for you), never by id, name or
    email."""

    return _list(db, request, response, user, None, status, mine, limit)


@router.get("/projects/{project_id}/questions", response_model=bq.QuestionList)
def read_project_questions(
    project_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
    status: bq.QuestionStatus | None = _STATUS_Q,
    mine: bool = _MINE_Q,
    limit: int = _LIMIT_Q,
) -> bq.QuestionList:
    """One project's questions to the business (the same shape and order as `GET /v1/questions`)."""

    return _list(db, request, response, user, project_id, status, mine, limit)


@router.post("/projects/{project_id}/questions", response_model=bq.QuestionDetailRead, status_code=201,
             responses=_WRITE_RESPONSES)
def ask_question(
    project_id: UUID,
    payload: bq.QuestionAskRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> bq.QuestionDetailRead:
    """Ask the business users of this workspace a question about the project, optionally linked to one of
    its model versions, prediction lists or monitoring weeks. People with ML-write only (others `403
    question_ask_not_permitted`). The business users get an Inbox item in this API (the web shows open
    questions in the Business view, since business users do not open the Studio Inbox) and, where email
    is on, an email (never the text). Requires `Idempotency-Key`."""

    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(operation=_ASK, principal_id=_principal_id(request, user), header_key=idempotency_key,
                                  path_params={"project_id": project_id}, body=payload.model_dump(mode="json"),
                                  required=True)
    written = _write(
        db, request, user, _ASK, binding, resource_kind=RESOURCE_BUSINESS_QUESTION, status=201,
        load=lambda rid: svc.find_question(db, workspace_id, rid),
        execute=lambda bind: svc.ask(db, actor=user, workspace_id=workspace_id, project_id=project_id,
                                     request=payload, before_commit=lambda row: bind(row.id)).id)
    return _respond(db, request, user, written, response)


@router.get("/questions/{question_id}", response_model=bq.QuestionDetailRead,
            responses={200: {"headers": ETAG_HEADER_DOC}})
def read_question(
    question_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
) -> bq.QuestionDetailRead:
    """One question with its replies (oldest first). `ETag` is its version (send it to close)."""

    return _detail(db, request, user, question_id, response)


@router.post("/questions/{question_id}/replies", response_model=bq.QuestionDetailRead, status_code=201,
             responses=_WRITE_RESPONSES)
def reply_to_question(
    question_id: UUID,
    payload: bq.QuestionReplyRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> bq.QuestionDetailRead:
    """Reply (append-only). This workspace's business users reply (the question becomes `answered` and
    the asker is told); the data team adds follow-ups (it becomes `open` again). Viewers and platform
    staff `403 question_reply_not_permitted`; a closed question `409 question_closed`. Requires
    `Idempotency-Key`."""

    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(operation=_REPLY, principal_id=_principal_id(request, user),
                                  header_key=idempotency_key, path_params={"question_id": question_id},
                                  body=payload.model_dump(mode="json"), required=True)
    written = _write(db, request, user, _REPLY, binding, resource_kind=RESOURCE_BUSINESS_QUESTION_REPLY, status=201,
           load=lambda rid: svc.find_question(db, workspace_id, question_id, reply_id=rid),
           execute=lambda bind: svc.reply(db, actor=user, workspace_id=workspace_id, question_id=question_id,
                                          request=payload, before_commit=lambda item: bind(item.id)).id)
    return _respond(db, request, user, written, response)


@router.post("/questions/{question_id}/close", response_model=bq.QuestionDetailRead,
             responses={200: {"headers": {**ETAG_HEADER_DOC, **REPLAY_HEADER_DOC}}, **error_responses(409, 412, 428)})
def close_question(
    question_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_session_workspace_member),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
    if_match: str | None = Depends(if_match_header),
) -> bq.QuestionDetailRead:
    """Close the question (final: no more replies). The asker (still with ML-write) or a workspace owner or
    administrator; others `403`. Requires `If-Match` (its ETag): stale `412`, missing `428`;
    already closed `409 question_closed`. `Idempotency-Key` is optional."""

    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(operation=_CLOSE, principal_id=_principal_id(request, user),
                                  header_key=idempotency_key, path_params={"question_id": question_id}, body={})
    written = _write(db, request, user, _CLOSE, binding, resource_kind=RESOURCE_BUSINESS_QUESTION, status=200,
           load=lambda rid: svc.find_question(db, workspace_id, rid),
           execute=lambda bind: svc.close(
               db, actor=user, workspace_id=workspace_id, question_id=question_id,
               precondition=lambda version: check_if_match(if_match, version_etag(version), required=True),
               before_commit=lambda row: bind(row.id)).id)
    return _respond(db, request, user, written, response)
