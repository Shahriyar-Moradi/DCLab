"""enforce append-only pipeline events in PostgreSQL

Revision ID: 0026_ml_run_events_append_only
Revises: 0025_pipeline_observability
Create Date: 2026-09-02
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0026_ml_run_events_append_only"
down_revision: Union[str, Sequence[str], None] = "0025_pipeline_observability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_ml_run_event_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'ml_run_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER ml_run_events_append_only
        BEFORE UPDATE OR DELETE ON ml_run_events
        FOR EACH ROW EXECUTE FUNCTION prevent_ml_run_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ml_run_events_append_only ON ml_run_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_ml_run_event_mutation()")
