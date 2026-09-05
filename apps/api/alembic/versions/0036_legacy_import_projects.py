"""create one legacy-import project per workspace and attach unambiguous orphans

Revision ID: 0036_legacy_import_projects
Revises: 0035_database_integrity
Create Date: 2026-09-05

Existing records without Project get one deterministic compatibility project
per Workspace (slug ``legacy-import``). Only rows with a known workspace_id and
NULL project_id are attached. Two historical Workflows in the same workspace
share that bucket; they are not merged into one case study.

Workspaces with orphan rows but no membership/user actor are skipped: Project
requires created_by, and inventing an owner would be unsafe.
"""

from __future__ import annotations

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

from app.domain.workspace_identity import (
    LEGACY_IMPORT_PROJECT_DESCRIPTION,
    LEGACY_IMPORT_PROJECT_NAME,
    LEGACY_IMPORT_PROJECT_SLUG,
)

revision: str = "0036_legacy_import_projects"
down_revision: Union[str, Sequence[str], None] = "0035_database_integrity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _quote(connection, name: str) -> str:
    return connection.dialect.identifier_preparer.quote(name)


def _nullable_project_tables(connection) -> list[str]:
    rows = connection.execute(
        sa.text(
            """
            SELECT c.table_name
            FROM information_schema.columns AS c
            JOIN information_schema.columns AS w
              ON w.table_schema = c.table_schema
             AND w.table_name = c.table_name
             AND w.column_name = 'workspace_id'
            WHERE c.table_schema = 'public'
              AND c.column_name = 'project_id'
              AND c.is_nullable = 'YES'
            ORDER BY c.table_name
            """
        )
    )
    return [str(row[0]) for row in rows]


def _orphan_workspace_ids(connection, tables: list[str]) -> set:
    ids: set = set()
    for table in tables:
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


def upgrade() -> None:
    connection = op.get_bind()
    # 0035 freezes datasets/model_versions. This data fix is the one allowed
    # exception: attach project_id on existing rows. Replica role skips user triggers.
    connection.execute(sa.text("SET LOCAL session_replication_role = 'replica'"))
    tables = _nullable_project_tables(connection)
    orphan_ids = _orphan_workspace_ids(connection, tables)
    actors = _actors_by_workspace(connection)
    existing = {
        row[0]
        for row in connection.execute(
            sa.text(
                "SELECT workspace_id FROM projects WHERE slug = :slug"
            ),
            {"slug": LEGACY_IMPORT_PROJECT_SLUG},
        )
    }

    for workspace_id in orphan_ids:
        if workspace_id in existing:
            continue
        created_by = actors.get(workspace_id)
        if created_by is None:
            continue
        connection.execute(
            sa.text(
                """
                INSERT INTO projects (
                    id, workspace_id, name, slug, description, status, created_by
                )
                VALUES (
                    :id, :workspace_id, :name, :slug, :description, 'active', :created_by
                )
                """
            ),
            {
                "id": uuid4(),
                "workspace_id": workspace_id,
                "name": LEGACY_IMPORT_PROJECT_NAME,
                "slug": LEGACY_IMPORT_PROJECT_SLUG,
                "description": LEGACY_IMPORT_PROJECT_DESCRIPTION,
                "created_by": created_by,
            },
        )
        existing.add(workspace_id)

    for table in tables:
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


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SET LOCAL session_replication_role = 'replica'"))
    tables = _nullable_project_tables(connection)
    for table in tables:
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
    connection.execute(
        sa.text("DELETE FROM projects WHERE slug = :slug"),
        {"slug": LEGACY_IMPORT_PROJECT_SLUG},
    )
