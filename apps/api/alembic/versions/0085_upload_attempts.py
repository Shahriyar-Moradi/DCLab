"""Upload log: one metadata row per upload attempt, accepted or rejected (90-day retention).

Revision ID: 0085_upload_attempts
Revises: 0084_run_attempts
Create Date: 2026-10-16

D3a (ADR 0005 amendment). Expand-only: one new table, no change to existing ones.

- ``upload_attempts``: workspace, project (NULL only for a legacy Labs rejection before the
  workspace has a Labs project), actor kind (person | connected_tool) and user, the service token
  if any, the file name (capped at 200, no control characters), size in bytes, outcome (accepted |
  rejected), a closed ``reason_code`` for rejections, the dataset an accepted upload created,
  the upload path (v1_api | labs) and ``created_at``. Never file content or row values.
  Composite tenant FKs to projects, service tokens and datasets (NO ACTION); workspace CASCADE.
- Append-only and retention-aware with the 0071 functions: ``upload_attempts_append_only``
  refuses UPDATE and allows DELETE only in a transaction ``retention_guard`` stamped (one
  workspace, rows older than the horizon, horizon >= 30 days; the worker purges after 90 days) or
  a workspace deletion; ``upload_attempts_created_at_now`` forces ``created_at = now()``.

No backfill: rejected uploads before this revision were not kept (ADR 0005 §8).

Locking: new table only (the FKs take brief SHARE ROW EXCLUSIVE locks on the parents);
``lock_timeout`` 5s. Downgrade drops the table (the 90-day log is lost; nothing references it).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0085_upload_attempts"
down_revision: Union[str, Sequence[str], None] = "0084_run_attempts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Identical literals in app/domain/data_goal.py (and the triggers in app/db/integrity.py).
_REASONS = (
    "'too_large', 'storage_limit', 'empty_file', 'unsafe_file_name', 'unsupported_type', 'content_mismatch', "
    "'unreadable_sheets', 'unsafe_content', 'unreadable_file'"
)
_CHECKS = {
    "ck_upload_attempts_outcome": "outcome IN ('accepted', 'rejected')",
    "ck_upload_attempts_reason": (
        "(outcome = 'accepted' AND reason_code IS NULL AND dataset_id IS NOT NULL) OR (outcome = 'rejected' "
        f"AND dataset_id IS NULL AND reason_code IN ({_REASONS}))"
    ),
    "ck_upload_attempts_actor": (
        "(actor_kind = 'person' AND service_token_id IS NULL) "
        "OR (actor_kind = 'connected_tool' AND service_token_id IS NOT NULL)"
    ),
    "ck_upload_attempts_source": "source IN ('v1_api', 'labs')",
    "ck_upload_attempts_file_name": "char_length(file_name) BETWEEN 1 AND 200 AND file_name !~ '[[:cntrl:]]'",
    "ck_upload_attempts_size": "size_bytes IS NULL OR size_bytes >= 0",
}
_TRIGGERS = (
    "CREATE TRIGGER upload_attempts_append_only BEFORE UPDATE OR DELETE ON upload_attempts "
    "FOR EACH ROW EXECUTE FUNCTION prevent_mutation_except_retention()",
    "CREATE TRIGGER upload_attempts_created_at_now BEFORE INSERT ON upload_attempts "
    "FOR EACH ROW EXECUTE FUNCTION force_created_at_now()",
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "upload_attempts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, nullable=True),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("actor_user_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("service_token_id", uuid, nullable=True),
        sa.Column("file_name", sa.String(length=200), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=True),
        sa.Column("dataset_id", uuid, nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_upload_attempts_workspace_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
            name="fk_upload_attempts_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "service_token_id"], ["service_tokens.workspace_id", "service_tokens.id"],
            name="fk_upload_attempts_service_token",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "dataset_id"], ["datasets.workspace_id", "datasets.id"],
            name="fk_upload_attempts_workspace_dataset",
        ),
        *(sa.CheckConstraint(sql, name=name) for name, sql in _CHECKS.items()),
    )
    op.create_index(
        "ix_upload_attempts_project_created_at", "upload_attempts",
        ["workspace_id", "project_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index("ix_upload_attempts_created_at", "upload_attempts", ["created_at"])
    op.create_index(
        "ix_upload_attempts_service_token_id", "upload_attempts", ["workspace_id", "service_token_id"],
        postgresql_where=sa.text("service_token_id IS NOT NULL"),
    )
    op.create_index(
        "ix_upload_attempts_dataset_id", "upload_attempts", ["workspace_id", "dataset_id"],
        postgresql_where=sa.text("dataset_id IS NOT NULL"),
    )
    op.create_index("ix_upload_attempts_actor_user_id", "upload_attempts", ["actor_user_id"])
    for statement in _TRIGGERS:
        op.execute(sa.text(statement))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for name in ("upload_attempts_created_at_now", "upload_attempts_append_only"):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {name} ON upload_attempts"))
    op.drop_table("upload_attempts")
