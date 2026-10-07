"""Versioned, server-authoritative effective capability resolution.

The matrix returned by this module is useful to clients for presentation only.
Every route and service must still resolve current membership and capabilities
when an operation executes. Resolution is cached only for the lifetime of the
current SQLAlchemy transaction, so a commit/rollback bounds staleness to one
request. Mutation services can invalidate earlier when authority changes in
the same transaction.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import event, select
from sqlalchemy.orm import Session, SessionTransaction

from app.config import get_settings
from app.db.models import (
    PlatformRole,
    PlatformMembership,
    User,
    UserRole,
    Workspace,
    WorkspaceCapability,
    WorkspaceEntitlement,
    WorkspaceKind,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.services.authorization_service import (
    AuthorizationError,
    active_workspace_memberships,
    can_execute_workspace_ml,
    can_manage_workspace_members,
    can_read_platform,
    can_read_workspace,
    can_write_platform,
    can_write_workspace,
    platform_role_for,
    workspace_role_for,
)
from app.services.workspace_entitlement_service import (
    max_members_for,
    max_ml_engineer_seats_for,
    member_count,
    technical_seat_count,
)

CAPABILITY_MATRIX_VERSION = "workspace-capabilities.v1"

# Stable names exposed by /v1/me and browser session identity. They are
# presentation hints, never credentials or authorization claims.
ACCOUNT_ACCESS = "account_access"
PLATFORM_READ = "platform_read"
PLATFORM_WRITE = "platform_write"
WORKSPACE_READ = "workspace_read"
WORKSPACE_WRITE = "workspace_write"
WORKSPACE_MANAGE_MEMBERS = "workspace_manage_members"
WORKSPACE_ADD_MEMBER = "workspace_add_member"
WORKSPACE_ADD_ML_ENGINEER = "workspace_add_ml_engineer"
WORKSPACE_EXECUTE_ML = "workspace_execute_ml"
APPLICATION_ACCESS = "application_access"
BUSINESS_ACCESS = "business_access"
DEVELOPMENT_ACCESS = "development_access"
# P4.1-A: presentation flag for the frozen Decision.ai vertical pages (navigation
# and the landing-page opportunity panel). The legacy /app routes still authorize.
LEGACY_DECISION_LAYER = "legacy_decision_layer_enabled"
# P4.12-A: presentation flag for the frozen Decision.ai marketing pages (web redirects
# them to `/` when off). Not an authorization input.
LEGACY_MARKETING_PAGES = "legacy_marketing_pages_enabled"

PIPELINE_MONITOR = "pipeline_monitor"
CV_FOLD_DETAILS = "cv_fold_details"
SEMANTIC_LLM_AUDIT = "semantic_llm_audit"
OPENAI_PIPELINE_AUDIT = "openai_pipeline_audit"
RAW_PIPELINE_DEBUG = "raw_pipeline_debug"
DECISION_LEDGER = "decision_ledger"
PREDICTION_DOWNLOAD = "prediction_download"
MODEL_MANAGEMENT = "model_management"
DEEP_AUDIT = "deep_audit"

BASE_CAPABILITIES = (
    ACCOUNT_ACCESS,
    PLATFORM_READ,
    PLATFORM_WRITE,
    WORKSPACE_READ,
    WORKSPACE_WRITE,
    WORKSPACE_MANAGE_MEMBERS,
    WORKSPACE_ADD_MEMBER,
    WORKSPACE_ADD_ML_ENGINEER,
    WORKSPACE_EXECUTE_ML,
    APPLICATION_ACCESS,
    BUSINESS_ACCESS,
    DEVELOPMENT_ACCESS,
    LEGACY_DECISION_LAYER,
    LEGACY_MARKETING_PAGES,
)

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

ALL_EFFECTIVE_CAPABILITIES = (*BASE_CAPABILITIES, *BUSINESS_CAPABILITIES)

_CACHE_KEY = "dclab_effective_capability_cache"
_CacheEntry = tuple[SessionTransaction, dict[str, bool]]
_AUTHORITY_MODELS = (
    PlatformMembership,
    WorkspaceMembership,
    WorkspaceEntitlement,
    WorkspaceCapability,
    Workspace,
    User,
)


def invalidate_capability_cache(
    db: Session,
    *,
    user_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> None:
    """Invalidate matching transaction-local capability resolutions."""

    cache = db.info.get(_CACHE_KEY)
    if not isinstance(cache, dict):
        return
    if user_id is None and workspace_id is None:
        cache.clear()
        return
    for key in list(cache):
        _version, cached_user_id, cached_workspace_id = key
        if user_id is not None and cached_user_id != user_id:
            continue
        if workspace_id is not None and cached_workspace_id != workspace_id:
            continue
        cache.pop(key, None)


@event.listens_for(Session, "after_flush")
def _invalidate_flushed_authority(session: Session, _flush_context: object) -> None:
    """A same-transaction role, suspension, entitlement, or flag edit is visible."""

    if any(
        isinstance(row, _AUTHORITY_MODELS)
        for rows in (session.new, session.dirty, session.deleted)
        for row in rows
    ):
        invalidate_capability_cache(session)


def _feature_capabilities(
    db: Session,
    user: User,
    workspace_id: UUID | None,
) -> dict[str, bool]:
    if workspace_id is None:
        return {key: False for key in BUSINESS_CAPABILITIES}
    if platform_role_for(db, user) is not None or user.role == UserRole.CLIENT_USER.value:
        return {key: True for key in BUSINESS_CAPABILITIES}
    rows = db.scalars(
        select(WorkspaceCapability).where(
            WorkspaceCapability.workspace_id == workspace_id,
            WorkspaceCapability.capability.in_(BUSINESS_CAPABILITIES),
        )
    )
    enabled = {row.capability: bool(row.enabled) for row in rows}
    return {key: enabled.get(key, False) for key in BUSINESS_CAPABILITIES}


def _has_business_membership(db: Session, user: User) -> bool:
    if platform_role_for(db, user) is not None:
        return True
    if user.role == UserRole.CLIENT_USER.value:
        return False
    for membership in active_workspace_memberships(db, user):
        if membership.role == WorkspaceRole.PERSONAL_DEVELOPER.value:
            continue
        workspace = db.get(Workspace, membership.workspace_id)
        if workspace is not None and workspace.kind == WorkspaceKind.BUSINESS.value:
            return True
    return False


def _quota_capabilities(
    db: Session,
    workspace_id: UUID | None,
    *,
    can_manage_members: bool,
) -> tuple[bool, bool]:
    if workspace_id is None or not can_manage_members:
        return False, False
    overall_limit = max_members_for(db, workspace_id)
    overall_available = overall_limit is None or member_count(db, workspace_id) < overall_limit
    engineer_limit = max_ml_engineer_seats_for(db, workspace_id)
    engineer_available = technical_seat_count(db, workspace_id) < engineer_limit
    return overall_available, overall_available and engineer_available


def _resolve_effective_capabilities(
    db: Session,
    user: User,
    workspace_id: UUID | None,
) -> dict[str, bool]:
    platform_read = can_read_platform(db, user)
    platform_write = can_write_platform(db, user)
    workspace = db.get(Workspace, workspace_id) if workspace_id is not None else None
    workspace_read = bool(
        workspace is not None and can_read_workspace(db, user, workspace.id)
    )
    workspace_write = bool(
        workspace_read and can_write_workspace(db, user, workspace.id)
    )
    execute_ml = bool(
        workspace_read and can_execute_workspace_ml(db, user, workspace.id)
    )
    manage_members = bool(
        workspace_read and can_manage_workspace_members(db, user, workspace.id)
    )
    add_member, add_engineer = _quota_capabilities(
        db,
        workspace.id if workspace is not None else None,
        can_manage_members=manage_members,
    )

    stored_workspace_role = (
        workspace_role_for(db, user, workspace.id) if workspace is not None else None
    )
    is_compatibility_client = user.role == UserRole.CLIENT_USER.value
    is_personal_developer = stored_workspace_role is WorkspaceRole.PERSONAL_DEVELOPER
    application_access = workspace_read and not is_personal_developer
    development_access = workspace_read and (
        platform_read
        or (stored_workspace_role is not None and not is_compatibility_client)
    )
    if workspace is None:
        business_access = _has_business_membership(db, user)
    else:
        business_access = bool(
            workspace_read
            and workspace.kind == WorkspaceKind.BUSINESS.value
            and (
                platform_read
                or (
                    stored_workspace_role is not None
                    and not is_personal_developer
                    and not is_compatibility_client
                )
            )
        )

    matrix = {
        ACCOUNT_ACCESS: True,
        PLATFORM_READ: platform_read,
        PLATFORM_WRITE: platform_write,
        WORKSPACE_READ: workspace_read,
        WORKSPACE_WRITE: workspace_write,
        WORKSPACE_MANAGE_MEMBERS: manage_members,
        WORKSPACE_ADD_MEMBER: add_member,
        WORKSPACE_ADD_ML_ENGINEER: add_engineer,
        WORKSPACE_EXECUTE_ML: execute_ml,
        APPLICATION_ACCESS: application_access,
        BUSINESS_ACCESS: business_access,
        DEVELOPMENT_ACCESS: development_access,
        LEGACY_DECISION_LAYER: bool(
            application_access and get_settings().legacy_decision_layer_enabled
        ),
        LEGACY_MARKETING_PAGES: bool(get_settings().legacy_marketing_pages_enabled),
    }
    matrix.update(_feature_capabilities(db, user, workspace_id))
    return {key: bool(matrix.get(key, False)) for key in ALL_EFFECTIVE_CAPABILITIES}


def effective_capability_matrix(
    db: Session,
    user: User,
    workspace_id: UUID | None,
) -> dict[str, bool]:
    """Return displayable capabilities resolved from current server state."""

    key = (CAPABILITY_MATRIX_VERSION, user.id, workspace_id)
    cache: dict[tuple[str, UUID, UUID | None], _CacheEntry] = db.info.setdefault(
        _CACHE_KEY, {}
    )
    transaction = db.get_transaction()
    cached = cache.get(key)
    if transaction is not None and cached is not None and cached[0] is transaction:
        return dict(cached[1])

    matrix = _resolve_effective_capabilities(db, user, workspace_id)
    transaction = db.get_transaction()
    if transaction is not None:
        cache[key] = (transaction, dict(matrix))
    return matrix


def capability_matrix(db: Session, user: User, workspace_id: UUID) -> dict[str, bool]:
    """Compatibility projection for existing Business API response shapes."""

    effective = effective_capability_matrix(db, user, workspace_id)
    return {key: effective[key] for key in BUSINESS_CAPABILITIES}


def capability_enabled(
    db: Session, user: User, workspace_id: UUID, capability: str
) -> bool:
    if capability not in BUSINESS_CAPABILITIES:
        return False
    return effective_capability_matrix(db, user, workspace_id)[capability]


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
    """Protect shared client routes without breaking legacy ``client_user``."""

    if user.role == UserRole.CLIENT_USER.value:
        return
    require_capability(db, user, workspace_id, capability)


def set_workspace_capability(
    db: Session,
    workspace_id: UUID,
    capability: str,
    enabled: bool,
    *,
    configuration: dict[str, Any] | None = None,
) -> WorkspaceCapability:
    """Update an allowlisted feature flag and invalidate same-transaction reads."""

    if capability not in BUSINESS_CAPABILITIES:
        raise ValueError(f"unknown workspace capability '{capability}'")
    row = db.scalar(
        select(WorkspaceCapability).where(
            WorkspaceCapability.workspace_id == workspace_id,
            WorkspaceCapability.capability == capability,
        )
    )
    if row is None:
        row = WorkspaceCapability(
            workspace_id=workspace_id,
            capability=capability,
            enabled=enabled,
            configuration=configuration or {},
        )
        db.add(row)
    else:
        row.enabled = enabled
        if configuration is not None:
            row.configuration = configuration
    db.flush()
    invalidate_capability_cache(db, workspace_id=workspace_id)
    return row


def platform_capabilities_unrestricted(db: Session, user: User) -> bool:
    return platform_role_for(db, user) in {
        PlatformRole.DCLAB_ADMIN,
        PlatformRole.DCLAB_DEVELOPER,
    }
