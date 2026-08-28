"""Admin-only. Where "ROC-AUC improved 0.89 -> 0.90" belongs (Step 6) — pulled
from real, already-stored retrain history, not fabricated."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class MetricDelta(BaseModel):
    previous: float
    current: float
    delta: float


class RetrainEvent(BaseModel):
    id: UUID
    source: str  # "experiment" | "simulation" | "client_trial"
    name: str
    status: str
    metrics: dict[str, Any]
    metric_deltas: dict[str, MetricDelta]
    created_at: datetime
    client_lab_run_id: UUID | None = None


class DatasetHealth(BaseModel):
    id: UUID
    name: str
    row_count: int
    column_count: int
    last_profiled_at: datetime | None
    status: str  # "healthy" | "not_profiled" | "empty"


class MonitoringOverview(BaseModel):
    retrain_events: list[RetrainEvent]
    dataset_health: list[DatasetHealth]
    drift_detection_note: str
