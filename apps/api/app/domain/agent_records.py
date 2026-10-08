"""Agent persistence vocabulary (ADR 0009 §2.1-§2.4, §2.8, §2.10; ADR 0008 §2b).

Names, bounds and the SQL CHECK expressions ``db/models.py`` uses for
``prompt_releases``, ``agent_runs``, ``agent_events``, ``agent_proposals``,
``semantic_decision_answers`` and the additive ``llm_invocations`` columns.
Alembic 0071 inlines the same strings (revisions never import ``app.*``).
Services (gateway, harness, proposals) arrive in later prompts; nothing here
writes rows.
"""

from __future__ import annotations

from app.domain.data_plane import sql_in_clause
from app.domain.state_graph import HEX_SHA256_PATTERN, sql_no_forbidden_keys

# Registry / purpose / decision-point keys (ADR 0009 §2 preamble).
KEY_PATTERN = "^[a-z][a-z0-9_.:-]{0,63}$"
CODE_PATTERN = "^[a-z][a-z0-9_]{0,63}$"
CURRENCY_PATTERN = "^[A-Z]{3}$"

# ADR 0008 §2b: the holdout never enters an AI call, so ``holdout`` is not storable.
OUTCOME_SCOPES = ("none", "cv")
# ADR 0009 §8, ordered; ``raw_rows`` has no code path and is not a valid value.
DATA_CLASSES = ("metadata", "aggregates", "sample_values")
AGENT_ROLES = ("lead", "specialist", "legacy_decision", "verifier")

PROMPT_RELEASE_STATUSES = ("draft", "released", "retired")

AGENT_RUN_KINDS = ("assistant", "lead", "specialist", "ops")
AGENT_RUNTIMES = ("fake", "nooa_predict", "lead_loop")
AGENT_RUN_STATUSES = (
    "queued",
    "running",
    "waiting_user",
    "completed",
    "failed",
    "rejected_by_validator",
    "over_budget",
    "timed_out",
    "cancelled",
    "closed",
)
# The gateway's release (ADR 0009 §4) is the only live -> terminal transition; a terminal
# status is final and ``budget_released_at`` is stamped with it (Alembic 0073).
AGENT_RUN_LIVE_STATUSES = ("queued", "running", "waiting_user")
AGENT_RUN_TERMINAL_STATUSES = tuple(s for s in AGENT_RUN_STATUSES if s not in AGENT_RUN_LIVE_STATUSES)
# ADR 0006 subject kinds that have a typed column here, plus kinds without one.
AGENT_SUBJECT_COLUMNS: dict[str, str] = {
    "problem_spec": "problem_spec_id",
    "dataset_version": "dataset_id",
    "split_plan": "split_plan_id",
    "experiment": "experiment_id",
    "model_version": "model_version_id",
}
AGENT_SUBJECT_KINDS_WITHOUT_COLUMN = ("project", "thread", "monitoring_window")
AGENT_SUBJECT_KINDS = (*AGENT_SUBJECT_COLUMNS, *AGENT_SUBJECT_KINDS_WITHOUT_COLUMN)

# ADR 0009 §5.4.
AGENT_EVENT_TYPES = (
    "run_started",
    "context_built",
    "budget_reserved",
    "user_message",
    "llm_call_started",
    "llm_call_finished",
    "step_validated",
    "step_rejected",
    "tool_call_requested",
    "tool_call_denied",
    "tool_call_finished",
    "proposal_created",
    "proposal_auto_applied",
    "assistant_message",
    "clarification_requested",
    "budget_exhausted",
    "budget_settled",
    "run_finished",
    "run_failed",
    "thread_closed",
    "replay_checked",
)

