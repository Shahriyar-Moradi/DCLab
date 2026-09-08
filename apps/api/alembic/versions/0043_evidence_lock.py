"""freeze a PipelineRun's canonical scientific evidence once it is complete

Revision ID: 0043_evidence_lock
Revises: 0042_provenance_immutability
Create Date: 2026-09-06

0035 and 0042 froze canonical version rows and reproducibility records, but the
scientific child rows of a finished run — CV folds, hyperparameters, evaluation
metrics, the winner decision, final-holdout evaluations and predictions,
features — were still open to direct SQL.

This revision adds ``experiments.scientific_evidence_locked_at`` and the
triggers that reject INSERT/UPDATE/DELETE on that run's evidence once the stamp
is set. ``app.services.evidence_lock_service`` stamps it only after every piece
of evidence exists.

Append-only post-run evidence is untouched: ``ml_run_events``,
``ml_run_verifications``, ``llm_invocations``, ``artifacts``, and
``experiments.status``/``result``. The deterministic verification, report, and
advisory audit PipelineStageRuns opened after the lock also stay writable.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.evidence_lock import (
    evidence_lock_downgrade_statements,
    evidence_lock_upgrade_statements,
)

revision: str = "0043_evidence_lock"
down_revision: Union[str, Sequence[str], None] = "0042_provenance_immutability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("scientific_evidence_locked_at", sa.DateTime(timezone=True), nullable=True),
    )
    # No index: every resolver reaches the stamp through experiments' primary key.
    for statement in evidence_lock_upgrade_statements():
        op.execute(sa.text(statement))
    # Historical runs are frozen only when the database can prove the same
    # complete evidence required for a new lock. Partial legacy runs stay open.
    op.execute(
        sa.text(
            """
            UPDATE experiments
            SET scientific_evidence_locked_at = statement_timestamp()
            WHERE scientific_evidence_locked_at IS NULL
              AND pipeline_run_scientific_evidence_complete(id)
            """
        )
    )


def downgrade() -> None:
    for statement in evidence_lock_downgrade_statements():
        op.execute(sa.text(statement))
    op.drop_column("experiments", "scientific_evidence_locked_at")
