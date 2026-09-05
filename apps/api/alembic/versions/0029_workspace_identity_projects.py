"""workspace kinds, customer roles, entitlements, projects, problem specs

Revision ID: 0029_workspace_identity
Revises: 0028_semantic_leakage_purpose
Create Date: 2026-09-05
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_workspace_identity"
down_revision: Union[str, Sequence[str], None] = "0028_semantic_leakage_purpose"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USERS_ROLE_NEW = (
    "role IN ('dclab_admin', 'dclab_developer', 'business_admin', "
    "'business_developer', 'client_user', 'workspace_owner', "
    "'workspace_admin', 'ml_engineer', 'viewer')"
)
_USERS_ROLE_OLD = (
    "role IN ('dclab_admin', 'dclab_developer', 'business_admin', "
    "'business_developer', 'client_user')"
)
_MEMBERSHIP_ROLE_NEW = (
    "role IN ('business_admin', 'business_developer', 'workspace_owner', "
    "'workspace_admin', 'ml_engineer', 'viewer')"
)
_MEMBERSHIP_ROLE_OLD = "role IN ('business_admin', 'business_developer')"


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "kind",
            sa.String(length=32),
            server_default="business",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_workspaces_kind_valid",
        "workspaces",
        "kind IN ('personal', 'business')",
    )

    op.drop_constraint("ck_users_role_valid", "users", type_="check")
    op.create_check_constraint("ck_users_role_valid", "users", _USERS_ROLE_NEW)

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

    op.create_table(
        "workspace_entitlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entitlement_key", sa.String(length=128), nullable=False),
        sa.Column("value_json", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
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
        sa.UniqueConstraint(
            "workspace_id",
            "entitlement_key",
            name="uq_workspace_entitlements_workspace_key",
        ),
    )
    op.create_index(
        "ix_workspace_entitlements_workspace_id",
        "workspace_entitlements",
        ["workspace_id"],
    )

    connection = op.get_bind()
    workspace_rows = connection.execute(sa.text("SELECT id FROM workspaces")).mappings()
    entitlement_table = sa.table(
        "workspace_entitlements",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
        sa.column("entitlement_key", sa.String()),
        sa.column("value_json", postgresql.JSONB()),
        sa.column("source", sa.String()),
    )
    op.bulk_insert(
        entitlement_table,
        [
            {
                "id": uuid4(),
                "workspace_id": row["id"],
                "entitlement_key": "max_members",
                "value_json": 5,
                "source": "system_default",
            }
            for row in workspace_rows
        ],
    )

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=4000), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
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
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_projects_workspace_slug"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_projects_workspace_id"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_projects_status_valid"),
    )
    op.create_index(
        "ix_projects_workspace_created_at",
        "projects",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_projects_workspace_status_created_at",
        "projects",
        ["workspace_id", "status", "created_at"],
    )

    op.create_table(
        "problem_specs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("target_column", sa.String(length=256), nullable=True),
        sa.Column("prediction_unit", sa.String(length=128), nullable=True),
        sa.Column("prediction_time_column", sa.String(length=256), nullable=True),
        sa.Column("prediction_horizon", sa.String(length=128), nullable=True),
        sa.Column("primary_metric", sa.String(length=128), nullable=True),
        sa.Column("business_objective", sa.String(length=4000), nullable=False),
        sa.Column("constraints", postgresql.JSONB(), nullable=False),
        sa.Column("success_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "project_id", "version", name="uq_problem_specs_project_version"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_problem_specs_workspace_project",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("version >= 1", name="ck_problem_specs_version_positive"),
        sa.CheckConstraint(
            "status IN ('draft', 'locked')",
            name="ck_problem_specs_status_valid",
        ),
    )
    op.create_index(
        "ix_problem_specs_workspace_id", "problem_specs", ["workspace_id"]
    )
    op.create_index("ix_problem_specs_project_id", "problem_specs", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_problem_specs_project_id", table_name="problem_specs")
    op.drop_index("ix_problem_specs_workspace_id", table_name="problem_specs")
    op.drop_table("problem_specs")
    op.drop_index(
        "ix_projects_workspace_status_created_at", table_name="projects"
    )
    op.drop_index("ix_projects_workspace_created_at", table_name="projects")
    op.drop_table("projects")
    op.drop_index(
        "ix_workspace_entitlements_workspace_id",
        table_name="workspace_entitlements",
    )
    op.drop_table("workspace_entitlements")

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
    op.drop_constraint("ck_users_role_valid", "users", type_="check")
    op.create_check_constraint("ck_users_role_valid", "users", _USERS_ROLE_OLD)
    op.drop_constraint("ck_workspaces_kind_valid", "workspaces", type_="check")
    op.drop_column("workspaces", "kind")
