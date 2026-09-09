"""lock canonical reproducibility records at the PostgreSQL level

Revision ID: 0042_provenance_immutability
Revises: 0041_artifact_pipeline_run
Create Date: 2026-09-06

0035 froze the canonical hierarchy. The reproducibility records added by 0034,
0039, and 0041 were still rewritable by direct SQL:

* ``pipeline_scientific_plans`` carries ``locked_at`` but had no trigger.
* ``runtime_environments`` is shared provenance keyed by ``environment_digest``.
* ``code_snapshots`` is canonical model provenance.
* ``artifacts`` identity (provider, bucket/key, digest, size) could be rewritten
  under records that already reference it.

Artifact DELETE stays governed by the existing foreign keys for
retention/offboarding, and the columns those flows write (``pipeline_run_id``,
``project_id``, ``artifact_type``, ``mime_type``, ``metadata``) stay writable.
``code_snapshots`` rejects every UPDATE and DELETE. Artifact rows themselves
have no DELETE trigger, so existing foreign keys remain the authority over
whether retention/offboarding may remove one.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from alembic_frozen.rev_0042_provenance import (
    provenance_immutability_downgrade_statements,
    provenance_immutability_upgrade_statements,
)

revision: str = "0042_provenance_immutability"
down_revision: Union[str, Sequence[str], None] = "0041_artifact_pipeline_run"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for statement in provenance_immutability_upgrade_statements():
        op.execute(sa.text(statement))


def downgrade() -> None:
    for statement in provenance_immutability_downgrade_statements():
        op.execute(sa.text(statement))
