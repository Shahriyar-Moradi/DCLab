"""DataAccess registry. Locators are non-secret; credentials stay in a secret manager."""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import DataAccess, DataSource, Project, Workspace
from app.domain.data_access import (
    ACCESS_ACTIVE,
    ACCESS_TYPE_UPLOAD,
    CREDENTIAL_REFERENCE_MAX_LENGTH,
    DATA_ACCESS_EXECUTION_MODES,
    DATA_ACCESS_STATUSES,
    DATA_ACCESS_TYPES,
    EXECUTION_MODE_COPY,
    LOCATOR_MAX_BYTES,
    OPAQUE_CREDENTIAL_REGEX,
    POLICY_MAX_BYTES,
)
from app.domain.data_plane import OBJECT_STORAGE_PROVIDERS
from app.domain.errors import (
    DataAccessConfigurationError,
    DataAccessNotFoundError,
    DataSourceConfigurationError,
    IdentityError,
)
from app.services.data_source_service import assert_configuration_has_no_secrets

_OPAQUE_CREDENTIAL = re.compile(OPAQUE_CREDENTIAL_REGEX)


def _require_workspace(db: Session, workspace_id: UUID) -> None:
    if db.get(Workspace, workspace_id) is None:
        raise IdentityError("workspace not found", status_code=404)


def _require_project(db: Session, workspace_id: UUID, project_id: UUID | None) -> None:
    if project_id is None:
        return
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise IdentityError("project does not belong to this workspace", status_code=404)


def _require_source(db: Session, workspace_id: UUID, data_source_id: UUID) -> DataSource:
    source = db.get(DataSource, data_source_id)
    if source is None or source.workspace_id != workspace_id:
        raise IdentityError("data source does not belong to this workspace", status_code=404)
    return source


def _as_object(value: dict[str, Any] | None, *, label: str) -> dict[str, Any]:
    payload = {} if value is None else value
    if type(payload) is not dict:
        raise DataAccessConfigurationError(f"{label} must be a JSON object")
    return payload


def _bounded_json(payload: dict[str, Any], *, limit: int, label: str) -> dict[str, Any]:
    raw = json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")
    if len(raw) > limit:
        raise DataAccessConfigurationError(f"{label} exceeds {limit} bytes")
    return payload


def assert_opaque_credential_reference(value: str | None) -> str | None:
    """Reject raw credentials. Only an opaque secret-manager pointer may be stored."""

    if value is None:
        return None
    reference = value.strip()
    if not reference:
        return None
    if "://" in reference or not _OPAQUE_CREDENTIAL.fullmatch(reference):
        raise DataAccessConfigurationError(
            "credential_reference must be an opaque secret-manager pointer, not a credential"
        )
    if len(reference) > CREDENTIAL_REFERENCE_MAX_LENGTH:
        raise DataAccessConfigurationError("credential_reference is too long")
    return reference


def create_data_access(
    db: Session,
    *,
    workspace_id: UUID,
    data_source_id: UUID,
    name: str,
    access_type: str,
    provider: str,
    execution_mode: str,
    created_by: UUID,
    project_id: UUID | None = None,
    resource_locator: dict | None = None,
    credential_reference: str | None = None,
    data_region: str | None = None,
    status: str = ACCESS_ACTIVE,
    privacy_policy: dict | None = None,
    retention_policy: dict | None = None,
) -> DataAccess:
    _require_workspace(db, workspace_id)
    _require_project(db, workspace_id, project_id)
    source = _require_source(db, workspace_id, data_source_id)
    if project_id is None:
        project_id = source.project_id
    if access_type not in DATA_ACCESS_TYPES:
        raise DataAccessConfigurationError(f"unsupported access_type: {access_type}")
    if execution_mode not in DATA_ACCESS_EXECUTION_MODES:
        raise DataAccessConfigurationError(f"unsupported execution_mode: {execution_mode}")
    if access_type == ACCESS_TYPE_UPLOAD and execution_mode != EXECUTION_MODE_COPY:
        raise DataAccessConfigurationError("upload access must use execution_mode=copy")
    if status not in DATA_ACCESS_STATUSES:
        raise DataAccessConfigurationError(f"unsupported status: {status}")
    if access_type == ACCESS_TYPE_UPLOAD and provider not in OBJECT_STORAGE_PROVIDERS:
        raise DataAccessConfigurationError(f"unsupported upload provider: {provider}")
    locator = _bounded_json(
        _as_object(resource_locator, label="resource_locator"),
        limit=LOCATOR_MAX_BYTES,
        label="resource_locator",
    )
    privacy = _bounded_json(
        _as_object(privacy_policy, label="privacy_policy"),
        limit=POLICY_MAX_BYTES,
        label="privacy_policy",
    )
    retention = _bounded_json(
        _as_object(retention_policy, label="retention_policy"),
        limit=POLICY_MAX_BYTES,
        label="retention_policy",
    )
    try:
        assert_configuration_has_no_secrets(locator, path="resource_locator")
        assert_configuration_has_no_secrets(privacy, path="privacy_policy")
        assert_configuration_has_no_secrets(retention, path="retention_policy")
    except DataSourceConfigurationError as exc:
        raise DataAccessConfigurationError(str(exc)) from exc
    region = (data_region or "").strip() or None
    if region is not None and len(region) > 64:
        raise DataAccessConfigurationError("data_region is too long")
    row = DataAccess(
        workspace_id=workspace_id,
        project_id=project_id,
        data_source_id=data_source_id,
        name=name.strip() or "data access",
        access_type=access_type,
        provider=provider.strip() or "local",
        execution_mode=execution_mode,
        resource_locator=locator,
        credential_reference=assert_opaque_credential_reference(credential_reference),
        data_region=region,
        status=status,
        privacy_policy=privacy,
        retention_policy=retention,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def get_data_access(
    db: Session, *, workspace_id: UUID, data_access_id: UUID
) -> DataAccess:
    row = db.get(DataAccess, data_access_id)
    if row is None or row.workspace_id != workspace_id:
        raise DataAccessNotFoundError("data access not found")
    return row
