"""DataSource registry. Secrets never belong in configuration JSON."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import DataSource, Project, Workspace
from app.domain.data_plane import DATA_SOURCE_STATUSES, DATA_SOURCE_TYPES
from app.domain.errors import (
    DataSourceConfigurationError,
    DataSourceNotFoundError,
    IdentityError,
)

_SECRET_PARTS = frozenset(
    {"password", "secret", "token", "credential", "apikey", "auth"}
)
_SECRET_SUBSTRINGS = ("api_key", "private_key", "access_key")


def _normalized_key(key: object) -> str:
    return str(key).strip().lower().replace("-", "_")


def assert_configuration_has_no_secrets(configuration: object, *, path: str = "configuration") -> None:
    """Reject nested keys that look like credentials. Use credential_reference instead."""

    if isinstance(configuration, dict):
        for key, value in configuration.items():
            normalized = _normalized_key(key)
            parts = set(normalized.split("_"))
            child = f"{path}.{key}"
            if parts & _SECRET_PARTS or any(token in normalized for token in _SECRET_SUBSTRINGS):
                raise DataSourceConfigurationError(
                    f"{child} looks like a secret; store a credential_reference instead"
                )
            assert_configuration_has_no_secrets(value, path=child)
        return
    if isinstance(configuration, list):
        for index, value in enumerate(configuration):
            assert_configuration_has_no_secrets(value, path=f"{path}[{index}]")


def _require_workspace(db: Session, workspace_id: UUID) -> None:
    if db.get(Workspace, workspace_id) is None:
        raise IdentityError("workspace not found", status_code=404)


def _require_project(db: Session, workspace_id: UUID, project_id: UUID | None) -> None:
    if project_id is None:
        return
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise IdentityError("project does not belong to this workspace", status_code=404)


def create_data_source(
    db: Session,
    *,
    workspace_id: UUID,
    name: str,
    source_type: str,
    provider: str,
    created_by: UUID,
    project_id: UUID | None = None,
    configuration: dict | None = None,
    credential_reference: str | None = None,
    status: str = "active",
) -> DataSource:
    _require_workspace(db, workspace_id)
    _require_project(db, workspace_id, project_id)
    if source_type not in DATA_SOURCE_TYPES:
        raise DataSourceConfigurationError(f"unsupported source_type: {source_type}")
    if status not in DATA_SOURCE_STATUSES:
        raise DataSourceConfigurationError(f"unsupported status: {status}")
    payload = dict(configuration or {})
    assert_configuration_has_no_secrets(payload)
    row = DataSource(
        workspace_id=workspace_id,
        project_id=project_id,
        name=name.strip() or "data source",
        source_type=source_type,
        provider=provider.strip() or "local",
        configuration=payload,
        credential_reference=credential_reference,
        status=status,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def get_data_source(
    db: Session, *, workspace_id: UUID, data_source_id: UUID
) -> DataSource:
    row = db.get(DataSource, data_source_id)
    if row is None or row.workspace_id != workspace_id:
        raise DataSourceNotFoundError("data source not found")
    return row
