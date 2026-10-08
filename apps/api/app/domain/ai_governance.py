"""AI governance vocabulary (ADR 0009 §2.5-§2.7, §2.9, §2.11; ADR 0008 §3).

Names and the SQL CHECK expressions ``db/models.py`` uses for ``ai_policies``,
``decision_point_policies``, ``workspace_llm_budgets``, ``ai_incidents`` and
``ai_switches``. Alembic 0072 inlines the same strings. Platform rows have
``workspace_id IS NULL``; the services live in ``app/agents/governance``.
"""

from __future__ import annotations

from app.domain.agent_records import (
    CURRENCY_PATTERN,
    sql_array,
    sql_digest,
    sql_key,
    sql_object,
)
from app.domain.data_plane import sql_in_clause

POLICY_STATES = ("proposed", "accepted", "rejected")
POLICY_CHANGE_KINDS = ("policy", "budget", "seed")
LEVEL_ACTOR_KINDS = ("human", "rule")
BUDGET_SCOPES = ("workspace", "project", "run_kind")
BUDGET_RUN_KINDS = ("assistant_turn", "assistant_thread", "specialist", "jev")
BUDGET_PERIODS = ("month", "day", "run")
INCIDENT_KINDS = (
    "validator_rejections",
    "eval_failure",
    "budget_exhausted",
    "provider_failure",
    "data_exposure",
    "revert_rate",
    "drift",
    "replay_mismatch",
    "reconciliation",
    "manual",
)
INCIDENT_SUBJECT_KINDS = ("decision_point", "agent", "purpose", "provider", "workspace")
INCIDENT_ACTIONS = ("none", "auto_demote", "switch_off", "breaker_open")
INCIDENT_STATUSES = ("open", "resolved", "closed")
SWITCH_STATES = ("on", "off")
SWITCH_KEY_PATTERN = (
    "^(all_ai|global_ai|agent:[a-z][a-z0-9_]{0,57}|provider:[a-z][a-z0-9_]{0,54}"
    "|purpose:[a-z][a-z0-9_.:-]{0,55})$"
)
POLICY_MAX_BYTES = 32768
EVIDENCE_MAX_ITEMS = 64
EVIDENCE_MAX_BYTES = 16384
INCIDENT_EVIDENCE_MAX_BYTES = 16384

# --- ai_policies (§2.5) --------------------------------------------------------
CK_AI_POLICIES_VERSION = "version >= 1 AND (base_version IS NULL OR base_version >= 1)"
CK_AI_POLICIES_STATE = sql_in_clause("state", POLICY_STATES)
CK_AI_POLICIES_POLICY = sql_object("policy", POLICY_MAX_BYTES)
CK_AI_POLICIES_DIGEST = sql_digest("policy_digest")
CK_AI_POLICIES_SCHEMA_VERSION = "schema_version >= 1"
CK_AI_POLICIES_CHANGE_KIND = sql_in_clause("change_kind", POLICY_CHANGE_KINDS)
CK_AI_POLICIES_SEED = "change_kind <> 'seed' OR (workspace_id IS NULL AND state = 'accepted')"
CK_AI_POLICIES_RATIONALE = "char_length(btrim(rationale)) > 0"
CK_AI_POLICIES_EVIDENCE = sql_array("evidence", EVIDENCE_MAX_BYTES, max_items=EVIDENCE_MAX_ITEMS)
CK_AI_POLICIES_ACTOR = (
    "(state <> 'proposed' OR proposed_by_user_id IS NOT NULL) "
    "AND (state = 'proposed' OR change_kind = 'seed' OR decided_by_user_id IS NOT NULL) "
    "AND (NOT self_approved OR (state = 'accepted' AND decided_by_user_id = proposed_by_user_id))"
)
CK_AI_POLICIES_SUPERSEDES = (
    "(supersedes_id IS NULL) = (state = 'proposed' OR change_kind = 'seed') "
    "AND (supersedes_id IS NULL OR supersedes_id <> id)"
)

# --- decision_point_policies (§2.6) --------------------------------------------
CK_DPP_KEY = sql_key("decision_point_key")
CK_DPP_LEVEL = "level BETWEEN 0 AND 3 AND cap_level BETWEEN 0 AND 3 AND level <= cap_level"
CK_DPP_PAIR = (
    "(prompt_release_id IS NULL) = (model_id IS NULL) "
    "AND (level = 0 OR prompt_release_id IS NOT NULL)"
)
CK_DPP_STATE = sql_in_clause("state", POLICY_STATES)
CK_DPP_ACTOR_KIND = sql_in_clause("actor_kind", LEVEL_ACTOR_KINDS)
CK_DPP_ACTOR = (
    "(actor_kind = 'human' AND actor_user_id IS NOT NULL AND actor_rule IS NULL) "
    "OR (actor_kind = 'rule' AND actor_rule IS NOT NULL AND actor_user_id IS NULL)"
)
CK_DPP_ACTOR_RULE = sql_key("actor_rule", nullable=True)
CK_DPP_RATIONALE = "char_length(btrim(rationale)) > 0"
CK_DPP_EVIDENCE = sql_array("evidence", EVIDENCE_MAX_BYTES, max_items=EVIDENCE_MAX_ITEMS)
# A proposal names the accepted head it would supersede (the base checked at accept).
CK_DPP_CHAIN = "supersedes_id IS NULL OR supersedes_id <> id"
CK_DPP_SELF_APPROVED = "NOT self_approved OR (state = 'accepted' AND decided_by_user_id = actor_user_id)"

