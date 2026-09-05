"""tenant-aware FKs, canonical immutability, measured list indexes

Revision ID: 0035_database_integrity
Revises: 0034_reproducible_code
Create Date: 2026-09-05

Cross-workspace lineage is rejected by PostgreSQL for the canonical hierarchy.
Locked/published scientific rows are immutable. PipelineRun status stays mutable.

Redundant indexes dropped only when a unique or composite index already covers
the same leading columns (EXPLAIN evidence is in tests).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.integrity import (
    immutability_downgrade_statements,
    immutability_upgrade_statements,
)

revision: str = "0035_database_integrity"
down_revision: Union[str, Sequence[str], None] = "0034_reproducible_code"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uq(name: str, table: str, columns: list[str]) -> None:
    op.create_unique_constraint(name, table, columns)


def _fk(
    name: str,
    source_table: str,
    source_cols: list[str],
    referent_table: str,
    *,
    ondelete: str | None = None,
) -> None:
    kwargs: dict[str, str] = {}
    if ondelete is not None:
        kwargs["ondelete"] = ondelete
    op.create_foreign_key(
        name,
        source_table,
        referent_table,
        source_cols,
        ["workspace_id", "id"],
        **kwargs,
    )


def _created_at_desc_index(name: str, table: str, *leading: str) -> None:
    op.create_index(
        name,
        table,
        [*leading, sa.desc("created_at")],
        unique=False,
    )


def upgrade() -> None:
    _uq("uq_workspace_domains_workspace_id", "workspace_domains", ["workspace_id", "id"])
    _uq("uq_dataset_columns_workspace_id", "dataset_columns", ["workspace_id", "id"])
    _uq("uq_model_assets_workspace_id", "model_assets", ["workspace_id", "id"])
    _uq(
        "uq_client_lab_uploads_workspace_id",
        "client_lab_uploads",
        ["workspace_id", "id"],
    )
    _uq("uq_ml_run_events_workspace_id", "ml_run_events", ["workspace_id", "id"])
    _uq("uq_llm_invocations_workspace_id", "llm_invocations", ["workspace_id", "id"])

    _fk(
        "fk_ml_workflows_workspace_workspace_domain",
        "ml_workflows",
        ["workspace_id", "workspace_domain_id"],
        "workspace_domains",
    )
    _fk(
        "fk_datasets_workspace_dataset_asset",
        "datasets",
        ["workspace_id", "dataset_asset_id"],
        "dataset_assets",
    )
    _fk(
        "fk_datasets_workspace_artifact",
        "datasets",
        ["workspace_id", "artifact_id"],
        "artifacts",
    )
    _fk(
        "fk_workflow_runs_workspace_source_upload",
        "workflow_runs",
        ["workspace_id", "source_upload_id"],
        "client_lab_uploads",
        ondelete="SET NULL",
    )
    _fk(
        "fk_experiments_workspace_workflow_run",
        "experiments",
        ["workspace_id", "workflow_run_id"],
        "workflow_runs",
    )
    _fk(
        "fk_experiments_workspace_dataset",
        "experiments",
        ["workspace_id", "dataset_id"],
        "datasets",
    )
    _fk(
        "fk_client_lab_uploads_workspace_dataset",
        "client_lab_uploads",
        ["workspace_id", "dataset_id"],
        "datasets",
    )
    _fk(
        "fk_client_lab_uploads_workspace_experiment",
        "client_lab_uploads",
        ["workspace_id", "experiment_id"],
        "experiments",
    )
    _fk(
        "fk_client_lab_uploads_workspace_artifact",
        "client_lab_uploads",
        ["workspace_id", "artifact_id"],
        "artifacts",
    )
    _fk(
        "fk_data_quality_findings_workspace_dataset_column",
        "data_quality_findings",
        ["workspace_id", "dataset_column_id"],
        "dataset_columns",
    )
    _fk(
        "fk_data_preparation_decisions_workspace_dataset_column",
        "data_preparation_decisions",
        ["workspace_id", "dataset_column_id"],
        "dataset_columns",
    )
    _fk(
        "fk_model_assets_workspace_workflow",
        "model_assets",
        ["workspace_id", "workflow_id"],
        "ml_workflows",
    )
    _fk(
        "fk_model_versions_workspace_workflow",
        "model_versions",
        ["workspace_id", "workflow_id"],
        "ml_workflows",
    )
    _fk(
        "fk_model_versions_workspace_workflow_run",
        "model_versions",
        ["workspace_id", "workflow_run_id"],
        "workflow_runs",
    )
    _fk(
        "fk_model_versions_workspace_pipeline_run",
        "model_versions",
        ["workspace_id", "pipeline_run_id"],
        "experiments",
    )
    _fk(
        "fk_model_versions_workspace_selected_candidate",
        "model_versions",
        ["workspace_id", "selected_candidate_id"],
        "experiment_candidates",
    )
    _fk(
        "fk_model_versions_workspace_dataset",
        "model_versions",
        ["workspace_id", "dataset_id"],
        "datasets",
    )
    _fk(
        "fk_model_versions_workspace_model_asset",
        "model_versions",
        ["workspace_id", "model_asset_id"],
        "model_assets",
    )
    _fk(
        "fk_model_evaluations_workspace_candidate",
        "model_evaluations",
        ["workspace_id", "candidate_id"],
        "experiment_candidates",
    )
    _fk(
        "fk_model_evaluations_workspace_model_version",
        "model_evaluations",
        ["workspace_id", "model_version_id"],
        "model_versions",
    )
    _fk(
        "fk_model_selection_decisions_workspace_selected_candidate",
        "model_selection_decisions",
        ["workspace_id", "selected_candidate_id"],
        "experiment_candidates",
    )
    _fk(
        "fk_model_selection_decisions_workspace_runner_up_candidate",
        "model_selection_decisions",
        ["workspace_id", "runner_up_candidate_id"],
        "experiment_candidates",
    )
    _fk(
        "fk_ml_run_events_workspace_workflow_run",
        "ml_run_events",
        ["workspace_id", "workflow_run_id"],
        "workflow_runs",
        ondelete="CASCADE",
    )
    _fk(
        "fk_ml_run_events_workspace_experiment",
        "ml_run_events",
        ["workspace_id", "experiment_id"],
        "experiments",
        ondelete="CASCADE",
    )
    _fk(
        "fk_llm_invocations_workspace_workflow_run",
        "llm_invocations",
        ["workspace_id", "workflow_run_id"],
        "workflow_runs",
        ondelete="CASCADE",
    )
    _fk(
        "fk_llm_invocations_workspace_experiment",
        "llm_invocations",
        ["workspace_id", "experiment_id"],
        "experiments",
        ondelete="CASCADE",
    )

    _created_at_desc_index(
        "ix_experiments_workspace_created_at", "experiments", "workspace_id"
    )
    _created_at_desc_index(
        "ix_experiments_workspace_status_created_at",
        "experiments",
        "workspace_id",
        "status",
    )
    _created_at_desc_index(
        "ix_experiments_project_created_at", "experiments", "project_id"
    )
    op.create_index(
        "ix_experiments_workflow_run_created_at",
        "experiments",
        ["workflow_run_id", "created_at"],
        unique=False,
    )
    _created_at_desc_index(
        "ix_workflow_runs_workspace_created_at", "workflow_runs", "workspace_id"
    )
    _created_at_desc_index(
        "ix_workflow_runs_workspace_status_created_at",
        "workflow_runs",
        "workspace_id",
        "status",
    )
    _created_at_desc_index(
        "ix_workflow_runs_project_created_at", "workflow_runs", "project_id"
    )
    _created_at_desc_index(
        "ix_datasets_workspace_created_at", "datasets", "workspace_id"
    )
    _created_at_desc_index(
        "ix_datasets_project_created_at", "datasets", "project_id"
    )
    _created_at_desc_index(
        "ix_ml_run_events_workspace_created_at", "ml_run_events", "workspace_id"
    )
    op.create_index(
        "ix_ml_run_events_workflow_run_created_at",
        "ml_run_events",
        ["workflow_run_id", "created_at"],
        unique=False,
    )
    _created_at_desc_index(
        "ix_llm_invocations_workspace_created_at",
        "llm_invocations",
        "workspace_id",
    )
    _created_at_desc_index(
        "ix_client_lab_uploads_workspace_created_at",
        "client_lab_uploads",
        "workspace_id",
    )
    _created_at_desc_index(
        "ix_client_lab_uploads_workspace_status_created_at",
        "client_lab_uploads",
        "workspace_id",
        "pipeline_status",
    )

    op.drop_index("ix_experiments_workspace_id", table_name="experiments")
    op.drop_index("ix_experiments_workflow_run_id", table_name="experiments")
    op.drop_index("ix_experiments_project_id", table_name="experiments")
    op.drop_index("ix_workflow_runs_workspace_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_project_id", table_name="workflow_runs")
    op.drop_index("ix_datasets_workspace_id", table_name="datasets")
    op.drop_index("ix_datasets_project_id", table_name="datasets")
    op.drop_index("ix_dataset_columns_workspace_id", table_name="dataset_columns")
    op.drop_index("ix_artifacts_workspace_id", table_name="artifacts")
    op.drop_index("ix_workspace_domains_workspace_id", table_name="workspace_domains")
    op.drop_index("ix_model_assets_workspace_id", table_name="model_assets")
    op.drop_index("ix_client_lab_uploads_workspace_id", table_name="client_lab_uploads")
    op.drop_index("ix_pipeline_stage_runs_pipeline_run_id", table_name="pipeline_stage_runs")
    op.drop_index("ix_cv_fold_runs_candidate_id", table_name="cv_fold_runs")
    op.drop_index(
        "ix_model_hyperparameters_candidate_id", table_name="model_hyperparameters"
    )
    op.drop_index(
        "ix_evaluation_metrics_evaluation_name", table_name="evaluation_metrics"
    )
    op.drop_index("ix_ml_run_events_workspace_id", table_name="ml_run_events")
    op.drop_index("ix_ml_run_events_workflow_run_id", table_name="ml_run_events")
    op.drop_index("ix_ml_run_events_experiment_id", table_name="ml_run_events")
    op.drop_index("ix_llm_invocations_workspace_id", table_name="llm_invocations")

    for statement in immutability_upgrade_statements():
        op.execute(sa.text(statement))


def downgrade() -> None:
    for statement in immutability_downgrade_statements():
        op.execute(sa.text(statement))

    op.create_index(
        "ix_llm_invocations_workspace_id", "llm_invocations", ["workspace_id"]
    )
    op.create_index(
        "ix_ml_run_events_experiment_id", "ml_run_events", ["experiment_id"]
    )
    op.create_index(
        "ix_ml_run_events_workflow_run_id", "ml_run_events", ["workflow_run_id"]
    )
    op.create_index(
        "ix_ml_run_events_workspace_id", "ml_run_events", ["workspace_id"]
    )
    op.create_index(
        "ix_evaluation_metrics_evaluation_name",
        "evaluation_metrics",
        ["model_evaluation_id", "metric_name"],
    )
    op.create_index(
        "ix_model_hyperparameters_candidate_id",
        "model_hyperparameters",
        ["candidate_id"],
    )
    op.create_index(
        "ix_cv_fold_runs_candidate_id", "cv_fold_runs", ["candidate_id"]
    )
    op.create_index(
        "ix_pipeline_stage_runs_pipeline_run_id",
        "pipeline_stage_runs",
        ["pipeline_run_id"],
    )
    op.create_index(
        "ix_client_lab_uploads_workspace_id",
        "client_lab_uploads",
        ["workspace_id"],
    )
    op.create_index(
        "ix_model_assets_workspace_id", "model_assets", ["workspace_id"]
    )
    op.create_index(
        "ix_workspace_domains_workspace_id",
        "workspace_domains",
        ["workspace_id"],
    )
    op.create_index("ix_artifacts_workspace_id", "artifacts", ["workspace_id"])
    op.create_index(
        "ix_dataset_columns_workspace_id", "dataset_columns", ["workspace_id"]
    )
    op.create_index("ix_datasets_project_id", "datasets", ["project_id"])
    op.create_index("ix_datasets_workspace_id", "datasets", ["workspace_id"])
    op.create_index(
        "ix_workflow_runs_project_id", "workflow_runs", ["project_id"]
    )
    op.create_index(
        "ix_workflow_runs_workspace_id", "workflow_runs", ["workspace_id"]
    )
    op.create_index("ix_experiments_project_id", "experiments", ["project_id"])
    op.create_index(
        "ix_experiments_workflow_run_id", "experiments", ["workflow_run_id"]
    )
    op.create_index(
        "ix_experiments_workspace_id", "experiments", ["workspace_id"]
    )

    op.drop_index(
        "ix_client_lab_uploads_workspace_status_created_at",
        table_name="client_lab_uploads",
    )
    op.drop_index(
        "ix_client_lab_uploads_workspace_created_at",
        table_name="client_lab_uploads",
    )
    op.drop_index(
        "ix_llm_invocations_workspace_created_at", table_name="llm_invocations"
    )
    op.drop_index(
        "ix_ml_run_events_workflow_run_created_at", table_name="ml_run_events"
    )
    op.drop_index(
        "ix_ml_run_events_workspace_created_at", table_name="ml_run_events"
    )
    op.drop_index("ix_datasets_project_created_at", table_name="datasets")
    op.drop_index("ix_datasets_workspace_created_at", table_name="datasets")
    op.drop_index("ix_workflow_runs_project_created_at", table_name="workflow_runs")
    op.drop_index(
        "ix_workflow_runs_workspace_status_created_at", table_name="workflow_runs"
    )
    op.drop_index(
        "ix_workflow_runs_workspace_created_at", table_name="workflow_runs"
    )
    op.drop_index(
        "ix_experiments_workflow_run_created_at", table_name="experiments"
    )
    op.drop_index("ix_experiments_project_created_at", table_name="experiments")
    op.drop_index(
        "ix_experiments_workspace_status_created_at", table_name="experiments"
    )
    op.drop_index("ix_experiments_workspace_created_at", table_name="experiments")

    for name, table in (
        ("fk_llm_invocations_workspace_experiment", "llm_invocations"),
        ("fk_llm_invocations_workspace_workflow_run", "llm_invocations"),
        ("fk_ml_run_events_workspace_experiment", "ml_run_events"),
        ("fk_ml_run_events_workspace_workflow_run", "ml_run_events"),
        (
            "fk_model_selection_decisions_workspace_runner_up_candidate",
            "model_selection_decisions",
        ),
        (
            "fk_model_selection_decisions_workspace_selected_candidate",
            "model_selection_decisions",
        ),
        ("fk_model_evaluations_workspace_model_version", "model_evaluations"),
        ("fk_model_evaluations_workspace_candidate", "model_evaluations"),
        ("fk_model_versions_workspace_model_asset", "model_versions"),
        ("fk_model_versions_workspace_dataset", "model_versions"),
        ("fk_model_versions_workspace_selected_candidate", "model_versions"),
        ("fk_model_versions_workspace_pipeline_run", "model_versions"),
        ("fk_model_versions_workspace_workflow_run", "model_versions"),
        ("fk_model_versions_workspace_workflow", "model_versions"),
        ("fk_model_assets_workspace_workflow", "model_assets"),
        (
            "fk_data_preparation_decisions_workspace_dataset_column",
            "data_preparation_decisions",
        ),
        (
            "fk_data_quality_findings_workspace_dataset_column",
            "data_quality_findings",
        ),
        ("fk_client_lab_uploads_workspace_artifact", "client_lab_uploads"),
        ("fk_client_lab_uploads_workspace_experiment", "client_lab_uploads"),
        ("fk_client_lab_uploads_workspace_dataset", "client_lab_uploads"),
        ("fk_experiments_workspace_dataset", "experiments"),
        ("fk_experiments_workspace_workflow_run", "experiments"),
        ("fk_workflow_runs_workspace_source_upload", "workflow_runs"),
        ("fk_datasets_workspace_artifact", "datasets"),
        ("fk_datasets_workspace_dataset_asset", "datasets"),
        ("fk_ml_workflows_workspace_workspace_domain", "ml_workflows"),
    ):
        op.drop_constraint(name, table, type_="foreignkey")

    op.drop_constraint(
        "uq_llm_invocations_workspace_id", "llm_invocations", type_="unique"
    )
    op.drop_constraint(
        "uq_ml_run_events_workspace_id", "ml_run_events", type_="unique"
    )
    op.drop_constraint(
        "uq_client_lab_uploads_workspace_id",
        "client_lab_uploads",
        type_="unique",
    )
    op.drop_constraint(
        "uq_model_assets_workspace_id", "model_assets", type_="unique"
    )
    op.drop_constraint(
        "uq_dataset_columns_workspace_id", "dataset_columns", type_="unique"
    )
    op.drop_constraint(
        "uq_workspace_domains_workspace_id",
        "workspace_domains",
        type_="unique",
    )
