"""Step 6 — Admin Organizations: list/detail of client accounts.

No plan/billing tier field is included — this build has no subscription or
billing model, and inventing one here would be exactly the kind of fabricated
content this project has avoided at every other step. What's shown is real:
who has access, how much of their own data is connected, and how much they've
actually used the product.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ClientLabRun, Decision, Opportunity, User, Workspace
from app.domain.admin_organization import OrganizationDetail, OrganizationSummary, OrganizationUserRead


def _summarize(db: Session, workspace: Workspace) -> OrganizationSummary:
    user_count = db.scalar(select(func.count(User.id)).where(User.workspace_id == workspace.id)) or 0
    opportunity_count = (
        db.scalar(select(func.count(Opportunity.id)).where(Opportunity.workspace_id == workspace.id)) or 0
    )
    decision_count = db.scalar(select(func.count(Decision.id)).where(Decision.workspace_id == workspace.id)) or 0
    trial_run_count = (
        db.scalar(select(func.count(ClientLabRun.id)).where(ClientLabRun.workspace_id == workspace.id)) or 0
    )
    return OrganizationSummary(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        created_at=workspace.created_at,
        user_count=user_count,
        opportunity_count=opportunity_count,
        decision_count=decision_count,
        trial_run_count=trial_run_count,
    )


def list_organizations(db: Session) -> list[OrganizationSummary]:
    workspaces = db.scalars(select(Workspace).order_by(Workspace.created_at)).all()
    return [_summarize(db, workspace) for workspace in workspaces]


def get_organization(db: Session, workspace_id: UUID) -> OrganizationDetail | None:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        return None
    summary = _summarize(db, workspace)
    users = db.scalars(
        select(User).where(User.workspace_id == workspace_id).order_by(User.created_at)
    ).all()
    return OrganizationDetail(
        **summary.model_dump(),
        users=[OrganizationUserRead.model_validate(user) for user in users],
    )