# --- workspace_llm_budgets (§2.7) ----------------------------------------------
CK_BUDGETS_SCOPE = sql_in_clause("scope", BUDGET_SCOPES)
CK_BUDGETS_PERIOD = sql_in_clause("period", BUDGET_PERIODS)
CK_BUDGETS_SHAPE = (
    "(scope = 'project') = (project_id IS NOT NULL) "
    "AND (scope = 'run_kind') = (run_kind IS NOT NULL) "
    "AND (scope = 'run_kind') = (period = 'run') "
    f"AND (run_kind IS NULL OR {sql_in_clause('run_kind', BUDGET_RUN_KINDS)})"
)
CK_BUDGETS_AMOUNTS = (
    "limit_micros >= 0 AND reserved_micros >= 0 AND spent_micros >= 0 AND calls >= 0 "
    f"AND currency ~ '{CURRENCY_PATTERN}' AND alert_fraction > 0 AND alert_fraction <= 1"
)

# --- ai_incidents (§2.9) -------------------------------------------------------
CK_INCIDENTS_KIND = sql_in_clause("kind", INCIDENT_KINDS)
CK_INCIDENTS_SUBJECT_KIND = sql_in_clause("subject_kind", INCIDENT_SUBJECT_KINDS)
CK_INCIDENTS_SUBJECT_KEY = "char_length(btrim(subject_key)) > 0"
CK_INCIDENTS_EVIDENCE = sql_object("evidence", INCIDENT_EVIDENCE_MAX_BYTES)
CK_INCIDENTS_ACTION = sql_in_clause("action", INCIDENT_ACTIONS)
CK_INCIDENTS_STATUS = sql_in_clause("status", INCIDENT_STATUSES)
CK_INCIDENTS_RESOLVED = "(status = 'open') = (resolved_at IS NULL)"
CK_INCIDENTS_SWITCH_OFF_RESOLVER = "status = 'open' OR action <> 'switch_off' OR resolved_by_user_id IS NOT NULL"
CK_INCIDENTS_ACTION_LINKS = (
    "(action_switch_id IS NULL OR action = 'switch_off') "
    "AND (action_level_policy_id IS NULL OR action = 'auto_demote')"
)

# --- ai_switches (§2.11) -------------------------------------------------------
CK_SWITCHES_KEY = (
    f"switch_key ~ '{SWITCH_KEY_PATTERN}' "
    "AND (switch_key <> 'global_ai' OR workspace_id IS NULL) "
    "AND (switch_key <> 'all_ai' OR workspace_id IS NOT NULL)"
)
CK_SWITCHES_STATE = sql_in_clause("state", SWITCH_STATES)
CK_SWITCHES_ACTOR = "num_nonnulls(changed_by_user_id, actor_rule) = 1"
CK_SWITCHES_ACTOR_RULE = sql_key("actor_rule", nullable=True)
CK_SWITCHES_REASON = "char_length(btrim(reason)) > 0"
CK_SWITCHES_CHAIN = "supersedes_id IS NULL OR supersedes_id <> id"
# Rules only switch off; the platform seed is the one rule-written ``on``.
CK_SWITCHES_RULE_OFF = "actor_rule IS NULL OR state = 'off' OR actor_rule = 'governance.seed.v1'"

# --- r3_runs (P6.8-A; ADR 0008 §4: promotion evidence is a stored, server-side R3 run) ----
R3_REPORT_MAX_BYTES = 512 * 1024  # bounded body: a deliberate exception to "large bodies go to object storage"
CK_R3_RUNS_CANDIDATE = "char_length(btrim(candidate)) > 0"
CK_R3_RUNS_DIGESTS = "content_digest ~ '^[0-9a-f]{64}$' AND run_digest ~ '^[0-9a-f]{64}$'"
CK_R3_RUNS_REPORT = (
    f"jsonb_typeof(report) = 'object' AND octet_length(report::text) <= {R3_REPORT_MAX_BYTES}"
)
CK_R3_RUNS_LIVE = "live = (left(candidate, 5) = 'live:')"
# the operator clock (created_at) may not run ahead of the database clock (recorded_at)
CK_R3_RUNS_CREATED_AT = "created_at <= recorded_at + interval '5 minutes'"
