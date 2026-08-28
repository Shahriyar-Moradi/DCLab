from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.translation.models import ClientFacingInsight, InsightCategory


class ClientLabProblem(BaseModel):
    """One of the fixed, pre-defined problems a trial can run — not open-ended
    configuration. `sample_scenario` names the illustrative sample dataset
    (a fictional company) that ships with DCLab so a run always works with zero
    setup; a client may instead upload their own file shaped the same way."""

    use_case: str
    category: InsightCategory
    question: str
    sample_scenario: str
    sample_row_count: int
    max_upload_rows: int
    max_trial_runs: int
    required_columns: list[str]


class ClientLabRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    use_case: str
    category: InsightCategory
    data_source: str
    row_count: int
    status: str
    failure_reason: str | None
    insights: list[ClientFacingInsight]
    created_at: datetime


class ClientLabQuotaRead(BaseModel):
    use_case: str
    max_trial_runs: int
    runs_used: int
    runs_remaining: int


class ClientLabUploadRead(BaseModel):
    """Capability 1: we accepted the file. Structuring it is not done yet."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category: InsightCategory
    filename: str
    kind: str
    record_count: int
    fields_noticed: list[str]
    has_named_fields: bool
    structured: bool = False
    message: str
    created_at: datetime
