"""persist Labs pipeline shells and explicit lineage failures

Revision ID: 0024_labs_runtime_lineage
Revises: 0023_data_model_lineage
Create Date: 2026-09-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_labs_runtime_lineage"
down_revision: Union[str, Sequence[str], None] = "0023_data_model_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A generic Labs PipelineRun is persisted before target/task resolution so
    # ingestion and resolution failures remain first-class technical runs.
    op.alter_column("experiments", "task_id", existing_type=sa.UUID(), nullable=True)
    op.add_column(
        "experiments",
        sa.Column("failure_reason", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("failure_reason", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    # Pipeline shells are runtime data and cannot satisfy the historical
    # non-null task constraint. Refuse an unsafe downgrade until they are gone.
    connection = op.get_bind()
    unresolved = connection.execute(
        sa.text("SELECT count(*) FROM experiments WHERE task_id IS NULL")
    ).scalar_one()
    if unresolved:
        raise RuntimeError(
            "cannot downgrade while unresolved Labs pipeline runs exist"
        )
    op.drop_column("workflow_runs", "failure_reason")
    op.drop_column("experiments", "failure_reason")
    op.alter_column("experiments", "task_id", existing_type=sa.UUID(), nullable=False)
