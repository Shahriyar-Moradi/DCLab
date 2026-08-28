"""Admin-only. The full detail behind a Labs custom-box upload: the simple-case
auto-train job (EDA, target choice, missing-value decisions, column roles,
candidate scores) that `apps/api/app/services/auto_train_service.py` runs
behind the client's upload response. See docs/LABS_DATA_UNDERSTANDING.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AdminClientUploadSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    category: str
    original_filename: str
    kind: str
    record_count: int
    has_named_fields: bool
    pipeline_status: str
    experiment_id: UUID | None
    created_at: datetime


class AdminClientUploadDetail(AdminClientUploadSummary):
    stored_path: str
    fields_noticed: list[str]
    pipeline_log: dict[str, Any] | None
