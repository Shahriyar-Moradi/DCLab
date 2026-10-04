"""Batch predictions and the dataset upload purpose.

Revision ID: 0069_batch_predictions
Revises: 0068_token_provenance
Create Date: 2026-10-04

P4.9-A (score new data with a model version). Expand-only:

- ``datasets.purpose varchar(16) NOT NULL DEFAULT 'training'`` with
  ``ck_datasets_purpose`` (training | scoring). A constant default is
  metadata-only in PostgreSQL 11+ (no rewrite, no row trigger fires); every
  existing row is a training upload. The CHECK is added ``NOT VALID`` then
  validated.
- ``ck_execution_requests_operation`` widens to a superset that adds
  ``model_batch_predict`` (the scoring request; NOT VALID then VALIDATE).
- ``batch_predictions``: one scoring run. Composite tenant FKs ``(workspace_id,
  x_id) → parent(workspace_id, id)`` with NO ACTION (projects, model versions,
  datasets, execution requests, ml jobs, artifacts and service tokens are never
  deleted under live evidence); ``workspace_id`` cascades; ``requested_by_user_id``
  is SET NULL like ``execution_requests``. ``model_release_id`` has no FK until
  Phase 7 adds releases. CHECKs make ``completed`` imply an output artifact and
  ``rows_out = rows_in``, and ``failed`` imply an ``error_code``.
- Trigger ``batch_predictions_terminal_immutable`` freezes completed/failed rows
  (reuses ``prevent_canonical_column_mutation`` from 0035; only
  ``requested_by_user_id`` may still change, to NULL by its FK).

Locking: Alembic runs this in ONE transaction; the CHECK swaps hold
``execution_requests``'/``datasets``' ACCESS EXCLUSIVE lock until commit. Both are
small at MVP; ``lock_timeout`` 5s fails fast instead of queueing behind workers.

Downgrade refuses while any batch prediction, scoring dataset or
``model_batch_predict`` request exists (no pre-0069 representation: a scoring
dataset would silently become a training source). Repair forward.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0069_batch_predictions"
down_revision: Union[str, Sequence[str], None] = "0068_token_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_OPERATION = "operation IN ('model_build')"
_NEW_OPERATION = "operation IN ('model_build', 'model_batch_predict')"
_PURPOSE = "purpose IN ('training', 'scoring')"

_TERMINAL_TRIGGER_SQL = """
CREATE TRIGGER batch_predictions_terminal_immutable
BEFORE UPDATE ON batch_predictions
FOR EACH ROW
WHEN (OLD.status IN ('completed', 'failed'))
EXECUTE FUNCTION prevent_canonical_column_mutation(
    'id,workspace_id,project_id,model_version_id,model_release_id,input_dataset_id,execution_request_id,ml_job_id,initiated_by_service_token_id,status,output_format,rows_in,rows_out,contract_check,decision_threshold,output_artifact_id,error_code,error_message,created_at,started_at,completed_at'
)
"""

_DOWNGRADE_PRECHECK_SQL = """
LOCK TABLE batch_predictions, datasets, execution_requests IN SHARE MODE;
DO $$
DECLARE
    referencing bigint;
BEGIN
    SELECT (SELECT count(*) FROM batch_predictions)
         + (SELECT count(*) FROM datasets WHERE purpose <> 'training')
         + (SELECT count(*) FROM execution_requests WHERE operation = 'model_batch_predict')
      INTO referencing;
    IF referencing > 0 THEN
        RAISE EXCEPTION '0069 downgrade refused: % batch prediction rows exist; repair forward', referencing;
    END IF;
