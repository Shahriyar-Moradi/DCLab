"""Frozen 0052 visualization check constraints and immutability DDL.

Copied from ``app.domain.visualizations`` at 0052. Do not import the live module.
"""

from __future__ import annotations

SPEC_MAX_BYTES = 16384

FORBIDDEN_SPEC_KEYS = (
    "password",
    "secret",
    "token",
    "api_key",
    "access_key",
    "credentials",
    "authorization",
    "rows",
    "records",
    "dataset_rows",
    "csv",
    "file_bytes",
    "contents",
    "points",
    "values",
    "samples",
    "bins",
    "y_true",
    "y_pred",
    "x_values",
    "y_values",
    "shap_values",
    "probabilities",
    "embeddings",
    "series_data",
)

CK_VISUALIZATIONS_TYPE = "visualization_type ~ '^[a-z][a-z0-9_]{0,63}$'"
CK_VISUALIZATIONS_SPEC_VERSION = "spec_version ~ '^[a-zA-Z0-9._-]{1,32}$'"
CK_VISUALIZATIONS_RENDERER = (
    "renderer_hint IS NULL OR renderer_hint ~ '^[a-z][a-z0-9_]{0,31}$'"
)
CK_VISUALIZATIONS_DIGEST = "content_digest ~ '^[a-f0-9]{64}$'"

_FORBIDDEN_SQL_ARRAY = ", ".join(f"'{key}'" for key in FORBIDDEN_SPEC_KEYS)

CK_VISUALIZATIONS_SPEC_OBJECT = "jsonb_typeof(spec) = 'object'"
CK_VISUALIZATIONS_SPEC_BOUNDED = (
    f"octet_length(CAST(spec AS TEXT)) <= {SPEC_MAX_BYTES}"
)
CK_VISUALIZATIONS_SPEC_NO_BULK = (
    f"NOT jsonb_exists_any(spec, ARRAY[{_FORBIDDEN_SQL_ARRAY}]::text[])"
)

PREVENT_VISUALIZATION_MUTATION_SQL = """
CREATE OR REPLACE FUNCTION prevent_visualization_mutation()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'visualizations rows are immutable';
    END IF;
    IF (to_jsonb(OLD) - 'project_id' - 'pipeline_stage_run_id'
            - 'candidate_id' - 'model_evaluation_id')
        IS DISTINCT FROM
       (to_jsonb(NEW) - 'project_id' - 'pipeline_stage_run_id'
            - 'candidate_id' - 'model_evaluation_id')
    THEN
        RAISE EXCEPTION 'visualizations identity/spec is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

VISUALIZATIONS_IMMUTABLE_TRIGGER_SQL = """
CREATE TRIGGER visualizations_immutable
BEFORE UPDATE OR DELETE ON visualizations
FOR EACH ROW EXECUTE FUNCTION prevent_visualization_mutation()
"""
