from __future__ import annotations

import math
from io import BytesIO
from typing import Any
from uuid import uuid4

import pandas as pd
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import DEFAULT_ORG_ID, Opportunity
from app.domain.opportunity import OpportunityCreate, OpportunityUploadResult, RowError

UPDATE_COLUMNS = (
    "customer_id",
    "amount",
    "currency",
    "stage",
    "source",
    "owner_id",
    "close_date",
    "last_contact_days_ago",
    "engagement_score",
    "sales_rep_available",
    "industry",
    "num_interactions",
    "converted",
)


def _cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if pd.isna(value):
        return None
    return value


def ingest_opportunities_csv(db: Session, content: bytes) -> OpportunityUploadResult:
    """Validate each CSV row independently and upsert valid rows on external_id."""
    try:
        frame = pd.read_csv(BytesIO(content))
    except Exception as exc:  # noqa: BLE001 — report as a single row error, never 500
        return OpportunityUploadResult(
            inserted=0,
            rejected=1,
            errors=[RowError(row=0, reason=f"could not parse CSV: {exc}")],
        )

    if frame.empty:
        return OpportunityUploadResult(inserted=0, rejected=0, errors=[])

    valid_payloads: list[dict[str, Any]] = []
    errors: list[RowError] = []

    for index, series in frame.iterrows():
        row_number = int(index) + 2  # header is row 1
        raw = {str(col): _cell(series[col]) for col in frame.columns}
        try:
            parsed = OpportunityCreate.model_validate(raw)
        except ValidationError as exc:
            reasons = "; ".join(err["msg"] for err in exc.errors())
            errors.append(RowError(row=row_number, reason=reasons))
            continue
        payload = parsed.model_dump()
        payload["id"] = uuid4()
        payload["org_id"] = DEFAULT_ORG_ID
        if payload.get("created_at") is None:
            payload.pop("created_at")
        valid_payloads.append(payload)

    if valid_payloads:
        stmt = insert(Opportunity).values(valid_payloads)
        stmt = stmt.on_conflict_do_update(
            index_elements=["external_id"],
            set_={column: getattr(stmt.excluded, column) for column in UPDATE_COLUMNS},
        )
        db.execute(stmt)

    return OpportunityUploadResult(
        inserted=len(valid_payloads),
        rejected=len(errors),
        errors=errors,
    )
