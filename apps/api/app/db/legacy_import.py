"""Frozen 0036 compatibility-project backfill targets.

This list is the tables that existed when ``0036_legacy_import_projects`` ran
and that carry nullable ``workspace_id`` + ``project_id``. Later tables such as
``pipeline_scientific_plans`` and ``ml_jobs`` are not included: they are created
after 0036 and receive ``project_id`` at insert time.
"""

from __future__ import annotations

# Deterministic, explicit. Do not discover tables from information_schema.
LEGACY_IMPORT_BACKFILL_TABLES: tuple[str, ...] = (
    "artifacts",
    "code_snapshots",
    "cv_fold_runs",
    "data_preparation_decisions",
    "data_quality_findings",
    "data_sources",
    "dataset_assets",
    "datasets",
    "experiment_candidates",
    "experiments",
    "ml_workflows",
    "model_evaluations",
    "model_selection_decisions",
    "model_versions",
    "pipeline_stage_runs",
    "preprocessing_steps",
    "workflow_runs",
)
