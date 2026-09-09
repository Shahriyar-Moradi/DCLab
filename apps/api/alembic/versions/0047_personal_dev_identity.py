"""personal_developer compatibility role and nullable workflow domain

Revision ID: 0047_personal_dev_identity
Revises: 0046_ingestion_job_tenant_fks
Create Date: 2026-09-09

Workspace.kind already exists from 0029_workspace_identity. This revision
adds the Personal Development compatibility role and lets Personal workflows
omit a Business domain.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_personal_dev_identity"
down_revision: Union[str, Sequence[str], None] = "0046_ingestion_job_tenant_fks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USERS_ROLE_NEW = (
    "role IN ('dclab_admin', 'dclab_developer', 'business_admin', "
    "'business_developer', 'personal_developer', 'client_user', "
    "'workspace_owner', 'workspace_admin', 'ml_engineer', 'viewer')"
)
_USERS_ROLE_OLD = (
    "role IN ('dclab_admin', 'dclab_developer', 'business_admin', "
    "'business_developer', 'client_user', 'workspace_owner', "
    "'workspace_admin', 'ml_engineer', 'viewer')"
)
_MEMBERSHIP_ROLE_NEW = (
    "role IN ('business_admin', 'business_developer', 'personal_developer', "
    "'workspace_owner', 'workspace_admin', 'ml_engineer', 'viewer')"
)
_MEMBERSHIP_ROLE_OLD = (
    "role IN ('business_admin', 'business_developer', 'workspace_owner', "
    "'workspace_admin', 'ml_engineer', 'viewer')"
)


def upgrade() -> None:
    op.drop_constraint("ck_users_role_valid", "users", type_="check")
    op.create_check_constraint("ck_users_role_valid", "users", _USERS_ROLE_NEW)
    op.drop_constraint("ck_users_client_requires_workspace", "users", type_="check")
    op.create_check_constraint(
        "ck_users_client_requires_workspace",
        "users",
        "role NOT IN ('client_user', 'personal_developer') OR workspace_id IS NOT NULL",
    )
    op.drop_constraint(
        "ck_workspace_memberships_role_valid",
        "workspace_memberships",
        type_="check",
    )
    op.create_check_constraint(
        "ck_workspace_memberships_role_valid",
        "workspace_memberships",
        _MEMBERSHIP_ROLE_NEW,
    )
    op.alter_column(
        "ml_workflows",
        "workspace_domain_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    connection = op.get_bind()
    personal_users = connection.execute(
        sa.text("SELECT count(*) FROM users WHERE role = 'personal_developer'")
    ).scalar_one()
    personal_memberships = connection.execute(
        sa.text(
            "SELECT count(*) FROM workspace_memberships "
            "WHERE role = 'personal_developer'"
        )
    ).scalar_one()
    domainless = connection.execute(
        sa.text("SELECT count(*) FROM ml_workflows WHERE workspace_domain_id IS NULL")
    ).scalar_one()
    if personal_users or personal_memberships or domainless:
        raise RuntimeError(
            "cannot downgrade 0047 while personal_developer rows or domain-less "
            "workflows exist"
        )
    op.alter_column(
        "ml_workflows",
        "workspace_domain_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_constraint(
        "ck_workspace_memberships_role_valid",
        "workspace_memberships",
        type_="check",
    )
    op.create_check_constraint(
        "ck_workspace_memberships_role_valid",
        "workspace_memberships",
        _MEMBERSHIP_ROLE_OLD,
    )
    op.drop_constraint("ck_users_client_requires_workspace", "users", type_="check")
    op.create_check_constraint(
        "ck_users_client_requires_workspace",
        "users",
        "role <> 'client_user' OR workspace_id IS NOT NULL",
    )
    op.drop_constraint("ck_users_role_valid", "users", type_="check")
    op.create_check_constraint("ck_users_role_valid", "users", _USERS_ROLE_OLD)