PROPOSAL_TYPES = (
    "ExperimentPlanProposal",
    "ExperimentReviewProposal",
    "DatasetInvestigationProposal",
    "ImprovementActionProposal",
    "ToolCallProposal",
    "ReleaseProposal",
    # P6.9-A (Alembic 0075): a Jev L1 disagreement at a decision point; no agent run.
    "SemanticReviewProposal",
)
# Proposal types only DCLab code writes (never an agent's draft).
SERVICE_ONLY_PROPOSAL_TYPES = frozenset({"ToolCallProposal", "SemanticReviewProposal"})
VALIDATOR_VERDICTS = ("accepted", "rejected")
PROPOSAL_STATUSES = (
    "shadow",
    "proposed",
    "rejected_by_validator",
    "accepted",
    "rejected",
    "applied",
    "reverted",
    "superseded",
    "expired",
)
PROPOSAL_INITIAL_STATUSES = ("shadow", "proposed", "rejected_by_validator", "applied")
# The arrows of ADR 0009 §2.3; enforced by the ``agent_proposals_transition`` trigger.
PROPOSAL_TRANSITIONS = (
    ("proposed", "accepted"),
    ("proposed", "rejected"),
    ("proposed", "expired"),
    ("proposed", "superseded"),
    ("accepted", "applied"),
    ("applied", "reverted"),
)
SUPERSEDE_REASONS = ("plan_exists", "results_exist", "newer_proposal")

SEMANTIC_PRIMITIVES = ("noul", "choice", "score")
EVIDENCE_PARTITIONS = ("metadata", "train")
SEMANTIC_AGREEMENTS = ("agree", "disagree", "abstain", "unavailable")
POLICY_OUTCOMES = ("rule", "ai", "review", "human")
LABEL_SOURCES = ("blind", "benchmark", "user_decision_exposed")

# Byte bounds (octet_length of the JSON text).
RUN_LIMITS_MAX_BYTES = 2048
RUN_USAGE_MAX_BYTES = 2048
PAGE_CONTEXT_MAX_BYTES = 1024
EVENT_PAYLOAD_MAX_BYTES = 16384
PROPOSAL_PAYLOAD_MAX_BYTES = 32768
PROPOSAL_RULE_ANSWER_MAX_BYTES = 8192
PROPOSAL_CITATIONS_MAX = 64
PROPOSAL_CITATIONS_MAX_BYTES = 16384
PROPOSAL_REASONS_MAX_BYTES = 4096
TOOL_ARGUMENTS_MAX_BYTES = 8192
SEMANTIC_ANSWER_MAX_BYTES = 2048
SEMANTIC_PROBABILITIES_MAX_BYTES = 4096

# GUCs read by ``prevent_mutation_except_retention()`` (always ``SET LOCAL``).
# All only take effect together with ``dclab.retention_xact`` = the current
# transaction id (``app.db.retention_guard``), so a session-level SET cannot leak.
RETENTION_XACT_GUC = "dclab.retention_xact"
RETENTION_HORIZON_GUC = "dclab.retention_horizon"
RETENTION_WORKSPACE_GUC = "dclab.retention_workspace"
DELETING_WORKSPACE_GUC = "dclab.deleting_workspace"
RETENTION_HORIZON_MIN_DAYS = 30


def sql_object(column: str, max_bytes: int, *, nullable: bool = False) -> str:
    clause = (
        f"jsonb_typeof({column}) = 'object' "
        f"AND octet_length(CAST({column} AS TEXT)) <= {max_bytes} "
        f"AND {sql_no_forbidden_keys(column)}"
    )
    return f"{column} IS NULL OR ({clause})" if nullable else clause


def sql_array(column: str, max_bytes: int, *, max_items: int | None = None) -> str:
    # No forbidden-key CHECK on arrays: ``?|`` on an array tests string elements.
    clause = f"jsonb_typeof({column}) = 'array' AND octet_length(CAST({column} AS TEXT)) <= {max_bytes}"
    if max_items is not None:
        clause += f" AND jsonb_array_length({column}) <= {max_items}"
    return clause


def sql_key(column: str, *, nullable: bool = False, pattern: str = KEY_PATTERN) -> str:
    clause = f"{column} ~ '{pattern}'"
    return f"{column} IS NULL OR {clause}" if nullable else clause


def sql_digest(column: str, *, nullable: bool = False) -> str:
    clause = f"{column} ~ '{HEX_SHA256_PATTERN}'"
    return f"{column} IS NULL OR {clause}" if nullable else clause


