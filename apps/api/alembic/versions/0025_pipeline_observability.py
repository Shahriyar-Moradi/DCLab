"""add pipeline event stream and generic LLM observability ledger

Revision ID: 0025_pipeline_observability
Revises: 0024_labs_runtime_lineage
Create Date: 2026-09-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_pipeline_observability"
down_revision: Union[str, Sequence[str], None] = "0024_labs_runtime_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("input_evidence_digest", sa.String(64), nullable=False),
        sa.Column("redaction_summary", postgresql.JSONB(), nullable=False),
        sa.Column("llm_used", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("validator_verdict", sa.String(1024), nullable=False),
        sa.Column("safe_output", postgresql.JSONB(), nullable=True),
        sa.Column("final_decision", postgresql.JSONB(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('semantic_target', 'semantic_missing_value', "
            "'semantic_column_type', 'pipeline_audit_routine', "
            "'pipeline_audit_deep')",
            name="ck_llm_invocations_purpose",
        ),
    )
    for name, columns in (
        ("ix_llm_invocations_workspace_id", ["workspace_id"]),
        ("ix_llm_invocations_workflow_run_id", ["workflow_run_id"]),
        ("ix_llm_invocations_experiment_id", ["experiment_id"]),
        ("ix_llm_invocations_purpose", ["purpose"]),
        ("ix_llm_invocations_status", ["status"]),
        ("ix_llm_invocations_created_at", ["created_at"]),
        ("ix_llm_invocations_input_evidence_digest", ["input_evidence_digest"]),
    ):
        op.create_index(name, "llm_invocations", columns)

    op.add_column(
        "lab_decision_records",
        sa.Column("llm_invocation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_lab_decision_records_llm_invocation_id",
        "lab_decision_records",
        "llm_invocations",
        ["llm_invocation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_lab_decision_records_llm_invocation_id",
        "lab_decision_records",
        ["llm_invocation_id"],
        unique=True,
    )
    op.add_column(
        "ml_run_verifications",
        sa.Column("llm_invocation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_ml_run_verifications_llm_invocation_id",
        "ml_run_verifications",
        "llm_invocations",
        ["llm_invocation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ml_run_verifications_llm_invocation_id",
        "ml_run_verifications",
        ["llm_invocation_id"],
        unique=True,
    )

    op.create_table(
        "ml_run_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(80), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "experiment_id",
            "sequence",
            name="uq_ml_run_events_experiment_sequence",
        ),
    )
    for name, columns in (
        ("ix_ml_run_events_workspace_id", ["workspace_id"]),
        ("ix_ml_run_events_workflow_run_id", ["workflow_run_id"]),
        ("ix_ml_run_events_experiment_id", ["experiment_id"]),
        ("ix_ml_run_events_stage", ["stage"]),
        ("ix_ml_run_events_timestamp", ["timestamp"]),
    ):
        op.create_index(name, "ml_run_events", columns)


def downgrade() -> None:
    for table, constraint, index in (
        (
            "ml_run_verifications",
            "fk_ml_run_verifications_llm_invocation_id",
            "ix_ml_run_verifications_llm_invocation_id",
        ),
        (
            "lab_decision_records",
            "fk_lab_decision_records_llm_invocation_id",
            "ix_lab_decision_records_llm_invocation_id",
        ),
    ):
        op.drop_index(index, table_name=table)
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.drop_column(table, "llm_invocation_id")
    for name in (
        "ix_ml_run_events_timestamp",
        "ix_ml_run_events_stage",
        "ix_ml_run_events_experiment_id",
        "ix_ml_run_events_workflow_run_id",
        "ix_ml_run_events_workspace_id",
    ):
        op.drop_index(name, table_name="ml_run_events")
    op.drop_table("ml_run_events")
    for name in (
        "ix_llm_invocations_input_evidence_digest",
        "ix_llm_invocations_created_at",
        "ix_llm_invocations_status",
        "ix_llm_invocations_purpose",
        "ix_llm_invocations_experiment_id",
        "ix_llm_invocations_workflow_run_id",
        "ix_llm_invocations_workspace_id",
    ):
        op.drop_index(name, table_name="llm_invocations")
    op.drop_table("llm_invocations")
