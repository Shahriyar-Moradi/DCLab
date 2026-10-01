"""ML state graph vocabulary (ADR 0006): node kinds, ref kinds, SplitPlan CHECKs.

Only names and the SQL CHECK expressions shared by ``db/models.py`` live here.
Alembic 0063 inlines the same strings literally (revisions never import
``app.domain``); the graph, ref and split-plan services arrive in later prompts.
"""

from __future__ import annotations

from app.domain.data_plane import sql_in_clause
from app.domain.execution_requests import FORBIDDEN_REQUEST_PAYLOAD_KEYS

# /v1 node kinds (ADR 0006 §1). Execution envelopes are never nodes.
NODE_KINDS = (
    "project",
    "problem_spec",
    "dataset_version",
    "split_plan",
    "feature_recipe",
    "experiment",
    "candidate",
    "model_selection",
    "model_version",
    "decision_record",
)

# Ref kinds are roles (§1/§2); each points at exactly one typed column.
REF_KINDS = ("problem_spec", "dataset", "split_plan", "feature_recipe", "champion_model")
REF_TARGET_COLUMNS: dict[str, str] = {
    "problem_spec": "problem_spec_id",
    "dataset": "dataset_id",
    "split_plan": "split_plan_id",
    "feature_recipe": "feature_set_version_id",
    "champion_model": "model_version_id",
}
REF_TARGET_NODE_KINDS: dict[str, str] = {
    "problem_spec": "problem_spec",
    "dataset": "dataset_version",
    "split_plan": "split_plan",
    "feature_recipe": "feature_recipe",
    "champion_model": "model_version",
}

SPLIT_PLAN_HOLDOUT_STRATEGIES = (
    "stratified_random",
    "random",
    "group_disjoint",
    "temporal_future",
)
SPLIT_PLAN_EVIDENCE_MAX_BYTES = 32768
STATE_GRAPH_JSON_MAX_BYTES = 16384
HEX_SHA256_PATTERN = "^[0-9a-f]{64}$"

_FORBIDDEN_SQL_ARRAY = ", ".join(f"'{key}'" for key in FORBIDDEN_REQUEST_PAYLOAD_KEYS)


def sql_no_forbidden_keys(column: str) -> str:
    """Top-level forbidden-key backstop (§4); Pydantic schemas are the real gate."""

    return f"NOT jsonb_exists_any({column}, ARRAY[{_FORBIDDEN_SQL_ARRAY}]::text[])"


def sql_exactly_one_named(
    discriminator: str, columns: dict[str, str], *, empty_value: str | None = None
) -> str:
    """Exactly one non-null column, and it is the one the discriminator names.

    With ``empty_value``, that discriminator value instead requires all NULL.
    """

    all_columns = ", ".join(dict.fromkeys(columns.values()))
    named = " OR ".join(
        f"({discriminator} = '{value}' AND {column} IS NOT NULL)"
        for value, column in columns.items()
    )
    clause = f"(num_nonnulls({all_columns}) = 1 AND ({named}))"
    if empty_value is None:
        return clause
    return f"({discriminator} = '{empty_value}' AND num_nonnulls({all_columns}) = 0) OR {clause}"


# --- project_refs (§2) -------------------------------------------------------
CK_PROJECT_REFS_REF_KIND = sql_in_clause("ref_kind", REF_KINDS)
CK_PROJECT_REFS_TARGET_MATCHES_KIND = sql_exactly_one_named("ref_kind", REF_TARGET_COLUMNS)
CK_PROJECT_REFS_VERSION = "version >= 1"

# --- split_plans (§3) --------------------------------------------------------
CK_SPLIT_PLANS_VERSION = "version >= 1"
CK_SPLIT_PLANS_HOLDOUT_STRATEGY = sql_in_clause("holdout_strategy", SPLIT_PLAN_HOLDOUT_STRATEGIES)
CK_SPLIT_PLANS_HOLDOUT_TEST_SIZE = "holdout_test_size > 0 AND holdout_test_size < 1"
CK_SPLIT_PLANS_VALIDATION_FOLDS = "validation_folds >= 2"
CK_SPLIT_PLANS_ROW_COUNTS = (
    "train_row_count >= 0 AND holdout_row_count >= 0 "
    "AND train_row_count + holdout_row_count = row_count"
)
CK_SPLIT_PLANS_PLAN_DIGEST = f"plan_digest ~ '{HEX_SHA256_PATTERN}'"
CK_SPLIT_PLANS_ASSIGNMENT_DIGEST = f"assignment_digest ~ '{HEX_SHA256_PATTERN}'"
CK_SPLIT_PLANS_HOLDOUT_PLAN_DIGEST = f"holdout_plan_digest ~ '{HEX_SHA256_PATTERN}'"
CK_SPLIT_PLANS_PLAN_EVIDENCE_OBJECT = "jsonb_typeof(plan_evidence) = 'object'"
CK_SPLIT_PLANS_PLAN_EVIDENCE_BOUNDED = (
    f"octet_length(CAST(plan_evidence AS TEXT)) <= {SPLIT_PLAN_EVIDENCE_MAX_BYTES}"
)
CK_SPLIT_PLANS_PLAN_EVIDENCE_NO_SECRETS = sql_no_forbidden_keys("plan_evidence")
