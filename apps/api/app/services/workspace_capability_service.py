"""Central capability policy for tenant-scoped technical administration."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PlatformRole, User, UserRole, WorkspaceCapability
from app.services.authorization_service import AuthorizationError, platform_role_for

PIPELINE_MONITOR = "pipeline_monitor"
CV_FOLD_DETAILS = "cv_fold_details"
SEMANTIC_LLM_AUDIT = "semantic_llm_audit"
OPENAI_PIPELINE_AUDIT = "openai_pipeline_audit"
RAW_PIPELINE_DEBUG = "raw_pipeline_debug"
DECISION_LEDGER = "decision_ledger"
PREDICTION_DOWNLOAD = "prediction_download"
MODEL_MANAGEMENT = "model_management"
DEEP_AUDIT = "deep_audit"

BUSINESS_CAPABILITIES = (
    PIPELINE_MONITOR,
    CV_FOLD_DETAILS,
    SEMANTIC_LLM_AUDIT,
    OPENAI_PIPELINE_AUDIT,
    RAW_PIPELINE_DEBUG,
    DECISION_LEDGER,
    PREDICTION_DOWNLOAD,
    MODEL_MANAGEMENT,
    DEEP_AUDIT,
)


def capability_matrix(db: Session, user: User, workspace_id: UUID) -> dict[str, bool]:
    """Return the effective bounded capability contract for one workspace.

    Platform memberships bypass tenant feature flags. Business capabilities fail
    closed: an absent row and an explicitly disabled row both mean disabled.
    """

    if (
        platform_role_for(db, user) is not None
        or user.role == UserRole.CLIENT_USER.value
    ):
        return {key: True for key in BUSINESS_CAPABILITIES}
    rows = db.scalars(
        select(WorkspaceCapability).where(
            WorkspaceCapability.workspace_id == workspace_id,
            WorkspaceCapability.capability.in_(BUSINESS_CAPABILITIES),
        )
    )
    enabled = {row.capability: bool(row.enabled) for row in rows}
    return {key: enabled.get(key, False) for key in BUSINESS_CAPABILITIES}


def capability_enabled(
    db: Session, user: User, workspace_id: UUID, capability: str
) -> bool:
    if capability not in BUSINESS_CAPABILITIES:
        return False
    return capability_matrix(db, user, workspace_id)[capability]


def require_capability(
    db: Session, user: User, workspace_id: UUID, capability: str
) -> None:
    if not capability_enabled(db, user, workspace_id, capability):
        raise AuthorizationError(
            f"workspace capability '{capability}' is not enabled",
            status_code=403,
        )


def require_modern_business_capability(
    db: Session, user: User, workspace_id: UUID, capability: str
) -> None:
    """Protect shared client routes without breaking legacy ``client_user``.

    Modern Business Admin/Developer accounts are capability-governed even when
    they call an older `/app` URL directly. The compatibility-only client role
    retains its existing translated client behavior during migration.
    """

    if user.role == UserRole.CLIENT_USER.value:
        return
    require_capability(db, user, workspace_id, capability)


def platform_capabilities_unrestricted(
    db: Session, user: User
) -> bool:
    return platform_role_for(db, user) in {
        PlatformRole.DCLAB_ADMIN,
        PlatformRole.DCLAB_DEVELOPER,
    }
