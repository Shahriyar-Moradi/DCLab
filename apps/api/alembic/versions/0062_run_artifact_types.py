"""add predictions and derived_dataset artifact types

Revision ID: 0062_run_artifact_types
Revises: 0061_ingestion_publication
Create Date: 2026-10-01

P1.3-A: run outputs live in object storage, not under the repository.
``predictions`` holds a run's holdout prediction CSV. ``derived_dataset`` holds a
table derived inside a training job from an already-published upload (the
prepared CSV); it is deliberately distinct from ``dataset`` so the upload
publication gate keeps guarding externally supplied bytes.

Downgrade only succeeds while no rows use the new types; after real use, repair
forward instead.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0062_run_artifact_types"
down_revision: Union[str, Sequence[str], None] = "0061_ingestion_publication"
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
    "reproduction_notebook",
    "reproduction_script",
)
_NEW_TYPES = _OLD_TYPES + ("predictions", "derived_dataset")


def _clause(values: tuple[str, ...]) -> str:
    inner = ", ".join(f"'{value}'" for value in values)
    return f"artifact_type IN ({inner})"


def upgrade() -> None:
    op.drop_constraint("ck_artifacts_type_valid", "artifacts", type_="check")
    op.create_check_constraint("ck_artifacts_type_valid", "artifacts", _clause(_NEW_TYPES))


def downgrade() -> None:
    op.drop_constraint("ck_artifacts_type_valid", "artifacts", type_="check")
    op.create_check_constraint("ck_artifacts_type_valid", "artifacts", _clause(_OLD_TYPES))