END
$$
"""


def _fk(name: str, column: str, parent: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id", column], [f"{parent}.workspace_id", f"{parent}.id"], name=name
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "datasets",
        sa.Column("purpose", sa.String(16), nullable=False, server_default="training"),
    )
    op.execute(f"ALTER TABLE datasets ADD CONSTRAINT ck_datasets_purpose CHECK ({_PURPOSE}) NOT VALID")
    op.execute("ALTER TABLE datasets VALIDATE CONSTRAINT ck_datasets_purpose")
    op.execute("ALTER TABLE execution_requests DROP CONSTRAINT ck_execution_requests_operation")
    op.execute(
        "ALTER TABLE execution_requests ADD CONSTRAINT ck_execution_requests_operation "
        f"CHECK ({_NEW_OPERATION}) NOT VALID"
    )
    op.execute("ALTER TABLE execution_requests VALIDATE CONSTRAINT ck_execution_requests_operation")

    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "batch_predictions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, nullable=True),
        sa.Column("model_version_id", uuid, nullable=False),
        sa.Column("model_release_id", uuid, nullable=True),
        sa.Column("input_dataset_id", uuid, nullable=False),
        sa.Column("execution_request_id", uuid, nullable=True),
        sa.Column("ml_job_id", uuid, nullable=True),
        sa.Column("requested_by_user_id", uuid, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("initiated_by_service_token_id", uuid, nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("output_format", sa.String(16), nullable=False),
        sa.Column("rows_in", sa.Integer(), nullable=True),
        sa.Column("rows_out", sa.Integer(), nullable=True),
        sa.Column("contract_check", postgresql.JSONB(), nullable=True),
        sa.Column("decision_threshold", sa.Float(), nullable=True),
        sa.Column("output_artifact_id", uuid, nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "id", name="uq_batch_predictions_workspace_id"),
        _fk("fk_batch_predictions_workspace_project", "project_id", "projects"),
        _fk("fk_batch_predictions_workspace_model_version", "model_version_id", "model_versions"),
        _fk("fk_batch_predictions_workspace_input_dataset", "input_dataset_id", "datasets"),
        _fk("fk_batch_predictions_workspace_execution_request", "execution_request_id", "execution_requests"),
        _fk("fk_batch_predictions_workspace_ml_job", "ml_job_id", "ml_jobs"),
        _fk("fk_batch_predictions_workspace_output_artifact", "output_artifact_id", "artifacts"),
        _fk("fk_batch_predictions_service_token", "initiated_by_service_token_id", "service_tokens"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')", name="ck_batch_predictions_status"
        ),
        sa.CheckConstraint("output_format IN ('csv', 'parquet')", name="ck_batch_predictions_output_format"),
        sa.CheckConstraint(
            "(rows_in IS NULL OR rows_in >= 0) AND (rows_out IS NULL OR rows_out >= 0)",
            name="ck_batch_predictions_rows",
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR (output_artifact_id IS NOT NULL AND rows_in IS NOT NULL "
            "AND rows_out = rows_in AND completed_at IS NOT NULL)",
            name="ck_batch_predictions_completed",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (error_code IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_batch_predictions_failed",
        ),
        sa.CheckConstraint(
            "decision_threshold IS NULL OR (decision_threshold >= 0 AND decision_threshold <= 1)",
            name="ck_batch_predictions_threshold",
        ),
        sa.CheckConstraint(
            "contract_check IS NULL OR jsonb_typeof(contract_check) = 'object'",
            name="ck_batch_predictions_contract_object",
        ),
        sa.CheckConstraint(
            "contract_check IS NULL OR octet_length(CAST(contract_check AS TEXT)) <= 65536",
            name="ck_batch_predictions_contract_bounded",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_batch_predictions_error_code",
        ),
    )
    op.create_index(
        "ix_batch_predictions_workspace_model_version_created_at",
        "batch_predictions",
        ["workspace_id", "model_version_id", "created_at"],
    )
    op.execute(_TERMINAL_TRIGGER_SQL)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(_DOWNGRADE_PRECHECK_SQL)
    op.execute("DROP TRIGGER IF EXISTS batch_predictions_terminal_immutable ON batch_predictions")
    op.drop_index("ix_batch_predictions_workspace_model_version_created_at", table_name="batch_predictions")
    op.drop_table("batch_predictions")
    op.execute("ALTER TABLE execution_requests DROP CONSTRAINT ck_execution_requests_operation")
    op.execute(
        f"ALTER TABLE execution_requests ADD CONSTRAINT ck_execution_requests_operation CHECK ({_OLD_OPERATION})"
    )
    op.drop_constraint("ck_datasets_purpose", "datasets", type_="check")
    op.drop_column("datasets", "purpose")
