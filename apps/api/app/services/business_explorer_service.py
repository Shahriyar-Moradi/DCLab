"""Tenant-scoped views over the shared platform explorer contracts."""

from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User, UserRole, Workspace, WorkspaceMembership
from app.services import platform_explorer_service
from app.services.authorization_service import (
    can_read_workspace,
    can_write_workspace,
    platform_role_for,
    workspace_role_for,
)
from app.services.workspace_capability_service import (
    CV_FOLD_DETAILS,
    DECISION_LEDGER,
    MODEL_MANAGEMENT,
    OPENAI_PIPELINE_AUDIT,
    RAW_PIPELINE_DEBUG,
    SEMANTIC_LLM_AUDIT,
    capability_matrix,
)


def _role(db: Session, user: User, workspace_id: UUID) -> str | None:
    platform_role = platform_role_for(db, user)
    if platform_role is not None:
        return platform_role.value
    workspace_role = workspace_role_for(db, user, workspace_id)
    return workspace_role.value if workspace_role is not None else None


def list_workspaces(db: Session, user: User) -> list[dict[str, Any]]:
    if platform_role_for(db, user) is not None:
        workspaces = list(db.scalars(select(Workspace).order_by(Workspace.name)))
    else:
        workspace_ids = select(WorkspaceMembership.workspace_id).where(
            WorkspaceMembership.user_id == user.id
        )
        workspaces = list(
            db.scalars(
                select(Workspace)
                .where(Workspace.id.in_(workspace_ids))
                .order_by(Workspace.name)
            )
        )
        if not workspaces and user.role == UserRole.CLIENT_USER.value and user.workspace_id:
            legacy = db.get(Workspace, user.workspace_id)
            workspaces = [legacy] if legacy is not None else []
    result = []
    for workspace in workspaces:
        detail = platform_explorer_service.get_business(
            db, workspace.id, enabled_domains_only=True
        )
        if detail is None:
            continue
        detail["domain_count"] = len(detail["domains"])
        detail["workflow_count"] = len(detail["workflows"])
        detail["run_count"] = len(detail["runs"])
        detail["model_count"] = len(detail["models"])
        detail["pipeline_count"] = sum(
            row["pipeline_count"] for row in detail["runs"]
        )
        result.append(
            {
                key: detail[key]
                for key in (
                    "id",
                    "slug",
                    "name",
                    "legal_name",
                    "industry",
                    "created_at",
                    "domain_count",
                    "workflow_count",
                    "run_count",
                    "pipeline_count",
                    "model_count",
                    "membership_count",
                )
            }
            | {
                "role": _role(db, user, workspace.id),
                "can_write": can_write_workspace(db, user, workspace.id),
                "capabilities": capability_matrix(db, user, workspace.id),
            }
        )
    return result


def get_business(db: Session, user: User, workspace_id: UUID) -> dict[str, Any] | None:
    if not can_read_workspace(db, user, workspace_id):
        return None
    detail = platform_explorer_service.get_business(
        db, workspace_id, enabled_domains_only=True
    )
    if detail is None:
        return None
    capabilities = capability_matrix(db, user, workspace_id)
    detail["memberships"] = []
    if not capabilities[MODEL_MANAGEMENT]:
        detail["models"] = []
    detail["domain_count"] = len(detail["domains"])
    detail["workflow_count"] = len(detail["workflows"])
    detail["run_count"] = len(detail["runs"])
    detail["model_count"] = len(detail["models"])
    detail["pipeline_count"] = sum(row["pipeline_count"] for row in detail["runs"])
    detail.update(
        role=_role(db, user, workspace_id),
        can_write=can_write_workspace(db, user, workspace_id),
        capabilities=capabilities,
    )
    return detail


def get_domain(db: Session, user: User, workspace_id: UUID, domain_id: UUID):
    if not can_read_workspace(db, user, workspace_id):
        return None
    return platform_explorer_service.get_domain(
        db, workspace_id, domain_id, require_enabled=True
    )


def get_workflow(db: Session, user: User, workspace_id: UUID, workflow_id: UUID):
    if not can_read_workspace(db, user, workspace_id):
        return None
    return platform_explorer_service.get_workflow(
        db, workspace_id, workflow_id, require_enabled_domain=True
    )


def get_workflow_run(db: Session, user: User, workspace_id: UUID, run_id: UUID):
    if not can_read_workspace(db, user, workspace_id):
        return None
    result = platform_explorer_service.get_workflow_run(
        db, workspace_id, run_id, require_enabled_domain=True
    )
    if result is not None:
        result["capabilities"] = capability_matrix(db, user, workspace_id)
        result["can_write"] = can_write_workspace(db, user, workspace_id)
    return result


def get_model(db: Session, user: User, workspace_id: UUID, model_id: UUID):
    if not can_read_workspace(db, user, workspace_id):
        return None
    if not capability_matrix(db, user, workspace_id)[MODEL_MANAGEMENT]:
        return None
    result = platform_explorer_service.get_model(
        db, workspace_id, model_id, require_enabled_domain=True
    )
    if result is not None:
        result["capabilities"] = capability_matrix(db, user, workspace_id)
        result["can_write"] = can_write_workspace(db, user, workspace_id)
    return result


def _without_keys(value: Any, blocked: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_keys(item, blocked)
            for key, item in value.items()
            if key not in blocked
        }
    if isinstance(value, list):
        return [_without_keys(item, blocked) for item in value]
    return value


def get_pipeline_monitor(
    db: Session, user: User, workspace_id: UUID, experiment_id: UUID
) -> dict[str, Any] | None:
    if not can_read_workspace(db, user, workspace_id):
        return None
    monitor = platform_explorer_service.get_pipeline_monitor(
        db,
        experiment_id,
        workspace_id=workspace_id,
        require_enabled_domain=True,
    )
    if monitor is None:
        return None
    result = copy.deepcopy(monitor)
    capabilities = capability_matrix(db, user, workspace_id)
    result["capabilities"] = capabilities

    if not capabilities[CV_FOLD_DETAILS]:
        result["events"] = [
            row
            for row in result["events"]
            if not str(row.get("event_type", "")).startswith("cv_fold_")
        ]
        result["candidates"] = _without_keys(
            result["candidates"], {"folds", "fold_metrics", "cv_folds"}
        )
    if not capabilities[RAW_PIPELINE_DEBUG]:
        result["events"] = [dict(row, payload={}) for row in result["events"]]
        result["sanitized_evidence"] = {}
    if not capabilities[SEMANTIC_LLM_AUDIT]:
        result["llm_invocations"] = [
            row
            for row in result["llm_invocations"]
            if not str(row.get("purpose", "")).startswith("semantic_")
        ]
        result["events"] = _without_keys(
            result["events"],
            {
                "llm_used",
                "llm_invocation_id",
                "provider",
                "model",
                "prompt_version",
                "validator_verdict",
            },
        )
    if not capabilities[OPENAI_PIPELINE_AUDIT]:
        result["llm_invocations"] = [
            row
            for row in result["llm_invocations"]
            if not str(row.get("purpose", "")).startswith("pipeline_audit_")
        ]
        result["openai_audits"] = []
        result["events"] = [
            row for row in result["events"] if row.get("stage") != "openai_audit"
        ]
        result["reports"] = _without_keys(
            result["reports"],
            {
                "openai_audit",
                "verification_attempt",
                "redaction_summary",
                "evidence_digest",
                "provider",
                "model",
                "prompt_version",
                "verification_schema_version",
            },
        )
    if not capabilities[DECISION_LEDGER]:
        result["reports"] = _without_keys(result["reports"], {"decision_records"})
    return result
