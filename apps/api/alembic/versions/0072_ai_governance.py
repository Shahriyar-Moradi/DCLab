"""AI governance: policies, decision-point levels, budgets, incidents, kill switches.

Revision ID: 0072_ai_governance
Revises: 0071_agent_runs
Create Date: 2026-10-04

P6.2-A2, ADR 0009 §2 preamble, §2.5-§2.7, §2.9, §2.11, §3 (ADR 0008 §3). Expand-only:

- ``ai_policies``, ``decision_point_policies``, ``ai_switches`` (append-only chains) and
  ``ai_incidents`` (only status / resolution change; action links fill once). Platform
  rows have ``workspace_id IS NULL`` (plain nullable FK, uniques NULLS NOT DISTINCT);
  ``enforce_ai_governance_scope()`` refuses any supersede / incident / action link
  between a platform row and a workspace row (or across workspaces, or across keys).
- ``enforce_decision_point_policy()``: accepted rows chain linearly per (scope, key);
  rule actors only demote (``ck_dpp_rule_only_down``); promotions need a human decider.
- ``workspace_llm_budgets``: tenant counters (CHECK >= 0), identity frozen.
- A1 follow-ups: ``enforce_agent_proposal_transition()`` refuses extending an expired
  proposal; ``guard_llm_invocation_ledger()`` freezes ``provider`` once completed and
  makes ``currency`` write-once. Downgrade restores the 0071 bodies.

Locking: new tables plus two CREATE OR REPLACE FUNCTION; the new FKs briefly take
SHARE ROW EXCLUSIVE on their parents (workspaces, users, projects, prompt_releases)
until commit. No existing row is rewritten; ``lock_timeout`` 5s fails fast.

Downgrade refuses while any row other than the platform seed rows exists (no pre-0072
representation). Repair forward.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0072_ai_governance"
down_revision: Union[str, Sequence[str], None] = "0071_agent_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_KEY = "^[a-z][a-z0-9_.:-]{0,63}$"
_HEX = "^[0-9a-f]{64}$"
_FORBIDDEN = (
    "ARRAY['password', 'secret', 'token', 'api_key', 'access_key', 'credentials', "
    "'authorization', 'rows', 'records', 'dataset_rows', 'csv', 'file_bytes', "
    "'contents']::text[]"
)
_SEED_RULE = "governance.seed.v1"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _object(column: str, max_bytes: int) -> str:
    return (
        f"jsonb_typeof({column}) = 'object' "
        f"AND octet_length(CAST({column} AS TEXT)) <= {max_bytes} "
        f"AND NOT jsonb_exists_any({column}, {_FORBIDDEN})"
    )


def _evidence_array() -> str:
    return (
        "jsonb_typeof(evidence) = 'array' AND octet_length(CAST(evidence AS TEXT)) <= 16384 "
        "AND jsonb_array_length(evidence) <= 64"
    )


def _nullable_key(column: str) -> str:
    return f"{column} IS NULL OR {column} ~ '{_KEY}'"


def _ck(expression: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(expression, name=name)


def _workspace(nullable: bool) -> sa.Column:
    return sa.Column(
        "workspace_id", _UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=nullable
    )


def _user(name: str) -> sa.Column:
    return sa.Column(name, _UUID, sa.ForeignKey("users.id"), nullable=True)


def _ts(name: str, *, default: bool = False) -> sa.Column:
    if default:
        return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def _evidence() -> sa.Column:
    return sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb"))


def _partial_index(name: str, table: str, *columns: str) -> None:
    op.create_index(name, table, list(columns), postgresql_where=sa.text(f"{columns[-1]} IS NOT NULL"))


def _unique_partial(name: str, table: str, columns: list[str], where: str, **kw) -> None:
    op.create_index(name, table, columns, unique=True, postgresql_where=sa.text(where), **kw)



# A1 follow-ups (identical to app.db.integrity *_V2_SQL).
ENFORCE_AGENT_PROPOSAL_TRANSITION_V2_SQL = """
CREATE OR REPLACE FUNCTION enforce_agent_proposal_transition()
RETURNS trigger AS $$
DECLARE
    mutable text[] := ARRAY['status', 'supersede_reason', 'decided_by_user_id', 'decided_at',
        'decision_record_id', 'applied_decision_record_id', 'expires_at'];
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status NOT IN ('shadow', 'proposed', 'rejected_by_validator', 'applied') THEN
            RAISE EXCEPTION 'agent_proposals cannot be created as %', NEW.status;
        END IF;
        IF NEW.status = 'applied' AND NEW.level_at_proposal < 2 THEN
            RAISE EXCEPTION 'agent_proposals: applied needs level 2 or 3, got %', NEW.level_at_proposal;
        END IF;
        IF NEW.decided_by_user_id IS NOT NULL OR NEW.decided_at IS NOT NULL
            OR NEW.decision_record_id IS NOT NULL
            OR (NEW.applied_decision_record_id IS NOT NULL AND NEW.status <> 'applied') THEN
            RAISE EXCEPTION 'agent_proposals: decision columns are set by the decision, not at creation';
        END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - mutable) IS DISTINCT FROM (to_jsonb(OLD) - mutable) THEN
        RAISE EXCEPTION 'agent_proposals: only status and decision columns may change';
    END IF;
    IF (OLD.decision_record_id IS NOT NULL
            AND NEW.decision_record_id IS DISTINCT FROM OLD.decision_record_id)
        OR (OLD.applied_decision_record_id IS NOT NULL
            AND NEW.applied_decision_record_id IS DISTINCT FROM OLD.applied_decision_record_id) THEN
        RAISE EXCEPTION 'agent_proposals: decision record links are write-once';
    END IF;
    IF OLD.status = 'proposed' AND NEW.status IN ('accepted', 'rejected') THEN
        IF NEW.decided_by_user_id IS NULL OR NEW.decided_at IS NULL THEN
            RAISE EXCEPTION 'agent_proposals: a decision needs decided_by_user_id and decided_at';
        END IF;
    ELSIF NEW.decided_by_user_id IS DISTINCT FROM OLD.decided_by_user_id
        OR NEW.decided_at IS DISTINCT FROM OLD.decided_at THEN
        RAISE EXCEPTION 'agent_proposals: decided_* change only when a proposal is accepted or rejected';
    END IF;
    IF NEW.status = OLD.status THEN
        IF OLD.status = 'proposed' THEN
            IF NEW.decision_record_id IS DISTINCT FROM OLD.decision_record_id
                OR NEW.applied_decision_record_id IS DISTINCT FROM OLD.applied_decision_record_id THEN
                RAISE EXCEPTION 'agent_proposals: a proposed row may change only expires_at';
            END IF;
            IF OLD.expires_at IS NOT NULL AND OLD.expires_at <= now()
                AND NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
                RAISE EXCEPTION 'agent_proposals: an expired proposal cannot be extended';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status IN ('accepted', 'applied') THEN
            IF NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
                RAISE EXCEPTION 'agent_proposals: expires_at changes only while proposed';
            END IF;
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'agent_proposals in status % are final', OLD.status;
    END IF;
    IF NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
        RAISE EXCEPTION 'agent_proposals: expires_at changes only while proposed';
    END IF;
    IF OLD.status = 'proposed' AND NEW.status = 'accepted'
        AND OLD.expires_at IS NOT NULL AND OLD.expires_at <= now() THEN
        RAISE EXCEPTION 'agent_proposals: the proposal has expired';
    END IF;
    IF (OLD.status, NEW.status) IN (
        ('proposed', 'accepted'), ('proposed', 'rejected'), ('proposed', 'expired'),
        ('proposed', 'superseded'), ('accepted', 'applied'), ('applied', 'reverted')
    ) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'agent_proposals: illegal transition % -> %', OLD.status, NEW.status;
