"""/v1/assistant: the in-app assistant's threads (P6.3-B2; ADR 0009 §7.2). Transport only.

Signed-in human sessions only: any ``Authorization`` header (service token or API bearer) is
``403 human_session_required``; CSRF and ``Origin`` are checked by the middleware for every
cookie POST (ADR 0001-0002). A thread is its creator's: another user of the workspace, and
any other workspace, get ``404``. Membership and ML-write authority are re-resolved on every
request (write tools only propose, and only for ML-write members). ``app.agents.assistant``
owns the rules.

**Reply rendering contract.** ``assistant_message.message`` is plain text whose only links are
``[label](dclab://<kind>/<id>)`` references to cited graph nodes: a model reply with any other
link, HTML, image or host name is rejected; a template (AI off) is built by DCLab and strips
link syntax from the names it quotes, but a name may still read like a host. Clients render
it with linkify off, map ``dclab://`` references to internal routes and never turn other text
into links. Model free text inside
proposals (``reason``, ``rationale``, ``business_objective``, ``intent``) and thread titles
are plain text too.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.agents.assistant import service as assistant
from app.api.deps import get_current_user, request_workspace_id, require_workspace_read
from app.api.v1 import _created, _key_scope, _keyed_command
from app.api.v1_conventions import (
    COMMON_ERROR_STATUSES,
    REPLAY_HEADER_DOC,
    V1APIError,
    error_responses,
    idempotency_binding,
    idempotency_key_header,
    mark_replayed,
)
from app.db.models import AgentRun, User
from app.db.session import get_db

router = APIRouter(prefix="/v1/assistant", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))

_CREATE = "POST /v1/assistant/threads"
_MESSAGE = "POST /v1/assistant/threads/{thread_id}/messages"
RESOURCE_THREAD = "assistant_thread"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _no_control(value: str | None) -> str | None:
    if value is not None and _CONTROL.search(value):
        raise ValueError("control characters are not allowed")
    return value


class AssistantThreadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    title: str | None = Field(default=None, max_length=200, description="Plain text (never rendered as markup).")
    _plain = field_validator("title")(_no_control)


class AssistantMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=assistant.MESSAGE_MAX_CHARS,
                      description="The new user message only; the server rebuilds the conversation from its record.")
    _plain = field_validator("text")(_no_control)


class AssistantThreadRead(BaseModel):
    id: UUID
    project_id: UUID
    title: str | None
    status: str
    created_at: datetime
    last_activity_at: datetime


class AssistantEventRead(BaseModel):
    type: str = Field(description="user_message, turn_started, tool_call, tool_result, step_rejected, call_refused, "
                                  "proposal_created, assistant_message, budget_exhausted or turn_done.")
    turn_id: UUID
    seq: int
    created_at: datetime | None = None
    data: dict[str, Any]


class AssistantThreadDetail(AssistantThreadRead):
    limits: dict[str, int]
    usage: dict[str, int]
    ai_available: bool
    quick_actions: list[str]
    events: list[AssistantEventRead]


def require_assistant_session(request: Request, db: Session = Depends(get_db)) -> User:
    """A signed-in person with workspace read; never a service token or bearer (ADR 0009 §7.2)."""

    if (request.headers.get("Authorization") or "").strip():
        raise HTTPException(status_code=403, detail={
            "code": "human_session_required", "message": "the assistant is for signed-in people; agents use MCP"})
    return require_workspace_read(request, get_current_user(request, db), db)


def _error(exc: assistant.AssistantError) -> V1APIError:
    return V1APIError(exc.status, exc.code, exc.message, details=exc.details or None)


def _thread_read(thread: AgentRun) -> AssistantThreadRead:
    return AssistantThreadRead(id=thread.id, project_id=thread.project_id, title=thread.title, status=thread.status,
                               created_at=thread.created_at, last_activity_at=thread.last_activity_at)


def _thread(request: Request, db: Session, user: User, thread_id: UUID) -> AgentRun:
    try:
        return assistant.load_thread(db, user=user, workspace_id=request_workspace_id(request), thread_id=thread_id)
    except assistant.AssistantError as exc:
        raise _error(exc) from exc


@router.post("/threads", response_model=AssistantThreadRead, status_code=201,
             responses={201: {"headers": REPLAY_HEADER_DOC}, **error_responses(409, 503)})
def create_thread(
    payload: AssistantThreadCreate,
    request: Request,
    response: Response,
    user: User = Depends(require_assistant_session),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> AssistantThreadRead:
    """Open an assistant thread on a project of the selected workspace. Requires ``Idempotency-Key``."""

    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(operation=_CREATE, principal_id=user.id, header_key=idempotency_key,
                                  body=payload.model_dump(mode="json"), required=True)
    title = " ".join((payload.title or "").split()) or None

    def load(resource_id: Any) -> AgentRun | None:
        try:
            return assistant.load_thread(db, user=user, workspace_id=workspace_id, thread_id=resource_id)
        except assistant.AssistantError:
            return None

    def execute(bind: Callable[[Any], None]) -> AgentRun:
        spec = assistant.thread_spec(db, user=user, workspace_id=workspace_id, project_id=payload.project_id)
        return assistant.open_thread(db, spec, title=title, bind=bind)

    try:
        thread, status, replayed = _keyed_command(db, _key_scope(request, user, _CREATE), binding,
                                                  resource_kind=RESOURCE_THREAD, load=load, execute=execute)
    except assistant.AssistantError as exc:
        db.rollback()
        raise _error(exc) from exc
    db.refresh(thread)
    _created(response, status, replayed, etag=None, location=f"/v1/assistant/threads/{thread.id}")
    return _thread_read(thread)


@router.get("/threads", response_model=list[AssistantThreadRead])
def list_threads(
    request: Request,
    project_id: UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(require_assistant_session),
    db: Session = Depends(get_db),
) -> list[AssistantThreadRead]:
    """The caller's own threads in the selected workspace, most recently active first."""

    rows = assistant.list_threads(db, user=user, workspace_id=request_workspace_id(request), project_id=project_id,
                                  limit=limit)
    return [_thread_read(row) for row in rows]


