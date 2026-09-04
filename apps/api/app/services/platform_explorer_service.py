"""Read-only platform explorer over the canonical tenant/lineage hierarchy."""

from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ClientLabUpload,
    Experiment,
    ExperimentCandidate,
    ExperimentTestPrediction,
    LlmInvocation,
    MlRunEvent,
    MlRunVerification,
    MlWorkflow,
    ModelAsset,
    ModelVersion,
    Workspace,
    WorkspaceDomain,
    WorkflowRun,
)
from app.services.observability_service import sanitize_observability_payload
from app.services.workspace_capability_service import BUSINESS_CAPABILITIES


def _count(db: Session, model, *criteria) -> int:
    return int(db.scalar(select(func.count(model.id)).where(*criteria)) or 0)


def _domain_row(db: Session, link: WorkspaceDomain) -> dict[str, Any]:
    domain = link.business_domain
    workflow_ids = select(MlWorkflow.id).where(MlWorkflow.workspace_domain_id == link.id)
    return {
        "id": link.id,
        "business_domain_id": domain.id,
        "slug": domain.slug,
        "name": domain.name,
        "description": domain.description,
        "enabled": link.enabled,
        "config": dict(link.config or {}),
        "workflow_count": _count(db, MlWorkflow, MlWorkflow.workspace_domain_id == link.id),
        "run_count": _count(db, WorkflowRun, WorkflowRun.workflow_id.in_(workflow_ids)),
    }


def _workflow_row(db: Session, workflow: MlWorkflow) -> dict[str, Any]:
    domain = workflow.workspace_domain.business_domain
    return {
        "id": workflow.id,
        "workspace_id": workflow.workspace_id,
        "workspace_domain_id": workflow.workspace_domain_id,
        "domain_slug": domain.slug,
        "domain_name": domain.name,
        "name": workflow.name,
        "slug": workflow.slug,
        "description": workflow.description,
        "business_objective": workflow.business_objective,
        "status": workflow.status,
        "config": dict(workflow.config or {}),
        "run_count": _count(db, WorkflowRun, WorkflowRun.workflow_id == workflow.id),
        "model_count": _count(db, ModelAsset, ModelAsset.workflow_id == workflow.id),
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
    }


def _run_row(db: Session, run: WorkflowRun) -> dict[str, Any]:
    domain = run.workflow.workspace_domain.business_domain
    return {
        "id": run.id,
        "workspace_id": run.workspace_id,
        "workflow_id": run.workflow_id,
        "workflow_name": run.workflow.name,
        "workspace_domain_id": run.workflow.workspace_domain_id,
        "domain_slug": domain.slug,
        "domain_name": domain.name,
        "trigger_type": run.trigger_type,
        "source_type": run.source_type,
        "source_upload_id": run.source_upload_id,
        "source_filename": run.source_upload.original_filename if run.source_upload else None,
        "explicit_target": run.explicit_target,
        "resolved_target": run.resolved_target,
        "task_type": run.task_type,
        "status": run.status,
        "failure_reason": run.failure_reason,
        "pipeline_count": _count(db, Experiment, Experiment.workflow_run_id == run.id),
        "model_version_count": _count(db, ModelVersion, ModelVersion.workflow_run_id == run.id),
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "created_at": run.created_at,
    }


def _version_row(version: ModelVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "version": version.version,
        "workflow_run_id": version.workflow_run_id,
        "pipeline_run_id": version.pipeline_run_id,
        "selected_candidate_id": version.selected_candidate_id,
        "dataset_id": version.dataset_id,
        "content_digest": version.content_digest,
        "metrics": dict(version.metrics or {}),
        "created_at": version.created_at,
    }


def _model_row(model: ModelAsset) -> dict[str, Any]:
    return {
        "id": model.id,
        "workspace_id": model.workspace_id,
        "workflow_id": model.workflow_id,
        "workflow_name": model.workflow.name,
        "name": model.name,
        "slug": model.slug,
        "description": model.description,
        "status": model.status,
        "versions": [_version_row(row) for row in sorted(model.versions, key=lambda row: row.created_at, reverse=True)],
        "created_at": model.created_at,
        "updated_at": model.updated_at,
    }


def _business_summary(db: Session, workspace: Workspace) -> dict[str, Any]:
    profile = workspace.business_profile
    return {
        "id": workspace.id,
        "slug": workspace.slug,
        "name": workspace.name,
        "legal_name": profile.legal_name if profile else None,
        "industry": profile.industry if profile else None,
        "created_at": workspace.created_at,
        "domain_count": _count(db, WorkspaceDomain, WorkspaceDomain.workspace_id == workspace.id),
        "workflow_count": _count(db, MlWorkflow, MlWorkflow.workspace_id == workspace.id),
        "run_count": _count(db, WorkflowRun, WorkflowRun.workspace_id == workspace.id),
        "pipeline_count": _count(db, Experiment, Experiment.workspace_id == workspace.id, Experiment.workflow_run_id.is_not(None)),
        "model_count": _count(db, ModelAsset, ModelAsset.workspace_id == workspace.id),
        "membership_count": len(workspace.memberships),
    }