END;
$$ LANGUAGE plpgsql
"""

GUARD_LLM_INVOCATION_LEDGER_V2_SQL = """
CREATE OR REPLACE FUNCTION guard_llm_invocation_ledger()
RETURNS trigger AS $$
DECLARE
    finalized text[] := ARRAY['status', 'validator_verdict', 'final_decision', 'model', 'provider',
        'cost_micros', 'cache_hit', 'refusal_code', 'provider_resolved_model',
        'provider_request_id', 'budget_reservation_id', 'safe_output', 'completed_at'];
    old_row jsonb := to_jsonb(OLD);
    new_row jsonb := to_jsonb(NEW);
    guarded text;
BEGIN
    IF OLD.safe_output IS NOT NULL AND NEW.safe_output IS NULL THEN
        IF (new_row - 'safe_output') IS DISTINCT FROM (old_row - 'safe_output') THEN
            RAISE EXCEPTION 'llm_invocations: the retention update may only null safe_output';
        END IF;
        RETURN NEW;
    END IF;
    FOREACH guarded IN ARRAY ARRAY['cost_micros', 'currency', 'budget_reservation_id',
        'provider_request_id', 'provider_resolved_model'] LOOP
        IF old_row ->> guarded IS NOT NULL AND new_row -> guarded IS DISTINCT FROM old_row -> guarded THEN
            RAISE EXCEPTION 'llm_invocations.% is write-once', guarded;
        END IF;
    END LOOP;
    IF OLD.budget_settled AND NOT NEW.budget_settled THEN
        RAISE EXCEPTION 'llm_invocations.budget_settled only moves from false to true';
    END IF;
    IF OLD.completed_at IS NOT NULL THEN
        FOREACH guarded IN ARRAY finalized LOOP
            IF new_row -> guarded IS DISTINCT FROM old_row -> guarded THEN
                RAISE EXCEPTION 'llm_invocations.% is final once completed', guarded;
            END IF;
        END LOOP;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# Verbatim 0071 bodies, restored by downgrade.