def sql_nullable_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IS NULL OR {sql_in_clause(column, values)}"


def sql_subject_matches_kind(*, nullable_kind: bool) -> str:
    named = " OR ".join(
        f"(subject_kind = '{kind}' AND {column} IS NOT NULL)"
        for kind, column in AGENT_SUBJECT_COLUMNS.items()
    )
    columns = ", ".join(AGENT_SUBJECT_COLUMNS.values())
    empty = sql_in_clause("subject_kind", AGENT_SUBJECT_KINDS_WITHOUT_COLUMN)
    if nullable_kind:
        empty = f"subject_kind IS NULL OR {empty}"
    return f"(({empty}) AND num_nonnulls({columns}) = 0) OR (num_nonnulls({columns}) = 1 AND ({named}))"


# --- prompt_releases (§2.8) --------------------------------------------------
CK_PROMPT_RELEASES_AGENT_KEY = sql_key("agent_key")
CK_PROMPT_RELEASES_VERSION = "version >= 1"
CK_PROMPT_RELEASES_PROMPT_DIGEST = sql_digest("prompt_digest")
CK_PROMPT_RELEASES_OUTPUT_SCHEMA_DIGEST = sql_digest("output_schema_digest")
CK_PROMPT_RELEASES_STATUS = sql_in_clause("status", PROMPT_RELEASE_STATUSES)
CK_PROMPT_RELEASES_RELEASED_AT = "status <> 'released' OR released_at IS NOT NULL"

# --- agent_runs (§2.1) -------------------------------------------------------
_AGENT_RUN_SUBJECTS = ", ".join(AGENT_SUBJECT_COLUMNS.values())
CK_AGENT_RUNS_KIND = sql_in_clause("kind", AGENT_RUN_KINDS)
CK_AGENT_RUNS_AGENT_KEY = sql_key("agent_key")
CK_AGENT_RUNS_ASSISTANT = (
    "kind <> 'assistant' OR (agent_key = 'lead' AND created_by_user_id IS NOT NULL "
    "AND created_by_service_token_id IS NULL)"
)
CK_AGENT_RUNS_RUNTIME = sql_in_clause("runtime", AGENT_RUNTIMES)
CK_AGENT_RUNS_PROMPT_RELEASE = "runtime = 'fake' OR prompt_release_id IS NOT NULL"
CK_AGENT_RUNS_PURPOSE = sql_key("purpose")
CK_AGENT_RUNS_DECISION_POINT = sql_key("decision_point_key", nullable=True)
CK_AGENT_RUNS_SUBJECT_KIND = sql_nullable_in("subject_kind", AGENT_SUBJECT_KINDS)
CK_AGENT_RUNS_SUBJECT_MATCHES_KIND = sql_subject_matches_kind(nullable_kind=True)
CK_AGENT_RUNS_SUBJECT_REQUIRES_PROJECT = (
    f"num_nonnulls({_AGENT_RUN_SUBJECTS}) = 0 OR project_id IS NOT NULL"
)
CK_AGENT_RUNS_PROJECT = "project_id IS NOT NULL OR (kind = 'ops' AND subject_kind IS NULL)"
CK_AGENT_RUNS_PARENT_NOT_SELF = "parent_run_id IS NULL OR parent_run_id <> id"
CK_AGENT_RUNS_CONTEXT_DIGEST = sql_digest("context_digest", nullable=True)
CK_AGENT_RUNS_POLICY_DIGEST = sql_digest("policy_digest")
CK_AGENT_RUNS_TOOL_CATALOG_DIGEST = sql_digest("tool_catalog_digest")
CK_AGENT_RUNS_DATA_CLASS = sql_in_clause("data_class", DATA_CLASSES)
CK_AGENT_RUNS_OUTCOME_SCOPE = sql_in_clause("outcome_scope", OUTCOME_SCOPES)
CK_AGENT_RUNS_STATUS = sql_in_clause("status", AGENT_RUN_STATUSES)
CK_AGENT_RUNS_RELEASED_TERMINAL = (
    f"budget_released_at IS NULL OR {sql_in_clause('status', AGENT_RUN_TERMINAL_STATUSES)}"
)
CK_AGENT_RUNS_LIMITS = sql_object("limits", RUN_LIMITS_MAX_BYTES)
CK_AGENT_RUNS_USAGE = sql_object("usage", RUN_USAGE_MAX_BYTES)
CK_AGENT_RUNS_PAGE_CONTEXT = sql_object("page_context", PAGE_CONTEXT_MAX_BYTES, nullable=True)
CK_AGENT_RUNS_MONEY = (
    f"held_micros >= 0 AND cost_micros >= 0 AND currency ~ '{CURRENCY_PATTERN}'"
)
CK_AGENT_RUNS_ERROR_CODE = sql_key("error_code", nullable=True, pattern=CODE_PATTERN)
CK_AGENT_RUNS_IDEMPOTENCY = (
    "(idempotency_key IS NULL) = (idempotency_digest IS NULL) "
    f"AND (idempotency_digest IS NULL OR idempotency_digest ~ '{HEX_SHA256_PATTERN}')"
)
# Mutable columns of §2.1; ``context_digest`` (step 3), ``held_micros`` (step 5) and
# ``budget_released_at`` (step 9, Alembic 0073) are write-once.
AGENT_RUNS_FROZEN_COLUMNS = (
    "id",
    "workspace_id",
    "project_id",
    "kind",
    "agent_key",
    "agent_version",
    "prompt_release_id",
    "runtime",
    "runtime_version",
    "purpose",
    "decision_point_key",
    "subject_kind",
    "experiment_id",
    "dataset_id",
    "problem_spec_id",
    "split_plan_id",
    "model_version_id",
    "parent_run_id",
    "policy_digest",
    "tool_catalog_digest",
    "data_class",
    "outcome_scope",
    "limits",
    "currency",
    "idempotency_key",
    "idempotency_digest",
    "created_by_user_id",
    "created_by_service_token_id",
    "created_at",
)

