"""Admin-only. Organizations are client accounts (Workspace rows) — full detail,
no translation layer involved (Step 6: that layer is a client-side-only concept).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class OrganizationUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime


class OrganizationSummary(BaseModel):
    id: UUID
    slug: str
    name: str
    created_at: datetime
    user_count: int
    opportunity_count: int
    decision_count: int
    trial_run_count: int


class OrganizationDetail(OrganizationSummary):
    users: list[OrganizationUserRead]
