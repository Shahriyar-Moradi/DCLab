"""create one legacy-import project per workspace and attach unambiguous orphans

Revision ID: 0036_legacy_import_projects
Revises: 0035_database_integrity
Create Date: 2026-09-05

Existing records without Project get one deterministic compatibility project
per Workspace (slug ``legacy-import``). Only rows on the frozen 0036 table list
with a known workspace_id and NULL project_id are attached. Two historical
Workflows in the same workspace share that bucket; they are not merged into one
case study.

Actorless workspaces still receive a system-created compatibility Project
(``provenance = system_legacy_import``, ``created_by`` NULL). No user is invented.
"""

from __future__ import annotations

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.integrity import (
    immutability_disable_trigger_statements,
    immutability_enable_trigger_statements,
)
from app.db.legacy_import import LEGACY_IMPORT_BACKFILL_TABLES
from app.domain.workspace_identity import (
    LEGACY_IMPORT_PROJECT_DESCRIPTION,
    LEGACY_IMPORT_PROJECT_NAME,
    LEGACY_IMPORT_PROJECT_SLUG,
    PROJECT_PROVENANCE_SYSTEM_LEGACY_IMPORT,
)

revision: str = "0036_legacy_import_projects"
down_revision: Union[str, Sequence[str], None] = "0035_database_integrity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _quote(connection, name: str) -> str:
    return connection.dialect.identifier_preparer.quote(name)


def _orphan_workspace_ids(connection) -> set:
    ids: set = set()
    for table in LEGACY_IMPORT_BACKFILL_TABLES:
        quoted = _quote(connection, table)
        rows = connection.execute(
            sa.text(
                f"SELECT DISTINCT workspace_id FROM {quoted} WHERE project_id IS NULL"
            )
        )
        ids.update(row[0] for row in rows if row[0] is not None)
    return ids


def _actors_by_workspace(connection) -> dict:
    actors: dict = {}
    for row in connection.execute(
        sa.text(
            """
            SELECT DISTINCT ON (workspace_id) workspace_id, user_id
            FROM workspace_memberships
            ORDER BY workspace_id, created_at ASC, user_id ASC
            """
        )
    ):
        actors[row[0]] = row[1]
    for row in connection.execute(
        sa.text(
            """
            SELECT DISTINCT ON (workspace_id) workspace_id, id
            FROM users
            WHERE workspace_id IS NOT NULL
            ORDER BY workspace_id, created_at ASC, id ASC
            """
        )
    ):
        actors.setdefault(row[0], row[1])
    return actors


def _run_with_immutability_triggers_disabled(connection, callback) -> None:
    disable = immutability_disable_trigger_statements(LEGACY_IMPORT_BACKFILL_TABLES)
    enable = immutability_enable_trigger_statements(LEGACY_IMPORT_BACKFILL_TABLES)
    for statement in disable:
        connection.execute(sa.text(statement))
    try:
        callback()
    finally:
        for statement in enable:
            connection.execute(sa.text(statement))


def _attach_legacy_import_projects(connection) -> None:
    for table in LEGACY_IMPORT_BACKFILL_TABLES:
        quoted = _quote(connection, table)
        connection.execute(
            sa.text(
                f"""
                UPDATE {quoted} AS target
                SET project_id = project.id
                FROM projects AS project
                WHERE project.workspace_id = target.workspace_id
                  AND project.slug = :slug
                  AND target.project_id IS NULL
                """
            ),
            {"slug": LEGACY_IMPORT_PROJECT_SLUG},
        )


def _detach_legacy_import_projects(connection) -> None:
    for table in LEGACY_IMPORT_BACKFILL_TABLES:
        quoted = _quote(connection, table)
        connection.execute(
            sa.text(
                f"""
                UPDATE {quoted} AS target
                SET project_id = NULL
                FROM projects AS project
                WHERE project.id = target.project_id
                  AND project.slug = :slug
                """
            ),
            {"slug": LEGACY_IMPORT_PROJECT_SLUG},
        )


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "provenance",
            sa.String(length=32),
            server_default="user",
            nullable=False,
        ),
    )
    op.alter_column(
        "projects",
        "created_by",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_projects_provenance_valid",
        "projects",
        "provenance IN ('user', 'system_legacy_import')",
    )
    op.create_check_constraint(
        "ck_projects_user_provenance_requires_actor",
        "projects",
        "provenance <> 'user' OR created_by IS NOT NULL",
    )

    connection = op.get_bind()
    orphan_ids = _orphan_workspace_ids(connection)
    actors = _actors_by_workspace(connection)
    existing = {
        row[0]
        for row in connection.execute(
            sa.text("SELECT workspace_id FROM projects WHERE slug = :slug"),
            {"slug": LEGACY_IMPORT_PROJECT_SLUG},
        )
    }

    for workspace_id in orphan_ids:
        if workspace_id in existing:
            continue
        connection.execute(
            sa.text(
                """
                INSERT INTO projects (
                    id, workspace_id, name, slug, description, status,
                    created_by, provenance
                )
                VALUES (
                    :id, :workspace_id, :name, :slug, :description, 'active',
                    :created_by, :provenance
                )
                """
            ),
            {
                "id": uuid4(),
                "workspace_id": workspace_id,
                "name": LEGACY_IMPORT_PROJECT_NAME,
                "slug": LEGACY_IMPORT_PROJECT_SLUG,
                "description": LEGACY_IMPORT_PROJECT_DESCRIPTION,
                "created_by": actors.get(workspace_id),
                "provenance": PROJECT_PROVENANCE_SYSTEM_LEGACY_IMPORT,
            },
        )
        existing.add(workspace_id)

    # 0035 freezes datasets / model_versions / model_selection_decisions.
    # Disable only those named DCLab triggers for this project_id backfill.
    _run_with_immutability_triggers_disabled(
        connection, lambda: _attach_legacy_import_projects(connection)
    )


def downgrade() -> None:
    connection = op.get_bind()
    _run_with_immutability_triggers_disabled(
        connection, lambda: _detach_legacy_import_projects(connection)
    )
    connection.execute(
        sa.text("DELETE FROM projects WHERE slug = :slug"),
        {"slug": LEGACY_IMPORT_PROJECT_SLUG},
    )
    op.drop_constraint(
        "ck_projects_user_provenance_requires_actor", "projects", type_="check"
    )
    op.drop_constraint("ck_projects_provenance_valid", "projects", type_="check")
    op.alter_column(
        "projects",
        "created_by",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
        existing_nullable=True,
    )
    op.drop_column("projects", "provenance")
