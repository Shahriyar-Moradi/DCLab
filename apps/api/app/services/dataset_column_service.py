"""DatasetColumn facts derived from a physical Dataset (DatasetVersion)."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Dataset, DatasetColumn, DatasetPolicyRevision, User
from app.domain.errors import IdentityError
from app.domain.privacy_audit import (
    CLASSIFICATION_SOURCES,
    DATA_USE_POLICIES,
    DATA_POLICY_SCHEMA_VERSION,
    RESIDENCY_CLASSES,
    RETENTION_CLASSES,
    SENSITIVITY_CLASSES,
)
from app.engine.schema.profiler import profile_frame
from app.services.authorization_service import can_read_workspace, can_write_workspace


@dataclass(frozen=True)
class EffectiveColumnPolicy:
    column_id: UUID
    sensitivity_class: str | None
    llm_exposure_policy: str
    retention_class: str | None
    residency_class: str | None


@dataclass(frozen=True)
class EffectiveDatasetPolicy:
    dataset_id: UUID
    workspace_id: UUID
    policy_schema_version: int
    dataset_policy_revision: int | None
    sensitivity_class: str | None
    llm_exposure_policy: str
    retention_class: str | None
    residency_class: str | None
    columns: tuple[EffectiveColumnPolicy, ...]
    complete: bool


_SENSITIVITY_RANK = {
    label: position for position, label in enumerate(SENSITIVITY_CLASSES)
}
_RESIDENCY_RANK = {"home_cloud_only": 0, "home_region_only": 1}


def _validate_label(value: str, choices: tuple[str, ...], field: str) -> None:
    if value not in choices:
        raise IdentityError(f"unsupported {field}: {value}", status_code=400)


def _restrict_exposure(dataset_value: str | None, column_value: str | None) -> str:
    if dataset_value is None or column_value is None:
        return "deny"
    if "deny" in (dataset_value, column_value):
        return "deny"
    if dataset_value == "allow":
        return column_value
    if column_value == "allow":
        return dataset_value
    # aggregate_only and metadata_only are incomparable without a purpose;
    # their intersection is deny, not a guessed widening.
    return dataset_value if dataset_value == column_value else "deny"


def _restrict_sensitivity(dataset_value: str | None, column_value: str | None) -> str | None:
    if dataset_value not in _SENSITIVITY_RANK or column_value not in _SENSITIVITY_RANK:
        return None
    return max((dataset_value, column_value), key=_SENSITIVITY_RANK.__getitem__)


def _restrict_residency(dataset_value: str | None, column_value: str | None) -> str | None:
    if dataset_value not in _RESIDENCY_RANK or column_value not in _RESIDENCY_RANK:
        return None
    return max((dataset_value, column_value), key=_RESIDENCY_RANK.__getitem__)


def _restrict_retention(dataset_value: str | None, column_value: str | None) -> str | None:
    # Short versus extended is a legal/product decision, not an order we can
    # invent. Conflicts remain unresolved and therefore block later actions.
    if dataset_value is None or column_value is None or "unknown" in (dataset_value, column_value):
        return None
    return dataset_value if dataset_value == column_value else None


def publish_dataset_policy_defaults(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    dataset_id: UUID,
    expected_revision: int,
    sensitivity_class: str,
    llm_exposure_policy: str,
    retention_class: str,
    residency_class: str,
    classification_source: str,
    classification_confidence: float,
) -> DatasetPolicyRevision:
    """Append a complete default without mutating immutable Dataset evidence."""

    if not can_write_workspace(db, actor, workspace_id):
        raise IdentityError("workspace write denied", status_code=403)
    for value, choices, field in (
        (sensitivity_class, SENSITIVITY_CLASSES, "sensitivity_class"),
        (llm_exposure_policy, DATA_USE_POLICIES, "llm_exposure_policy"),
        (retention_class, RETENTION_CLASSES, "retention_class"),
        (residency_class, RESIDENCY_CLASSES, "residency_class"),
        (classification_source, CLASSIFICATION_SOURCES, "classification_source"),
    ):
        _validate_label(value, choices, field)
    if type(classification_confidence) not in (int, float) or (
        not math.isfinite(classification_confidence)
        or not 0 <= classification_confidence <= 1
    ):
        raise IdentityError("classification_confidence must be between 0 and 1", status_code=400)
    if type(expected_revision) is not int or expected_revision < 0:
        raise IdentityError("expected_revision must be nonnegative", status_code=400)
    dataset = db.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.workspace_id == workspace_id).with_for_update()
    )
    if dataset is None:
        raise IdentityError("dataset not found", status_code=404)
    current = db.scalar(
        select(DatasetPolicyRevision.revision)
        .where(DatasetPolicyRevision.dataset_id == dataset_id, DatasetPolicyRevision.workspace_id == workspace_id)
        .order_by(DatasetPolicyRevision.revision.desc())
        .limit(1)
    ) or 0
    if current != expected_revision:
        raise IdentityError("dataset policy revision conflict", status_code=409)
    row = DatasetPolicyRevision(
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        revision=current + 1,
        policy_schema_version=DATA_POLICY_SCHEMA_VERSION,
        sensitivity_class=sensitivity_class,
        llm_exposure_policy=llm_exposure_policy,
        retention_class=retention_class,
        residency_class=residency_class,
        classification_source=classification_source,
        classification_confidence=classification_confidence,
        created_by_user_id=actor.id,
    )
    db.add(row)
    db.flush()
    return row


def resolve_dataset_policy(
    db: Session, *, actor: User, workspace_id: UUID, dataset_id: UUID
) -> EffectiveDatasetPolicy:
    """Return conservative metadata only; this does not authorize LLM egress."""

    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("workspace read denied", status_code=403)
    dataset = db.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.workspace_id == workspace_id)
    )
    if dataset is None:
        raise IdentityError("dataset not found", status_code=404)
    default = db.scalar(
        select(DatasetPolicyRevision)
        .where(DatasetPolicyRevision.dataset_id == dataset_id, DatasetPolicyRevision.workspace_id == workspace_id)
        .order_by(DatasetPolicyRevision.revision.desc())
        .limit(1)
    )
    columns = list(
        db.scalars(
            select(DatasetColumn)
            .where(DatasetColumn.dataset_id == dataset_id, DatasetColumn.workspace_id == workspace_id)
            .order_by(DatasetColumn.ordinal_position, DatasetColumn.id)
        )
    )
    effective: list[EffectiveColumnPolicy] = []
    for column in columns:
        known = bool(
            default is not None
            and default.policy_schema_version == DATA_POLICY_SCHEMA_VERSION
            and default.classification_confidence > 0
            and column.policy_schema_version == DATA_POLICY_SCHEMA_VERSION
            and column.classification_source in CLASSIFICATION_SOURCES
            and column.classification_confidence is not None
            and column.classification_confidence > 0
        )
        sensitivity = _restrict_sensitivity(
            default.sensitivity_class if default else None, column.sensitivity_class
        ) if known else None
        exposure = _restrict_exposure(
            default.llm_exposure_policy if default else None, column.llm_exposure_policy
        ) if known else "deny"
        retention = _restrict_retention(
            default.retention_class if default else None, column.retention_class
        ) if known else None
        residency = _restrict_residency(
            default.residency_class if default else None, column.residency_class
        ) if known else None
        if sensitivity is None or retention is None or residency is None:
            exposure = "deny"
        effective.append(
            EffectiveColumnPolicy(column.id, sensitivity, exposure, retention, residency)
        )
    # A dataset with no tabular columns (e.g. an unstructured log) is governed by
    # its dataset-level default alone; any dataset with columns needs every one.
    no_columns = not columns and dataset.column_count == 0
    complete = bool(default and default.classification_confidence > 0 and (
        no_columns
        or (columns and len(columns) == dataset.column_count and all(
            item.sensitivity_class is not None
            and item.retention_class is not None
            and item.residency_class is not None
            for item in effective
        ))
    ))
    dataset_exposure = default.llm_exposure_policy if default and complete else "deny"
    for item in effective:
        dataset_exposure = _restrict_exposure(dataset_exposure, item.llm_exposure_policy)
    return EffectiveDatasetPolicy(
        dataset_id=dataset_id,
        workspace_id=workspace_id,
        policy_schema_version=DATA_POLICY_SCHEMA_VERSION,
        dataset_policy_revision=default.revision if default else None,
        sensitivity_class=(
            max((item.sensitivity_class for item in effective), key=_SENSITIVITY_RANK.__getitem__)
            if complete and effective else (default.sensitivity_class if complete else None)
        ),
        llm_exposure_policy=dataset_exposure,
        retention_class=(
            default.retention_class if complete and all(item.retention_class == default.retention_class for item in effective)
            else None
        ),
        residency_class=(
            max((item.residency_class for item in effective), key=_RESIDENCY_RANK.__getitem__)
            if complete and effective else (default.residency_class if complete else None)
        ),
        columns=tuple(effective),
        complete=complete,
    )


def schema_digest_from_columns(columns: list[dict[str, Any]]) -> str:
    payload = [
        {
            "name": column.get("name"),
            "dtype": column.get("dtype") or column.get("physical_dtype"),
            "semantic": column.get("semantic") or column.get("semantic_type"),
        }
        for column in columns
    ]
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return str(value)
    if isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int,)):
        return int(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return float(value)
    if hasattr(value, "item"):
        try:
            return _jsonable(value.item())
        except (ValueError, AttributeError):
            return str(value)
    return str(value)


def persist_dataset_columns(
    db: Session,
    *,
    workspace_id: UUID,
    dataset_id: UUID,
    schema: dict[str, Any] | None = None,
    frame: pd.DataFrame | None = None,
) -> list[DatasetColumn]:
    """Insert searchable column rows. Dataset itself stays immutable."""

    profile_by_name: dict[str, dict[str, Any]] = {}
    if frame is not None:
        try:
            profile = profile_frame(frame)
            profile_by_name = {
                str(item["name"]): item for item in profile.get("columns") or []
            }
        except Exception:
            profile_by_name = {}

    schema_columns = list((schema or {}).get("columns") or [])
    if not schema_columns and frame is not None:
        schema_columns = [
            {"name": str(name), "dtype": str(dtype), "semantic": "unknown"}
            for name, dtype in frame.dtypes.items()
        ]

    rows: list[DatasetColumn] = []
    seen: dict[str, int] = {}
    for position, column in enumerate(schema_columns, start=1):
        base_name = str(column.get("name") or f"column_{position}")
        count = seen.get(base_name, 0)
        name = base_name if count == 0 else f"{base_name}_{count}"
        seen[base_name] = count + 1
        stats = dict(profile_by_name.get(base_name) or profile_by_name.get(name) or {})
        missing_count = int(stats.get("missing_count") or stats.get("missing") or 0)
        missing_fraction = float(stats.get("missing_pct") or stats.get("missing_fraction") or 0.0)
        unique_count = int(stats.get("unique_count") or stats.get("unique") or 0)
        cardinality = stats.get("cardinality")
        mean = _jsonable(stats.get("mean"))
        median = _jsonable(stats.get("median"))
        extra = {
            key: value
            for key, value in stats.items()
            if key
            not in {
                "name",
                "dtype",
                "missing",
                "missing_count",
                "missing_pct",
                "missing_percentage",
                "missing_ratio",
                "unique",
                "unique_count",
                "cardinality",
                "min",
                "max",
                "mean",
                "median",
            }
        }
        row = DatasetColumn(
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            ordinal_position=position,
            name=name[:256],
            physical_dtype=str(column.get("dtype") or stats.get("dtype") or "unknown")[:64],
            semantic_type=(column.get("semantic") or None),
            role=None,
            nullable=True,
            missing_count=missing_count,
            missing_fraction=missing_fraction,
            unique_count=unique_count,
            cardinality=int(cardinality) if cardinality is not None else (unique_count or None),
            min_value=_jsonable(stats.get("min")),
            max_value=_jsonable(stats.get("max")),
            mean_value=float(mean) if isinstance(mean, (int, float)) else None,
            median_value=float(median) if isinstance(median, (int, float)) else None,
            stats=extra,
            # Policy fields stay NULL. This service does not classify columns.
        )
        db.add(row)
        rows.append(row)
    if rows:
        db.flush()
    return rows


def set_dataset_column_policy(
    db: Session,
    *,
    workspace_id: UUID,
    column_id: UUID,
    sensitivity_class: str | None = None,
    classification_source: str | None = None,
    model_use_policy: str | None = None,
    llm_exposure_policy: str | None = None,
    retention_class: str | None = None,
    residency_class: str | None = None,
    classification_confidence: float | None = None,
) -> DatasetColumn:
    """Store declared policy labels. Does not infer or classify."""

    row = db.get(DatasetColumn, column_id)
    if row is None or row.workspace_id != workspace_id:
        raise IdentityError("dataset column not found", status_code=404)
    if sensitivity_class is not None and sensitivity_class not in SENSITIVITY_CLASSES:
        raise IdentityError(f"unsupported sensitivity_class: {sensitivity_class}", status_code=400)
    if (
        classification_source is not None
        and classification_source not in CLASSIFICATION_SOURCES
    ):
        raise IdentityError(
            f"unsupported classification_source: {classification_source}",
            status_code=400,
        )
    if model_use_policy is not None and model_use_policy not in DATA_USE_POLICIES:
        raise IdentityError(f"unsupported model_use_policy: {model_use_policy}", status_code=400)
    if llm_exposure_policy is not None and llm_exposure_policy not in DATA_USE_POLICIES:
        raise IdentityError(
            f"unsupported llm_exposure_policy: {llm_exposure_policy}",
            status_code=400,
        )
    if retention_class is not None and retention_class not in RETENTION_CLASSES:
        raise IdentityError(f"unsupported retention_class: {retention_class}", status_code=400)
    if residency_class is not None and residency_class not in RESIDENCY_CLASSES:
        raise IdentityError(f"unsupported residency_class: {residency_class}", status_code=400)
    if classification_confidence is not None and (
        type(classification_confidence) not in (int, float)
        or not math.isfinite(classification_confidence)
        or not 0 <= classification_confidence <= 1
    ):
        raise IdentityError("classification_confidence must be between 0 and 1", status_code=400)
    row.sensitivity_class = sensitivity_class
    row.classification_source = classification_source
    row.model_use_policy = model_use_policy
    row.llm_exposure_policy = llm_exposure_policy
    row.retention_class = retention_class
    row.residency_class = residency_class
    row.classification_confidence = classification_confidence
    row.policy_schema_version = DATA_POLICY_SCHEMA_VERSION
    db.flush()
    return row
