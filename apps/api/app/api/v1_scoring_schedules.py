"""/v1 weekly scheduled scoring: read and save a project's schedule; list its runs.

Transport only (``scoring_schedule_service``). ``PUT`` is a signed-in person's act with an
ML-write role (Idempotency-Key; service tokens and agents get 403); each save writes an
accepted ``scoring_schedule_changed`` History entry. Reads are workspace-scoped (another
tenant's project is 404); service tokens get no user ids. The worker runs the schedule.
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id, require_workspace_ml_execution, require_workspace_read
from app.api.v1 import _bad_cursor, _created, _not_found, _principal_id
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
    set_etag,
)
from app.api.v1_decisions import _keyed as keyed_decision
from app.db.models import ProjectDecisionRecord, User
from app.db.session import get_db
from app.domain.errors import DecisionRecordError, IdentityError, InvalidCursorError, ProjectNotFoundError
from app.domain.scoring_schedules import (
    RUN_PAGE_MAX,
    ScoringScheduleRunPage,
    ScoringScheduleState,
    ScoringScheduleWrite,
)
from app.services.scoring_schedule_service import list_runs, save_schedule, schedule_state

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))

_SAVE_SCHEDULE = "PUT /v1/projects/{project_id}/scoring-schedule"


def _state(db: Session, request: Request, user: User, project_id: UUID) -> ScoringScheduleState:
    try:
        return schedule_state(db, actor=user, workspace_id=request_workspace_id(request), project_id=project_id,
                              agent=is_agent(request))
    except IdentityError as exc:
        raise domain_error(exc) from exc
    except ProjectNotFoundError as exc:
        raise _not_found(str(exc)) from exc


@router.get(
    "/projects/{project_id}/scoring-schedule",
    response_model=ScoringScheduleState,
    responses={200: {"headers": ETAG_HEADER_DOC}},
)
def read_scoring_schedule(
    project_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> ScoringScheduleState:
    """The project's weekly scoring schedule (``schedule`` null when never set), whether it is
    ``on``, ``off`` or ``paused_by_platform``, the next slot (UTC) and the newest 10 handled weeks.
    It scores only files uploaded for scoring, never retrains and never fetches data. Service
    tokens may read it (no user ids)."""

    body = _state(db, request, user, project_id)
    set_etag(response, representation_etag(body))
    return body


@router.put(
    "/projects/{project_id}/scoring-schedule",
    response_model=ScoringScheduleState,
    responses={200: {"headers": {**ETAG_HEADER_DOC, **REPLAY_HEADER_DOC}}, **error_responses(409)},
)
def save_scoring_schedule(
    project_id: UUID,
    payload: ScoringScheduleWrite,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> ScoringScheduleState:
    """Turn weekly scoring on or off, or change its day and hour (UTC). One schedule per
    project; ``enabled=false`` turns it off and keeps it and its runs. Each week it scores the
    newest file a person uploaded for scoring since the last run (files a connected tool uploaded
    with a service token are ignored; your own sign-in or own API token counts as you) with the
    model in use approved for scoring, acting as you (re-checked every run). Writes an accepted
    ``scoring_schedule_changed`` History entry (``rationale``: your reason). People with
    ML-write only: a service token gets ``403 service_token_not_permitted``, any agent
    ``403 human_session_required``, a viewer ``403 forbidden``. Requires ``Idempotency-Key``."""

    if is_agent(request):
        raise V1APIError(403, "human_session_required", "weekly scoring is set by people, not agents")
    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(
        operation=_SAVE_SCHEDULE, principal_id=_principal_id(request, user), header_key=idempotency_key,
        path_params={"project_id": project_id}, body=payload.model_dump(mode="json"), required=True,
    )

    def execute(bind: Callable[[Any], None]) -> ProjectDecisionRecord:
        row = save_schedule(db, actor=user, workspace_id=workspace_id, project_id=project_id, request=payload)
        bind(row.id)
        db.commit()
        return row

    try:
        _row, status, replayed = keyed_decision(db, request, user, _SAVE_SCHEDULE, binding, execute, status=200)
    except ProjectNotFoundError as exc:
        db.rollback()
        raise _not_found(str(exc)) from exc
    except (IdentityError, DecisionRecordError) as exc:
        db.rollback()
        raise domain_error(exc) from exc
    body = _state(db, request, user, project_id)
    _created(response, status, replayed, etag=representation_etag(body), location=None)
    return body


@router.get("/projects/{project_id}/scoring-schedule/runs", response_model=ScoringScheduleRunPage)
def read_scoring_schedule_runs(
    project_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
    cursor: str | None = Query(None, max_length=256, description="Opaque `next_cursor` of the previous page."),
    limit: int = Query(20, ge=1, le=RUN_PAGE_MAX),
) -> ScoringScheduleRunPage:
    """Every handled week of the project's schedule, newest first: ``queued`` with the week's
    prediction (its file is the weekly list) or why nothing was scored, in plain words."""

    try:
        return list_runs(db, actor=user, workspace_id=request_workspace_id(request), project_id=project_id,
                         cursor=cursor, limit=limit)
    except IdentityError as exc:
        raise domain_error(exc) from exc
    except ProjectNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except InvalidCursorError as exc:
        raise _bad_cursor(exc) from exc
