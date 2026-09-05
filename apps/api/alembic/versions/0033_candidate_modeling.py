"""normalize experiment candidates, hyperparameters, cv folds, evaluations, selection

Revision ID: 0033_candidate_modeling
Revises: 0032_scientific_lineage
Create Date: 2026-09-05

Fingerprint uniqueness:
Existing ``experiment_candidates.fingerprint`` values are a 20-character SHA-256
prefix and are already unique per experiment in current generators. Empty
fingerprints and any remaining ``(experiment_id, fingerprint)`` duplicates are
repaired before the unique constraint is added:

* empty fingerprints become ``sha256(experiment_id:id:candidate_key)[:40]``
* extra duplicate rows keep the oldest ``created_at``/``id`` and rehash the rest
  as ``sha256(experiment_id:id:original_fingerprint)[:40]``
"""

from __future__ import annotations

import hashlib
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.domain.scientific_plane import (
    CK_CV_FOLD_RUN_STATUS,
    CK_HYPERPARAMETER_SOURCE,
    CK_MODEL_EVALUATION_SCOPE,
    CK_MODEL_EVALUATION_STATUS,
    CK_MODEL_EVALUATION_TYPE,
)

revision: str = "0033_candidate_modeling"
down_revision: Union[str, Sequence[str], None] = "0032_scientific_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _digest(*parts: object) -> str:
    payload = ":".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def _repair_fingerprints() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, experiment_id, candidate_key, fingerprint, created_at "
            "FROM experiment_candidates ORDER BY experiment_id, fingerprint, created_at, id"
        )
    ).mappings()
    seen: dict[tuple[object, str], object] = {}
    for row in rows:
        fingerprint = str(row["fingerprint"] or "").strip()
        if not fingerprint:
            fingerprint = _digest(row["experiment_id"], row["id"], row["candidate_key"])
            connection.execute(
                sa.text(
                    "UPDATE experiment_candidates SET fingerprint = :fp WHERE id = :id"
                ),
                {"fp": fingerprint, "id": row["id"]},
            )
        key = (row["experiment_id"], fingerprint)
        kept = seen.get(key)
        if kept is None:
            seen[key] = row["id"]
            continue
        repaired = _digest(row["experiment_id"], row["id"], fingerprint)
        connection.execute(
            sa.text("UPDATE experiment_candidates SET fingerprint = :fp WHERE id = :id"),
            {"fp": repaired, "id": row["id"]},
        )
        seen[(row["experiment_id"], repaired)] = row["id"]


