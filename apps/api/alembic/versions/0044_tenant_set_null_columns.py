"""preserve tenant keys in composite SET NULL foreign keys

Revision ID: 0044_tenant_set_null_columns
Revises: 0043_evidence_lock
Create Date: 2026-09-08

PostgreSQL's unqualified composite ``ON DELETE SET NULL`` clears every child
column. These tenant FKs pair a non-null ``workspace_id`` with one nullable
relationship column, so their delete action must name only that relationship.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from alembic_frozen.rev_0042_provenance import PREVENT_CODE_SNAPSHOT_MUTATION_SQL

revision: str = "0044_tenant_set_null_columns"
down_revision: Union[str, Sequence[str], None] = "0043_evidence_lock"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINTS = (
    (
        "workflow_runs",
        "fk_workflow_runs_workspace_source_upload",
        "source_upload_id",
        "client_lab_uploads",
    ),
    (
        "artifacts",
        "fk_artifacts_workspace_pipeline_run",
        "pipeline_run_id",
        "experiments",
    ),
    (
        "code_snapshots",
        "fk_code_snapshots_workspace_pipeline_stage_run",
        "pipeline_stage_run_id",
        "pipeline_stage_runs",
    ),
)


def _replace_constraints(*, column_specific: bool) -> None:
    for table, constraint, child_id, parent in _CONSTRAINTS:
        op.execute(
            sa.text(f'ALTER TABLE "{table}" DROP CONSTRAINT "{constraint}"')
        )
        action = f'ON DELETE SET NULL ("{child_id}")' if column_specific else "ON DELETE SET NULL"
        op.execute(
            sa.text(
                f'ALTER TABLE "{table}" '
                f'ADD CONSTRAINT "{constraint}" '
                f'FOREIGN KEY ("workspace_id", "{child_id}") '
                f'REFERENCES "{parent}" ("workspace_id", "id") {action}'
            )
        )


def upgrade() -> None:
    _replace_constraints(column_specific=True)
    # CodeSnapshot remains immutable to direct SQL. Its one permitted internal
    # update is PostgreSQL clearing this optional stage FK during parent DELETE.
    op.execute(sa.text(PREVENT_CODE_SNAPSHOT_MUTATION_SQL))
    op.execute(sa.text("DROP TRIGGER IF EXISTS code_snapshots_immutable ON code_snapshots"))
    op.execute(
        sa.text(
            """
            CREATE TRIGGER code_snapshots_immutable
            BEFORE UPDATE OR DELETE ON code_snapshots
            FOR EACH ROW EXECUTE FUNCTION prevent_code_snapshot_mutation()
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS code_snapshots_immutable ON code_snapshots"))
    op.execute(
        sa.text(
            """
            CREATE TRIGGER code_snapshots_immutable
            BEFORE UPDATE OR DELETE ON code_snapshots
            FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()
            """
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS prevent_code_snapshot_mutation()"))
    _replace_constraints(column_specific=False)