def list_businesses(db: Session) -> list[dict[str, Any]]:
    return [_business_summary(db, row) for row in db.scalars(select(Workspace).order_by(Workspace.name, Workspace.id))]


def get_business(
    db: Session, workspace_id: UUID, *, enabled_domains_only: bool = False
) -> dict[str, Any] | None:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        return None
    domain_links = [
        row for row in workspace.domain_links if not enabled_domains_only or row.enabled
    ]
    enabled_domain_ids = [row.id for row in domain_links]
    workflow_stmt = select(MlWorkflow).where(MlWorkflow.workspace_id == workspace_id)
    if enabled_domains_only:
        workflow_stmt = workflow_stmt.where(
            MlWorkflow.workspace_domain_id.in_(enabled_domain_ids)
        )
    workflows = list(db.scalars(workflow_stmt.order_by(MlWorkflow.name)))
    workflow_ids = [row.id for row in workflows]
    run_stmt = select(WorkflowRun).where(WorkflowRun.workspace_id == workspace_id)
    model_stmt = select(ModelAsset).where(ModelAsset.workspace_id == workspace_id)
    if enabled_domains_only:
        run_stmt = run_stmt.where(WorkflowRun.workflow_id.in_(workflow_ids))
        model_stmt = model_stmt.where(ModelAsset.workflow_id.in_(workflow_ids))
    runs = list(db.scalars(run_stmt.order_by(WorkflowRun.created_at.desc())))
    models = list(db.scalars(model_stmt.order_by(ModelAsset.name)))
    return {
        **_business_summary(db, workspace),
        "profile_data": dict(workspace.business_profile.profile_data or {}) if workspace.business_profile else {},
        "domains": [_domain_row(db, row) for row in sorted(domain_links, key=lambda item: item.business_domain.name)],
        "workflows": [_workflow_row(db, row) for row in workflows],
        "models": [_model_row(row) for row in models],
        "runs": [_run_row(db, row) for row in runs],
        "memberships": [
            {
                "id": membership.id,
                "user_id": membership.user_id,
                "email": membership.user.email,
                "full_name": membership.user.full_name,
                "role": membership.role,
                "is_active": membership.user.is_active,
                "created_at": membership.created_at,
            }
            for membership in sorted(workspace.memberships, key=lambda item: item.created_at)
        ],
    }


def get_domain(
    db: Session,
    workspace_id: UUID,
    domain_id: UUID,
    *,
    require_enabled: bool = False,
) -> dict[str, Any] | None:
    workspace = db.get(Workspace, workspace_id)
    link = db.get(WorkspaceDomain, domain_id)
    if (
        workspace is None
        or link is None
        or link.workspace_id != workspace_id
        or (require_enabled and not link.enabled)
    ):
        return None
    workflows = list(db.scalars(select(MlWorkflow).where(MlWorkflow.workspace_domain_id == link.id).order_by(MlWorkflow.name)))
    workflow_ids = [row.id for row in workflows]
    runs = list(db.scalars(select(WorkflowRun).where(WorkflowRun.workflow_id.in_(workflow_ids)).order_by(WorkflowRun.created_at.desc()))) if workflow_ids else []
    return {**_domain_row(db, link), "workspace_id": workspace_id, "business_name": workspace.name, "workflows": [_workflow_row(db, row) for row in workflows], "runs": [_run_row(db, row) for row in runs]}


def get_workflow(
    db: Session,
    workspace_id: UUID,
    workflow_id: UUID,
    *,
    require_enabled_domain: bool = False,
) -> dict[str, Any] | None:
    workspace = db.get(Workspace, workspace_id)
    workflow = db.get(MlWorkflow, workflow_id)
    if (
        workspace is None
        or workflow is None
        or workflow.workspace_id != workspace_id
        or (require_enabled_domain and not workflow.workspace_domain.enabled)
    ):
        return None
    runs = list(db.scalars(select(WorkflowRun).where(WorkflowRun.workflow_id == workflow.id).order_by(WorkflowRun.created_at.desc())))
    models = list(db.scalars(select(ModelAsset).where(ModelAsset.workflow_id == workflow.id).order_by(ModelAsset.name)))
    return {**_workflow_row(db, workflow), "business_name": workspace.name, "runs": [_run_row(db, row) for row in runs], "models": [_model_row(row) for row in models]}