def upgrade() -> None:
    op.add_column(
        "experiment_candidates",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "experiment_candidates",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "experiment_candidates",
        sa.Column("model_family", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "experiment_candidates",
        sa.Column("algorithm", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "experiment_candidates",
        sa.Column("implementation_library", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "experiment_candidates",
        sa.Column("implementation_class", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "experiment_candidates",
        sa.Column("library_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "experiment_candidates",
        sa.Column("search_stage", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "experiment_candidates",
        sa.Column("trial_number", sa.Integer(), nullable=True),
    )
    op.add_column(
        "experiment_candidates",
        sa.Column(
            "feature_set_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("feature_set_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "experiment_candidates",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "experiment_candidates",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "experiment_candidates",
        sa.Column("duration_ms", sa.Float(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE experiment_candidates AS c SET workspace_id = e.workspace_id, "
            "project_id = e.project_id FROM experiments AS e WHERE e.id = c.experiment_id"
        )
    )
    op.alter_column("experiment_candidates", "workspace_id", nullable=False)
    _repair_fingerprints()
    op.create_unique_constraint(
        "uq_experiment_candidates_experiment_fingerprint",
        "experiment_candidates",
        ["experiment_id", "fingerprint"],
    )
    op.create_unique_constraint(
        "uq_experiment_candidates_workspace_id",
        "experiment_candidates",
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_experiment_candidates_workspace_project",
        "experiment_candidates",
        "projects",
        ["workspace_id", "project_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_experiment_candidates_workspace_pipeline_run",
        "experiment_candidates",
        "experiments",
        ["workspace_id", "experiment_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_experiment_candidates_workspace_feature_set_version",
        "experiment_candidates",
        "feature_set_versions",
        ["workspace_id", "feature_set_version_id"],
        ["workspace_id", "id"],
    )
    op.create_index(
        "ix_experiment_candidates_workspace_id", "experiment_candidates", ["workspace_id"]
    )
    op.create_index(
        "ix_experiment_candidates_project_id", "experiment_candidates", ["project_id"]
    )
    op.create_index(
        "ix_experiment_candidates_model_family", "experiment_candidates", ["model_family"]
    )
    op.create_index(
        "ix_experiment_candidates_feature_set_version_id",
        "experiment_candidates",
        ["feature_set_version_id"],
    )
    op.alter_column("experiment_candidates", "model_family", server_default=None)
    op.alter_column("experiment_candidates", "algorithm", server_default=None)

    op.create_table(
        "model_hyperparameters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parameter_name", sa.String(length=128), nullable=False),
        sa.Column("value_json", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "candidate_id",
            "parameter_name",
            name="uq_model_hyperparameters_candidate_name",
        ),
        sa.CheckConstraint(
            CK_HYPERPARAMETER_SOURCE, name="ck_model_hyperparameters_source_valid"
        ),
    )
    op.create_index(
        "ix_model_hyperparameters_candidate_id", "model_hyperparameters", ["candidate_id"]
    )
    op.create_index(
        "ix_model_hyperparameters_parameter_name",
        "model_hyperparameters",
        ["parameter_name"],
    )

    op.create_table(
        "cv_fold_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fold_number", sa.Integer(), nullable=False),
        sa.Column("train_row_count", sa.Integer(), nullable=False),
        sa.Column("validation_row_count", sa.Integer(), nullable=False),
        sa.Column("train_group_count", sa.Integer(), nullable=True),
        sa.Column("validation_group_count", sa.Integer(), nullable=True),
        sa.Column("train_time_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("train_time_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_time_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_time_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "candidate_id", "fold_number", name="uq_cv_fold_runs_candidate_fold"
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_cv_fold_runs_workspace_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_cv_fold_runs_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["experiment_candidates.workspace_id", "experiment_candidates.id"],
            name="fk_cv_fold_runs_workspace_candidate",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(CK_CV_FOLD_RUN_STATUS, name="ck_cv_fold_runs_status_valid"),
    )
    op.create_index("ix_cv_fold_runs_workspace_id", "cv_fold_runs", ["workspace_id"])
    op.create_index("ix_cv_fold_runs_project_id", "cv_fold_runs", ["project_id"])
    op.create_index("ix_cv_fold_runs_candidate_id", "cv_fold_runs", ["candidate_id"])

    op.create_table(
        "model_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_candidates.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "model_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("evaluation_type", sa.String(length=64), nullable=False),
        sa.Column("evaluation_scope", sa.String(length=64), nullable=False),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasets.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_model_evaluations_workspace_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_model_evaluations_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.id"],
            name="fk_model_evaluations_workspace_dataset",
        ),
        sa.CheckConstraint(CK_MODEL_EVALUATION_TYPE, name="ck_model_evaluations_type_valid"),
        sa.CheckConstraint(
            CK_MODEL_EVALUATION_SCOPE, name="ck_model_evaluations_scope_valid"
        ),
        sa.CheckConstraint(
            CK_MODEL_EVALUATION_STATUS, name="ck_model_evaluations_status_valid"
        ),
        sa.CheckConstraint(
            "candidate_id IS NOT NULL OR model_version_id IS NOT NULL",
            name="ck_model_evaluations_subject_present",
        ),
    )
    op.create_index(
        "ix_model_evaluations_workspace_id", "model_evaluations", ["workspace_id"]
    )
    op.create_index("ix_model_evaluations_project_id", "model_evaluations", ["project_id"])
    op.create_index(
        "ix_model_evaluations_candidate_id", "model_evaluations", ["candidate_id"]
    )
    op.create_index(
        "ix_model_evaluations_model_version_id",
        "model_evaluations",
        ["model_version_id"],
    )
    op.create_index("ix_model_evaluations_dataset_id", "model_evaluations", ["dataset_id"])
    op.create_index("ix_model_evaluations_scope", "model_evaluations", ["evaluation_scope"])

    op.create_table(
        "evaluation_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "model_evaluation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_evaluations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("metric_name", sa.String(length=64), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "model_evaluation_id",
            "metric_name",
            name="uq_evaluation_metrics_evaluation_name",
        ),
    )
    op.create_index(
        "ix_evaluation_metrics_name_value",
        "evaluation_metrics",
        ["metric_name", "metric_value"],
    )
    op.create_index(
        "ix_evaluation_metrics_evaluation_name",
        "evaluation_metrics",
        ["model_evaluation_id", "metric_name"],
    )

    op.create_table(
        "model_selection_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "selected_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("selection_metric", sa.String(length=64), nullable=False),
        sa.Column("selected_score", sa.Float(), nullable=False),
        sa.Column("selection_policy", sa.String(length=256), nullable=False),
        sa.Column(
            "runner_up_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(length=2048), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "pipeline_run_id", name="uq_model_selection_decisions_pipeline_run"
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_model_selection_decisions_workspace_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_model_selection_decisions_workspace_project",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_model_selection_decisions_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_model_selection_decisions_workspace_id",
        "model_selection_decisions",
        ["workspace_id"],
    )
    op.create_index(
        "ix_model_selection_decisions_project_id",
        "model_selection_decisions",
        ["project_id"],
    )
    op.create_index(
        "ix_model_selection_decisions_pipeline_run_id",
        "model_selection_decisions",
        ["pipeline_run_id"],
    )
    op.create_index(
        "ix_model_selection_decisions_selected_candidate_id",
        "model_selection_decisions",
        ["selected_candidate_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_selection_decisions_selected_candidate_id",
        table_name="model_selection_decisions",
    )
    op.drop_index(
        "ix_model_selection_decisions_pipeline_run_id",
        table_name="model_selection_decisions",
    )
    op.drop_index(
        "ix_model_selection_decisions_project_id",
        table_name="model_selection_decisions",
    )
    op.drop_index(
        "ix_model_selection_decisions_workspace_id",
        table_name="model_selection_decisions",
    )
    op.drop_table("model_selection_decisions")
    op.drop_index(
        "ix_evaluation_metrics_evaluation_name", table_name="evaluation_metrics"
    )
    op.drop_index("ix_evaluation_metrics_name_value", table_name="evaluation_metrics")
    op.drop_table("evaluation_metrics")
    op.drop_index("ix_model_evaluations_scope", table_name="model_evaluations")
    op.drop_index("ix_model_evaluations_dataset_id", table_name="model_evaluations")
    op.drop_index("ix_model_evaluations_model_version_id", table_name="model_evaluations")
    op.drop_index("ix_model_evaluations_candidate_id", table_name="model_evaluations")
    op.drop_index("ix_model_evaluations_project_id", table_name="model_evaluations")
    op.drop_index("ix_model_evaluations_workspace_id", table_name="model_evaluations")
    op.drop_table("model_evaluations")
    op.drop_index("ix_cv_fold_runs_candidate_id", table_name="cv_fold_runs")
    op.drop_index("ix_cv_fold_runs_project_id", table_name="cv_fold_runs")
    op.drop_index("ix_cv_fold_runs_workspace_id", table_name="cv_fold_runs")
    op.drop_table("cv_fold_runs")
    op.drop_index(
        "ix_model_hyperparameters_parameter_name", table_name="model_hyperparameters"
    )
    op.drop_index(
        "ix_model_hyperparameters_candidate_id", table_name="model_hyperparameters"
    )
    op.drop_table("model_hyperparameters")
    op.drop_index(
        "ix_experiment_candidates_feature_set_version_id",
        table_name="experiment_candidates",
    )
    op.drop_index(
        "ix_experiment_candidates_model_family", table_name="experiment_candidates"
    )
    op.drop_index(
        "ix_experiment_candidates_project_id", table_name="experiment_candidates"
    )
    op.drop_index(
        "ix_experiment_candidates_workspace_id", table_name="experiment_candidates"
    )
    op.drop_constraint(
        "fk_experiment_candidates_workspace_feature_set_version",
        "experiment_candidates",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_experiment_candidates_workspace_pipeline_run",
        "experiment_candidates",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_experiment_candidates_workspace_project",
        "experiment_candidates",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_experiment_candidates_workspace_id",
        "experiment_candidates",
        type_="unique",
    )
    op.drop_constraint(
        "uq_experiment_candidates_experiment_fingerprint",
        "experiment_candidates",
        type_="unique",
    )
    op.drop_column("experiment_candidates", "duration_ms")
    op.drop_column("experiment_candidates", "completed_at")
    op.drop_column("experiment_candidates", "started_at")
    op.drop_column("experiment_candidates", "feature_set_version_id")
    op.drop_column("experiment_candidates", "trial_number")
    op.drop_column("experiment_candidates", "search_stage")
    op.drop_column("experiment_candidates", "library_version")
    op.drop_column("experiment_candidates", "implementation_class")
    op.drop_column("experiment_candidates", "implementation_library")
    op.drop_column("experiment_candidates", "algorithm")
    op.drop_column("experiment_candidates", "model_family")
    op.drop_column("experiment_candidates", "project_id")
    op.drop_column("experiment_candidates", "workspace_id")
