"""DatasetColumn facts derived from a physical Dataset (DatasetVersion)."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import DatasetColumn
from app.engine.schema.profiler import profile_frame


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
        )
        db.add(row)
        rows.append(row)
    if rows:
        db.flush()
    return rows
