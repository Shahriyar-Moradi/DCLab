"""Add deny-by-default publication lineage for uploaded datasets.

Revision ID: 0061_ingestion_publication
Revises: 0060_dataset_policy_bootstrap

No historical run is assumed scanned or published.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0061_ingestion_publication"
down_revision: Union[str, Sequence[str], None] = "0060_dataset_policy_bootstrap"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ingestion_runs", sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("ingestion_runs", sa.Column("publication_state", sa.String(24), nullable=False, server_default="received"))
    op.add_column("ingestion_runs", sa.Column("publication_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_runs", sa.Column("publication_digest", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_ingestion_runs_workspace_artifact", "ingestion_runs", "artifacts",
        ["workspace_id", "artifact_id"], ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_ingestion_runs_artifact_id", "ingestion_runs", "artifacts",
        ["artifact_id"], ["id"],
    )
    op.create_unique_constraint("uq_ingestion_runs_artifact_id", "ingestion_runs", ["artifact_id"])
    op.create_check_constraint(
        "ck_ingestion_runs_publication_state", "ingestion_runs",
        "publication_state IN ('received', 'quarantined', 'scanned', 'classified', 'publishable', 'published', 'rejected', 'expired')",
    )
    op.create_check_constraint("ck_ingestion_runs_publication_version", "ingestion_runs", "publication_version >= 0")
    op.create_check_constraint(
        "ck_ingestion_runs_publication_digest", "ingestion_runs",
        "publication_digest IS NULL OR publication_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_table(
        "ingestion_publication_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ingestion_runs.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(24), nullable=False),
        sa.Column("to_state", sa.String(24), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("policy_schema_version", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["workspace_id", "ingestion_run_id"], ["ingestion_runs.workspace_id", "ingestion_runs.id"],
            name="fk_ingestion_publication_events_workspace_run",
        ),
        sa.UniqueConstraint("ingestion_run_id", "version", name="uq_ingestion_publication_events_run_version"),
        sa.CheckConstraint("version > 0", name="ck_ingestion_publication_events_version"),
        sa.CheckConstraint("actor_type IN ('user', 'system', 'operator')", name="ck_ingestion_publication_events_actor"),
        sa.CheckConstraint(
            "(actor_type = 'system' AND actor_user_id IS NULL) OR (actor_type IN ('user', 'operator') AND actor_user_id IS NOT NULL)",
            name="ck_ingestion_publication_events_actor_identity",
        ),
        sa.CheckConstraint("policy_schema_version > 0", name="ck_ingestion_publication_events_policy_version"),
        sa.CheckConstraint("content_digest ~ '^[0-9a-f]{64}$'", name="ck_ingestion_publication_events_digest"),
    )
    op.create_index(
        "ix_ingestion_publication_events_workspace_run", "ingestion_publication_events",
        ["workspace_id", "ingestion_run_id"],
    )
    op.execute(
        "CREATE TRIGGER ingestion_publication_events_immutable "
        "BEFORE UPDATE OR DELETE ON ingestion_publication_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ingestion_publication_events_immutable ON ingestion_publication_events")
    op.drop_index("ix_ingestion_publication_events_workspace_run", table_name="ingestion_publication_events")
    op.drop_table("ingestion_publication_events")
    for name in (
        "ck_ingestion_runs_publication_digest", "ck_ingestion_runs_publication_version",
        "ck_ingestion_runs_publication_state",
    ):
        op.drop_constraint(name, "ingestion_runs", type_="check")
    op.drop_constraint("uq_ingestion_runs_artifact_id", "ingestion_runs", type_="unique")
    op.drop_constraint("fk_ingestion_runs_workspace_artifact", "ingestion_runs", type_="foreignkey")
    op.drop_constraint("fk_ingestion_runs_artifact_id", "ingestion_runs", type_="foreignkey")
    for name in ("publication_digest", "publication_version", "publication_state", "artifact_id"):
        op.drop_column("ingestion_runs", name)