# --- agent_events (§2.2) -----------------------------------------------------
CK_AGENT_EVENTS_SEQ = "seq >= 1"
CK_AGENT_EVENTS_TYPE = sql_in_clause("type", AGENT_EVENT_TYPES)
CK_AGENT_EVENTS_PAYLOAD = sql_object("payload", EVENT_PAYLOAD_MAX_BYTES)
CK_AGENT_EVENTS_PAYLOAD_DIGEST = sql_digest("payload_digest")

# --- agent_proposals (§2.3) --------------------------------------------------
CK_AGENT_PROPOSALS_DECISION_POINT = sql_key("decision_point_key")
CK_AGENT_PROPOSALS_LEVELS = (
    "level_at_proposal BETWEEN 0 AND 3 AND answer_ceiling BETWEEN 0 AND 3 "
    "AND level_at_proposal <= answer_ceiling"
)
CK_AGENT_PROPOSALS_TYPE = sql_in_clause("proposal_type", PROPOSAL_TYPES)
CK_AGENT_PROPOSALS_SCHEMA_VERSION = "schema_version >= 1"
CK_AGENT_PROPOSALS_PAYLOAD = sql_object("payload", PROPOSAL_PAYLOAD_MAX_BYTES)
CK_AGENT_PROPOSALS_PAYLOAD_DIGEST = sql_digest("payload_digest")
CK_AGENT_PROPOSALS_RULE_ANSWER = sql_object(
    "rule_answer", PROPOSAL_RULE_ANSWER_MAX_BYTES, nullable=True
)
CK_AGENT_PROPOSALS_CITATIONS = sql_array(
    "citations", PROPOSAL_CITATIONS_MAX_BYTES, max_items=PROPOSAL_CITATIONS_MAX
)
CK_AGENT_PROPOSALS_VALIDATOR_VERDICT = sql_in_clause("validator_verdict", VALIDATOR_VERDICTS)
CK_AGENT_PROPOSALS_VALIDATOR_REASONS = sql_array("validator_reasons", PROPOSAL_REASONS_MAX_BYTES)
CK_AGENT_PROPOSALS_STATUS = sql_in_clause("status", PROPOSAL_STATUSES)
CK_AGENT_PROPOSALS_VERDICT_MATCHES_STATUS = (
    "(validator_verdict = 'rejected') = (status = 'rejected_by_validator')"
)
CK_AGENT_PROPOSALS_SUPERSEDE_REASON = (
    f"(status = 'superseded') = (supersede_reason IS NOT NULL) "
    f"AND ({sql_nullable_in('supersede_reason', SUPERSEDE_REASONS)})"
)
CK_AGENT_PROPOSALS_SUBJECT_KIND = sql_in_clause("subject_kind", AGENT_SUBJECT_KINDS)
CK_AGENT_PROPOSALS_SUBJECT_MATCHES_KIND = sql_subject_matches_kind(nullable_kind=False)
CK_AGENT_PROPOSALS_TOOL_CALL = (
    "(proposal_type = 'ToolCallProposal') = (tool_name IS NOT NULL AND tool_arguments IS NOT NULL) "
    "AND (tool_name IS NOT NULL OR tool_arguments IS NULL)"
)
CK_AGENT_PROPOSALS_TOOL_NAME = sql_key("tool_name", nullable=True)
CK_AGENT_PROPOSALS_TOOL_ARGUMENTS = sql_object(
    "tool_arguments", TOOL_ARGUMENTS_MAX_BYTES, nullable=True
)
CK_AGENT_PROPOSALS_ESTIMATES = (
    "(estimated_cost_micros IS NULL OR estimated_cost_micros >= 0) "
    "AND (estimated_duration_s IS NULL OR estimated_duration_s >= 0)"
)
CK_AGENT_PROPOSALS_DECIDED = (
    "status NOT IN ('accepted', 'rejected') "
    "OR (decided_by_user_id IS NOT NULL AND decided_at IS NOT NULL)"
)
CK_AGENT_PROPOSALS_DECIDED_STATUS = (
    "decided_by_user_id IS NULL OR status IN ('accepted', 'rejected', 'applied', 'reverted')"
)
# L0 is evaluation only (shadow); every proposal a person may act on is L1+.
CK_AGENT_PROPOSALS_LEVEL_STATUS = (
    "(level_at_proposal = 0 AND status IN ('shadow', 'rejected_by_validator')) "
    "OR (level_at_proposal >= 1 AND status <> 'shadow')"
)
# ADR 0008: every lead write tool is L1 (confirm card), never auto-applied.
CK_AGENT_PROPOSALS_TOOL_CALL_LEVEL = "proposal_type <> 'ToolCallProposal' OR level_at_proposal <= 1"
CK_AGENT_PROPOSALS_IDEMPOTENCY = CK_AGENT_RUNS_IDEMPOTENCY
# Alembic 0075: an agent run's proposal or a Jev answer's review item (exactly one source);
# a Jev review item is always L1.
CK_AGENT_PROPOSALS_SOURCE = (
    "num_nonnulls(run_id, semantic_answer_id) = 1 "
    "AND (semantic_answer_id IS NOT NULL) = (proposal_type = 'SemanticReviewProposal') "
    "AND (semantic_answer_id IS NULL OR level_at_proposal = 1)"
)
# Columns the transition trigger lets an UPDATE change (payload is immutable).
AGENT_PROPOSALS_MUTABLE_COLUMNS = (
    "status",
    "supersede_reason",
    "decided_by_user_id",
    "decided_at",
    "decision_record_id",
    "applied_decision_record_id",
    "expires_at",
)

