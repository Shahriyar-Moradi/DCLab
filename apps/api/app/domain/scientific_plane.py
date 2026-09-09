"""Canonical scientific-plane vocabularies. JSONB compatibility payloads stay in place."""

from __future__ import annotations

from app.domain.data_plane import sql_in_clause

DATA_QUALITY_FINDING_TYPES = (
    "missing_values",
    "duplicates",
    "outlier",
    "high_cardinality",
    "constant",
    "schema_problem",
    "target_leakage",
    "prediction_time_leakage",
)

DATA_QUALITY_SEVERITIES = ("info", "warning", "error", "critical")

PREPARATION_DECISION_SOURCES = (
    "deterministic",
    "user",
    "llm",
    "fallback",
)

FEATURE_STATUSES = ("modeled", "excluded", "dropped")

PREPROCESSING_FIT_SCOPES = (
    "fold_train",
    "all_train",
    "non_learned",
)

FEATURE_LINEAGE_RELATIONSHIPS = ("source",)

HYPERPARAMETER_SOURCES = (
    "default",
    "planner",
    "user",
    "random_search",
    "optuna",
    "other_search",
)

CV_FOLD_RUN_STATUSES = ("completed", "failed", "running")

MODEL_EVALUATION_TYPES = (
    "cross_validation",
    "final_holdout",
    "robustness",
    "slice",
    "calibration",
    "latency",
)

MODEL_EVALUATION_SCOPES = (
    "cv_fold",
    "cv_aggregate",
    "final_holdout",
    "slice",
    "robustness",
    "calibration",
    "latency",
)

MODEL_EVALUATION_STATUSES = ("completed", "failed", "running")

CK_DATA_QUALITY_FINDING_TYPE = sql_in_clause("finding_type", DATA_QUALITY_FINDING_TYPES)
CK_DATA_QUALITY_SEVERITY = sql_in_clause("severity", DATA_QUALITY_SEVERITIES)
CK_PREPARATION_DECISION_SOURCE = sql_in_clause("decision_source", PREPARATION_DECISION_SOURCES)
CK_FEATURE_STATUS = sql_in_clause("status", FEATURE_STATUSES)
CK_PREPROCESSING_FIT_SCOPE = sql_in_clause("fit_scope", PREPROCESSING_FIT_SCOPES)
CK_FEATURE_SET_VERSION_POSITIVE = "version >= 1"
CK_FEATURE_LINEAGE_RELATIONSHIP = sql_in_clause(
    "relationship", FEATURE_LINEAGE_RELATIONSHIPS
)
CK_HYPERPARAMETER_SOURCE = sql_in_clause("source", HYPERPARAMETER_SOURCES)
CK_CV_FOLD_RUN_STATUS = sql_in_clause("status", CV_FOLD_RUN_STATUSES)
CK_MODEL_EVALUATION_TYPE = sql_in_clause("evaluation_type", MODEL_EVALUATION_TYPES)
CK_MODEL_EVALUATION_SCOPE = sql_in_clause("evaluation_scope", MODEL_EVALUATION_SCOPES)
CK_MODEL_EVALUATION_STATUS = sql_in_clause("status", MODEL_EVALUATION_STATUSES)

QUALITY_CODE_TO_FINDING = {
    "missing_values": "missing_values",
    "target_missingness": "missing_values",
    "duplicate_rows": "duplicates",
    "constant_column": "constant",
    "near_constant_column": "constant",
    "high_cardinality": "high_cardinality",
    "infinite_values": "schema_problem",
    "target_imbalance": "schema_problem",
    "insufficient_samples": "schema_problem",
}

MISSING_ACTION_TO_STRATEGY = {
    "impute_median": "median_imputation",
    "impute_most_frequent": "most_frequent",
    "drop_column": "drop_column",
    "drop_row": "drop_row",
    "domain_fill": "domain_fill",
    "keep": "keep",
    "keep_with_warning": "keep",
}

LEDGER_SOURCE_TO_DECISION_SOURCE = {
    "rule": "deterministic",
    "llm": "llm",
    "fallback": "fallback",
    "deterministic": "deterministic",
    "user": "user",
}
