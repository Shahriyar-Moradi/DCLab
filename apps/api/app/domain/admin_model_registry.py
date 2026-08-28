"""Admin-only. The full content moved in Step 4 — unrestricted, no translation
layer. Unifies every place this system trains a model: Lab experiments (client
data, ad-hoc tasks), the simulation pack (the eight bundled use cases), and
Step 7's client-triggered Labs trials (audited in full even though the
client-facing side of that same run only ever sees translated insights)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class RegisteredModel(BaseModel):
    id: UUID
    source: str  # "experiment" | "simulation" | "client_trial"
    name: str
    status: str
    model_family: str | None
    fusion: str | None
    metrics: dict[str, Any]
    candidate_count: int | None
    created_at: datetime
    client_lab_run_id: UUID | None = None


class ClientTrialAuditDetail(BaseModel):
    """Full, unrestricted `run_use_case` output for one client-triggered trial —
    the admin-only counterpart of a translated `ClientLabRunRead`."""

    id: UUID
    client_lab_run_id: UUID
    use_case: str
    payload: dict[str, Any]
    created_at: datetime
