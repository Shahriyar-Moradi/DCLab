"""Canonical visualization metadata. Specs are small; bytes live in artifacts.

This module does not generate charts. visualization_type values are reserved
vocabulary for Studio/MCP/notebook/report later. PostgreSQL accepts any slug
matching the format check so a new chart kind does not need a schema redesign.
"""

from __future__ import annotations

VISUALIZATION_TYPES = (
    "distribution",
    "missingness",
    "correlation",
    "confusion_matrix",
    "roc_curve",
    "precision_recall_curve",
    "calibration_curve",
    "feature_importance",
    "shap_summary",
    "residual_plot",
    "candidate_comparison",
    "cv_fold_comparison",
    "threshold_tradeoff",
)

SPEC_VERSION_DEFAULT = "1"
SPEC_MAX_BYTES = 16384
SPEC_MAX_ARRAY_LENGTH = 32

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
