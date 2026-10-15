"""/v1 operator reads for DCLab platform staff (P8.5-A; the P8.5-UI ``/operator`` console).

* Signed-in people only: any ``Authorization`` header (API bearer or service token) is ``403
  human_session_required``; then the existing platform guard (``require_platform_read``: a
  ``dclab_admin`` / ``dclab_developer`` platform role) — every workspace role is ``403``.
* Cross-tenant by design and read-only: ids, codes and counts. No customer rows, values, file
  names, prompts, job payloads or free-text failure reasons. Workspace names are customer text.
* ``Cache-Control: private, no-store``. No MCP tool and no service-token scope (HUMAN_ONLY_ROUTES).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_platform_read
from app.api.v1_conventions import COMMON_ERROR_STATUSES, V1APIError, error_responses
from app.db.models import User
from app.db.session import get_db
from app.domain.errors import InvalidCursorError
from app.domain.operator import OperatorFailedJobPage, OperatorOverviewRead, OperatorWorkspacePage
from app.services import operator_service

router = APIRouter(prefix="/v1/operator", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))
_NO_STORE = "private, no-store"


def require_operator_session(request: Request, db: Session = Depends(get_db)) -> User:
    if (request.headers.get("Authorization") or "").strip():
        raise HTTPException(status_code=403, detail={
            "code": "human_session_required", "message": "the operator console is for signed-in platform staff"})
    return require_platform_read(request, get_current_user(request, db), db)


@router.get("/overview", response_model=OperatorOverviewRead)
def read_overview(response: Response, db: Session = Depends(get_db),
                  _user: User = Depends(require_operator_session)) -> OperatorOverviewRead:
    """Platform health in counts: workspaces, jobs of the last 24 h by type and status, queue
    depth and oldest age, workers (no ids), failure codes, platform AI caps, readiness."""

    response.headers["Cache-Control"] = _NO_STORE
    return operator_service.overview(db)


@router.get("/workspaces", response_model=OperatorWorkspacePage)
def read_workspaces(
    response: Response,
    cursor: str | None = Query(None, max_length=256),
    limit: int = Query(operator_service.PAGE_DEFAULT, ge=1, le=operator_service.PAGE_MAX),
    db: Session = Depends(get_db),
    _user: User = Depends(require_operator_session),
) -> OperatorWorkspacePage:
    """Every workspace (oldest first): id, name, kind, member / project / job counts."""

    response.headers["Cache-Control"] = _NO_STORE
    try:
        return operator_service.list_workspaces(db, cursor=cursor, limit=limit)
    except InvalidCursorError as exc:
        raise V1APIError(400, "invalid_cursor", str(exc)) from exc


@router.get("/failed-jobs", response_model=OperatorFailedJobPage)
def read_failed_jobs(
    response: Response,
    cursor: str | None = Query(None, max_length=256),
    limit: int = Query(operator_service.PAGE_DEFAULT, ge=1, le=operator_service.PAGE_MAX),
    db: Session = Depends(get_db),
    _user: User = Depends(require_operator_session),
) -> OperatorFailedJobPage:
    """Failed ML jobs, newest first: job and workspace ids, type, handler, failure code, attempts."""

    response.headers["Cache-Control"] = _NO_STORE
    try:
        return operator_service.list_failed_jobs(db, cursor=cursor, limit=limit)
    except InvalidCursorError as exc:
        raise V1APIError(400, "invalid_cursor", str(exc)) from exc
