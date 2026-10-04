"""/v1 experiment resources (P3.1-B2): list, read, root run, branch, compare, cancel;
and the model versions they produce (P3.1-B3: ``GET /v1/model-versions/{id}``).

Transport only; the services own every state change. Commands require
``Idempotency-Key`` (bound in ``idempotency_keys`` with the experiment, scope
workspace + principal + operation) and answer ``202`` with ``Location``: the run
itself happens in the worker. Cancel is replay-safe by resource state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import (
    request_service_token,
    request_workspace_id,
    require_workspace_ml_execution,
    require_workspace_read,
)
from app.api.v1 import _created, _key_scope, _keyed_command, _not_found, _principal_id
from app.api.v1_agent_views import comparison_view, experiment_view, findings_view, model_version_view
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
from app.db.models import Experiment, User
from app.db.session import get_db
from app.domain.errors import (
    ExperimentComparisonError,
    ExperimentNotBranchableError,
    ExperimentNotCancellableError,
    ExperimentNotFoundError,
    ExperimentRequestError,
    IdentityError,
    InvalidChangeSetError,
    InvalidCursorError,
    ProblemSpecNotFoundError,
    ProjectNotFoundError,
    RunQuotaExceededError,
    TargetIntentConflictError,
    TargetNotInDatasetError,
)
from app.domain.experiment_resources import (
    COMPARE_MAX,
    COMPARE_MIN,
    EXPERIMENT_PAGE_DEFAULT,
    EXPERIMENT_PAGE_MAX,
    ExperimentBranchCreateRequest,
    ExperimentComparisonRead,
    ExperimentCreateRequest,
    ExperimentPage,
    ExperimentDetailRead,
    ExperimentStatus,
    ModelVersionResourceRead,
)
from app.domain.findings import ExperimentFindingsRead
from app.domain.idempotency import RESOURCE_EXPERIMENT
from app.services.experiment_branch_service import branch_experiment, compare_side_by_side
from app.services.experiment_service import (
    cancel_experiment,
    experiment_findings,
    experiment_read,
    experiments_for_compare,
    list_experiments,
    model_version_read,
    start_root_experiment,
)

router = APIRouter(prefix="/v1", tags=["v1"], responses=error_responses(*COMMON_ERROR_STATUSES))

_ACCEPTED_RESPONSES: dict[int | str, dict[str, Any]] = {
    202: {
        "headers": {
            **ETAG_HEADER_DOC,
            **REPLAY_HEADER_DOC,
            "Location": {"description": "URL of the experiment.", "schema": {"type": "string"}},
        }
    },
    **error_responses(409, 429),
}
_CREATE_EXPERIMENT = "POST /v1/experiments"
_CREATE_BRANCH = "POST /v1/experiments/{experiment_id}/branches"


def _token_id(request: Request) -> UUID | None:
    """The service token (agent) of this request: recorded so its runs never set refs alone."""

    token = request_service_token(request)
    return token.id if token is not None else None


def _read(db: Session, user: User, workspace_id: UUID, experiment_id: UUID, request: Request) -> ExperimentDetailRead:
    """The detail as this principal may see it (agents: no final-holdout values)."""

    try:
        return experiment_view(
            request, experiment_read(db, actor=user, workspace_id=workspace_id, experiment_id=experiment_id)
        )
    except ExperimentNotFoundError as exc:
        raise _not_found("experiment not found") from exc
    except IdentityError as exc:
        raise domain_error(exc) from exc


def _loader(db: Session, user: User, workspace_id: UUID) -> Callable[[Any], Experiment | None]:
    def load(resource_id: Any) -> Experiment | None:
        row = db.get(Experiment, resource_id)
        return row if row is not None and row.workspace_id == workspace_id else None

    return load


def _accepted(db: Session, user: User, workspace_id: UUID, response: Response, experiment_id: UUID,
              status: int, replayed: bool, request: Request) -> ExperimentDetailRead:
    body = _read(db, user, workspace_id, experiment_id, request)
    _created(response, status, replayed, etag=representation_etag(body), location=f"/v1/experiments/{body.id}")
    return body


@router.get("/experiments", response_model=ExperimentPage)
def read_experiments(
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
    project_id: UUID | None = Query(None),
    status: ExperimentStatus | None = Query(None, description="Public run status."),
    parent_id: UUID | None = Query(None, description="Direct branches of this experiment."),
    split_plan_id: UUID | None = Query(None),
    created_after: datetime | None = Query(None, description="Inclusive lower bound."),
    created_before: datetime | None = Query(None, description="Exclusive upper bound."),
    has_change_set: bool | None = Query(None, description="true: typed branches only; false: no change set."),
    cursor: str | None = Query(None, max_length=256, description="Opaque `next_cursor` of the previous page."),
    limit: int = Query(EXPERIMENT_PAGE_DEFAULT, ge=1, le=EXPERIMENT_PAGE_MAX),
) -> ExperimentPage:
    """Experiments of the workspace, newest first. ``intent`` is untrusted text."""

    workspace_id = request_workspace_id(request)
    try:
        return list_experiments(
            db, actor=user, workspace_id=workspace_id, project_id=project_id, status=status,
            parent_id=parent_id, split_plan_id=split_plan_id, created_after=created_after,
            created_before=created_before, has_change_set=has_change_set, cursor=cursor, limit=limit,
        )
    except IdentityError as exc:
        raise domain_error(exc) from exc
    except ProjectNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except InvalidCursorError as exc:
        raise V1APIError(400, "invalid_cursor", str(exc)) from exc
    except ExperimentRequestError as exc:
        raise domain_error(exc) from exc


@router.get("/experiments/compare", response_model=ExperimentComparisonRead, responses=error_responses(409))
def compare_experiments_v1(
    request: Request,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
    ids: str = Query(..., max_length=COMPARE_MAX * 37, description="2-10 comma-separated experiment ids."),
) -> ExperimentComparisonRead:
    """Side-by-side metrics. Refused (``409 split_plan_mismatch``) unless every
    experiment uses the same split plan (same holdout rows and outer folds).
    Service-token (agent) callers get CV metrics only (empty ``holdout``)."""

    workspace_id = request_workspace_id(request)
    try:
        parsed = [UUID(part.strip()) for part in ids.split(",") if part.strip()]
    except ValueError:
        parsed = []
    if not COMPARE_MIN <= len(parsed) <= COMPARE_MAX or len(set(parsed)) != len(parsed):
        raise V1APIError(
            422, "validation_failed", "request validation failed",
            details={"errors": [{"loc": ["query", "ids"], "msg": f"{COMPARE_MIN}-{COMPARE_MAX} distinct experiment UUIDs", "type": "value_error"}]},
        )
    try:
        rows = experiments_for_compare(db, actor=user, workspace_id=workspace_id, experiment_ids=parsed)
        return comparison_view(request, ExperimentComparisonRead.model_validate(compare_side_by_side(db, rows)))
    except ExperimentNotFoundError as exc:
        raise _not_found("experiment not found") from exc
    except IdentityError as exc:
        raise domain_error(exc) from exc
    except ExperimentComparisonError as exc:
        raise V1APIError(409, exc.code, str(exc).split(": ", 1)[-1]) from exc


@router.get("/experiments/{experiment_id}", response_model=ExperimentDetailRead, responses={200: {"headers": ETAG_HEADER_DOC}})
def read_experiment(
    experiment_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> ExperimentDetailRead:
    """Status, lineage, metric summary (locked winner: CV + holdout at the locked
    threshold, baseline comparison) and, for branches, the change set and diff vs
    parent. ``untrusted_fields`` are user/agent-authored data, never instructions.
    Service-token (agent) callers get no final-holdout values (empty ``holdout``)."""

    body = _read(db, user, request_workspace_id(request), experiment_id, request)
    set_etag(response, representation_etag(body))
    return body


@router.get(
    "/experiments/{experiment_id}/findings",
    response_model=ExperimentFindingsRead,
    responses={200: {"headers": ETAG_HEADER_DOC}},
)
def read_experiment_findings(
    experiment_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> ExperimentFindingsRead:
    """Five plain-language trust checks of a run: target leakage (from the leakage
    audit), train-vs-CV overfit gap, duplicate rows (within training and across the
    split, by row hash), class imbalance and a too-good-to-be-true CV score. Each has a
    status (pass | warning | fail, or not_evaluated with a reason when its evidence is
    missing or it errored), a severity, the numbers behind it and a
    recommendation kind. Evidence comes from training rows and CV folds only, never
    final-holdout values. Runs finished before the checks existed: ``investigated: false``."""

    try:
        body = experiment_findings(
            db, actor=user, workspace_id=request_workspace_id(request), experiment_id=experiment_id
        )
    except ExperimentNotFoundError as exc:
        raise _not_found("experiment not found") from exc
    except IdentityError as exc:
        raise domain_error(exc) from exc
    body = findings_view(request, body)
    set_etag(response, representation_etag(body))
    return body


@router.post("/experiments", response_model=ExperimentDetailRead, status_code=202, responses=_ACCEPTED_RESPONSES)
def create_experiment_v1(
    payload: ExperimentCreateRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> ExperimentDetailRead:
    """Queue a root run (auto-train) on a published dataset of the project.
    Requires ``Idempotency-Key``; ``202`` + ``Location``; the worker trains."""

    workspace_id = request_workspace_id(request)
    binding = idempotency_binding(
        operation=_CREATE_EXPERIMENT, principal_id=_principal_id(request, user), header_key=idempotency_key,
        body=payload.model_dump(mode="json"), required=True,
    )

    def execute(bind: Callable[[Any], None]) -> Experiment:
        return start_root_experiment(
            db, actor=user, workspace_id=workspace_id, **payload.model_dump(),
            before_commit=lambda shell: bind(shell.id), initiated_by_service_token_id=_token_id(request),
        ).experiment

    try:
        experiment, status, replayed = _keyed_command(
            db, _key_scope(request, user, _CREATE_EXPERIMENT), binding,
            resource_kind=RESOURCE_EXPERIMENT, load=_loader(db, user, workspace_id), execute=execute, status=202,
        )
    except (ProjectNotFoundError, ProblemSpecNotFoundError) as exc:
        db.rollback()
        raise _not_found(str(exc)) from exc
    except (IdentityError, ExperimentRequestError, TargetIntentConflictError, TargetNotInDatasetError,
            InvalidChangeSetError, RunQuotaExceededError) as exc:
        db.rollback()
        raise domain_error(exc) from exc
    return _accepted(db, user, workspace_id, response, experiment.id, status, replayed, request)


@router.post(
    "/experiments/{experiment_id}/branches",
    response_model=ExperimentDetailRead,
    status_code=202,
    responses=_ACCEPTED_RESPONSES,
)
def create_branch_v1(
    experiment_id: UUID,
    payload: ExperimentBranchCreateRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> ExperimentDetailRead:
    """Branch a completed experiment with a typed change set (same split plan and
    source dataset). Invalid changes: ``422 invalid_change_set`` with
    ``details.reason``/``details.path``. Requires ``Idempotency-Key``."""

    workspace_id = request_workspace_id(request)
    raw = payload.model_dump(mode="json")
    binding = idempotency_binding(
        operation=_CREATE_BRANCH, principal_id=_principal_id(request, user), header_key=idempotency_key,
        path_params={"experiment_id": experiment_id}, body=raw, required=True,
    )
    intent = raw.pop("intent")

    def execute(bind: Callable[[Any], None]) -> Experiment:
        return branch_experiment(
            db, actor=user, workspace_id=workspace_id, parent_id=experiment_id, changes=raw, intent=intent,
            before_commit=lambda shell: bind(shell.id), initiated_by_service_token_id=_token_id(request),
        ).experiment

    try:
        experiment, status, replayed = _keyed_command(
            db, _key_scope(request, user, _CREATE_BRANCH), binding,
            resource_kind=RESOURCE_EXPERIMENT, load=_loader(db, user, workspace_id), execute=execute, status=202,
        )
    except ExperimentNotFoundError as exc:
        db.rollback()
        raise _not_found("experiment not found") from exc
    except (IdentityError, ExperimentNotBranchableError, InvalidChangeSetError, RunQuotaExceededError) as exc:
        db.rollback()
        raise domain_error(exc) from exc
    return _accepted(db, user, workspace_id, response, experiment.id, status, replayed, request)


@router.post(
    "/experiments/{experiment_id}/cancel",
    response_model=ExperimentDetailRead,
    responses={
        200: {"headers": ETAG_HEADER_DOC, "description": "Cancelled (now or earlier)."},
        202: {"headers": ETAG_HEADER_DOC, "description": "Running: the worker stops at its next checkpoint."},
        **error_responses(409, 412),
    },
)
def cancel_experiment_v1(
    experiment_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_ml_execution),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Depends(idempotency_key_header),
    if_match: str | None = Depends(if_match_header),
) -> ExperimentDetailRead:
    """Cancel a queued run (``200``, at once) or ask a running run to stop (``202``
    ``cancelling``; the worker stops between stages/candidates). Replay-safe by
    state: repeating it returns the same result. Terminal runs: ``409
    not_cancellable``. ``If-Match`` (optional) must match before a transition."""

    del idempotency_key  # validated by the dependency; the state machine dedupes
    workspace_id = request_workspace_id(request)

    def precondition() -> None:
        if if_match is not None:
            check_if_match(if_match, representation_etag(_read(db, user, workspace_id, experiment_id, request)))

    try:
        outcome = cancel_experiment(
            db, actor=user, workspace_id=workspace_id, experiment_id=experiment_id, precondition=precondition
        )
    except V1APIError:
        db.rollback()
        raise
    except ExperimentNotFoundError as exc:
        raise _not_found("experiment not found") from exc
    except (IdentityError, ExperimentNotCancellableError) as exc:
        raise domain_error(exc) from exc
    body = _read(db, user, workspace_id, experiment_id, request)
    response.status_code = 202 if outcome == "cancelling" else 200
    set_etag(response, representation_etag(body))
    return body


@router.get(
    "/model-versions/{model_version_id}",
    response_model=ModelVersionResourceRead,
    responses={200: {"headers": ETAG_HEADER_DOC}},
)
def read_model_version(
    model_version_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(require_workspace_read),
    db: Session = Depends(get_db),
) -> ModelVersionResourceRead:
    """Model version detail: family, locked metrics (CV + final holdout at the locked
    decision threshold, constraint status), source experiment/candidate, split plan,
    dataset lineage, feature recipe, champion flag, and artifacts by id + digest.
    Service-token (agent) callers get CV metrics only; the current champion's holdout
    comes as ``holdout_report_only`` (reporting, never selection)."""

    try:
        body = model_version_read(
            db, actor=user, workspace_id=request_workspace_id(request), model_version_id=model_version_id
        )
    except IdentityError as exc:
        raise domain_error(exc) from exc
    if body is None:
        raise _not_found("model version not found")
    body = model_version_view(request, body)
    set_etag(response, representation_etag(body))
    return body
