"""repair tenant lineage for legacy Labs uploads

Revision ID: 0027_repair_tenant_lineage
Revises: 0026_ml_run_events_append_only
Create Date: 2026-09-04
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0027_repair_tenant_lineage"
down_revision: Union[str, Sequence[str], None] = "0026_ml_run_events_append_only"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Only repair defaulted rows whose upload lineage identifies exactly one
    # tenant. Ambiguous or unlinked legacy rows deliberately remain untouched.
    op.execute(
        """
        WITH dataset_lineage AS (
            SELECT dataset_id, min(workspace_id::text)::uuid AS workspace_id
            FROM client_lab_uploads
            WHERE dataset_id IS NOT NULL
            GROUP BY dataset_id
            HAVING count(DISTINCT workspace_id) = 1
        )
        UPDATE datasets AS dataset
        SET workspace_id = lineage.workspace_id
        FROM dataset_lineage AS lineage
        WHERE dataset.id = lineage.dataset_id
          AND dataset.workspace_id = '00000000-0000-0000-0000-000000000001'
        """
    )
    op.execute(
        """
        WITH asset_lineage AS (
            SELECT dataset_asset_id, min(workspace_id::text)::uuid AS workspace_id
            FROM datasets
            GROUP BY dataset_asset_id
            HAVING count(DISTINCT workspace_id) = 1
        )
        UPDATE dataset_assets AS asset
        SET workspace_id = lineage.workspace_id
        FROM asset_lineage AS lineage
        WHERE asset.id = lineage.dataset_asset_id
          AND asset.workspace_id = '00000000-0000-0000-0000-000000000001'
        """
    )
    op.execute(
        """
        WITH experiment_candidates AS (
            SELECT experiment_id AS id, workspace_id
            FROM client_lab_uploads
            WHERE experiment_id IS NOT NULL
            UNION ALL
            SELECT experiment.id, upload.workspace_id
            FROM experiments AS experiment
            JOIN client_lab_uploads AS upload
              ON upload.dataset_id = experiment.dataset_id
        ),
        experiment_lineage AS (
            SELECT id, min(workspace_id::text)::uuid AS workspace_id
            FROM experiment_candidates
            GROUP BY id
            HAVING count(DISTINCT workspace_id) = 1
        )
        UPDATE experiments AS experiment
        SET workspace_id = lineage.workspace_id
        FROM experiment_lineage AS lineage
        WHERE experiment.id = lineage.id
          AND experiment.workspace_id = '00000000-0000-0000-0000-000000000001'
        """
    )


def downgrade() -> None:
    # The previous workspace values cannot be reconstructed after this data repair.
    pass
