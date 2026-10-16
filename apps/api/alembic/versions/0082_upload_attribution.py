"""Upload attribution: which service token (connected tool) uploaded a dataset.

Revision ID: 0082_upload_attribution
Revises: 0081_usage_records
Create Date: 2026-10-16

Founder decision 2026-10-16 ("add upload attribution before real customers use schedules").
Expand-only:

- ``datasets.uploaded_by_service_token_id``: the service token whose ``POST /v1/datasets`` call
  created the DatasetVersion. NULL = a signed-in person uploaded it, or the row predates 0082 (no
  backfill is possible: the only earlier trace is the idempotency key's principal, which is not a
  dataset fact). Weekly scheduled scoring scores only rows where it is NULL.

One nullable column, no ``upload_source`` enum: the token id is the fact, and a second column
could disagree with it. ``datasets`` rows are immutable (``datasets_immutable`` trigger, UPDATE and
DELETE refused), so the column is written in the INSERT only and never updated.

Composite FK ``(workspace_id, uploaded_by_service_token_id) -> service_tokens (workspace_id, id)``
with NO ACTION, the 0068 / ``fk_batch_predictions_service_token`` pattern: a token of another
workspace cannot be named, and provenance is kept (deleting a token that uploaded a dataset fails,
and so does deleting its creator, whose FK cascades to the token, until the rows are repaired
forward; tokens are revoked and users deactivated, not deleted, in normal operation). No ON DELETE SET NULL: it would be refused
by the immutability trigger anyway. A partial index backs the FK check.

Locking: ``ADD CONSTRAINT ... NOT VALID`` then ``VALIDATE`` in this one transaction (every row is
NULL, so validation reads but finds nothing to check); plain CREATE INDEX (not CONCURRENTLY) on a
small table; ``lock_timeout`` 5s fails fast.

Downgrade locks ``datasets`` (SHARE) and refuses while any row names a service token (repair
forward), like 0068. An emergency rollback of the application does not need this downgrade: the
column is nullable and older code never names it. While older code runs, token uploads land with
NULL here and would later count as a person's upload (weekly scoring could pick them).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0082_upload_attribution"
down_revision: Union[str, Sequence[str], None] = "0081_usage_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMN = "uploaded_by_service_token_id"
_FK = "fk_datasets_service_token"
_INDEX = "ix_datasets_service_token"
_DOWNGRADE_PRECHECK_SQL = """
LOCK TABLE datasets IN SHARE MODE;
DO $$
DECLARE
    referencing bigint;
BEGIN
    SELECT count(*) FROM datasets WHERE uploaded_by_service_token_id IS NOT NULL INTO referencing;
    IF referencing > 0 THEN
        RAISE EXCEPTION '0082 downgrade refused: % datasets name a service token; repair forward', referencing;
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("datasets", sa.Column(_COLUMN, postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(
        f"ALTER TABLE datasets ADD CONSTRAINT {_FK} FOREIGN KEY (workspace_id, {_COLUMN}) "
        "REFERENCES service_tokens (workspace_id, id) NOT VALID"
    )
    op.execute(f"ALTER TABLE datasets VALIDATE CONSTRAINT {_FK}")
    op.create_index(_INDEX, "datasets", ["workspace_id", _COLUMN], postgresql_where=sa.text(f"{_COLUMN} IS NOT NULL"))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(_DOWNGRADE_PRECHECK_SQL)
    op.drop_index(_INDEX, table_name="datasets")
    op.drop_constraint(_FK, "datasets", type_="foreignkey")
    op.drop_column("datasets", _COLUMN)
