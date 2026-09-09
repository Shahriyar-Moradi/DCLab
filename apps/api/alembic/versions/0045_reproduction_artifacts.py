"""add reproduction_notebook and reproduction_script artifact types

Revision ID: 0045_reproduction_artifacts
Revises: 0044_tenant_set_null_columns
Create Date: 2026-09-08

Completed model builds persist a safe Jupyter notebook and Python script
through the existing Artifact / object-storage registry.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0045_reproduction_artifacts"
down_revision: Union[str, Sequence[str], None] = "0044_tenant_set_null_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_TYPES = (
    "dataset",
    "source_code",
    "training_script",
    "model",
    "preprocessor",
    "report",
    "plot",
    "result_json",
    "feature_manifest",
    "dependency_lock",
)
_NEW_TYPES = _OLD_TYPES + ("reproduction_notebook", "reproduction_script")


def _clause(values: tuple[str, ...]) -> str:
    inner = ", ".join(f"'{value}'" for value in values)
    return f"artifact_type IN ({inner})"


def upgrade() -> None:
    op.drop_constraint("ck_artifacts_type_valid", "artifacts", type_="check")
    op.create_check_constraint("ck_artifacts_type_valid", "artifacts", _clause(_NEW_TYPES))


def downgrade() -> None:
    op.drop_constraint("ck_artifacts_type_valid", "artifacts", type_="check")
    op.create_check_constraint("ck_artifacts_type_valid", "artifacts", _clause(_OLD_TYPES))