@router.get("/threads/{thread_id}", response_model=AssistantThreadDetail)
def read_thread(
    thread_id: UUID,
    request: Request,
    user: User = Depends(require_assistant_session),
    db: Session = Depends(get_db),
) -> AssistantThreadDetail:
    """The caller's thread: limits, usage over its turns, whether the model is on, and its history
    (the public events of every turn, oldest first; at most the last 400)."""

    from app.agents.governance.policy import PolicyUnavailable, effective_policy

    thread = _thread(request, db, user, thread_id)
    try:
        held = assistant.allowance(db, thread, effective_policy(db, thread.workspace_id).policy)
        limits, used = held.limits.model_dump(exclude={"tool_calls"}), held.used
    except PolicyUnavailable:
        limits, used = {}, {}
    return AssistantThreadDetail(
        **_thread_read(thread).model_dump(), limits=limits, usage=used,
        ai_available=assistant.ai_blocked(db, thread) is None, quick_actions=list(assistant.QUICK_ACTIONS),
        events=[AssistantEventRead(**item) for item in assistant.history(db, thread)])


def _sse(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    async def stream() -> AsyncIterator[str]:
        async for item in events:
            body = json.dumps({"turn_id": item["turn_id"], **item["data"]}, default=str, separators=(",", ":"))
            yield f"id: {item['seq']}\nevent: {item['type']}\ndata: {body}\n\n"

    return stream()


@router.post(
    "/threads/{thread_id}/messages",
    response_class=StreamingResponse,
    responses={200: {"description": "`text/event-stream`: one event per step (`id` = the event's sequence in the "
                                    "turn, `event` = its type, `data` = JSON), ending with `turn_done`.",
                     "content": {"text/event-stream": {"schema": {"type": "string"}}},
                     "headers": REPLAY_HEADER_DOC},
               **error_responses(409, 429, 503)},
)
def post_message(
    thread_id: UUID,
    payload: AssistantMessageCreate,
    request: Request,
    user: User = Depends(require_assistant_session),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> StreamingResponse:
    """Run one turn and stream it as Server-Sent Events: ``turn_started``, ``tool_call``, ``tool_result``
    (shaped, holdout-free preview), ``step_rejected`` (the model's step failed validation),
    ``call_refused`` (a tool or model call refused by capability, limit or policy), ``proposal_created``
    (pending your confirmation), ``assistant_message``, ``budget_exhausted`` and ``turn_done``. Requires
    ``Idempotency-Key``: the same key replays the stored turn (never a second turn or proposal); a turn
    already running on the thread is ``409 turn_in_progress``; an exhausted thread or user limit is ``429``.
    With AI off the turn is a deterministic template (``templated: true``, ``llm_used: false``). Replies
    follow the rendering contract above: plain text, ``dclab://`` references only, linkify off. A dropped
    stream never stops the turn: resend the same request and key to replay it (``Last-Event-ID`` is
    not read yet)."""

    thread = _thread(request, db, user, thread_id)
    binding = idempotency_binding(operation=_MESSAGE, principal_id=user.id, header_key=idempotency_key,
                                  path_params={"thread_id": thread_id}, body=payload.model_dump(mode="json"),
                                  required=True)
    try:
        turn = assistant.post_message(db, user=user, thread=thread, text=payload.text,
                                      scope=_key_scope(request, user, _MESSAGE), binding=binding)
    except assistant.AssistantError as exc:
        db.rollback()
        raise _error(exc) from exc
    response = StreamingResponse(_sse(turn.events), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
    mark_replayed(response, turn.replayed)
    return response
