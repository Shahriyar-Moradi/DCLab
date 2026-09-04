"""add personal workspace and shared ML execution identity foundation

Revision ID: 0028_personal_business_shared_core
Revises: 0027_repair_tenant_lineage
Create Date: 2026-09-04

This migration is deliberately additive. Existing workspaces are Business
workspaces, existing membership role strings remain valid, and existing Business
workflow-domain links are preserved. Personal Development uses the same Workspace
and ML lineage tables rather than introducing a second tenant/model hierarchy.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_personal_business_shared_core"
down_revision: Union[str, Sequence[str], None] = "0027_repair_tenant_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "kind",
            sa.String(16),
            nullable=False,
            server_default="business",
        ),
    )
    op.create_check_constraint(
        "ck_workspaces_kind_valid",
        "workspaces",
        "kind IN ('personal', 'business')",
    )
    op.create_index("ix_workspaces_kind", "workspaces", ["kind"])

    op.drop_constraint("ck_users_role_valid", "users", type_="check")
    op.create_check_constraint(
        "ck_users_role_valid",
        "users",
        "role IN ('dclab_admin', 'dclab_developer', 'business_admin', "
        "'business_developer', 'personal_developer', 'client_user')",
    )
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
        "role IN ('business_admin', 'business_developer', 'personal_developer')",
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
    personal_workspaces = connection.execute(
        sa.text("SELECT count(*) FROM workspaces WHERE kind = 'personal'")
    ).scalar_one()
    domainless_workflows = connection.execute(
        sa.text("SELECT count(*) FROM ml_workflows WHERE workspace_domain_id IS NULL")
    ).scalar_one()
    if any(
        (
            personal_users,
            personal_memberships,
            personal_workspaces,
            domainless_workflows,
        )
    ):
        raise RuntimeError(
            "cannot downgrade 0028 while Personal/shared-core data exists; "
            "migrate or remove those rows explicitly first"
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
        "role IN ('business_admin', 'business_developer')",
    )

    op.drop_constraint("ck_users_client_requires_workspace", "users", type_="check")
    op.create_check_constraint(
        "ck_users_client_requires_workspace",
        "users",
        "role <> 'client_user' OR workspace_id IS NOT NULL",
    )
    op.drop_constraint("ck_users_role_valid", "users", type_="check")
    op.create_check_constraint(
        "ck_users_role_valid",
        "users",
        "role IN ('dclab_admin', 'dclab_developer', 'business_admin', "
        "'business_developer', 'client_user')",
    )

    op.drop_index("ix_workspaces_kind", table_name="workspaces")
    op.drop_constraint("ck_workspaces_kind_valid", "workspaces", type_="check")
    op.drop_column("workspaces", "kind")
