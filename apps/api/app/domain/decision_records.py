"""ProjectDecisionRecord vocabulary (ADR 0006 §5). Append-only project memory.

``superseded`` is derived from ``supersedes_id`` and never stored. The record
service (``record``/``accept``/``reject``) is P2.5-A; this module holds only the
code-owned allowlists and the SQL CHECK expressions ``db/models.py`` uses.
"""

from __future__ import annotations

from uuid import UUID

from app.domain.data_plane import sql_in_clause
from app.domain.state_graph import (
    STATE_GRAPH_JSON_MAX_BYTES,
    sql_exactly_one_named,
    sql_no_forbidden_keys,
)

DECISION_TYPES = (
    "winner_locked",
    "split_plan_created",
    "ref_initialized",
    "problem_spec_locked",
    "ref_moved",
    "champion_promoted",
    "experiment_accepted",
    "experiment_rejected",
    "proposal_accepted",
    "proposal_rejected",
)
DECISION_WINNER_LOCKED = "winner_locked"

DECISION_STATES = ("proposed", "accepted", "rejected")
STATE_ACCEPTED = "accepted"
EFFECTIVE_STATE_SUPERSEDED = "superseded"

SUBJECT_KINDS = (
    "project",
    "problem_spec",
    "dataset_version",
    "split_plan",
    "feature_recipe",
    "experiment",
    "candidate",
    "model_version",
)
# subject_kind -> the typed subject column (``project`` has none).
SUBJECT_COLUMNS: dict[str, str] = {
    "problem_spec": "problem_spec_id",
    "dataset_version": "dataset_id",
    "split_plan": "split_plan_id",
    "feature_recipe": "feature_set_version_id",
    "experiment": "experiment_id",
    "candidate": "candidate_id",
    "model_version": "model_version_id",
}

ACTOR_KINDS = ("human", "rule", "agent")
ACTOR_RULE = "rule"

DECISION_POLICY_VERSION = "dclab.decisions.v1"
RULE_WINNER_LOCKED = "selection.cv_winner.v1"
RULE_WINNER_LOCKED_BACKFILL = "selection.cv_winner.backfill.v1"
RULE_REFS_BOOTSTRAP = "refs.bootstrap.v1"
WINNER_LOCKED_SCHEMA_VERSION = 1

RATIONALE_MAX_CHARS = 4000
EVIDENCE_REFS_MAX = 64
IDEMPOTENCY_KEY_MAX_CHARS = 128


def winner_locked_idempotency_key(model_selection_decision_id: UUID | str) -> str:
    """One ``winner_locked`` record per ModelSelection, whichever path writes it."""

    return f"winner_locked:{model_selection_decision_id}"


CK_PDR_DECISION_TYPE = "decision_type ~ '^[a-z][a-z0-9_]{0,63}$'"
CK_PDR_STATE = sql_in_clause("state", DECISION_STATES)
CK_PDR_SUBJECT_KIND = sql_in_clause("subject_kind", SUBJECT_KINDS)
CK_PDR_SUBJECT_MATCHES_KIND = sql_exactly_one_named(
    "subject_kind", SUBJECT_COLUMNS, empty_value="project"
)
CK_PDR_SUBJECT_DIGEST = "subject_digest IS NULL OR subject_digest ~ '^[0-9a-f]{1,64}$'"
CK_PDR_ACTOR_KIND = sql_in_clause("actor_kind", ACTOR_KINDS)
CK_PDR_ACTOR = (
    "(actor_kind = 'human' AND actor_user_id IS NOT NULL AND actor_rule IS NULL "
    "AND actor_agent_run_id IS NULL AND actor_service_token_id IS NULL) "
    "OR (actor_kind = 'rule' AND actor_rule IS NOT NULL "
    "AND actor_agent_run_id IS NULL AND actor_service_token_id IS NULL) "
    "OR (actor_kind = 'agent' "
    "AND (actor_agent_run_id IS NOT NULL OR actor_service_token_id IS NOT NULL))"
)
CK_PDR_RATIONALE = "char_length(btrim(rationale)) > 0"
CK_PDR_RATIONALE_UNTRUSTED = "rationale_untrusted = (actor_kind = 'agent')"
CK_PDR_FACTS_OBJECT = "jsonb_typeof(facts) = 'object'"
CK_PDR_FACTS_BOUNDED = f"octet_length(CAST(facts AS TEXT)) <= {STATE_GRAPH_JSON_MAX_BYTES}"
CK_PDR_FACTS_NO_SECRETS = sql_no_forbidden_keys("facts")
# No forbidden-key CHECK on evidence_refs: it is an array of objects, where a
# top-level key test is meaningless; the Pydantic write schema is the gate.
CK_PDR_EVIDENCE_REFS_ARRAY = (
    "CASE WHEN jsonb_typeof(evidence_refs) = 'array' "
    f"THEN jsonb_array_length(evidence_refs) <= {EVIDENCE_REFS_MAX} ELSE false END"
)
CK_PDR_EVIDENCE_REFS_BOUNDED = (
    f"octet_length(CAST(evidence_refs AS TEXT)) <= {STATE_GRAPH_JSON_MAX_BYTES}"
)
CK_PDR_DETAILS_OBJECT = "jsonb_typeof(details) = 'object'"
CK_PDR_DETAILS_BOUNDED = f"octet_length(CAST(details AS TEXT)) <= {STATE_GRAPH_JSON_MAX_BYTES}"
CK_PDR_DETAILS_NO_SECRETS = sql_no_forbidden_keys("details")
CK_PDR_SCHEMA_VERSION = "schema_version >= 1"
CK_PDR_SUPERSEDES_NOT_SELF = "supersedes_id IS NULL OR supersedes_id <> id"
