"""split business technical seats from overall membership cap

Revision ID: 0037_ml_engineer_seats
Revises: 0036_legacy_import_projects
Create Date: 2026-09-05

Business workspaces get ``max_ml_engineer_seats = 5``. Owner/admin/viewer
memberships do not consume that cap. Personal workspaces keep ``max_members = 1``.
Business ``max_members`` system defaults of 5 are removed so that key is not
reused as the technical-seat limit; an operator may still set ``max_members``
as a separate overall safety cap.
"""

from __future__ import annotations

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_ml_engineer_seats"
down_revision: Union[str, Sequence[str], None] = "0036_legacy_import_projects"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    entitlement_table = sa.table(
        "workspace_entitlements",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
        sa.column("entitlement_key", sa.String()),
        sa.column("value_json", postgresql.JSONB()),
        sa.column("source", sa.String()),
    )
    business_ids = [
        row[0]
        for row in connection.execute(
            sa.text("SELECT id FROM workspaces WHERE kind = 'business'")
        )
    ]
    existing_seat_rows = {
        row[0]
        for row in connection.execute(
            sa.text(
                "SELECT workspace_id FROM workspace_entitlements "
                "WHERE entitlement_key = 'max_ml_engineer_seats'"
            )
        )
    }
    rows = [
        {
            "id": uuid4(),
            "workspace_id": workspace_id,
            "entitlement_key": "max_ml_engineer_seats",
            "value_json": 5,
            "source": "system_default",
        }
        for workspace_id in business_ids
        if workspace_id not in existing_seat_rows
    ]
    if rows:
        op.bulk_insert(entitlement_table, rows)
    connection.execute(
        sa.text(
            """
            DELETE FROM workspace_entitlements
            WHERE entitlement_key = 'max_members'
              AND source = 'system_default'
              AND workspace_id IN (
                  SELECT id FROM workspaces WHERE kind = 'business'
              )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE workspace_entitlements AS e
            SET value_json = '1'::jsonb
            FROM workspaces AS w
            WHERE e.workspace_id = w.id
              AND w.kind = 'personal'
              AND e.entitlement_key = 'max_members'
              AND e.source = 'system_default'
            """
        )
    )
    personal_ids = [
        row[0]
        for row in connection.execute(
            sa.text("SELECT id FROM workspaces WHERE kind = 'personal'")
        )
    ]
    existing_member_cap_rows = {
        row[0]
        for row in connection.execute(
            sa.text(
                "SELECT workspace_id FROM workspace_entitlements "
                "WHERE entitlement_key = 'max_members'"
            )
        )
    }
    personal_caps = [
        {
            "id": uuid4(),
            "workspace_id": workspace_id,
            "entitlement_key": "max_members",
            "value_json": 1,
            "source": "system_default",
        }
        for workspace_id in personal_ids
        if workspace_id not in existing_member_cap_rows
    ]
    if personal_caps:
        op.bulk_insert(entitlement_table, personal_caps)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            DELETE FROM workspace_entitlements
            WHERE entitlement_key = 'max_ml_engineer_seats'
              AND source = 'system_default'
            """
        )
    )
    entitlement_table = sa.table(
        "workspace_entitlements",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
        sa.column("entitlement_key", sa.String()),
        sa.column("value_json", postgresql.JSONB()),
        sa.column("source", sa.String()),
    )
    missing = [
        row[0]
        for row in connection.execute(
            sa.text(
                """
                SELECT w.id
                FROM workspaces AS w
                WHERE w.kind = 'business'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM workspace_entitlements AS e
                      WHERE e.workspace_id = w.id
                        AND e.entitlement_key = 'max_members'
                  )
                """
            )
        )
    ]
    restore = [
        {
            "id": uuid4(),
            "workspace_id": workspace_id,
            "entitlement_key": "max_members",
            "value_json": 5,
            "source": "system_default",
        }
        for workspace_id in missing
    ]
    if restore:
        op.bulk_insert(entitlement_table, restore)
