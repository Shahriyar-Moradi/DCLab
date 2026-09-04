"""Shared ML-engineering API surface for Personal and Business workspaces.

This router is intentionally thin. It exposes workspace-scoped ML engineering
capabilities while reusing the existing DCLab core services. Business organization
administration remains under /business and translated client behavior remains under
/app.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, request_workspace_access
from app.db.models import User
from app.db.session import get_db
from app.services.authorization_service import can_execute_workspace_ml

router = APIRouter(tags=["development"])


class DevelopmentContextRead(BaseModel):
    workspace_id: UUID
    role: str | None
    can_execute_ml: bool


@router.get("/context", response_model=DevelopmentContextRead)
def development_context(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DevelopmentContextRead:
    access = request_workspace_access(request)
    role = access.workspace_role
    role_value = role.value if hasattr(role, "value") else role
    return DevelopmentContextRead(
        workspace_id=access.workspace_id,
        role=role_value,
        can_execute_ml=can_execute_workspace_ml(db, user, access.workspace_id),
    )