def _pipeline_row(db: Session, pipeline: Experiment) -> dict[str, Any]:
    version = pipeline.model_version
    return {
        "id": pipeline.id,
        "workspace_id": pipeline.workspace_id,
        "workflow_run_id": pipeline.workflow_run_id,
        "pipeline_name": pipeline.pipeline_name,
        "pipeline_index": pipeline.pipeline_index,
        "pipeline_purpose": pipeline.pipeline_purpose,
        "status": pipeline.status,
        "failure_reason": pipeline.failure_reason,
        "task_type": pipeline.task.task_type if pipeline.task else pipeline.workflow_run.task_type if pipeline.workflow_run else None,
        "dataset_id": pipeline.dataset_id,
        "dataset_name": pipeline.dataset.name,
        "candidate_count": len(pipeline.candidates),
        "event_count": _count(db, MlRunEvent, MlRunEvent.experiment_id == pipeline.id),
        "latest_sequence": int(db.scalar(select(func.max(MlRunEvent.sequence)).where(MlRunEvent.experiment_id == pipeline.id)) or 0),
        "model_version_id": version.id if version else None,
        "model_asset_id": version.model_asset_id if version else None,
        "model_name": version.model_asset.name if version else None,
        "model_version": version.version if version else None,
        "started_at": pipeline.started_at,
        "ended_at": pipeline.ended_at,
    }


def get_workflow_run(
    db: Session,
    workspace_id: UUID,
    run_id: UUID,
    *,
    require_enabled_domain: bool = False,
) -> dict[str, Any] | None:
    workspace = db.get(Workspace, workspace_id)
    run = db.get(WorkflowRun, run_id)
    if (
        workspace is None
        or run is None
        or run.workspace_id != workspace_id
        or (require_enabled_domain and not run.workflow.workspace_domain.enabled)
    ):
        return None
    pipelines = list(db.scalars(select(Experiment).where(Experiment.workflow_run_id == run.id).order_by(Experiment.pipeline_index, Experiment.created_at)))
    return {**_run_row(db, run), "business_name": workspace.name, "pipelines": [_pipeline_row(db, row) for row in pipelines]}


def get_model(
    db: Session,
    workspace_id: UUID,
    model_id: UUID,
    *,
    require_enabled_domain: bool = False,
) -> dict[str, Any] | None:
    workspace = db.get(Workspace, workspace_id)
    model = db.get(ModelAsset, model_id)
    if (
        workspace is None
        or model is None
        or model.workspace_id != workspace_id
        or (require_enabled_domain and not model.workflow.workspace_domain.enabled)
    ):
        return None
    domain = model.workflow.workspace_domain.business_domain
    return {**_model_row(model), "business_name": workspace.name, "domain_slug": domain.slug, "domain_name": domain.name}


