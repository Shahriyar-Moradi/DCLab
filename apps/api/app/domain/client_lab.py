from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.translation.models import ClientFacingInsight, InsightCategory


class ClientLabPredictionRow(BaseModel):
    prediction: str
    probability: float | None = None


class ClientLabRunOutcome(BaseModel):
    """Plain-language completed-run summary. No engine internals."""

    dataset_name: str
    record_count: int
    feature_count: int
    target_label: str
    task_kind: str
    method_label: str
    performance_percent: float
    performance_summary: str
    prediction_count: int
    title: str
    summary: str
    records_line: str
    target_line: str
    predictions: list[ClientLabPredictionRow] = Field(default_factory=list)
    download_available: bool = False


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
    """Capability 1: we accepted the file. Structuring it is not done yet.

    `progress` is a client-safe view of the behind-the-scenes job: looking /
    ready / saved. Engine internals never appear on this payload.

    `run_id` is this upload's ML-run identity (the same UUID as `id`). `status`,
    `stage`, and `pipeline_status` are the same four-state view (queued /
    processing / completed / failed). `headline` is the single processing line
    while the job is running; `steps` is always empty — the client page is not
    a stage tracker. When the job has finished, `outcome` holds a plain-language
    result and the predictions — still free of engine internals.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    dataset_id: UUID | None = None
    status: str
    stage: str
    headline: str = ""
    steps: list[dict] = Field(default_factory=list)
    category: InsightCategory
    filename: str
    kind: str
    record_count: int
    fields_noticed: list[str]
    has_named_fields: bool
    structured: bool = False
    progress: str
    message: str
    pipeline_status: str
    insights: list[ClientFacingInsight] = []
    outcome: ClientLabRunOutcome | None = None
    created_at: datetime