# --- semantic_decision_answers (§2.4) ----------------------------------------
CK_SDA_SUBJECT_REQUIRES_PROJECT = "num_nonnulls(experiment_id, dataset_id) = 0 OR project_id IS NOT NULL"
CK_SDA_DECISION_POINT = sql_key("decision_point_key")
CK_SDA_PURPOSE = sql_key("purpose")
CK_SDA_QUESTION_DIGEST = sql_digest("question_digest")
CK_SDA_DATA_CLASS = sql_in_clause("data_class", DATA_CLASSES)
CK_SDA_EVIDENCE_PARTITION = sql_in_clause("evidence_partition", EVIDENCE_PARTITIONS)
CK_SDA_PRIMITIVE = sql_in_clause("primitive", SEMANTIC_PRIMITIVES)
CK_SDA_ANSWER = sql_object("answer", SEMANTIC_ANSWER_MAX_BYTES)
CK_SDA_PROBABILITIES = sql_object(
    "probabilities", SEMANTIC_PROBABILITIES_MAX_BYTES, nullable=True
)
CK_SDA_CONFIDENCE = "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)"
CK_SDA_RULE_ANSWER = sql_object("rule_answer", SEMANTIC_ANSWER_MAX_BYTES, nullable=True)
CK_SDA_AGREEMENT = sql_in_clause("agreement", SEMANTIC_AGREEMENTS)
CK_SDA_LEVEL = "level BETWEEN 0 AND 3"
CK_SDA_POLICY_OUTCOME = sql_in_clause("policy_outcome", POLICY_OUTCOMES)
CK_SDA_VALUE_USED = sql_object("value_used", SEMANTIC_ANSWER_MAX_BYTES, nullable=True)
CK_SDA_LATENCY = "latency_ms IS NULL OR latency_ms >= 0"
CK_SDA_GROUND_TRUTH = sql_object("ground_truth", SEMANTIC_ANSWER_MAX_BYTES, nullable=True)
CK_SDA_LABEL_SOURCE = sql_nullable_in("label_source", LABEL_SOURCES)
CK_SDA_LABEL = (
    "(ground_truth IS NULL AND label_source IS NULL AND labeled_at IS NULL "
    "AND labeled_by_user_id IS NULL AND supersedes_label_id IS NULL AND labels_version = 0) "
    "OR (ground_truth IS NOT NULL AND label_source IS NOT NULL AND labeled_at IS NOT NULL "
    "AND labels_version >= 1)"
)
CK_SDA_SUPERSEDES_NOT_SELF = "supersedes_label_id IS NULL OR supersedes_label_id <> id"
CK_SDA_LABEL_CHAIN = "labels_version = 0 OR supersedes_label_id IS NOT NULL"

