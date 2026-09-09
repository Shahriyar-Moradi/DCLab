"""Frozen 0030 data-plane check-constraint vocabularies.

Copied from ``app.domain.data_plane`` at 0030. Reproduction artifact types are
added later by 0045; do not import the live module.
"""

from __future__ import annotations

from alembic_frozen.clauses import sql_in_clause

ARTIFACT_TYPES = (
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

DATA_SOURCE_TYPES = (
    "upload",
    "database",
    "object_storage",
    "api",
    "crm",
    "logs",
    "other",
)

OBJECT_STORAGE_PROVIDERS = ("local", "s3", "gcs")

DATA_SOURCE_STATUSES = ("active", "disabled", "error")

INGESTION_RUN_STATUSES = ("queued", "running", "completed", "failed")

CK_ARTIFACTS_TYPE = sql_in_clause("artifact_type", ARTIFACT_TYPES)
CK_ARTIFACTS_PROVIDER = sql_in_clause("provider", OBJECT_STORAGE_PROVIDERS)
CK_DATA_SOURCES_TYPE = sql_in_clause("source_type", DATA_SOURCE_TYPES)
CK_DATA_SOURCES_STATUS = sql_in_clause("status", DATA_SOURCE_STATUSES)
CK_INGESTION_RUNS_STATUS = sql_in_clause("status", INGESTION_RUN_STATUSES)
