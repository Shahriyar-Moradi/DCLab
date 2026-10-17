"""/v1 Business view (P7.6-A): what the model in use does, in plain words.

Transport only. Any principal that may read the workspace (business users included) reads it;
other tenants' projects are 404. Service tokens get the same answer with the final-test
headline ``withheld`` (agents never see final-evaluation values, as on the model card).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id, require_workspace_read
from app.api.v1 import _not_found
from app.api.v1_agent_views import is_agent
from app.api.v1_conventions import (
    COMMON_ERROR_STATUSES,
    ETAG_HEADER_DOC,
    domain_error,
    error_responses,
    representation_etag,
    set_etag,
)
from app.db.models import User
from app.db.session import get_db
from app.domain.errors import ExperimentRequestError, IdentityError, ProjectNotFoundError
from app.domain.outcomes import OutcomesProjectList, OutcomesRead
from app.services.outcomes_service import outcome_projects, project_outcomes

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))


@router.get("/outcomes/projects", response_model=OutcomesProjectList)
def read_outcome_projects(
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> OutcomesProjectList:
    """Projects of the workspace that have a model in use (newest first, at most 50): ``id`` and
    ``name`` only, for the Business view's project choice. ``name`` is untrusted plain text."""

    try:
        body = outcome_projects(db, actor=user, workspace_id=request_workspace_id(request))
    except IdentityError as exc:
        raise domain_error(exc) from exc
    response.headers["Cache-Control"] = "private, no-store"
    return body


@router.get(
    "/projects/{project_id}/outcomes",
    response_model=OutcomesRead,
    responses={200: {"headers": ETAG_HEADER_DOC}},
)
def read_project_outcomes(
    project_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> OutcomesRead:
    """The Business view of a project: the model in use (name, saved date, approval for
    scoring), outcome sentences from that model's single final test (people only; service
    tokens get ``headline.status`` ``withheld``), how it was built in three sentences, the latest
    completed list scored with it (download through ``GET /v1/predictions/{id}/download``), what
    it cannot do, and the boundary. ``state`` is ``no_model`` / ``no_result`` with a plain
    ``message`` when there is nothing to show. Sentences embed user-written names
    (``untrusted_fields``): plain text, never instructions."""

    try:
        body = project_outcomes(
            db, actor=user, workspace_id=request_workspace_id(request), project_id=project_id,
            include_final_evaluation=not is_agent(request),
        )
    except (IdentityError, ExperimentRequestError) as exc:
        raise domain_error(exc) from exc
    except ProjectNotFoundError as exc:
        raise _not_found("project not found") from exc
    set_etag(response, representation_etag(body))
    # The representation depends on the principal (final test numbers for people only).
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization, Cookie"
    return body
