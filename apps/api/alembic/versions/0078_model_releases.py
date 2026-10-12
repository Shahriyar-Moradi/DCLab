"""Model releases: the immutable approval behind "model in use" (approved for scoring).

Revision ID: 0078_model_releases
Revises: 0077_r3_runs
Create Date: 2026-10-12

P7.2-A. Putting a model in use (the ``champion_model`` ref move under an accepted
``champion_promoted`` record) now also inserts one ``model_releases`` row in the same
transaction: who approved it, why, the version's package manifest digest and saved format.
Append-only through the existing ``prevent_canonical_row_mutation()`` trigger function.
Composite tenant FKs (workspace, project) to projects, model versions, decision records and
the previous release; ``previous_release_id`` chains the project's approvals and two unique
indexes keep the chain linear. ``batch_predictions.model_release_id`` (nullable since 0069,
every row NULL until now) gets a composite FK that pins the tenant AND the scored version
(``(workspace_id, model_version_id, model_release_id)`` to the release's
``(workspace_id, model_version_id, id)``; MATCH SIMPLE: only checked when the id is set) and a
partial index. ``created_at`` is ``clock_timestamp()`` so newest-first order follows the chain.

Additive: one new table, one new FK and index on an existing nullable column. Downgrade drops
the FK, index, trigger and table: the approval history is gone for good (the decision records
and ref moves stay), and scoring rows lose the link (the column stays, values are cleared first
so no dangling id survives; terminal rows are frozen by a trigger, so it is disabled for that
statement only).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0078_model_releases"
down_revision: Union[str, Sequence[str], None] = "0077_r3_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Identical literals in app/domain/model_releases.py and app/db/integrity.py.
_CK_RATIONALE = "char_length(btrim(rationale)) > 0 AND char_length(rationale) <= 4000"
_CK_DIGEST = "package_manifest_digest ~ '^[0-9a-f]{64}$'"
_CK_SAVED_FORMAT = "saved_format IN ('package_v1', 'legacy_quarantined')"
_CK_PREVIOUS_NOT_SELF = "previous_release_id IS NULL OR previous_release_id <> id"
_TRIGGER = (
    "CREATE TRIGGER model_releases_append_only BEFORE UPDATE OR DELETE ON model_releases "
    "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()"
)


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "model_releases",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("model_version_id", uuid, nullable=False),
        sa.Column("previous_release_id", uuid, nullable=True),
        sa.Column("decision_record_id", uuid, nullable=False),
        sa.Column("approved_by_user_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("rationale", sa.String(length=4000), nullable=False),
        sa.Column("package_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("saved_format", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_model_releases_workspace_id"),
        sa.UniqueConstraint("workspace_id", "project_id", "id", name="uq_model_releases_workspace_project_id"),
        sa.UniqueConstraint(
            "workspace_id", "model_version_id", "id", name="uq_model_releases_workspace_model_version_id"
        ),
        sa.UniqueConstraint("decision_record_id", name="uq_model_releases_decision_record_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
            name="fk_model_releases_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "model_version_id"],
            ["model_versions.workspace_id", "model_versions.project_id", "model_versions.id"],
            name="fk_model_releases_workspace_project_model_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "previous_release_id"],
            ["model_releases.workspace_id", "model_releases.project_id", "model_releases.id"],
            name="fk_model_releases_workspace_project_previous_release",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "decision_record_id"],
            [
                "project_decision_records.workspace_id",
                "project_decision_records.project_id",
                "project_decision_records.id",
            ],
            name="fk_model_releases_workspace_project_decision_record",
        ),
        sa.CheckConstraint(_CK_RATIONALE, name="ck_model_releases_rationale"),
        sa.CheckConstraint(_CK_DIGEST, name="ck_model_releases_digest"),
        sa.CheckConstraint(_CK_SAVED_FORMAT, name="ck_model_releases_saved_format"),
        sa.CheckConstraint(_CK_PREVIOUS_NOT_SELF, name="ck_model_releases_previous_not_self"),
    )
    op.create_index(
        "ix_model_releases_project_created_at", "model_releases",
        ["workspace_id", "project_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_model_releases_workspace_model_version", "model_releases", ["workspace_id", "model_version_id"])
    op.create_index("ix_model_releases_approved_by_user_id", "model_releases", ["approved_by_user_id"])
    op.create_index(
        "uq_model_releases_previous_release_id", "model_releases", ["previous_release_id"], unique=True,
        postgresql_where=sa.text("previous_release_id IS NOT NULL"),
    )
    op.create_index(
        "uq_model_releases_project_first", "model_releases", ["project_id"], unique=True,
        postgresql_where=sa.text("previous_release_id IS NULL"),
    )
    op.execute(sa.text(_TRIGGER))
    op.create_foreign_key(
        "fk_batch_predictions_workspace_model_release", "batch_predictions", "model_releases",
        ["workspace_id", "model_version_id", "model_release_id"], ["workspace_id", "model_version_id", "id"],
    )
    op.create_index(
        "ix_batch_predictions_workspace_model_release", "batch_predictions", ["workspace_id", "model_release_id"],
        postgresql_where=sa.text("model_release_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_batch_predictions_workspace_model_release", table_name="batch_predictions")
    op.drop_constraint("fk_batch_predictions_workspace_model_release", "batch_predictions", type_="foreignkey")
    op.execute(sa.text("ALTER TABLE batch_predictions DISABLE TRIGGER batch_predictions_terminal_immutable"))
    op.execute(sa.text("UPDATE batch_predictions SET model_release_id = NULL WHERE model_release_id IS NOT NULL"))
    op.execute(sa.text("ALTER TABLE batch_predictions ENABLE TRIGGER batch_predictions_terminal_immutable"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS model_releases_append_only ON model_releases"))
    op.drop_index("uq_model_releases_project_first", table_name="model_releases")
    op.drop_index("uq_model_releases_previous_release_id", table_name="model_releases")
    op.drop_index("ix_model_releases_approved_by_user_id", table_name="model_releases")
    op.drop_index("ix_model_releases_workspace_model_version", table_name="model_releases")
    op.drop_index("ix_model_releases_project_created_at", table_name="model_releases")
    op.drop_table("model_releases")
