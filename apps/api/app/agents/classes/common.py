"""Helpers shared by the specialist classes (P6.4-A): strict output models, the
number-citation check and the dataset column metadata section (no rows, no whole-file
statistics: ADR 0008 §2c — a whole-file profile includes the holdout rows)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.agents.runtime.base import ContextRequest
from app.agents.tools.shaping import Shaped, code, data_text

NAME_MAX = 256
_DECIMAL = re.compile(r"(?<![\w.])(\d+\.\d+|\d+(?:\.\d+)?%)")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def uncited_numbers(texts: Iterable[str], cited: Iterable[float]) -> list[str]:
    """Decimal numbers and percentages in free text that are not one of the cited values
    at the stated precision (an agent may round a CV metric, never invent one)."""

    values = [float(v) for v in cited]
    found = []
    for text in texts:
        for token in _DECIMAL.findall(text or ""):
            percent = token.endswith("%")
            raw = token.rstrip("%")
            places = len(raw.split(".", 1)[1]) if "." in raw else 0
            number = float(raw)
            scale = 100.0 if percent else 1.0
            if not any(abs(v * scale - number) <= 0.5 * 10 ** -places + 1e-12 for v in values):
                found.append(token)
    return found


def dtype_family(physical_dtype: str | None) -> str:
    value = (physical_dtype or "").lower()
    if "bool" in value:
        return "boolean"
    if "datetime" in value or "date" in value:
        return "datetime"
    if "int" in value or "float" in value or "decimal" in value or "double" in value:
        return "numeric"
    return "text_or_category"


def _code_or_none(value: Any) -> Any:
    return code(value) if isinstance(value, str) and _CODE.fullmatch(value) else None


def dataset_columns(request: ContextRequest, dataset_id: UUID) -> list[Any]:
    """The dataset's recorded columns after the same authorization as ``GET /v1/datasets/{id}``."""

    from app.agents.tools.definitions.reads import ServiceReads, map_service_errors
    from app.db.models import DatasetColumn

    ctx = request.tool_ctx
    with map_service_errors():
        ServiceReads(ctx).dataset(dataset_id)  # authorizes (404-safe) before the columns are read
    return list(ctx.db.scalars(select(DatasetColumn).where(
        DatasetColumn.workspace_id == ctx.workspace_id, DatasetColumn.dataset_id == dataset_id,
    ).order_by(DatasetColumn.ordinal_position)))


def column_section(dataset_id: UUID, rows: list[Any]) -> Shaped:
    """Column metadata: name (dataset text), dtype family, recorded semantic type and role,
    and whether values are missing at all (a flag, never a whole-file fraction)."""

    columns = [{
        "name": data_text(row.name, NAME_MAX), "position": int(row.ordinal_position),
        "dtype": code(dtype_family(row.physical_dtype)), "semantic_type": _code_or_none(row.semantic_type),
        "role": _code_or_none(row.role), "has_missing": bool(row.missing_count),
    } for row in rows[:200]]
    return Shaped({"dataset_id": dataset_id, "columns": columns, "omitted": max(0, len(rows) - 200),
                   "note": data_text("Column metadata only: no rows, no whole-file statistics.")},
                  source_datasets=(dataset_id,))


def column_names(db: Any, workspace_id: UUID, dataset_id: UUID | None) -> set[str]:
    from app.db.models import DatasetColumn

    if dataset_id is None:
        return set()
    return set(db.scalars(select(DatasetColumn.name).where(
        DatasetColumn.workspace_id == workspace_id, DatasetColumn.dataset_id == dataset_id)))