ENFORCE_AGENT_PROPOSAL_TRANSITION_0071_SQL = """
CREATE OR REPLACE FUNCTION enforce_agent_proposal_transition()
RETURNS trigger AS $$
DECLARE
    mutable text[] := ARRAY['status', 'supersede_reason', 'decided_by_user_id', 'decided_at',
        'decision_record_id', 'applied_decision_record_id', 'expires_at'];
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status NOT IN ('shadow', 'proposed', 'rejected_by_validator', 'applied') THEN
            RAISE EXCEPTION 'agent_proposals cannot be created as %', NEW.status;
        END IF;
        IF NEW.status = 'applied' AND NEW.level_at_proposal < 2 THEN
            RAISE EXCEPTION 'agent_proposals: applied needs level 2 or 3, got %', NEW.level_at_proposal;
        END IF;
        IF NEW.decided_by_user_id IS NOT NULL OR NEW.decided_at IS NOT NULL
            OR NEW.decision_record_id IS NOT NULL
            OR (NEW.applied_decision_record_id IS NOT NULL AND NEW.status <> 'applied') THEN
            RAISE EXCEPTION 'agent_proposals: decision columns are set by the decision, not at creation';
        END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - mutable) IS DISTINCT FROM (to_jsonb(OLD) - mutable) THEN
        RAISE EXCEPTION 'agent_proposals: only status and decision columns may change';
    END IF;
    IF (OLD.decision_record_id IS NOT NULL
            AND NEW.decision_record_id IS DISTINCT FROM OLD.decision_record_id)
        OR (OLD.applied_decision_record_id IS NOT NULL
            AND NEW.applied_decision_record_id IS DISTINCT FROM OLD.applied_decision_record_id) THEN
        RAISE EXCEPTION 'agent_proposals: decision record links are write-once';
    END IF;
    IF OLD.status = 'proposed' AND NEW.status IN ('accepted', 'rejected') THEN
        IF NEW.decided_by_user_id IS NULL OR NEW.decided_at IS NULL THEN
            RAISE EXCEPTION 'agent_proposals: a decision needs decided_by_user_id and decided_at';
        END IF;
    ELSIF NEW.decided_by_user_id IS DISTINCT FROM OLD.decided_by_user_id
        OR NEW.decided_at IS DISTINCT FROM OLD.decided_at THEN
        RAISE EXCEPTION 'agent_proposals: decided_* change only when a proposal is accepted or rejected';
    END IF;
    IF NEW.status = OLD.status THEN
        IF OLD.status = 'proposed' THEN
            IF NEW.decision_record_id IS DISTINCT FROM OLD.decision_record_id
                OR NEW.applied_decision_record_id IS DISTINCT FROM OLD.applied_decision_record_id THEN
                RAISE EXCEPTION 'agent_proposals: a proposed row may change only expires_at';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status IN ('accepted', 'applied') THEN
            IF NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
                RAISE EXCEPTION 'agent_proposals: expires_at changes only while proposed';
            END IF;
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'agent_proposals in status % are final', OLD.status;
    END IF;
    IF NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
        RAISE EXCEPTION 'agent_proposals: expires_at changes only while proposed';
    END IF;
    IF OLD.status = 'proposed' AND NEW.status = 'accepted'
        AND OLD.expires_at IS NOT NULL AND OLD.expires_at <= now() THEN
        RAISE EXCEPTION 'agent_proposals: the proposal has expired';
    END IF;
    IF (OLD.status, NEW.status) IN (
        ('proposed', 'accepted'), ('proposed', 'rejected'), ('proposed', 'expired'),
        ('proposed', 'superseded'), ('accepted', 'applied'), ('applied', 'reverted')
    ) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'agent_proposals: illegal transition % -> %', OLD.status, NEW.status;
END;
$$ LANGUAGE plpgsql
"""

GUARD_LLM_INVOCATION_LEDGER_0071_SQL = """
CREATE OR REPLACE FUNCTION guard_llm_invocation_ledger()
RETURNS trigger AS $$
DECLARE
    finalized text[] := ARRAY['status', 'validator_verdict', 'final_decision', 'model',
        'cost_micros', 'cache_hit', 'refusal_code', 'provider_resolved_model',
        'provider_request_id', 'budget_reservation_id', 'safe_output', 'completed_at'];
    old_row jsonb := to_jsonb(OLD);
    new_row jsonb := to_jsonb(NEW);
    guarded text;
BEGIN
    IF OLD.safe_output IS NOT NULL AND NEW.safe_output IS NULL THEN
        IF (new_row - 'safe_output') IS DISTINCT FROM (old_row - 'safe_output') THEN
            RAISE EXCEPTION 'llm_invocations: the retention update may only null safe_output';
        END IF;
        RETURN NEW;
    END IF;
    FOREACH guarded IN ARRAY ARRAY['cost_micros', 'budget_reservation_id', 'provider_request_id',
        'provider_resolved_model'] LOOP
        IF old_row ->> guarded IS NOT NULL AND new_row -> guarded IS DISTINCT FROM old_row -> guarded THEN
            RAISE EXCEPTION 'llm_invocations.% is write-once', guarded;
        END IF;
    END LOOP;
    IF OLD.budget_settled AND NOT NEW.budget_settled THEN
        RAISE EXCEPTION 'llm_invocations.budget_settled only moves from false to true';
    END IF;
    IF OLD.completed_at IS NOT NULL THEN
        FOREACH guarded IN ARRAY finalized LOOP
            IF new_row -> guarded IS DISTINCT FROM old_row -> guarded THEN
                RAISE EXCEPTION 'llm_invocations.% is final once completed', guarded;
            END IF;
        END LOOP;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# Platform rows (workspace_id NULL) and workspace rows never reference each other:
# args are (column, table, same-key column or '-') triples.
ENFORCE_AI_GOVERNANCE_SCOPE_SQL = """
CREATE OR REPLACE FUNCTION enforce_ai_governance_scope()
RETURNS trigger AS $$
DECLARE
    new_row jsonb := to_jsonb(NEW);
    ref jsonb;
    i integer := 0;
    col text;
    tbl text;
    key_col text;
