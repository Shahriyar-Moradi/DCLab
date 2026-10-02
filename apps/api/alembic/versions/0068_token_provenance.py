"""Service-token provenance on execution requests and problem specs.

Revision ID: 0068_token_provenance
Revises: 0067_service_tokens
Create Date: 2026-10-02

P3.4-A (security review). Expand-only:

- ``execution_requests.initiated_by_service_token_id``: the service token (an
  agent) whose ``/v1`` call queued the run. The ref bootstrap reads it so a
  token-initiated run never initializes project refs on its own: it leaves a
  proposal a human accepts.
- ``problem_specs.created_by_service_token_id``: agent provenance of a spec.

Both nullable (NULL = a human/session or a pre-0068 row; no backfill is possible
or needed), composite FK ``(workspace_id, <column>) → service_tokens(workspace_id,
id)`` with NO ACTION like ``fk_pdr_actor_service_token`` (0067): provenance is
kept. Consequence (deliberate, same as 0067): once a token initiated a request or
wrote a spec, deleting that token fails, and so does deleting its creator (the
creator FK cascades to the token) until the rows are repaired forward; users are
deactivated, not deleted, in normal operation.

Partial indexes back the FK checks, plus ``(workspace_id, pipeline_run_id) WHERE
initiated_by_service_token_id IS NOT NULL`` for the ref-bootstrap lookup.

Locking: ``ADD CONSTRAINT ... NOT VALID`` then ``VALIDATE`` run in this one
transaction, so they give no lock-duration benefit over a plain ADD; acceptable
because both tables are small at MVP and every row is NULL. Plain CREATE INDEX
(not CONCURRENTLY) for the same reason. ``lock_timeout`` 5s fails fast.

Downgrade locks both tables (SHARE) and refuses while any row names a service
token (repair forward).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0068_token_provenance"
down_revision: Union[str, Sequence[str], None] = "0067_service_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, fk name, index name)
_COLUMNS = (
    ("execution_requests", "initiated_by_service_token_id", "fk_execution_requests_service_token",
     "ix_execution_requests_service_token"),
    ("problem_specs", "created_by_service_token_id", "fk_problem_specs_service_token",
     "ix_problem_specs_service_token"),
)

_BOOTSTRAP_INDEX = "ix_execution_requests_token_pipeline_run"
_DOWNGRADE_PRECHECK_SQL = """
LOCK TABLE execution_requests, problem_specs IN SHARE MODE;
DO $$
DECLARE
    referencing bigint;
BEGIN
    SELECT (SELECT count(*) FROM execution_requests WHERE initiated_by_service_token_id IS NOT NULL)
         + (SELECT count(*) FROM problem_specs WHERE created_by_service_token_id IS NOT NULL)
      INTO referencing;
    IF referencing > 0 THEN
        RAISE EXCEPTION '0068 downgrade refused: % rows reference service tokens; repair forward', referencing;
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table, column, fk, index in _COLUMNS:
        op.add_column(table, sa.Column(column, postgresql.UUID(as_uuid=True), nullable=True))
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {fk} FOREIGN KEY (workspace_id, {column}) "
            "REFERENCES service_tokens (workspace_id, id) NOT VALID"
        )
        op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {fk}")
        op.create_index(index, table, ["workspace_id", column], postgresql_where=sa.text(f"{column} IS NOT NULL"))
    op.create_index(
        _BOOTSTRAP_INDEX, "execution_requests", ["workspace_id", "pipeline_run_id"],
        postgresql_where=sa.text("initiated_by_service_token_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(_DOWNGRADE_PRECHECK_SQL)
    op.drop_index(_BOOTSTRAP_INDEX, table_name="execution_requests")
    for table, column, fk, index in reversed(_COLUMNS):
        op.drop_index(index, table_name=table)
        op.drop_constraint(fk, table, type_="foreignkey")
        op.drop_column(table, column)
