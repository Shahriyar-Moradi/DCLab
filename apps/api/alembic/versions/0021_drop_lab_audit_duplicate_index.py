"""remove redundant client-lab audit lookup index

Revision ID: 0021_lab_audit_index
Revises: 0020_ml_verifications
Create Date: 2026-09-02

``client_lab_run_id`` is a one-to-one foreign key. The historical migration
created both its unique constraint (which owns a unique index in PostgreSQL)
and an additional non-unique index. The latter is redundant and does not match
the SQLAlchemy metadata.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0021_lab_audit_index"
down_revision: Union[str, Sequence[str], None] = "0020_ml_verifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(
        "ix_client_lab_run_audits_client_lab_run_id",
        table_name="client_lab_run_audits",
    )


def downgrade() -> None:
    op.create_index(
        "ix_client_lab_run_audits_client_lab_run_id",
        "client_lab_run_audits",
        ["client_lab_run_id"],
    )
