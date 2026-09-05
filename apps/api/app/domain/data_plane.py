"""Canonical data-plane vocabularies. Bytes live in object storage, not PostgreSQL."""

from __future__ import annotations

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

LABS_PROJECT_SLUG = "labs"
LABS_PROJECT_NAME = "Labs"


def sql_in_clause(column: str, values: tuple[str, ...]) -> str:
    inner = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({inner})"


CK_ARTIFACTS_TYPE = sql_in_clause("artifact_type", ARTIFACT_TYPES)
CK_ARTIFACTS_PROVIDER = sql_in_clause("provider", OBJECT_STORAGE_PROVIDERS)
CK_DATA_SOURCES_TYPE = sql_in_clause("source_type", DATA_SOURCE_TYPES)
CK_DATA_SOURCES_STATUS = sql_in_clause("status", DATA_SOURCE_STATUSES)
CK_INGESTION_RUNS_STATUS = sql_in_clause("status", INGESTION_RUN_STATUSES)
