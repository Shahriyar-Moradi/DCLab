"""Shared ML-engineering API surface for Personal and Business workspaces."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, request_workspace_access
from app.db.models import User
from app.db.session import get_db
from app.services.workspace_capability_service import (
    WORKSPACE_EXECUTE_ML,
    effective_capability_matrix,
)

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
        can_execute_ml=effective_capability_matrix(db, user, access.workspace_id)[
            WORKSPACE_EXECUTE_ML
        ],
    )
