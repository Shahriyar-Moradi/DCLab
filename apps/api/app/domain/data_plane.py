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
    "reproduction_notebook",
    "reproduction_script",
    "predictions",
    "derived_dataset",
    "split_assignment",
)

PREDICTIONS_TYPE = "predictions"
DERIVED_DATASET_TYPE = "derived_dataset"
# Row -> partition/fold map of a SplitPlan (ADR 0006 §3). Canonical CSV, digest-addressed.
SPLIT_ASSIGNMENT_TYPE = "split_assignment"
REPRODUCTION_NOTEBOOK_TYPE = "reproduction_notebook"
REPRODUCTION_SCRIPT_TYPE = "reproduction_script"

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

# P4.9-A: a ``scoring`` upload has no target and is never a training source.
DATASET_PURPOSE_TRAINING = "training"
DATASET_PURPOSE_SCORING = "scoring"
DATASET_PURPOSES = (DATASET_PURPOSE_TRAINING, DATASET_PURPOSE_SCORING)

# 0082: who uploaded a DatasetVersion, as reads show it (no token ids or names). ``connected_tool``
# = ``datasets.uploaded_by_service_token_id`` is set (only a service token sets it); ``person`` = not
# uploaded with a service token: a browser session or the person's own /auth/tokens API token, a row
# from before 0082, or a derived/prepared training dataset (never a scoring file).
UPLOADED_BY_PERSON = "person"
UPLOADED_BY_CONNECTED_TOOL = "connected_tool"
UPLOADED_BY_DESCRIPTION = (
    "Who uploaded the file: `connected_tool` (uploaded with a service token, i.e. by a connected tool or agent) or "
    "`person` (not uploaded with a service token: a person's own sign-in or own API token counts as the person, "
    "as do rows from before attribution existed and derived training datasets, which are never scoring files). "
    "Weekly scheduled scoring scores only files a person uploaded."
)


def uploaded_by(service_token_id: object | None) -> str:
    return UPLOADED_BY_CONNECTED_TOOL if service_token_id is not None else UPLOADED_BY_PERSON

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
CK_DATASETS_PURPOSE = sql_in_clause("purpose", DATASET_PURPOSES)
