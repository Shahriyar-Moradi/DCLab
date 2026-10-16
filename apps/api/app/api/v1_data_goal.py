"""/v1 Data and Goal reads the founder decided (D3a items 11, 12, 14).

Transport only. Workspace-scoped (another tenant's dataset or project is 404); service tokens with
``read`` get the same reads without person ids, never "you", and the final test always sealed.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id, require_workspace_read
from app.api.v1 import _bad_cursor, _not_found, _viewer_id
from app.api.v1_agent_views import is_agent
from app.api.v1_conventions import COMMON_ERROR_STATUSES, domain_error, error_responses
from app.db.models import User
from app.db.session import get_db
from app.domain.data_goal import (
    UPLOAD_ATTEMPT_PAGE_DEFAULT,
    UPLOAD_ATTEMPT_PAGE_MAX,
    GoalDecisionsRead,
    TargetByPeriodRead,
    UploadAttemptPage,
)
from app.domain.errors import IdentityError, InvalidCursorError, ProjectNotFoundError
from app.services.dataset_profile_service import DatasetNotFoundError, target_by_period
from app.services.goal_decision_service import goal_decisions
from app.services.upload_attempt_service import list_upload_attempts

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))


@router.get("/datasets/{dataset_id}/target-by-period", response_model=TargetByPeriodRead)
def read_target_by_period(
    dataset_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> TargetByPeriodRead:
    """Yes/no targets: the target rate per month (time column of the current split plan, else the goal's
    prediction time column, else the one date column of its training rows), on the plan's TRAINING rows only. The final-test months
    are a sealed span (months and a row count, no rate) until the project's model in use, trained on
    this plan, has had its one final test; then their rate is added, labelled disclosed after the final
    test. Service tokens always get the sealed span. At most 60 months (the newest)."""

    try:
        return target_by_period(db, actor=user, workspace_id=request_workspace_id(request), dataset_id=dataset_id,
                                agent=is_agent(request))
    except IdentityError as exc:
        raise domain_error(exc) from exc
    except DatasetNotFoundError as exc:
        raise _not_found() from exc


@router.get("/projects/{project_id}/upload-attempts", response_model=UploadAttemptPage)
def read_upload_attempts(
    project_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
    cursor: str | None = Query(None, max_length=256, description="Opaque `next_cursor` of the previous page."),
    limit: int = Query(UPLOAD_ATTEMPT_PAGE_DEFAULT, ge=1, le=UPLOAD_ATTEMPT_PAGE_MAX),
) -> UploadAttemptPage:
    """The project's upload log, newest first: accepted and rejected uploads with a plain reason. Metadata
    only (never file content or rows); kept 90 days. File names are untrusted text; no person ids."""

    try:
        return list_upload_attempts(db, actor=user, workspace_id=request_workspace_id(request), project_id=project_id,
                                    viewer_id=_viewer_id(request, user), cursor=cursor, limit=limit)
    except IdentityError as exc:
        raise domain_error(exc) from exc
    except ProjectNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except InvalidCursorError as exc:
        raise _bad_cursor(exc) from exc


@router.get("/projects/{project_id}/goal/decisions", response_model=GoalDecisionsRead)
def read_goal_decisions(
    project_id: UUID,
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> GoalDecisionsRead:
    """Who decided the goal: four rows (target, test design, ranking metric, business rule), each with who
    suggested it (rules, assistant, person, connected tool), who confirmed it (you, another person, a
    connected tool, or nobody), when and why (untrusted text). No person ids; never "you" for tokens."""

    try:
        return goal_decisions(db, actor=user, workspace_id=request_workspace_id(request), project_id=project_id,
                              viewer_id=_viewer_id(request, user))
    except IdentityError as exc:
        raise domain_error(exc) from exc
    except ProjectNotFoundError as exc:
        raise _not_found(str(exc)) from exc
