"""column policy metadata and append-only data-access audit events

Revision ID: 0050_privacy_audit
Revises: 0049_data_access
Create Date: 2026-09-09

Queryable DatasetColumn policy fields stay NULL until a future classifier or
human sets them. data_access_events is an append-only ledger of access, not a
place to store raw rows. This revision does not add a PII model or privacy UI.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from alembic_frozen.rev_0050_privacy_audit import (
    CK_DATA_ACCESS_EVENTS_ACTOR,
    CK_DATA_ACCESS_EVENTS_COLUMN_BOUNDED,
    CK_DATA_ACCESS_EVENTS_COLUMN_NO_ROWS,
    CK_DATA_ACCESS_EVENTS_COLUMN_OBJECT,
    CK_DATA_ACCESS_EVENTS_COMPLETED,
    CK_DATA_ACCESS_EVENTS_FAILURE,
    CK_DATA_ACCESS_EVENTS_OPERATION,
    CK_DATA_ACCESS_EVENTS_PURPOSE,
    CK_DATA_ACCESS_EVENTS_RESOURCE_BOUNDED,
    CK_DATA_ACCESS_EVENTS_RESOURCE_NO_ROWS,
    CK_DATA_ACCESS_EVENTS_RESOURCE_OBJECT,
    CK_DATA_ACCESS_EVENTS_STATUS,
    CK_DATASET_COLUMNS_CLASSIFICATION_SOURCE,
    CK_DATASET_COLUMNS_LLM_EXPOSURE,
    CK_DATASET_COLUMNS_MODEL_USE,
    CK_DATASET_COLUMNS_SENSITIVITY,
    DATA_ACCESS_EVENTS_APPEND_ONLY_TRIGGER_SQL,
    PREVENT_DATA_ACCESS_EVENT_MUTATION_SQL,
)

revision: str = "0050_privacy_audit"
down_revision: Union[str, Sequence[str], None] = "0049_data_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_set_null_fk(table: str, name: str, child_id: str, parent: str) -> None:
    op.execute(
        sa.text(
            f'ALTER TABLE "{table}" '
            f'ADD CONSTRAINT "{name}" '
            f'FOREIGN KEY ("workspace_id", "{child_id}") '
            f'REFERENCES "{parent}" ("workspace_id", "id") '
            f'ON DELETE SET NULL ("{child_id}")'
        )
    )


def upgrade() -> None:
    op.add_column(
        "dataset_columns",
        sa.Column("sensitivity_class", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "dataset_columns",
        sa.Column("classification_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "dataset_columns",
        sa.Column("model_use_policy", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "dataset_columns",
        sa.Column("llm_exposure_policy", sa.String(length=32), nullable=True),
    )
    op.create_check_constraint(
        "ck_dataset_columns_sensitivity_class",
        "dataset_columns",
        CK_DATASET_COLUMNS_SENSITIVITY,
    )
    op.create_check_constraint(
        "ck_dataset_columns_classification_source",
        "dataset_columns",
        CK_DATASET_COLUMNS_CLASSIFICATION_SOURCE,
    )
    op.create_check_constraint(
        "ck_dataset_columns_model_use_policy",
        "dataset_columns",
        CK_DATASET_COLUMNS_MODEL_USE,
    )
    op.create_check_constraint(
        "ck_dataset_columns_llm_exposure_policy",
        "dataset_columns",
        CK_DATASET_COLUMNS_LLM_EXPOSURE,
    )
    op.create_index(
        "ix_dataset_columns_workspace_sensitivity_class",
        "dataset_columns",
        ["workspace_id", "sensitivity_class"],
        postgresql_where=sa.text("sensitivity_class IS NOT NULL"),
    )
    op.create_index(
        "ix_dataset_columns_workspace_llm_exposure_policy",
        "dataset_columns",
        ["workspace_id", "llm_exposure_policy"],
        postgresql_where=sa.text("llm_exposure_policy IS NOT NULL"),
    )

    op.create_table(
        "data_access_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "data_access_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_accesses.id"),
            nullable=False,
        ),
        sa.Column(
            "execution_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "ingestion_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column(
            "resource_summary",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "column_summary",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("rows_read", sa.Integer(), nullable=True),
        sa.Column("bytes_read", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_data_access_events_workspace_id"
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESS_EVENTS_ACTOR, name="ck_data_access_events_actor_type"
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESS_EVENTS_PURPOSE, name="ck_data_access_events_purpose"
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESS_EVENTS_OPERATION, name="ck_data_access_events_operation"
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESS_EVENTS_STATUS, name="ck_data_access_events_status"
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESS_EVENTS_COMPLETED, name="ck_data_access_events_completed"
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESS_EVENTS_FAILURE, name="ck_data_access_events_failure"
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESS_EVENTS_RESOURCE_OBJECT,
            name="ck_data_access_events_resource_object",
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESS_EVENTS_RESOURCE_BOUNDED,
            name="ck_data_access_events_resource_bounded",
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESS_EVENTS_RESOURCE_NO_ROWS,
            name="ck_data_access_events_resource_no_rows",
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESS_EVENTS_COLUMN_OBJECT,
            name="ck_data_access_events_column_object",
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESS_EVENTS_COLUMN_BOUNDED,
            name="ck_data_access_events_column_bounded",
        ),
        sa.CheckConstraint(
            CK_DATA_ACCESS_EVENTS_COLUMN_NO_ROWS,
            name="ck_data_access_events_column_no_rows",
        ),
    )
    op.create_foreign_key(
        "fk_data_access_events_workspace_data_access",
        "data_access_events",
        "data_accesses",
        ["workspace_id", "data_access_id"],
        ["workspace_id", "id"],
    )
    _add_set_null_fk(
        "data_access_events",
        "fk_data_access_events_workspace_execution_request",
        "execution_request_id",
        "execution_requests",
    )
    _add_set_null_fk(
        "data_access_events",
        "fk_data_access_events_workspace_ingestion_run",
        "ingestion_run_id",
        "ingestion_runs",
    )
    op.create_index(
        "ix_data_access_events_workspace_created_at",
        "data_access_events",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_data_access_events_data_access_id",
        "data_access_events",
        ["data_access_id"],
    )
    op.create_index(
        "ix_data_access_events_workspace_status_started_at",
        "data_access_events",
        ["workspace_id", "status", "started_at"],
    )
    op.execute(sa.text(PREVENT_DATA_ACCESS_EVENT_MUTATION_SQL))
    op.execute(sa.text(DATA_ACCESS_EVENTS_APPEND_ONLY_TRIGGER_SQL))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS data_access_events_append_only ON data_access_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_data_access_event_mutation()")
    op.drop_index(
        "ix_data_access_events_workspace_status_started_at",
        table_name="data_access_events",
    )
    op.drop_index("ix_data_access_events_data_access_id", table_name="data_access_events")
    op.drop_index(
        "ix_data_access_events_workspace_created_at", table_name="data_access_events"
    )
    op.drop_table("data_access_events")
    op.drop_index(
        "ix_dataset_columns_workspace_llm_exposure_policy",
        table_name="dataset_columns",
    )
    op.drop_index(
        "ix_dataset_columns_workspace_sensitivity_class",
        table_name="dataset_columns",
    )
    op.drop_constraint(
        "ck_dataset_columns_llm_exposure_policy",
        "dataset_columns",
        type_="check",
    )
    op.drop_constraint(
        "ck_dataset_columns_model_use_policy",
        "dataset_columns",
        type_="check",
    )
    op.drop_constraint(
        "ck_dataset_columns_classification_source",
        "dataset_columns",
        type_="check",
    )
    op.drop_constraint(
        "ck_dataset_columns_sensitivity_class",
        "dataset_columns",
        type_="check",
    )
    op.drop_column("dataset_columns", "llm_exposure_policy")
    op.drop_column("dataset_columns", "model_use_policy")
    op.drop_column("dataset_columns", "classification_source")
    op.drop_column("dataset_columns", "sensitivity_class")
