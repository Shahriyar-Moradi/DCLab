"""Frozen 0036 compatibility-project backfill constants.

Copied from ``app.db.legacy_import`` and ``app.domain.workspace_identity`` at
0036. Do not import the live modules; later tables must not join this list.
"""

from __future__ import annotations

LEGACY_IMPORT_PROJECT_SLUG = "legacy-import"
LEGACY_IMPORT_PROJECT_NAME = "Legacy import"
LEGACY_IMPORT_PROJECT_DESCRIPTION = (
    "Compatibility project for historical records that had no Project. "
    "Attaching multiple Workflows here does not mean they were the same case study."
)
PROJECT_PROVENANCE_SYSTEM_LEGACY_IMPORT = "system_legacy_import"

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