BEGIN
    WHILE i < TG_NARGS LOOP
        col := TG_ARGV[i];
        tbl := TG_ARGV[i + 1];
        key_col := TG_ARGV[i + 2];
        i := i + 3;
        CONTINUE WHEN new_row ->> col IS NULL;
        CONTINUE WHEN TG_OP = 'UPDATE' AND (to_jsonb(OLD) -> col) IS NOT DISTINCT FROM (new_row -> col);
        EXECUTE format('SELECT to_jsonb(t) FROM %I AS t WHERE id = $1', tbl)
            INTO ref USING (new_row ->> col)::uuid;
        IF ref IS NULL THEN
            RAISE EXCEPTION '%.% references a missing % row', TG_TABLE_NAME, col, tbl;
        END IF;
        IF (ref -> 'workspace_id') IS DISTINCT FROM (new_row -> 'workspace_id') THEN
            RAISE EXCEPTION '%.% must reference a row of the same workspace scope', TG_TABLE_NAME, col;
        END IF;
        IF key_col <> '-' AND (ref -> key_col) IS DISTINCT FROM (new_row -> key_col) THEN
            RAISE EXCEPTION '%.% must keep the same %', TG_TABLE_NAME, col, key_col;
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# ADR 0008 §3 / ADR 0009 §2.6: accepted rows form one chain per (workspace, key);
# rule actors only demote (ck_dpp_rule_only_down); promotions need a human decider.
ENFORCE_DECISION_POINT_POLICY_SQL = """
CREATE OR REPLACE FUNCTION enforce_decision_point_policy()
RETURNS trigger AS $$
DECLARE
    prev_level smallint := 0;
    prev_state text;
    prev_release uuid;
    prev_model text;
    new_pair boolean;
BEGIN
    IF NEW.supersedes_id IS NOT NULL THEN
        SELECT level, state, prompt_release_id, model_id
          INTO prev_level, prev_state, prev_release, prev_model
          FROM decision_point_policies WHERE id = NEW.supersedes_id;
        IF prev_state IS DISTINCT FROM 'accepted' THEN
            RAISE EXCEPTION 'decision_point_policies may only supersede an accepted row';
        END IF;
    END IF;
    new_pair := NEW.level > 0
        AND (NEW.prompt_release_id, NEW.model_id) IS DISTINCT FROM (prev_release, prev_model);
    IF NEW.state = 'accepted' THEN
        IF NEW.actor_kind = 'rule'
            AND (NEW.supersedes_id IS NULL OR NEW.level >= prev_level OR new_pair) THEN
            RAISE EXCEPTION 'ck_dpp_rule_only_down: a rule actor may only lower a level';
        END IF;
        IF (NEW.level > prev_level OR new_pair)
            AND (NEW.actor_kind <> 'human' OR NEW.decided_by_user_id IS NULL) THEN
            RAISE EXCEPTION 'decision_point_policies: a promotion needs a human decider';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# ADR 0009 §2.9: only status / resolved_* / resolution change; the action links are
# filled once (the switch or level row is written after the incident it cites).
ENFORCE_AI_INCIDENT_UPDATE_SQL = """
CREATE OR REPLACE FUNCTION enforce_ai_incident_update()
RETURNS trigger AS $$
DECLARE
    mutable text[] := ARRAY['status', 'resolved_by_user_id', 'resolved_at', 'resolution',
        'action_level_policy_id', 'action_switch_id'];
BEGIN
    IF NEW.action_switch_id IS NOT NULL
        AND (TG_OP = 'INSERT' OR NEW.action_switch_id IS DISTINCT FROM OLD.action_switch_id)
        AND NOT EXISTS (
            SELECT 1 FROM ai_switches WHERE id = NEW.action_switch_id AND incident_id = NEW.id
        ) THEN
        RAISE EXCEPTION 'ai_incidents: the linked switch row must cite this incident';
    END IF;
    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - mutable) IS DISTINCT FROM (to_jsonb(OLD) - mutable) THEN
        RAISE EXCEPTION 'ai_incidents: only status and resolution columns may change';
    END IF;
    IF (OLD.action_level_policy_id IS NOT NULL
            AND NEW.action_level_policy_id IS DISTINCT FROM OLD.action_level_policy_id)
        OR (OLD.action_switch_id IS NOT NULL
            AND NEW.action_switch_id IS DISTINCT FROM OLD.action_switch_id)
        OR (OLD.resolved_at IS NOT NULL AND (NEW.resolved_at IS DISTINCT FROM OLD.resolved_at
            OR NEW.resolved_by_user_id IS DISTINCT FROM OLD.resolved_by_user_id
            OR NEW.resolution IS DISTINCT FROM OLD.resolution)) THEN
        RAISE EXCEPTION 'ai_incidents: action links and resolution are write-once';
    END IF;
    IF OLD.status = 'closed' AND NEW.status <> 'closed' OR OLD.status <> 'open' AND NEW.status = 'open' THEN
        RAISE EXCEPTION 'ai_incidents: illegal status change % -> %', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# ADR 0009 §2.5: a decision row restates exactly the proposal it decides.
