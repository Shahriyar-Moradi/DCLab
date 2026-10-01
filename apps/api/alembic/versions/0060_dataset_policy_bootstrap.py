"""Versioned dataset policy defaults and nullable column policy extensions.

Revision ID: 0060_dataset_policy_bootstrap
Revises: 0059_auth_session_constraints

Existing NULL column labels remain NULL. No historical data is classified or
implicitly approved. Dataset rows remain immutable; defaults are append-only.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060_dataset_policy_bootstrap"
down_revision: Union[str, Sequence[str], None] = "0059_auth_session_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dataset_columns", sa.Column("retention_class", sa.String(32), nullable=True))
    op.add_column("dataset_columns", sa.Column("residency_class", sa.String(32), nullable=True))
    op.add_column("dataset_columns", sa.Column("classification_confidence", sa.Float(), nullable=True))
    op.add_column("dataset_columns", sa.Column("policy_schema_version", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_dataset_columns_retention_class", "dataset_columns",
        "retention_class IS NULL OR retention_class IN ('unknown', 'short', 'standard', 'extended')",
    )
    op.create_check_constraint(
        "ck_dataset_columns_residency_class", "dataset_columns",
        "residency_class IS NULL OR residency_class IN ('unknown', 'home_cloud_only', 'home_region_only')",
    )
    op.create_check_constraint(
        "ck_dataset_columns_classification_confidence", "dataset_columns",
        "classification_confidence IS NULL OR (classification_confidence >= 0 AND classification_confidence <= 1)",
    )
    op.create_check_constraint(
        "ck_dataset_columns_policy_schema_version", "dataset_columns",
        "policy_schema_version IS NULL OR policy_schema_version = 1",
    )

    op.create_table(
        "dataset_policy_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("policy_schema_version", sa.Integer(), nullable=False),
        sa.Column("sensitivity_class", sa.String(32), nullable=False),
        sa.Column("llm_exposure_policy", sa.String(32), nullable=False),
        sa.Column("retention_class", sa.String(32), nullable=False),
        sa.Column("residency_class", sa.String(32), nullable=False),
        sa.Column("classification_source", sa.String(32), nullable=False),
        sa.Column("classification_confidence", sa.Float(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "id", name="uq_dataset_policy_revisions_workspace_id"),
        sa.UniqueConstraint("dataset_id", "revision", name="uq_dataset_policy_revisions_dataset_revision"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "dataset_id"], ["datasets.workspace_id", "datasets.id"],
            name="fk_dataset_policy_revisions_workspace_dataset", ondelete="CASCADE",
        ),
        sa.CheckConstraint("revision > 0", name="ck_dataset_policy_revisions_revision"),
        sa.CheckConstraint("policy_schema_version = 1", name="ck_dataset_policy_revisions_schema_version"),
        sa.CheckConstraint("classification_confidence >= 0 AND classification_confidence <= 1", name="ck_dataset_policy_revisions_confidence"),
        sa.CheckConstraint("sensitivity_class IN ('public', 'internal', 'identifier', 'pii', 'sensitive', 'restricted')", name="ck_dataset_policy_revisions_sensitivity"),
        sa.CheckConstraint("llm_exposure_policy IN ('allow', 'deny', 'aggregate_only', 'metadata_only')", name="ck_dataset_policy_revisions_llm_exposure"),
        sa.CheckConstraint("retention_class IN ('unknown', 'short', 'standard', 'extended')", name="ck_dataset_policy_revisions_retention"),
        sa.CheckConstraint("residency_class IN ('unknown', 'home_cloud_only', 'home_region_only')", name="ck_dataset_policy_revisions_residency"),
        sa.CheckConstraint("classification_source IN ('manual', 'system', 'policy', 'import')", name="ck_dataset_policy_revisions_source"),
    )
    op.create_index(
        "ix_dataset_policy_revisions_workspace_dataset_revision",
        "dataset_policy_revisions", ["workspace_id", "dataset_id", sa.text("revision DESC")],
    )
    op.execute(
        "CREATE TRIGGER dataset_policy_revisions_immutable "
        "BEFORE UPDATE OR DELETE ON dataset_policy_revisions "
        "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS dataset_policy_revisions_immutable ON dataset_policy_revisions")
    op.drop_index("ix_dataset_policy_revisions_workspace_dataset_revision", table_name="dataset_policy_revisions")
    op.drop_table("dataset_policy_revisions")
    for name in (
        "ck_dataset_columns_policy_schema_version",
        "ck_dataset_columns_classification_confidence",
        "ck_dataset_columns_residency_class",
        "ck_dataset_columns_retention_class",
    ):
        op.drop_constraint(name, "dataset_columns", type_="check")
    for name in (
        "policy_schema_version", "classification_confidence", "residency_class", "retention_class"
    ):
        op.drop_column("dataset_columns", name)
