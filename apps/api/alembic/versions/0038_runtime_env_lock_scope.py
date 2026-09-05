"""move dependency-lock artifacts off shared runtime environments

Revision ID: 0038_runtime_env_lock_scope
Revises: 0037_ml_engineer_seats
Create Date: 2026-09-05

RuntimeEnvironment is a globally reusable fingerprint. Workspace-owned
dependency-lock Artifacts belong on CodeSnapshot, with a composite tenant FK.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038_runtime_env_lock_scope"
down_revision: Union[str, Sequence[str], None] = "0037_ml_engineer_seats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "code_snapshots",
        sa.Column(
            "dependency_lock_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE code_snapshots AS cs
            SET dependency_lock_artifact_id = re.dependency_lock_artifact_id
            FROM runtime_environments AS re
            JOIN artifacts AS a ON a.id = re.dependency_lock_artifact_id
            WHERE cs.runtime_environment_id = re.id
              AND cs.dependency_lock_artifact_id IS NULL
              AND a.workspace_id = cs.workspace_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE code_snapshots AS cs
            SET dependency_lock_artifact_id = matched.id
            FROM (
                SELECT DISTINCT ON (
                    workspace_id, "metadata"->>'pipeline_run_id'
                )
                    id,
                    workspace_id,
                    "metadata"->>'pipeline_run_id' AS pipeline_run_id
                FROM artifacts
                WHERE artifact_type = 'dependency_lock'
                ORDER BY
                    workspace_id,
                    "metadata"->>'pipeline_run_id',
                    created_at DESC,
                    id
            ) AS matched
            WHERE cs.dependency_lock_artifact_id IS NULL
              AND matched.workspace_id = cs.workspace_id
              AND matched.pipeline_run_id = cs.pipeline_run_id::text
            """
        )
    )
    op.create_foreign_key(
        "fk_code_snapshots_workspace_dependency_lock_artifact",
        "code_snapshots",
        "artifacts",
        ["workspace_id", "dependency_lock_artifact_id"],
        ["workspace_id", "id"],
    )
    op.create_index(
        "ix_code_snapshots_dependency_lock_artifact_id",
        "code_snapshots",
        ["dependency_lock_artifact_id"],
    )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for foreign_key in inspector.get_foreign_keys("runtime_environments"):
        if "dependency_lock_artifact_id" in foreign_key.get("constrained_columns", []):
            op.drop_constraint(
                foreign_key["name"], "runtime_environments", type_="foreignkey"
            )
    op.drop_column("runtime_environments", "dependency_lock_artifact_id")


def downgrade() -> None:
    op.add_column(
        "runtime_environments",
        sa.Column(
            "dependency_lock_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE runtime_environments AS re
            SET dependency_lock_artifact_id = picked.lock_id
            FROM (
                SELECT DISTINCT ON (runtime_environment_id)
                    runtime_environment_id,
                    dependency_lock_artifact_id AS lock_id
                FROM code_snapshots
                WHERE dependency_lock_artifact_id IS NOT NULL
                ORDER BY runtime_environment_id, created_at, id
            ) AS picked
            WHERE picked.runtime_environment_id = re.id
            """
        )
    )
    op.drop_constraint(
        "fk_code_snapshots_workspace_dependency_lock_artifact",
        "code_snapshots",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_code_snapshots_dependency_lock_artifact_id",
        table_name="code_snapshots",
    )
    op.drop_column("code_snapshots", "dependency_lock_artifact_id")