ENFORCE_AI_POLICY_DECISION_SQL = """
CREATE OR REPLACE FUNCTION enforce_ai_policy_decision()
RETURNS trigger AS $$
DECLARE
    target ai_policies%ROWTYPE;
BEGIN
    SELECT * INTO target FROM ai_policies WHERE id = NEW.supersedes_id;
    IF NOT FOUND OR target.state <> 'proposed' THEN
        RAISE EXCEPTION 'ai_policies: a decision must supersede a proposed row';
    END IF;
    IF NEW.policy IS DISTINCT FROM target.policy
        OR NEW.policy_digest IS DISTINCT FROM target.policy_digest
        OR NEW.base_version IS DISTINCT FROM target.base_version
        OR NEW.change_kind IS DISTINCT FROM target.change_kind
        OR NEW.schema_version IS DISTINCT FROM target.schema_version
        OR NEW.proposed_by_user_id IS DISTINCT FROM target.proposed_by_user_id THEN
        RAISE EXCEPTION 'ai_policies: a decision must restate its proposal';
    END IF;
    IF NEW.version <= target.version THEN
        RAISE EXCEPTION 'ai_policies: a decision needs a later version than its proposal';
    END IF;
    IF NEW.decided_by_user_id = NEW.proposed_by_user_id AND NOT NEW.self_approved THEN
        RAISE EXCEPTION 'ai_policies: a self-decision must be flagged self_approved';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

AI_GOVERNANCE_FUNCTIONS = (
    ENFORCE_AI_GOVERNANCE_SCOPE_SQL,
    ENFORCE_DECISION_POINT_POLICY_SQL,
    ENFORCE_AI_INCIDENT_UPDATE_SQL,
    ENFORCE_AI_POLICY_DECISION_SQL,
)


def _append_only(table: str) -> tuple[str, str, str]:
    return (
        f"{table}_append_only",
        table,
        f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()",
    )


AI_GOVERNANCE_TRIGGERS: tuple[tuple[str, str, str], ...] = (
    _append_only("ai_policies"),
    (
        "ai_policies_scope",
        "ai_policies",
        "CREATE TRIGGER ai_policies_scope BEFORE INSERT ON ai_policies FOR EACH ROW "
        "EXECUTE FUNCTION enforce_ai_governance_scope('supersedes_id', 'ai_policies', '-')",
    ),
    (
        "ai_policies_decision",
        "ai_policies",
        "CREATE TRIGGER ai_policies_decision BEFORE INSERT ON ai_policies FOR EACH ROW "
        "WHEN (NEW.supersedes_id IS NOT NULL) EXECUTE FUNCTION enforce_ai_policy_decision()",
    ),
    _append_only("decision_point_policies"),
    (
        "decision_point_policies_scope",
        "decision_point_policies",
        "CREATE TRIGGER decision_point_policies_scope BEFORE INSERT ON decision_point_policies "
        "FOR EACH ROW EXECUTE FUNCTION enforce_ai_governance_scope("
        "'supersedes_id', 'decision_point_policies', 'decision_point_key')",
    ),
    (
        "decision_point_policies_levels",
        "decision_point_policies",
        "CREATE TRIGGER decision_point_policies_levels BEFORE INSERT ON decision_point_policies "
        "FOR EACH ROW EXECUTE FUNCTION enforce_decision_point_policy()",
    ),
    _append_only("ai_switches"),
    (
        "ai_switches_scope",
        "ai_switches",
        "CREATE TRIGGER ai_switches_scope BEFORE INSERT ON ai_switches FOR EACH ROW "
        "EXECUTE FUNCTION enforce_ai_governance_scope("
        "'supersedes_id', 'ai_switches', 'switch_key', 'incident_id', 'ai_incidents', '-')",
    ),
    (
        "ai_incidents_update_guard",
        "ai_incidents",
        "CREATE TRIGGER ai_incidents_update_guard BEFORE INSERT OR UPDATE ON ai_incidents "
        "FOR EACH ROW EXECUTE FUNCTION enforce_ai_incident_update()",
    ),
    (
        "ai_incidents_no_delete",
        "ai_incidents",
        "CREATE TRIGGER ai_incidents_no_delete BEFORE DELETE ON ai_incidents "
        "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()",
    ),
    (
        "ai_incidents_scope",
        "ai_incidents",
        "CREATE TRIGGER ai_incidents_scope BEFORE INSERT OR UPDATE OF action_level_policy_id, "
        "action_switch_id ON ai_incidents FOR EACH ROW EXECUTE FUNCTION enforce_ai_governance_scope("
        "'action_level_policy_id', 'decision_point_policies', '-', "
        "'action_switch_id', 'ai_switches', '-')",
    ),
    (
        "workspace_llm_budgets_columns_immutable",
        "workspace_llm_budgets",
        "CREATE TRIGGER workspace_llm_budgets_columns_immutable BEFORE UPDATE "
        "ON workspace_llm_budgets FOR EACH ROW EXECUTE FUNCTION prevent_canonical_column_mutation("
        "'id,workspace_id,scope,project_id,run_kind,period,created_at')",
    ),
)


_DOWNGRADE_PRECHECK_SQL = f"""
LOCK TABLE ai_policies, decision_point_policies, workspace_llm_budgets, ai_incidents,
    ai_switches IN SHARE MODE;
