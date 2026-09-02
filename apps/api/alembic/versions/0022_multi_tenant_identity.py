"""add membership-based multi-tenant identity foundation

Revision ID: 0022_multi_tenant_identity
Revises: 0021_lab_audit_index
Create Date: 2026-09-02
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_multi_tenant_identity"
down_revision: Union[str, Sequence[str], None] = "0021_lab_audit_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_users_role_valid", "users", type_="check")
    op.create_check_constraint(
        "ck_users_role_valid",
        "users",
        "role IN ('dclab_admin', 'dclab_developer', 'business_admin', "
        "'business_developer', 'client_user')",
    )

    op.create_table(
        "business_profiles",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("legal_name", sa.String(256), nullable=True),
        sa.Column("industry", sa.String(128), nullable=True),
        sa.Column("profile_data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "platform_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", name="uq_platform_memberships_user_id"),
        sa.CheckConstraint(
            "role IN ('dclab_admin', 'dclab_developer')",
            name="ck_platform_memberships_role_valid",
        ),
    )
    op.create_index(
        "ix_platform_memberships_role", "platform_memberships", ["role"]
    )

    op.create_table(
        "workspace_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_memberships_workspace_user",
        ),
        sa.CheckConstraint(
            "role IN ('business_admin', 'business_developer')",
            name="ck_workspace_memberships_role_valid",
        ),
    )
    op.create_index(
        "ix_workspace_memberships_workspace_id",
        "workspace_memberships",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_memberships_user_id",
        "workspace_memberships",
        ["user_id"],
    )
    op.create_index(
        "ix_workspace_memberships_role", "workspace_memberships", ["role"]
    )

    op.create_table(
        "workspace_capabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "capability",
            name="uq_workspace_capabilities_workspace_key",
        ),
    )
    op.create_index(
        "ix_workspace_capabilities_workspace_id",
        "workspace_capabilities",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_capabilities_capability",
        "workspace_capabilities",
        ["capability"],
    )

    # Business names are copied from the canonical Workspace; no competing
    # organization identifier is introduced.
    connection = op.get_bind()
    workspace_rows = connection.execute(
        sa.text("SELECT id, name FROM workspaces")
    ).mappings()
    business_profile = sa.table(
        "business_profiles",
        sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
        sa.column("legal_name", sa.String()),
        sa.column("industry", sa.String()),
        sa.column("profile_data", postgresql.JSONB()),
    )
    op.bulk_insert(
        business_profile,
        [
            {
                "workspace_id": row["id"],
                "legal_name": row["name"],
                "industry": None,
                "profile_data": {},
            }
            for row in workspace_rows
        ],
    )

    user_rows = connection.execute(
        sa.text("SELECT id, role, workspace_id FROM users")
    ).mappings()
    platform_membership = sa.table(
        "platform_memberships",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("user_id", postgresql.UUID(as_uuid=True)),
        sa.column("role", sa.String()),
    )
    workspace_membership = sa.table(
        "workspace_memberships",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
        sa.column("user_id", postgresql.UUID(as_uuid=True)),
        sa.column("role", sa.String()),
    )
    platform_rows: list[dict[str, object]] = []
    member_rows: list[dict[str, object]] = []
    for row in user_rows:
        if row["role"] == "dclab_admin":
            platform_rows.append(
                {"id": uuid4(), "user_id": row["id"], "role": "dclab_admin"}
            )
        elif row["role"] == "client_user" and row["workspace_id"] is not None:
            member_rows.append(
                {
                    "id": uuid4(),
                    "workspace_id": row["workspace_id"],
                    "user_id": row["id"],
                    "role": "business_admin",
                }
            )
    if platform_rows:
        op.bulk_insert(platform_membership, platform_rows)
    if member_rows:
        op.bulk_insert(workspace_membership, member_rows)

    # External business identifiers are tenant-local, not globally unique.
    op.drop_index("ix_opportunities_external_id", table_name="opportunities")
    op.create_index(
        "ix_opportunities_external_id",
        "opportunities",
        ["external_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_opportunities_workspace_external_id",
        "opportunities",
        ["workspace_id", "external_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_opportunities_workspace_external_id",
        "opportunities",
        type_="unique",
    )
    op.drop_index("ix_opportunities_external_id", table_name="opportunities")
    op.create_index(
        "ix_opportunities_external_id",
        "opportunities",
        ["external_id"],
        unique=True,
    )

    op.drop_index(
        "ix_workspace_capabilities_capability",
        table_name="workspace_capabilities",
    )
    op.drop_index(
        "ix_workspace_capabilities_workspace_id",
        table_name="workspace_capabilities",
    )
    op.drop_table("workspace_capabilities")
    op.drop_index("ix_workspace_memberships_role", table_name="workspace_memberships")
    op.drop_index(
        "ix_workspace_memberships_user_id", table_name="workspace_memberships"
    )
    op.drop_index(
        "ix_workspace_memberships_workspace_id", table_name="workspace_memberships"
    )
    op.drop_table("workspace_memberships")
    op.drop_index("ix_platform_memberships_role", table_name="platform_memberships")
    op.drop_table("platform_memberships")
    op.drop_table("business_profiles")

    op.drop_constraint("ck_users_role_valid", "users", type_="check")
    op.create_check_constraint(
        "ck_users_role_valid",
        "users",
        "role IN ('dclab_admin', 'client_user')",
    )