def get_pipeline_monitor(
    db: Session,
    experiment_id: UUID,
    *,
    workspace_id: UUID | None = None,
    require_enabled_domain: bool = False,
) -> dict[str, Any] | None:
    pipeline = db.get(Experiment, experiment_id)
    if (
        pipeline is None
        or pipeline.workflow_run is None
        or (workspace_id is not None and pipeline.workspace_id != workspace_id)
        or (
            require_enabled_domain
            and not pipeline.workflow_run.workflow.workspace_domain.enabled
        )
    ):
        return None
    run = pipeline.workflow_run
    workflow = run.workflow
    domain = workflow.workspace_domain.business_domain
    workspace = pipeline.workspace
    version = pipeline.model_version
    upload = db.scalar(select(ClientLabUpload).where(ClientLabUpload.experiment_id == pipeline.id))
    events = list(db.scalars(select(MlRunEvent).where(MlRunEvent.experiment_id == pipeline.id).order_by(MlRunEvent.sequence)))
    invocations = list(db.scalars(select(LlmInvocation).where(LlmInvocation.experiment_id == pipeline.id).order_by(LlmInvocation.started_at, LlmInvocation.id)))
    candidates: list[dict[str, Any]] = []
    for candidate in sorted(pipeline.candidates, key=lambda row: row.created_at):
        safe_payload, _ = sanitize_observability_payload(candidate.payload or {})
        candidates.append({"id": str(candidate.id), "candidate_key": candidate.candidate_key, "status": candidate.status, "selected": bool(version and version.selected_candidate_id == candidate.id), "payload": safe_payload, "created_at": candidate.created_at.isoformat()})
    verification = db.scalar(select(MlRunVerification).where(MlRunVerification.experiment_id == pipeline.id).order_by(MlRunVerification.created_at.desc()).limit(1))
    result = dict(pipeline.result or {})
    technical_report = dict(result.get("technical_report") or {})
    deterministic = dict(technical_report.get("deterministic_verification") or {})
    if verification is not None:
        deterministic = {"overall_status": verification.deterministic_status, "schema_version": verification.deterministic_schema_version, "checks": list(verification.deterministic_checks or [])}
    evidence_source = {
        "config": pipeline.config,
        "data_quality": result.get("data_quality"),
        "validation": result.get("validation"),
        "selection": result.get("selection"),
        "train_metrics": result.get("train_metrics"),
        "test_metrics": result.get("test_metrics"),
        "feature_engineering": result.get("feature_engineering"),
        "column_roles": result.get("column_roles"),
    }
    safe_evidence, redaction = sanitize_observability_payload(evidence_source)
    report_source = {
        key: value
        for key, value in technical_report.items()
        if key not in {"prediction_evidence", "artifacts", "decision_records"}
    }
    report_source["artifacts"] = {
        key: bool(value)
        for key, value in dict(technical_report.get("artifacts") or {}).items()
    }
    report_source["decision_records"] = [
        {
            key: value
            for key, value in object_row.items()
            if key
            in {
                "id",
                "column",
                "source",
                "rule_decision",
                "final_decision",
                "validator_verdict",
                "prompt_version",
                "created_at",
            }
        }
        for object_row in technical_report.get("decision_records") or []
        if isinstance(object_row, dict)
    ]
    safe_report, report_redaction = sanitize_observability_payload(report_source)
    prediction_rows = list(db.scalars(select(ExperimentTestPrediction).where(ExperimentTestPrediction.experiment_id == pipeline.id)))
    distribution = Counter(str(row.predicted_value) for row in prediction_rows)
    return {
        "capabilities": {key: True for key in BUSINESS_CAPABILITIES},
        "hierarchy": {
            "business": {"id": str(workspace.id), "name": workspace.name},
            "domain": {"id": str(workflow.workspace_domain_id), "slug": domain.slug, "name": domain.name},
            "workflow": {"id": str(workflow.id), "name": workflow.name},
            "workflow_run": {"id": str(run.id), "status": run.status},
            "pipeline_run": {"id": str(pipeline.id), "name": pipeline.pipeline_name},
            "model": ({"id": str(version.model_asset_id), "version_id": str(version.id), "name": version.model_asset.name, "version": version.version} if version else None),
            "source_upload": ({"id": str(upload.id), "filename": upload.original_filename} if upload else None),
        },
        "summary": _pipeline_row(db, pipeline),
        "events": [{"id": str(row.id), "sequence": row.sequence, "stage": row.stage, "event_type": row.event_type, "status": row.status, "timestamp": row.timestamp.isoformat(), "duration_ms": row.duration_ms, "payload": row.payload, "created_at": row.created_at.isoformat()} for row in events],
        "llm_invocations": [{"id": str(row.id), "purpose": row.purpose, "llm_used": row.llm_used, "reason": row.reason, "provider": row.provider, "model": row.model, "mode": row.mode, "prompt_version": row.prompt_version, "schema_version": row.schema_version, "input_evidence_digest": row.input_evidence_digest, "redaction_summary": row.redaction_summary, "status": row.status, "validator_verdict": row.validator_verdict, "safe_output": row.safe_output, "final_decision": row.final_decision, "latency_ms": row.latency_ms, "input_tokens": row.input_tokens, "output_tokens": row.output_tokens, "total_tokens": row.total_tokens, "estimated_cost": row.estimated_cost, "started_at": row.started_at.isoformat(), "completed_at": row.completed_at.isoformat() if row.completed_at else None} for row in invocations],
        "candidates": candidates,
        "preprocessing": {
            "numerical": ["Median Imputer", "StandardScaler"],
            "categorical": ["Most-Frequent Imputer", "OneHotEncoder"],
            "one_hot": {"drop": "first", "handle_unknown": "ignore"},
            "fit_guarantees": ["Preprocessing is fitted on training data only.", "During cross-validation, preprocessing is fitted independently inside each training fold.", "The final holdout is never used for preprocessing, candidate ranking, or winner selection."],
        },
        "predictions": {"count": len(prediction_rows), "distribution": dict(distribution), "raw_rows_included": False},
        "deterministic_verification": deterministic,
        "openai_audits": [row.safe_output or {"status": row.status, "reason": row.reason} for row in invocations if row.purpose.startswith("pipeline_audit_")],
        "reports": {"technical_report": safe_report, "redaction": report_redaction},
        "sanitized_evidence": {"payload": safe_evidence, "redaction": redaction},
    }