DO $$
DECLARE
    referencing bigint;
BEGIN
    SELECT (SELECT count(*) FROM ai_policies WHERE change_kind <> 'seed')
         + (SELECT count(*) FROM decision_point_policies)
         + (SELECT count(*) FROM workspace_llm_budgets)
         + (SELECT count(*) FROM ai_incidents)
         + (SELECT count(*) FROM ai_switches WHERE actor_rule IS DISTINCT FROM '{_SEED_RULE}')
      INTO referencing;
    IF referencing > 0 THEN
        RAISE EXCEPTION '0072 downgrade refused: % governance rows besides the platform seed; repair forward', referencing;
    END IF;
END
$$
"""


def _create_tables() -> None:
    op.create_table(
        "ai_policies",
        sa.Column("id", _UUID, nullable=False),
        _workspace(True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("policy", postgresql.JSONB(), nullable=False),
        sa.Column("policy_digest", sa.CHAR(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("change_kind", sa.String(16), nullable=False),
        sa.Column("rationale", sa.String(4000), nullable=False),
        _evidence(),
        _user("proposed_by_user_id"),
        _user("decided_by_user_id"),
        sa.Column("self_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("supersedes_id", _UUID, sa.ForeignKey("ai_policies.id"), nullable=True),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "version", name="uq_ai_policies_workspace_version",
            postgresql_nulls_not_distinct=True,
        ),
        _ck("version >= 1 AND (base_version IS NULL OR base_version >= 1)", "ck_ai_policies_version"),
        _ck(_in("state", ("proposed", "accepted", "rejected")), "ck_ai_policies_state"),
        _ck(_object("policy", 32768), "ck_ai_policies_policy"),
        _ck(f"policy_digest ~ '{_HEX}'", "ck_ai_policies_policy_digest"),
        _ck("schema_version >= 1", "ck_ai_policies_schema_version"),
        _ck(_in("change_kind", ("policy", "budget", "seed")), "ck_ai_policies_change_kind"),
        _ck("change_kind <> 'seed' OR (workspace_id IS NULL AND state = 'accepted')", "ck_ai_policies_seed"),
        _ck("char_length(btrim(rationale)) > 0", "ck_ai_policies_rationale"),
        _ck(_evidence_array(), "ck_ai_policies_evidence"),
        _ck(
            "(state <> 'proposed' OR proposed_by_user_id IS NOT NULL) "
            "AND (state = 'proposed' OR change_kind = 'seed' OR decided_by_user_id IS NOT NULL) "
            "AND (NOT self_approved OR (state = 'accepted' AND decided_by_user_id = proposed_by_user_id))",
            "ck_ai_policies_actor",
        ),
        _ck(
            "(supersedes_id IS NULL) = (state = 'proposed' OR change_kind = 'seed') "
            "AND (supersedes_id IS NULL OR supersedes_id <> id)",
            "ck_ai_policies_supersedes",
        ),
    )
    _unique_partial("uq_ai_policies_supersedes_id", "ai_policies", ["supersedes_id"], "supersedes_id IS NOT NULL")
    _partial_index("ix_ai_policies_proposed_by_user_id", "ai_policies", "proposed_by_user_id")
    _partial_index("ix_ai_policies_decided_by_user_id", "ai_policies", "decided_by_user_id")

    op.create_table(
        "decision_point_policies",
        sa.Column("id", _UUID, nullable=False),
        _workspace(True),
        sa.Column("decision_point_key", sa.String(64), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.Column("cap_level", sa.SmallInteger(), nullable=False),
        sa.Column("prompt_release_id", _UUID, sa.ForeignKey("prompt_releases.id"), nullable=True),
        sa.Column("model_id", sa.String(64), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("actor_kind", sa.String(8), nullable=False),
        _user("actor_user_id"),
        sa.Column("actor_rule", sa.String(128), nullable=True),
        _user("decided_by_user_id"),
        sa.Column("rationale", sa.String(4000), nullable=False),
        _evidence(),
        sa.Column("self_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("supersedes_id", _UUID, sa.ForeignKey("decision_point_policies.id"), nullable=True),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id"),
        _ck(f"decision_point_key ~ '{_KEY}'", "ck_dpp_decision_point_key"),
        _ck("level BETWEEN 0 AND 3 AND cap_level BETWEEN 0 AND 3 AND level <= cap_level", "ck_dpp_level"),
        _ck(
            "(prompt_release_id IS NULL) = (model_id IS NULL) "
            "AND (level = 0 OR prompt_release_id IS NOT NULL)",
            "ck_dpp_pair",
        ),
        _ck(_in("state", ("proposed", "accepted", "rejected")), "ck_dpp_state"),
        _ck(_in("actor_kind", ("human", "rule")), "ck_dpp_actor_kind"),
        _ck(
            "(actor_kind = 'human' AND actor_user_id IS NOT NULL AND actor_rule IS NULL) "
            "OR (actor_kind = 'rule' AND actor_rule IS NOT NULL AND actor_user_id IS NULL)",
            "ck_dpp_actor",
        ),
        _ck(_nullable_key("actor_rule"), "ck_dpp_actor_rule"),
        _ck("char_length(btrim(rationale)) > 0", "ck_dpp_rationale"),
        _ck(_evidence_array(), "ck_dpp_evidence"),
        _ck("supersedes_id IS NULL OR supersedes_id <> id", "ck_dpp_chain"),
        _ck(
            "NOT self_approved OR (state = 'accepted' AND decided_by_user_id = actor_user_id)",
            "ck_dpp_self_approved",
        ),
    )
    _unique_partial("uq_dpp_supersedes_id", "decision_point_policies", ["supersedes_id"],
                    "supersedes_id IS NOT NULL AND state = 'accepted'")
    _unique_partial("uq_dpp_chain_root", "decision_point_policies", ["workspace_id", "decision_point_key"],
                    "state = 'accepted' AND supersedes_id IS NULL", postgresql_nulls_not_distinct=True)
    op.create_index("ix_dpp_workspace_key", "decision_point_policies", ["workspace_id", "decision_point_key"])
    _partial_index("ix_dpp_prompt_release_id", "decision_point_policies", "prompt_release_id")
    _partial_index("ix_dpp_actor_user_id", "decision_point_policies", "actor_user_id")
    _partial_index("ix_dpp_decided_by_user_id", "decision_point_policies", "decided_by_user_id")

    op.create_table(
        "workspace_llm_budgets",
        sa.Column("id", _UUID, nullable=False),
        _workspace(False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("project_id", _UUID, nullable=True),
        sa.Column("run_kind", sa.String(16), nullable=True),
        sa.Column("period", sa.String(8), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("limit_micros", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("alert_fraction", sa.Numeric(3, 2), nullable=False, server_default=sa.text("0.80")),
        sa.Column("hard_stop", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("reserved_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("spent_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        _ts("updated_at", default=True),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_workspace_llm_budgets_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "scope", "project_id", "run_kind", "period",
            name="uq_workspace_llm_budgets_scope", postgresql_nulls_not_distinct=True,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
            name="fk_workspace_llm_budgets_workspace_project",
        ),
        _ck(_in("scope", ("workspace", "project", "run_kind")), "ck_workspace_llm_budgets_scope"),
        _ck(_in("period", ("month", "day", "run")), "ck_workspace_llm_budgets_period"),
        _ck(
            "(scope = 'project') = (project_id IS NOT NULL) "
            "AND (scope = 'run_kind') = (run_kind IS NOT NULL) "
            "AND (scope = 'run_kind') = (period = 'run') "
            "AND (run_kind IS NULL OR "
            + _in("run_kind", ("assistant_turn", "assistant_thread", "specialist", "jev"))
            + ")",
            "ck_workspace_llm_budgets_shape",
        ),
        _ck(
            "limit_micros >= 0 AND reserved_micros >= 0 AND spent_micros >= 0 AND calls >= 0 "
            "AND currency ~ '^[A-Z]{3}$' AND alert_fraction > 0 AND alert_fraction <= 1",
            "ck_workspace_llm_budgets_amounts",
        ),
    )
    _partial_index("ix_workspace_llm_budgets_project_id", "workspace_llm_budgets", "workspace_id", "project_id")

    op.create_table(
        "ai_incidents",
        sa.Column("id", _UUID, nullable=False),
        _workspace(True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("subject_kind", sa.String(16), nullable=False),
        sa.Column("subject_key", sa.String(64), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column(
            "action_level_policy_id", _UUID, sa.ForeignKey("decision_point_policies.id"), nullable=True
        ),
        sa.Column("action_switch_id", _UUID, nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        _ts("opened_at", default=True),
        _user("resolved_by_user_id"),
        _ts("resolved_at"),
        sa.Column("resolution", sa.String(2000), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        _ck(
            _in("kind", ("validator_rejections", "eval_failure", "budget_exhausted", "provider_failure",
                         "data_exposure", "revert_rate", "drift", "replay_mismatch", "reconciliation",
                         "manual")),
            "ck_ai_incidents_kind",
        ),
        _ck(_in("subject_kind", ("decision_point", "agent", "purpose", "provider", "workspace")),
            "ck_ai_incidents_subject_kind"),
        _ck("char_length(btrim(subject_key)) > 0", "ck_ai_incidents_subject_key"),
        _ck(_object("evidence", 16384), "ck_ai_incidents_evidence"),
        _ck(_in("action", ("none", "auto_demote", "switch_off", "breaker_open")), "ck_ai_incidents_action"),
        _ck(_in("status", ("open", "resolved", "closed")), "ck_ai_incidents_status"),
        _ck("(status = 'open') = (resolved_at IS NULL)", "ck_ai_incidents_resolved"),
        _ck("status = 'open' OR action <> 'switch_off' OR resolved_by_user_id IS NOT NULL",
            "ck_ai_incidents_switch_off_resolver"),
        _ck(
            "(action_switch_id IS NULL OR action = 'switch_off') "
            "AND (action_level_policy_id IS NULL OR action = 'auto_demote')",
            "ck_ai_incidents_action_links",
        ),
    )
    op.create_index("ix_ai_incidents_workspace_status", "ai_incidents", ["workspace_id", "status"])
    _partial_index("ix_ai_incidents_action_level_policy_id", "ai_incidents", "action_level_policy_id")
    _partial_index("ix_ai_incidents_action_switch_id", "ai_incidents", "action_switch_id")
    _partial_index("ix_ai_incidents_resolved_by_user_id", "ai_incidents", "resolved_by_user_id")

    op.create_table(
        "ai_switches",
        sa.Column("id", _UUID, nullable=False),
        _workspace(True),
        sa.Column("switch_key", sa.String(64), nullable=False),
        sa.Column("state", sa.String(4), nullable=False),
        _user("changed_by_user_id"),
        sa.Column("actor_rule", sa.String(128), nullable=True),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("incident_id", _UUID, sa.ForeignKey("ai_incidents.id"), nullable=True),
        sa.Column("supersedes_id", _UUID, sa.ForeignKey("ai_switches.id"), nullable=True),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id"),
        _ck(
            "switch_key ~ '^(all_ai|global_ai|agent:[a-z][a-z0-9_]{0,57}|provider:[a-z][a-z0-9_]{0,54}"
            "|purpose:[a-z][a-z0-9_.:-]{0,55})$' "
            "AND (switch_key <> 'global_ai' OR workspace_id IS NULL) "
            "AND (switch_key <> 'all_ai' OR workspace_id IS NOT NULL)",
            "ck_ai_switches_key",
        ),
        _ck(_in("state", ("on", "off")), "ck_ai_switches_state"),
        _ck("num_nonnulls(changed_by_user_id, actor_rule) = 1", "ck_ai_switches_actor"),
        _ck(_nullable_key("actor_rule"), "ck_ai_switches_actor_rule"),
        _ck("char_length(btrim(reason)) > 0", "ck_ai_switches_reason"),
        _ck("supersedes_id IS NULL OR supersedes_id <> id", "ck_ai_switches_chain"),
        _ck(f"actor_rule IS NULL OR state = 'off' OR actor_rule = '{_SEED_RULE}'", "ck_ai_switches_rule_off"),
    )
    _unique_partial("uq_ai_switches_supersedes_id", "ai_switches", ["supersedes_id"], "supersedes_id IS NOT NULL")
    _unique_partial("uq_ai_switches_chain_root", "ai_switches", ["workspace_id", "switch_key"],
                    "supersedes_id IS NULL", postgresql_nulls_not_distinct=True)
    op.create_index("ix_ai_switches_workspace_key", "ai_switches", ["workspace_id", "switch_key"])
    _partial_index("ix_ai_switches_incident_id", "ai_switches", "incident_id")
    _partial_index("ix_ai_switches_changed_by_user_id", "ai_switches", "changed_by_user_id")
    op.create_foreign_key(
        "fk_ai_incidents_action_switch", "ai_incidents", "ai_switches", ["action_switch_id"], ["id"]
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(ENFORCE_AGENT_PROPOSAL_TRANSITION_V2_SQL)
    op.execute(GUARD_LLM_INVOCATION_LEDGER_V2_SQL)
    _create_tables()
    for sql in AI_GOVERNANCE_FUNCTIONS:
        op.execute(sql)
    for _name, _table, sql in AI_GOVERNANCE_TRIGGERS:
        op.execute(sql)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(_DOWNGRADE_PRECHECK_SQL)
    op.drop_constraint("fk_ai_incidents_action_switch", "ai_incidents", type_="foreignkey")
    for table in ("ai_switches", "ai_incidents", "workspace_llm_budgets", "decision_point_policies", "ai_policies"):
        op.drop_table(table)
    for function in (
        "enforce_ai_policy_decision",
        "enforce_ai_incident_update",
        "enforce_decision_point_policy",
        "enforce_ai_governance_scope",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}()")
    op.execute(ENFORCE_AGENT_PROPOSAL_TRANSITION_0071_SQL)
    op.execute(GUARD_LLM_INVOCATION_LEDGER_0071_SQL)