# --- llm_invocations additive columns (§2.10) --------------------------------
CK_LLM_INVOCATIONS_PURPOSE_KEY = sql_key("purpose")
CK_LLM_INVOCATIONS_DATA_CLASS = sql_nullable_in("data_class", DATA_CLASSES)
CK_LLM_INVOCATIONS_OUTCOME_SCOPE = sql_nullable_in("outcome_scope", OUTCOME_SCOPES)
CK_LLM_INVOCATIONS_AGENT_ROLE = sql_nullable_in("agent_role", AGENT_ROLES)
CK_LLM_INVOCATIONS_DECISION_POINT = sql_key("decision_point_key", nullable=True)
CK_LLM_INVOCATIONS_REFUSAL_CODE = sql_key("refusal_code", nullable=True, pattern=CODE_PATTERN)
CK_LLM_INVOCATIONS_COST = (
    "(cost_micros IS NULL OR cost_micros >= 0) "
    f"AND (currency IS NULL OR currency ~ '{CURRENCY_PATTERN}')"
)
CK_LLM_INVOCATIONS_AGENT_SCOPE = (
    "(prompt_release_id IS NULL AND agent_run_id IS NULL) "
    "OR (outcome_scope IS NOT NULL AND data_class IS NOT NULL)"
)
# ``agent_run_id`` is frozen too, except that its FK may set it NULL (separate trigger).
LLM_INVOCATIONS_FROZEN_COLUMNS = (
    "id",
    "workspace_id",
    "workflow_run_id",
    "experiment_id",
    "project_id",
    "purpose",
    "prompt_release_id",
    "input_evidence_digest",
    "data_class",
    "outcome_scope",
    "agent_role",
    "decision_point_key",
    "provider_kind",
    "mode",
    "prompt_version",
    "schema_version",
    "llm_used",
    "started_at",
    "created_at",
)
