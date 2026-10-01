from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, request_workspace_id, require_workspace_read
from app.db.models import User
from app.db.session import get_db
from app.domain.observability import (
    LlmInvocationRead,
    MlRunEventRead,
    PipelineSummaryRead,
    WorkflowRunPipelineRead,
)
from app.services import observatory_query_service
from app.services.audience_projection import event_read, invocation_read, public_diagnostic
from app.services.authorization_service import AuthorizationError
from app.services.observability_service import AUDIT_PURPOSES, SEMANTIC_PURPOSES
from app.services.workspace_capability_service import (
    CV_FOLD_DETAILS,
    OPENAI_PIPELINE_AUDIT,
    PIPELINE_MONITOR,
    RAW_PIPELINE_DEBUG,
    SEMANTIC_LLM_AUDIT,
    capability_matrix,
    require_modern_business_capability,
)

admin_router = APIRouter(
    prefix="/observatory",
    tags=["pipeline-observatory"],
    dependencies=[Depends(require_workspace_read)],
)
business_router = APIRouter(prefix="/observatory", tags=["pipeline-observatory"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="pipeline observability record not found")


def _admin_workspace_id(request: Request, query_workspace_id: UUID | None) -> UUID:
    selected = request_workspace_id(request)
    if query_workspace_id is not None and query_workspace_id != selected:
        raise _not_found()
    return selected


def _summary(db: Session, experiment_id: UUID, workspace_id: UUID):
    row = observatory_query_service.get_pipeline_summary(
        db, experiment_id, workspace_id=workspace_id
    )
    if row is None:
        raise _not_found()
    return public_diagnostic(row)


def _events(
    db: Session,
    experiment_id: UUID,
    workspace_id: UUID,
    after_sequence: int,
):
    rows = observatory_query_service.list_pipeline_events(
        db,
        experiment_id,
        workspace_id=workspace_id,
        after_sequence=after_sequence,
    )
    if rows is None:
        raise _not_found()
    return rows


def _llm_list(
    db: Session,
    experiment_id: UUID,
    workspace_id: UUID,
    *,
    include_semantic: bool = True,
    include_audit: bool = True,
):
    rows = observatory_query_service.list_pipeline_llm_invocations(
        db,
        experiment_id,
        workspace_id=workspace_id,
        include_semantic=include_semantic,
        include_audit=include_audit,
    )
    if rows is None:
        raise _not_found()
    return rows


def _llm_detail(
    db: Session,
    invocation_id: UUID,
    workspace_id: UUID,
    allowed_purposes: frozenset[str] | None = None,
):
    row = observatory_query_service.get_llm_invocation(
        db, invocation_id, workspace_id=workspace_id,
        allowed_purposes=allowed_purposes,
    )
    if row is None:
        raise _not_found()
    return row


def _pipelines(db: Session, workflow_run_id: UUID, workspace_id: UUID):
    rows = observatory_query_service.list_workflow_run_pipelines(
        db, workflow_run_id, workspace_id=workspace_id
    )
    if rows is None:
        raise _not_found()
    return [public_diagnostic(WorkflowRunPipelineRead.model_validate(row).model_dump()) for row in rows]


def _require_business_capability(
    request: Request, db: Session, user: User, capability: str
) -> UUID:
    workspace_id = request_workspace_id(request)
    try:
        require_modern_business_capability(db, user, workspace_id, capability)
    except AuthorizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return workspace_id


def _business_events(
    db: Session,
    user: User,
    workspace_id: UUID,
    experiment_id: UUID,
    after_sequence: int,
):
    rows = _events(db, experiment_id, workspace_id, after_sequence)
    capabilities = capability_matrix(db, user, workspace_id)
    result = []
    semantic_keys = {
        "llm_used",
        "llm_invocation_id",
        "provider",
        "model",
        "prompt_version",
        "validator_verdict",
    }
    for row in rows:
        if (
            row.event_type.startswith("cv_fold_")
            and not capabilities[CV_FOLD_DETAILS]
        ):
            continue
        if row.stage == "openai_audit" and not capabilities[OPENAI_PIPELINE_AUDIT]:
            continue
        payload = dict(row.payload or {})
        if not capabilities[SEMANTIC_LLM_AUDIT]:
            payload = {key: value for key, value in payload.items() if key not in semantic_keys}
        result.append(event_read(row, payload=payload))
    return result


@admin_router.get("/pipeline-runs/{experiment_id}/summary", response_model=PipelineSummaryRead)
def admin_pipeline_summary(
    experiment_id: UUID,
    request: Request,
    workspace_id: UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    return _summary(db, experiment_id, _admin_workspace_id(request, workspace_id))


@admin_router.get("/pipeline-runs/{experiment_id}/events", response_model=list[MlRunEventRead])
def admin_pipeline_events(
    experiment_id: UUID,
    request: Request,
    after_sequence: int = Query(0, ge=0),
    workspace_id: UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    return [
        event_read(row)
        for row in _events(db, experiment_id, _admin_workspace_id(request, workspace_id), after_sequence)
    ]


@admin_router.get(
    "/pipeline-runs/{experiment_id}/events/incremental",
    response_model=list[MlRunEventRead],
)
def admin_incremental_pipeline_events(
    experiment_id: UUID,
    request: Request,
    after_sequence: int = Query(..., ge=0),
    workspace_id: UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    return [
        event_read(row)
        for row in _events(db, experiment_id, _admin_workspace_id(request, workspace_id), after_sequence)
    ]


@admin_router.get(
    "/pipeline-runs/{experiment_id}/llm-invocations",
    response_model=list[LlmInvocationRead],
)
def admin_pipeline_llm_invocations(
    experiment_id: UUID,
    request: Request,
    workspace_id: UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    return [
        invocation_read(row)
        for row in _llm_list(db, experiment_id, _admin_workspace_id(request, workspace_id))
    ]


@admin_router.get("/llm-invocations/{invocation_id}", response_model=LlmInvocationRead)
def admin_llm_invocation(
    invocation_id: UUID,
    request: Request,
    workspace_id: UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    return invocation_read(
        _llm_detail(db, invocation_id, _admin_workspace_id(request, workspace_id))
    )


@admin_router.get(
    "/workflow-runs/{workflow_run_id}/pipelines",
    response_model=list[WorkflowRunPipelineRead],
)
def admin_workflow_pipelines(
    workflow_run_id: UUID,
    request: Request,
    workspace_id: UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    return _pipelines(db, workflow_run_id, _admin_workspace_id(request, workspace_id))


@business_router.get("/pipeline-runs/{experiment_id}/summary", response_model=PipelineSummaryRead)
def business_pipeline_summary(
    experiment_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace_id = _require_business_capability(
        request, db, user, PIPELINE_MONITOR
    )
    row = _summary(db, experiment_id, workspace_id)
    capabilities = capability_matrix(db, user, workspace_id)
    if not capabilities[SEMANTIC_LLM_AUDIT]:
        row["semantic_llm_count"] = 0
    if not capabilities[OPENAI_PIPELINE_AUDIT]:
        row["pipeline_audit_count"] = 0
    return row


@business_router.get("/pipeline-runs/{experiment_id}/events", response_model=list[MlRunEventRead])
def business_pipeline_events(
    experiment_id: UUID,
    request: Request,
    after_sequence: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_business_capability(request, db, user, PIPELINE_MONITOR)
    workspace_id = _require_business_capability(
        request, db, user, RAW_PIPELINE_DEBUG
    )
    return _business_events(db, user, workspace_id, experiment_id, after_sequence)


@business_router.get(
    "/pipeline-runs/{experiment_id}/events/incremental",
    response_model=list[MlRunEventRead],
)
def business_incremental_pipeline_events(
    experiment_id: UUID,
    request: Request,
    after_sequence: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_business_capability(request, db, user, PIPELINE_MONITOR)
    workspace_id = _require_business_capability(
        request, db, user, RAW_PIPELINE_DEBUG
    )
    return _business_events(db, user, workspace_id, experiment_id, after_sequence)


@business_router.get(
    "/pipeline-runs/{experiment_id}/llm-invocations",
    response_model=list[LlmInvocationRead],
)
def business_pipeline_llm_invocations(
    experiment_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace_id = _require_business_capability(
        request, db, user, PIPELINE_MONITOR
    )
    capabilities = capability_matrix(db, user, workspace_id)
    rows = _llm_list(
        db,
        experiment_id,
        workspace_id,
        include_semantic=capabilities[SEMANTIC_LLM_AUDIT],
        include_audit=capabilities[OPENAI_PIPELINE_AUDIT],
    )
    return [invocation_read(row) for row in rows]


@business_router.get("/llm-invocations/{invocation_id}", response_model=LlmInvocationRead)
def business_llm_invocation(
    invocation_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace_id = _require_business_capability(
        request, db, user, PIPELINE_MONITOR
    )
    capabilities = capability_matrix(db, user, workspace_id)
    allowed = frozenset(
        (SEMANTIC_PURPOSES if capabilities[SEMANTIC_LLM_AUDIT] else frozenset())
        | (AUDIT_PURPOSES if capabilities[OPENAI_PIPELINE_AUDIT] else frozenset())
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="LLM audit access is not permitted")
    row = _llm_detail(db, invocation_id, workspace_id, allowed)
    return invocation_read(row)


@business_router.get(
    "/workflow-runs/{workflow_run_id}/pipelines",
    response_model=list[WorkflowRunPipelineRead],
)
def business_workflow_pipelines(
    workflow_run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace_id = _require_business_capability(
        request, db, user, PIPELINE_MONITOR
    )
    return _pipelines(db, workflow_run_id, workspace_id)
