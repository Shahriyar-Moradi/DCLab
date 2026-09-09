"""tenant-aware FKs for ingestion, labs uploads, and ML jobs

Revision ID: 0046_ingestion_job_tenant_fks
Revises: 0045_reproduction_artifacts
Create Date: 2026-09-09

``ingestion_runs.data_source_id`` was a simple id FK, so a workspace-A run
could name a workspace-B source. Datasets, Labs uploads, and ML jobs had the
same hole on their optional lineage pointers.

Parent keys UNIQUE(workspace_id, id) on data_sources and ingestion_runs let
children declare FOREIGN KEY (workspace_id, child_id).

Deletion actions are deliberate:
- IngestionRun → DataSource: CASCADE (run cannot exist without its source)
- Dataset → IngestionRun: NO ACTION (datasets are immutable; SET NULL/CASCADE
  would try to mutate or delete a frozen row)
- ClientLabUpload → DataSource / IngestionRun: SET NULL only the optional
  child column, never workspace_id
- MlJob → Project: SET NULL only project_id
- MlJob → ClientLabUpload: CASCADE (the job is bound to that upload)
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0046_ingestion_job_tenant_fks"
down_revision: Union[str, Sequence[str], None] = "0045_reproduction_artifacts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CROSS_TENANT = (
    ("ingestion_runs", "data_source_id", "data_sources"),
    ("datasets", "ingestion_run_id", "ingestion_runs"),
    ("client_lab_uploads", "data_source_id", "data_sources"),
    ("client_lab_uploads", "ingestion_run_id", "ingestion_runs"),
    ("ml_jobs", "project_id", "projects"),
    ("ml_jobs", "upload_id", "client_lab_uploads"),
)


def _reject_cross_workspace_rows() -> None:
    bind = op.get_bind()
    for child, child_id, parent in _CROSS_TENANT:
        count = bind.execute(
            sa.text(
                f"""
                SELECT COUNT(*)
                FROM "{child}" AS child_row
                JOIN "{parent}" AS parent_row
                  ON parent_row.id = child_row."{child_id}"
                WHERE child_row."{child_id}" IS NOT NULL
                  AND child_row.workspace_id IS DISTINCT FROM parent_row.workspace_id
                """
            )
        ).scalar()
        if count:
            raise RuntimeError(
                f"{child}.{child_id} has {count} cross-workspace row(s) versus {parent}"
            )


def _add_set_null_fk(table: str, name: str, child_id: str, parent: str) -> None:
    op.execute(
        sa.text(
            f'ALTER TABLE "{table}" '
            f'ADD CONSTRAINT "{name}" '
            f'FOREIGN KEY ("workspace_id", "{child_id}") '
            f'REFERENCES "{parent}" ("workspace_id", "id") '
            f'ON DELETE SET NULL ("{child_id}")'
        )
    )


def _drop_fk(table: str, name: str) -> None:
    op.execute(sa.text(f'ALTER TABLE "{table}" DROP CONSTRAINT "{name}"'))


def upgrade() -> None:
    _reject_cross_workspace_rows()

    op.create_unique_constraint(
        "uq_data_sources_workspace_id", "data_sources", ["workspace_id", "id"]
    )
    op.create_unique_constraint(
        "uq_ingestion_runs_workspace_id", "ingestion_runs", ["workspace_id", "id"]
    )

    op.create_foreign_key(
        "fk_ingestion_runs_workspace_data_source",
        "ingestion_runs",
        "data_sources",
        ["workspace_id", "data_source_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_datasets_workspace_ingestion_run",
        "datasets",
        "ingestion_runs",
        ["workspace_id", "ingestion_run_id"],
        ["workspace_id", "id"],
    )
    _add_set_null_fk(
        "client_lab_uploads",
        "fk_client_lab_uploads_workspace_data_source",
        "data_source_id",
        "data_sources",
    )
    _add_set_null_fk(
        "client_lab_uploads",
        "fk_client_lab_uploads_workspace_ingestion_run",
        "ingestion_run_id",
        "ingestion_runs",
    )

    _drop_fk("ml_jobs", "fk_ml_jobs_workspace_project")
    _add_set_null_fk(
        "ml_jobs",
        "fk_ml_jobs_workspace_project",
        "project_id",
        "projects",
    )
    _drop_fk("ml_jobs", "fk_ml_jobs_workspace_upload")
    op.create_foreign_key(
        "fk_ml_jobs_workspace_upload",
        "ml_jobs",
        "client_lab_uploads",
        ["workspace_id", "upload_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    _drop_fk("ml_jobs", "fk_ml_jobs_workspace_upload")
    op.create_foreign_key(
        "fk_ml_jobs_workspace_upload",
        "ml_jobs",
        "client_lab_uploads",
        ["workspace_id", "upload_id"],
        ["workspace_id", "id"],
    )
    _drop_fk("ml_jobs", "fk_ml_jobs_workspace_project")
    op.create_foreign_key(
        "fk_ml_jobs_workspace_project",
        "ml_jobs",
        "projects",
        ["workspace_id", "project_id"],
        ["workspace_id", "id"],
    )
    _drop_fk("client_lab_uploads", "fk_client_lab_uploads_workspace_ingestion_run")
    _drop_fk("client_lab_uploads", "fk_client_lab_uploads_workspace_data_source")
    op.drop_constraint(
        "fk_datasets_workspace_ingestion_run", "datasets", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_ingestion_runs_workspace_data_source",
        "ingestion_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_ingestion_runs_workspace_id", "ingestion_runs", type_="unique"
    )
    op.drop_constraint("uq_data_sources_workspace_id", "data_sources", type_="unique")
