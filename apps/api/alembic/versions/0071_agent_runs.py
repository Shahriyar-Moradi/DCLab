"""Agent persistence: prompt releases, agent runs, events, proposals, semantic answers.

Revision ID: 0071_agent_runs
Revises: 0070_investigation_findings
Create Date: 2026-10-04

P6.2-A1, ADR 0009 §2.1-§2.4, §2.8, §2.10 (ADR 0008 §2b, §7). Expand-only:

- ``prompt_releases`` (platform rows; identity frozen, never deleted).
- ``agent_runs``, ``agent_events``, ``agent_proposals``, ``semantic_decision_answers``:
  ``workspace_id`` cascades, ``UNIQUE(workspace_id, id)``, every tenant reference a
  composite FK (three-column ``(workspace_id, project_id, x_id)`` for subject nodes,
  NO ACTION); CHECKs make subject ids imply ``project_id`` (MATCH SIMPLE would
  skip the FK otherwise) and keep ``outcome_scope`` to ``none``/``cv`` (never
  holdout). JSONB columns are bounded objects (arrays where stated) with the ADR
  0006 forbidden-key backstop.
- New helper ``prevent_mutation_except_retention()``: UPDATE refused; DELETE only
  inside a transaction stamped with ``dclab.retention_xact = pg_current_xact_id()``
  and either ``dclab.retention_horizon`` (>= 30 days) + ``dclab.retention_workspace``
  covering the row, or ``dclab.deleting_workspace`` equal to ``workspace_id``. Used
  for UPDATE/DELETE of ``agent_events`` and ``semantic_decision_answers``, DELETE of
  ``agent_runs``, ``agent_proposals`` and ``llm_invocations``, and nulling
  ``llm_invocations.safe_output``. ``force_created_at_now()`` stamps ``created_at``
  on insert for every retention-guarded table (no backdating).
  ``enforce_agent_proposal_transition()`` allows only the decision columns to change,
  along the §2.3 arrows, with write-once decision fields.
- ``guard_llm_invocation_ledger()``: once ``completed_at`` is set the outcome and
  ledger columns are final; cost / reservation / provider ids are write-once;
  ``budget_settled`` only false -> true (settle may follow finalize).
- ``llm_invocations``: gateway ledger columns (nullable or constant default:
  metadata-only, no rewrite), ``ck_llm_invocations_purpose`` becomes the key regex
  (the six legacy purposes match), composite FK to ``agent_runs`` ``ON DELETE SET
  NULL (agent_run_id)`` (PostgreSQL 15+ column list keeps ``workspace_id``), identity
  columns frozen. Because ``experiments``/``workflow_runs`` cascade into
  ``llm_invocations``, deleting one with invocations now needs the deletion GUC.
- ``project_decision_records.actor_agent_run_id``: composite FK, NO ACTION.

Locking: one transaction; ``lock_timeout`` 5s. ``ADD COLUMN`` holds ACCESS EXCLUSIVE
on ``llm_invocations`` and the new FKs hold SHARE ROW EXCLUSIVE on their parents
until commit (``NOT VALID`` + ``VALIDATE`` here only names a pre-existing dangling
``agent_run_id`` / ``actor_agent_run_id``; nothing writes them before this revision).
Run with the worker stopped.

Known limit: ``ON DELETE SET NULL (agent_run_id)`` fails ``ck_llm_invocations_attributed``
for an invocation attributed only to its run (fails closed); retention deletes
invocations before runs.

Downgrade refuses while any agent row, prompt release, gateway purpose or ledger
value exists (no pre-0071 representation). Repair forward.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0071_agent_runs"
down_revision: Union[str, Sequence[str], None] = "0070_investigation_findings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KEY = "^[a-z][a-z0-9_.:-]{0,63}$"
_CODE = "^[a-z][a-z0-9_]{0,63}$"
_HEX = "^[0-9a-f]{64}$"
_FORBIDDEN = (
    "ARRAY['password', 'secret', 'token', 'api_key', 'access_key', 'credentials', "
    "'authorization', 'rows', 'records', 'dataset_rows', 'csv', 'file_bytes', "
    "'contents']::text[]"
)
_LEGACY_PURPOSES = (
    "'semantic_target', 'semantic_missing_value', 'semantic_column_type', "
    "'semantic_leakage', 'pipeline_audit_routine', 'pipeline_audit_deep'"
)
_DATA_CLASSES = ("metadata", "aggregates", "sample_values")
_OUTCOME_SCOPES = ("none", "cv")
_SUBJECT_COLUMNS = (
    ("problem_spec", "problem_spec_id"),
    ("dataset_version", "dataset_id"),
    ("split_plan", "split_plan_id"),
    ("experiment", "experiment_id"),
    ("model_version", "model_version_id"),
)
_SUBJECT_KINDS_WITHOUT_COLUMN = ("project", "thread", "monitoring_window")
_SUBJECT_KINDS = tuple(kind for kind, _ in _SUBJECT_COLUMNS) + _SUBJECT_KINDS_WITHOUT_COLUMN
_SUBJECT_TABLES = (
    ("experiment_id", "experiments"),
    ("dataset_id", "datasets"),
    ("problem_spec_id", "problem_specs"),
    ("split_plan_id", "split_plans"),
    ("model_version_id", "model_versions"),
)
_EVENT_TYPES = (
    "run_started", "context_built", "budget_reserved", "user_message", "llm_call_started",
    "llm_call_finished", "step_validated", "step_rejected", "tool_call_requested",
    "tool_call_denied", "tool_call_finished", "proposal_created", "proposal_auto_applied",
    "assistant_message", "clarification_requested", "budget_exhausted", "budget_settled",
    "run_finished", "run_failed", "thread_closed", "replay_checked",
)
_RUN_STATUSES = (
    "queued", "running", "waiting_user", "completed", "failed", "rejected_by_validator",
    "over_budget", "timed_out", "cancelled", "closed",
)
_PROPOSAL_TYPES = (
    "ExperimentPlanProposal", "ExperimentReviewProposal", "DatasetInvestigationProposal",
    "ImprovementActionProposal", "ToolCallProposal", "ReleaseProposal",
)
_PROPOSAL_STATUSES = (
    "shadow", "proposed", "rejected_by_validator", "accepted", "rejected", "applied",
    "reverted", "superseded", "expired",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _nullable_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IS NULL OR {_in(column, values)}"


def _key(column: str, *, nullable: bool = False, pattern: str = _KEY) -> str:
    clause = f"{column} ~ '{pattern}'"
    return f"{column} IS NULL OR {clause}" if nullable else clause


def _digest(column: str, *, nullable: bool = False) -> str:
    return _key(column, nullable=nullable, pattern=_HEX)


def _object(column: str, max_bytes: int, *, nullable: bool = False) -> str:
    clause = (
        f"jsonb_typeof({column}) = 'object' "
        f"AND octet_length(CAST({column} AS TEXT)) <= {max_bytes} "
        f"AND NOT jsonb_exists_any({column}, {_FORBIDDEN})"
    )
    return f"{column} IS NULL OR ({clause})" if nullable else clause


def _array(column: str, max_bytes: int, *, max_items: int | None = None) -> str:
    clause = f"jsonb_typeof({column}) = 'array' AND octet_length(CAST({column} AS TEXT)) <= {max_bytes}"
    if max_items is not None:
        clause += f" AND jsonb_array_length({column}) <= {max_items}"
    return clause


def _subject_matches_kind(*, nullable_kind: bool) -> str:
    named = " OR ".join(
        f"(subject_kind = '{kind}' AND {column} IS NOT NULL)" for kind, column in _SUBJECT_COLUMNS
    )
    columns = ", ".join(column for _, column in _SUBJECT_COLUMNS)
    empty = _in("subject_kind", _SUBJECT_KINDS_WITHOUT_COLUMN)
    if nullable_kind:
        empty = f"subject_kind IS NULL OR {empty}"
    return f"(({empty}) AND num_nonnulls({columns}) = 0) OR (num_nonnulls({columns}) = 1 AND ({named}))"


_IDEMPOTENCY = (
    "(idempotency_key IS NULL) = (idempotency_digest IS NULL) "
    f"AND (idempotency_digest IS NULL OR idempotency_digest ~ '{_HEX}')"
)


def _ck(expression: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(expression, name=name)


def _fk(prefix: str, column: str, table: str, *, project: bool = False) -> sa.ForeignKeyConstraint:
    local = ["workspace_id", "project_id", column] if project else ["workspace_id", column]
    remote = [f"{table}.{name}" for name in (local[:-1] + ["id"])]
    middle = "workspace_project" if project else "workspace"
    return sa.ForeignKeyConstraint(local, remote, name=f"fk_{prefix}_{middle}_{column.removesuffix('_id')}")


def _project_fk(prefix: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
        name=f"fk_{prefix}_workspace_project",
    )


def _partial_index(name: str, table: str, *columns: str) -> None:
    op.create_index(name, table, list(columns), postgresql_where=sa.text(f"{columns[-1]} IS NOT NULL"))


_UUID = postgresql.UUID(as_uuid=True)


def _uuid(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, _UUID, nullable=nullable)


def _workspace() -> sa.Column:
    return sa.Column(
        "workspace_id", _UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )


def _ts(name: str, *, nullable: bool = True, default: bool = False) -> sa.Column:
    if default:
        return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _subject_columns() -> list[sa.Column]:
    return [_uuid(column) for column, _ in _SUBJECT_TABLES]


def _subject_fks(prefix: str) -> list[sa.ForeignKeyConstraint]:
    return [_fk(prefix, column, table, project=True) for column, table in _SUBJECT_TABLES]


def _subject_indexes(prefix: str, table: str) -> None:
    for column, _ in _SUBJECT_TABLES:
        _partial_index(f"ix_{prefix}_{column}", table, column)


PREVENT_MUTATION_EXCEPT_RETENTION_SQL = """
CREATE OR REPLACE FUNCTION prevent_mutation_except_retention()
RETURNS trigger AS $$
DECLARE
    xact text := coalesce(current_setting('dclab.retention_xact', true), '');
    horizon text := coalesce(current_setting('dclab.retention_horizon', true), '');
    retention_workspace text := coalesce(current_setting('dclab.retention_workspace', true), '');
    deleting text := coalesce(current_setting('dclab.deleting_workspace', true), '');
    permitted boolean := false;
BEGIN
    IF TG_OP = 'DELETE' OR (TG_NARGS > 0 AND TG_ARGV[0] = 'retention_update') THEN
        IF xact <> '' THEN
            IF xact = pg_current_xact_id()::text THEN
                IF horizon <> '' AND retention_workspace <> '' THEN
                    IF horizon::interval >= interval '30 days' THEN
                        permitted := coalesce(
                            retention_workspace::uuid = OLD.workspace_id
                            AND OLD.created_at < now() - horizon::interval,
                            false
                        );
                    END IF;
                END IF;
                IF NOT permitted AND deleting <> '' THEN
                    permitted := coalesce(deleting::uuid = OLD.workspace_id, false);
                END IF;
            END IF;
        END IF;
    END IF;
    IF NOT permitted THEN
        RAISE EXCEPTION '% rows are append-only outside retention', TG_TABLE_NAME;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

ENFORCE_AGENT_PROPOSAL_TRANSITION_SQL = """
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

# Frozen identity is a separate column trigger; this guards the ledger outcome.
GUARD_LLM_INVOCATION_LEDGER_SQL = """
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

# Only the FK's ON DELETE SET NULL (agent_run_id) may clear the link: the run is gone.
PREVENT_LLM_INVOCATION_AGENT_RUN_CLEAR_SQL = """
CREATE OR REPLACE FUNCTION prevent_llm_invocation_agent_run_clear()
RETURNS trigger AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM agent_runs WHERE workspace_id = OLD.workspace_id AND id = OLD.agent_run_id
    ) THEN
        RAISE EXCEPTION 'llm_invocations.agent_run_id is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# Retention compares created_at, so rows can never be inserted backdated.
FORCE_CREATED_AT_NOW_SQL = """
CREATE OR REPLACE FUNCTION force_created_at_now()
RETURNS trigger AS $$
BEGIN
    NEW.created_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

AGENT_PERSISTENCE_FUNCTIONS = (
    PREVENT_MUTATION_EXCEPT_RETENTION_SQL,
    ENFORCE_AGENT_PROPOSAL_TRANSITION_SQL,
    GUARD_LLM_INVOCATION_LEDGER_SQL,
    PREVENT_LLM_INVOCATION_AGENT_RUN_CLEAR_SQL,
    FORCE_CREATED_AT_NOW_SQL,
)


def _created_at_trigger(table: str) -> tuple[str, str, str]:
    return (
        f"{table}_created_at_now",
        table,
        f"CREATE TRIGGER {table}_created_at_now BEFORE INSERT ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION force_created_at_now()",
    )


def _retention_delete_trigger(table: str) -> tuple[str, str, str]:
    return (
        f"{table}_retention_delete",
        table,
        f"CREATE TRIGGER {table}_retention_delete BEFORE DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation_except_retention()",
    )


AGENT_PERSISTENCE_TRIGGERS: tuple[tuple[str, str, str], ...] = (
    (
        "prompt_releases_columns_immutable",
        "prompt_releases",
        "CREATE TRIGGER prompt_releases_columns_immutable BEFORE UPDATE ON prompt_releases "
        "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_column_mutation("
        "'id,agent_key,version,prompt_digest,output_schema_digest,created_at')",
    ),
    (
        # draft -> released -> retired only; released_at is write-once.
        "prompt_releases_status_transition",
        "prompt_releases",
        "CREATE TRIGGER prompt_releases_status_transition BEFORE UPDATE OF status, released_at "
        "ON prompt_releases FOR EACH ROW WHEN ((NEW.status IS DISTINCT FROM OLD.status "
        "AND NOT ((OLD.status = 'draft' AND NEW.status = 'released') "
        "OR (OLD.status = 'released' AND NEW.status = 'retired'))) "
        "OR (OLD.released_at IS NOT NULL AND NEW.released_at IS DISTINCT FROM OLD.released_at)) "
        "EXECUTE FUNCTION prevent_canonical_row_mutation()",
    ),
    (
        "prompt_releases_no_delete",
        "prompt_releases",
        "CREATE TRIGGER prompt_releases_no_delete BEFORE DELETE ON prompt_releases "
        "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()",
    ),
    (
        "agent_runs_columns_immutable",
        "agent_runs",
        "CREATE TRIGGER agent_runs_columns_immutable BEFORE UPDATE ON agent_runs "
        "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_column_mutation("
        "'id,workspace_id,project_id,kind,agent_key,agent_version,prompt_release_id,runtime,"
        "runtime_version,purpose,decision_point_key,subject_kind,experiment_id,dataset_id,"
        "problem_spec_id,split_plan_id,model_version_id,parent_run_id,policy_digest,"
        "tool_catalog_digest,data_class,outcome_scope,limits,currency,"
        "idempotency_key,idempotency_digest,created_by_user_id,created_by_service_token_id,"
        "created_at')",
    ),
    (
        "agent_runs_context_digest_write_once",
        "agent_runs",
        "CREATE TRIGGER agent_runs_context_digest_write_once BEFORE UPDATE OF context_digest "
        "ON agent_runs FOR EACH ROW WHEN (OLD.context_digest IS NOT NULL "
        "AND NEW.context_digest IS DISTINCT FROM OLD.context_digest) "
        "EXECUTE FUNCTION prevent_canonical_row_mutation()",
    ),
    (
        # ADR 0009 §5.1 step 5 reserves the hold after the row exists.
        "agent_runs_held_micros_write_once",
        "agent_runs",
        "CREATE TRIGGER agent_runs_held_micros_write_once BEFORE UPDATE OF held_micros "
        "ON agent_runs FOR EACH ROW WHEN (OLD.held_micros <> 0 "
        "AND NEW.held_micros IS DISTINCT FROM OLD.held_micros) "
        "EXECUTE FUNCTION prevent_canonical_row_mutation()",
    ),
    _created_at_trigger("agent_runs"),
    _retention_delete_trigger("agent_runs"),
    (
        "agent_events_append_only",
        "agent_events",
        "CREATE TRIGGER agent_events_append_only BEFORE UPDATE OR DELETE ON agent_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation_except_retention()",
    ),
    _created_at_trigger("agent_events"),
    (
        "agent_proposals_transition",
        "agent_proposals",
        "CREATE TRIGGER agent_proposals_transition BEFORE INSERT OR UPDATE ON agent_proposals "
        "FOR EACH ROW EXECUTE FUNCTION enforce_agent_proposal_transition()",
    ),
    _created_at_trigger("agent_proposals"),
    _retention_delete_trigger("agent_proposals"),
    (
        "semantic_decision_answers_append_only",
        "semantic_decision_answers",
        "CREATE TRIGGER semantic_decision_answers_append_only BEFORE UPDATE OR DELETE "
        "ON semantic_decision_answers "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation_except_retention()",
    ),
    _created_at_trigger("semantic_decision_answers"),
    (
        "llm_invocations_columns_immutable",
        "llm_invocations",
        "CREATE TRIGGER llm_invocations_columns_immutable BEFORE UPDATE ON llm_invocations "
        "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_column_mutation("
        "'id,workspace_id,workflow_run_id,experiment_id,project_id,purpose,prompt_release_id,"
        "input_evidence_digest,data_class,outcome_scope,agent_role,decision_point_key,"
        "provider_kind,mode,prompt_version,schema_version,llm_used,started_at,created_at')",
    ),
    (
        # Never set to (another) run after insert.
        "llm_invocations_agent_run_frozen",
        "llm_invocations",
        "CREATE TRIGGER llm_invocations_agent_run_frozen BEFORE UPDATE OF agent_run_id "
        "ON llm_invocations FOR EACH ROW WHEN (NEW.agent_run_id IS NOT NULL "
        "AND NEW.agent_run_id IS DISTINCT FROM OLD.agent_run_id) "
        "EXECUTE FUNCTION prevent_canonical_row_mutation()",
    ),
    (
        "llm_invocations_agent_run_clear_by_fk",
        "llm_invocations",
        "CREATE TRIGGER llm_invocations_agent_run_clear_by_fk BEFORE UPDATE OF agent_run_id "
        "ON llm_invocations FOR EACH ROW WHEN (OLD.agent_run_id IS NOT NULL "
        "AND NEW.agent_run_id IS NULL) "
        "EXECUTE FUNCTION prevent_llm_invocation_agent_run_clear()",
    ),
    (
        "llm_invocations_ledger_guard",
        "llm_invocations",
        "CREATE TRIGGER llm_invocations_ledger_guard BEFORE UPDATE ON llm_invocations "
        "FOR EACH ROW EXECUTE FUNCTION guard_llm_invocation_ledger()",
    ),
    _created_at_trigger("llm_invocations"),
    _retention_delete_trigger("llm_invocations"),
    (
        "llm_invocations_safe_output_retention",
        "llm_invocations",
        "CREATE TRIGGER llm_invocations_safe_output_retention BEFORE UPDATE OF safe_output "
        "ON llm_invocations FOR EACH ROW WHEN (OLD.safe_output IS NOT NULL "
        "AND NEW.safe_output IS NULL) "
        "EXECUTE FUNCTION prevent_mutation_except_retention('retention_update')",
    ),
)


def _llm_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("prompt_release_id", _UUID, nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.CHAR(3), nullable=True),
        sa.Column("data_class", sa.String(16), nullable=True),
        sa.Column("outcome_scope", sa.String(8), nullable=True),
        sa.Column("agent_role", sa.String(16), nullable=True),
        sa.Column("decision_point_key", sa.String(64), nullable=True),
        sa.Column("refusal_code", sa.String(64), nullable=True),
        sa.Column("budget_reservation_id", _UUID, nullable=True),
        sa.Column("budget_settled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("provider_request_id", sa.String(128), nullable=True),
        sa.Column("provider_resolved_model", sa.String(128), nullable=True),
    )


_LLM_CHECKS = (
    ("ck_llm_invocations_data_class", _nullable_in("data_class", _DATA_CLASSES)),
    ("ck_llm_invocations_outcome_scope", _nullable_in("outcome_scope", _OUTCOME_SCOPES)),
    (
        "ck_llm_invocations_agent_role",
        _nullable_in("agent_role", ("lead", "specialist", "legacy_decision", "verifier")),
    ),
    ("ck_llm_invocations_decision_point_key", _key("decision_point_key", nullable=True)),
    ("ck_llm_invocations_refusal_code", _key("refusal_code", nullable=True, pattern=_CODE)),
    (
        "ck_llm_invocations_cost",
        "(cost_micros IS NULL OR cost_micros >= 0) "
        "AND (currency IS NULL OR currency ~ '^[A-Z]{3}$')",
    ),
    (
        "ck_llm_invocations_agent_scope",
        "(prompt_release_id IS NULL AND agent_run_id IS NULL) "
        "OR (outcome_scope IS NOT NULL AND data_class IS NOT NULL)",
    ),
)

_DOWNGRADE_PRECHECK_SQL = f"""
LOCK TABLE prompt_releases, agent_runs, agent_events, agent_proposals, semantic_decision_answers,
    llm_invocations IN SHARE MODE;
DO $$
DECLARE
    referencing bigint;
BEGIN
    SELECT (SELECT count(*) FROM prompt_releases)
         + (SELECT count(*) FROM agent_runs)
         + (SELECT count(*) FROM agent_events)
         + (SELECT count(*) FROM agent_proposals)
         + (SELECT count(*) FROM semantic_decision_answers)
         + (SELECT count(*) FROM llm_invocations
             WHERE purpose NOT IN ({_LEGACY_PURPOSES})
                OR cache_hit OR budget_settled
                OR num_nonnulls(prompt_release_id, cost_micros, currency, data_class, outcome_scope,
                    agent_role, decision_point_key, refusal_code, budget_reservation_id,
                    provider_request_id, provider_resolved_model) > 0)
      INTO referencing;
    IF referencing > 0 THEN
        RAISE EXCEPTION '0071 downgrade refused: % rows depend on 0071 (agent tables, prompt releases or gateway ledger values); repair forward', referencing;
    END IF;
END
$$
"""


def _create_prompt_releases() -> None:
    op.create_table(
        "prompt_releases",
        _uuid("id", nullable=False),
        sa.Column("agent_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("prompt_digest", sa.CHAR(64), nullable=False),
        sa.Column("output_schema_digest", sa.CHAR(64), nullable=False),
        sa.Column("model_hint", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        _ts("released_at"),
        sa.Column("notes", sa.String(2000), nullable=True),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_key", "version", name="uq_prompt_releases_agent_key_version"),
        _ck(_key("agent_key"), "ck_prompt_releases_agent_key"),
        _ck("version >= 1", "ck_prompt_releases_version"),
        _ck(_digest("prompt_digest"), "ck_prompt_releases_prompt_digest"),
        _ck(_digest("output_schema_digest"), "ck_prompt_releases_output_schema_digest"),
        _ck(_in("status", ("draft", "released", "retired")), "ck_prompt_releases_status"),
        _ck("status <> 'released' OR released_at IS NOT NULL", "ck_prompt_releases_released_at"),
    )


def _create_agent_runs() -> None:
    subjects = ", ".join(column for _, column in _SUBJECT_COLUMNS)
    op.create_table(
        "agent_runs",
        _uuid("id", nullable=False),
        _workspace(),
        _uuid("project_id"),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("agent_key", sa.String(64), nullable=False),
        sa.Column("agent_version", sa.String(32), nullable=False),
        sa.Column(
            "prompt_release_id", _UUID,
            sa.ForeignKey("prompt_releases.id", name="fk_agent_runs_prompt_release"), nullable=True,
        ),
        sa.Column("runtime", sa.String(16), nullable=False),
        sa.Column("runtime_version", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("decision_point_key", sa.String(64), nullable=True),
        sa.Column("subject_kind", sa.String(32), nullable=True),
        *_subject_columns(),
        _uuid("parent_run_id"),
        sa.Column("context_digest", sa.CHAR(64), nullable=True),
        sa.Column("policy_digest", sa.CHAR(64), nullable=False),
        sa.Column("tool_catalog_digest", sa.CHAR(64), nullable=False),
        sa.Column("data_class", sa.String(16), nullable=False),
        sa.Column("outcome_scope", sa.String(8), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("limits", postgresql.JSONB(), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("held_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("currency", sa.CHAR(3), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("idempotency_digest", sa.CHAR(64), nullable=True),
        sa.Column("created_by_user_id", _UUID, sa.ForeignKey("users.id"), nullable=True),
        _uuid("created_by_service_token_id"),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("page_context", postgresql.JSONB(), nullable=True),
        _ts("last_activity_at", default=True),
        _ts("started_at"),
        _ts("finished_at"),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_agent_runs_workspace_id"),
        _project_fk("agent_runs"),
        *_subject_fks("agent_runs"),
        _fk("agent_runs", "parent_run_id", "agent_runs"),
        _fk("agent_runs", "created_by_service_token_id", "service_tokens"),
        _ck(_in("kind", ("assistant", "lead", "specialist", "ops")), "ck_agent_runs_kind"),
        _ck(_key("agent_key"), "ck_agent_runs_agent_key"),
        _ck(
            "kind <> 'assistant' OR (agent_key = 'lead' AND created_by_user_id IS NOT NULL "
            "AND created_by_service_token_id IS NULL)",
            "ck_agent_runs_assistant",
        ),
        _ck(_in("runtime", ("fake", "nooa_predict", "lead_loop")), "ck_agent_runs_runtime"),
        _ck("runtime = 'fake' OR prompt_release_id IS NOT NULL", "ck_agent_runs_prompt_release"),
        _ck(_key("purpose"), "ck_agent_runs_purpose"),
        _ck(_key("decision_point_key", nullable=True), "ck_agent_runs_decision_point_key"),
        _ck(_nullable_in("subject_kind", _SUBJECT_KINDS), "ck_agent_runs_subject_kind"),
        _ck(_subject_matches_kind(nullable_kind=True), "ck_agent_runs_subject_matches_kind"),
        _ck(
            f"num_nonnulls({subjects}) = 0 OR project_id IS NOT NULL",
            "ck_agent_runs_subject_requires_project",
        ),
        _ck("project_id IS NOT NULL OR (kind = 'ops' AND subject_kind IS NULL)", "ck_agent_runs_project"),
        _ck("parent_run_id IS NULL OR parent_run_id <> id", "ck_agent_runs_parent_not_self"),
        _ck(_digest("context_digest", nullable=True), "ck_agent_runs_context_digest"),
        _ck(_digest("policy_digest"), "ck_agent_runs_policy_digest"),
        _ck(_digest("tool_catalog_digest"), "ck_agent_runs_tool_catalog_digest"),
        _ck(_in("data_class", _DATA_CLASSES), "ck_agent_runs_data_class"),
        _ck(_in("outcome_scope", _OUTCOME_SCOPES), "ck_agent_runs_outcome_scope"),
        _ck(_in("status", _RUN_STATUSES), "ck_agent_runs_status"),
        _ck(_object("limits", 2048), "ck_agent_runs_limits"),
        _ck(_object("usage", 2048), "ck_agent_runs_usage"),
        _ck(_object("page_context", 1024, nullable=True), "ck_agent_runs_page_context"),
        _ck(
            "held_micros >= 0 AND cost_micros >= 0 AND currency ~ '^[A-Z]{3}$'",
            "ck_agent_runs_money",
        ),
        _ck(_key("error_code", nullable=True, pattern=_CODE), "ck_agent_runs_error_code"),
        _ck(_IDEMPOTENCY, "ck_agent_runs_idempotency"),
    )
    op.create_index(
        "ix_agent_runs_workspace_project_created_at", "agent_runs",
        ["workspace_id", "project_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_agent_runs_workspace_kind_status", "agent_runs", ["workspace_id", "kind", "status"])
    op.create_index("ix_agent_runs_agent_key_created_at", "agent_runs", ["agent_key", sa.text("created_at DESC")])
    op.create_index(
        "ix_agent_runs_assistant_user_activity", "agent_runs",
        ["created_by_user_id", sa.text("last_activity_at DESC")],
        postgresql_where=sa.text("kind = 'assistant'"),
    )
    op.create_index(
        "uq_agent_runs_active_subject", "agent_runs",
        ["workspace_id", "agent_key", "subject_kind", "project_id", "experiment_id", "dataset_id",
         "problem_spec_id", "split_plan_id", "model_version_id"],
        unique=True, postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("status IN ('queued', 'running') AND kind <> 'assistant'"),
    )
    op.create_index(
        "uq_agent_runs_workspace_idempotency_key", "agent_runs", ["workspace_id", "idempotency_key"],
        unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index("ix_agent_runs_created_by_user_id", "agent_runs", ["created_by_user_id"])
    _subject_indexes("agent_runs", "agent_runs")
    _partial_index("ix_agent_runs_parent_run_id", "agent_runs", "parent_run_id")
    _partial_index("ix_agent_runs_prompt_release_id", "agent_runs", "prompt_release_id")
    _partial_index("ix_agent_runs_created_by_service_token_id", "agent_runs", "created_by_service_token_id")


def _create_agent_events() -> None:
    op.create_table(
        "agent_events",
        _uuid("id", nullable=False),
        _workspace(),
        _uuid("run_id", nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(48), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_digest", sa.CHAR(64), nullable=False),
        _uuid("llm_invocation_id"),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_agent_events_workspace_id"),
        sa.UniqueConstraint("run_id", "seq", name="uq_agent_events_run_seq"),
        _fk("agent_events", "run_id", "agent_runs"),
        _fk("agent_events", "llm_invocation_id", "llm_invocations"),
        _ck("seq >= 1", "ck_agent_events_seq"),
        _ck(_in("type", _EVENT_TYPES), "ck_agent_events_type"),
        _ck(_object("payload", 16384), "ck_agent_events_payload"),
        _ck(_digest("payload_digest"), "ck_agent_events_payload_digest"),
    )
    op.create_index(
        "ix_agent_events_workspace_created_at", "agent_events", ["workspace_id", sa.text("created_at DESC")]
    )
    _partial_index("ix_agent_events_llm_invocation_id", "agent_events", "llm_invocation_id")


def _create_agent_proposals() -> None:
    op.create_table(
        "agent_proposals",
        _uuid("id", nullable=False),
        _workspace(),
        _uuid("project_id", nullable=False),
        _uuid("run_id", nullable=False),
        sa.Column("decision_point_key", sa.String(64), nullable=False),
        sa.Column("level_at_proposal", sa.SmallInteger(), nullable=False),
        sa.Column("answer_ceiling", sa.SmallInteger(), nullable=False),
        sa.Column("proposal_type", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_digest", sa.CHAR(64), nullable=False),
        sa.Column("rule_answer", postgresql.JSONB(), nullable=True),
        sa.Column("citations", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("validator_verdict", sa.String(16), nullable=False),
        sa.Column(
            "validator_reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("supersede_reason", sa.String(32), nullable=True),
        sa.Column("subject_kind", sa.String(32), nullable=False),
        *_subject_columns(),
        sa.Column("tool_name", sa.String(64), nullable=True),
        sa.Column("tool_arguments", postgresql.JSONB(), nullable=True),
        sa.Column("proposed_rationale", sa.String(4000), nullable=True),
        sa.Column("estimated_cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("estimated_duration_s", sa.Integer(), nullable=True),
        _ts("expires_at"),
        sa.Column("decided_by_user_id", _UUID, sa.ForeignKey("users.id"), nullable=True),
        _ts("decided_at"),
        _uuid("decision_record_id"),
        _uuid("applied_decision_record_id"),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("idempotency_digest", sa.CHAR(64), nullable=True),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_agent_proposals_workspace_id"),
        _project_fk("agent_proposals"),
        _fk("agent_proposals", "run_id", "agent_runs"),
        *_subject_fks("agent_proposals"),
        _fk("agent_proposals", "decision_record_id", "project_decision_records", project=True),
        _fk("agent_proposals", "applied_decision_record_id", "project_decision_records", project=True),
        _ck(_key("decision_point_key"), "ck_agent_proposals_decision_point_key"),
        _ck(
            "level_at_proposal BETWEEN 0 AND 3 AND answer_ceiling BETWEEN 0 AND 3 "
            "AND level_at_proposal <= answer_ceiling",
            "ck_agent_proposals_levels",
        ),
        _ck(_in("proposal_type", _PROPOSAL_TYPES), "ck_agent_proposals_type"),
        _ck("schema_version >= 1", "ck_agent_proposals_schema_version"),
        _ck(_object("payload", 32768), "ck_agent_proposals_payload"),
        _ck(_digest("payload_digest"), "ck_agent_proposals_payload_digest"),
        _ck(_object("rule_answer", 8192, nullable=True), "ck_agent_proposals_rule_answer"),
        _ck(_array("citations", 16384, max_items=64), "ck_agent_proposals_citations"),
        _ck(_in("validator_verdict", ("accepted", "rejected")), "ck_agent_proposals_validator_verdict"),
        _ck(_array("validator_reasons", 4096), "ck_agent_proposals_validator_reasons"),
        _ck(_in("status", _PROPOSAL_STATUSES), "ck_agent_proposals_status"),
        _ck(
            "(validator_verdict = 'rejected') = (status = 'rejected_by_validator')",
            "ck_agent_proposals_verdict_status",
        ),
        _ck(
            "(status = 'superseded') = (supersede_reason IS NOT NULL) AND ("
            + _nullable_in("supersede_reason", ("plan_exists", "results_exist", "newer_proposal"))
            + ")",
            "ck_agent_proposals_supersede_reason",
        ),
        _ck(_in("subject_kind", _SUBJECT_KINDS), "ck_agent_proposals_subject_kind"),
        _ck(_subject_matches_kind(nullable_kind=False), "ck_agent_proposals_subject_matches_kind"),
        _ck(
            "(proposal_type = 'ToolCallProposal') = (tool_name IS NOT NULL AND tool_arguments IS NOT NULL) "
            "AND (tool_name IS NOT NULL OR tool_arguments IS NULL)",
            "ck_agent_proposals_tool_call",
        ),
        _ck(_key("tool_name", nullable=True), "ck_agent_proposals_tool_name"),
        _ck(_object("tool_arguments", 8192, nullable=True), "ck_agent_proposals_tool_arguments"),
        _ck(
            "(estimated_cost_micros IS NULL OR estimated_cost_micros >= 0) "
            "AND (estimated_duration_s IS NULL OR estimated_duration_s >= 0)",
            "ck_agent_proposals_estimates",
        ),
        _ck(
            "status NOT IN ('accepted', 'rejected') "
            "OR (decided_by_user_id IS NOT NULL AND decided_at IS NOT NULL)",
            "ck_agent_proposals_decided",
        ),
        _ck(
            "decided_by_user_id IS NULL OR status IN ('accepted', 'rejected', 'applied', 'reverted')",
            "ck_agent_proposals_decided_status",
        ),
        _ck(
            "(level_at_proposal = 0 AND status IN ('shadow', 'rejected_by_validator')) "
            "OR (level_at_proposal >= 1 AND status <> 'shadow')",
            "ck_agent_proposals_level_status",
        ),
        _ck(
            "proposal_type <> 'ToolCallProposal' OR level_at_proposal <= 1",
            "ck_agent_proposals_tool_call_level",
        ),
        _ck(_IDEMPOTENCY, "ck_agent_proposals_idempotency"),
    )
    op.create_index(
        "ix_agent_proposals_workspace_project_status_created_at", "agent_proposals",
        ["workspace_id", "project_id", "status", sa.text("created_at DESC")],
    )
    op.create_index("ix_agent_proposals_decision_point_status", "agent_proposals", ["decision_point_key", "status"])
    op.create_index("ix_agent_proposals_run_id", "agent_proposals", ["run_id"])
    op.create_index(
        "uq_agent_proposals_workspace_idempotency_key", "agent_proposals", ["workspace_id", "idempotency_key"],
        unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    _subject_indexes("agent_proposals", "agent_proposals")
    _partial_index("ix_agent_proposals_decision_record_id", "agent_proposals", "decision_record_id")
    _partial_index(
        "ix_agent_proposals_applied_decision_record_id", "agent_proposals", "applied_decision_record_id"
    )
    _partial_index("ix_agent_proposals_decided_by_user_id", "agent_proposals", "decided_by_user_id")


def _create_semantic_decision_answers() -> None:
    op.create_table(
        "semantic_decision_answers",
        _uuid("id", nullable=False),
        _workspace(),
        _uuid("project_id"),
        _uuid("experiment_id"),
        _uuid("dataset_id"),
        _uuid("llm_invocation_id", nullable=False),
        _uuid("agent_run_id"),
        sa.Column("decision_point_key", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("release_version", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(64), nullable=False),
        sa.Column("question_key", sa.String(200), nullable=False),
        sa.Column("question_digest", sa.CHAR(64), nullable=False),
        sa.Column("data_class", sa.String(16), nullable=False),
        sa.Column("evidence_partition", sa.String(8), nullable=False),
        sa.Column("primitive", sa.String(8), nullable=False),
        sa.Column("answer", postgresql.JSONB(), nullable=False),
        sa.Column("probabilities", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("in_acting_band", sa.Boolean(), nullable=False),
        sa.Column("rule_answer", postgresql.JSONB(), nullable=True),
        sa.Column("agreement", sa.String(16), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.Column("policy_outcome", sa.String(16), nullable=False),
        sa.Column("value_used", postgresql.JSONB(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("ground_truth", postgresql.JSONB(), nullable=True),
        sa.Column("labeled_by_user_id", _UUID, sa.ForeignKey("users.id"), nullable=True),
        _ts("labeled_at"),
        sa.Column("label_source", sa.String(16), nullable=True),
        sa.Column("labels_version", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        _uuid("supersedes_label_id"),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_sda_workspace_id"),
        _project_fk("sda"),
        _fk("sda", "experiment_id", "experiments", project=True),
        _fk("sda", "dataset_id", "datasets", project=True),
        _fk("sda", "llm_invocation_id", "llm_invocations"),
        _fk("sda", "agent_run_id", "agent_runs"),
        _fk("sda", "supersedes_label_id", "semantic_decision_answers"),
        _ck("num_nonnulls(experiment_id, dataset_id) = 0 OR project_id IS NOT NULL",
            "ck_sda_subject_requires_project"),
        _ck(_key("decision_point_key"), "ck_sda_decision_point_key"),
        _ck(_key("purpose"), "ck_sda_purpose"),
        _ck(_digest("question_digest"), "ck_sda_question_digest"),
        _ck(_in("data_class", _DATA_CLASSES), "ck_sda_data_class"),
        _ck(_in("evidence_partition", ("metadata", "train")), "ck_sda_evidence_partition"),
        _ck(_in("primitive", ("noul", "choice", "score")), "ck_sda_primitive"),
        _ck(_object("answer", 2048), "ck_sda_answer"),
        _ck(_object("probabilities", 4096, nullable=True), "ck_sda_probabilities"),
        _ck("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", "ck_sda_confidence"),
        _ck(_object("rule_answer", 2048, nullable=True), "ck_sda_rule_answer"),
        _ck(_in("agreement", ("agree", "disagree", "abstain", "unavailable")), "ck_sda_agreement"),
        _ck("level BETWEEN 0 AND 3", "ck_sda_level"),
        _ck(_in("policy_outcome", ("rule", "ai", "review", "human")), "ck_sda_policy_outcome"),
        _ck(_object("value_used", 2048, nullable=True), "ck_sda_value_used"),
        _ck("latency_ms IS NULL OR latency_ms >= 0", "ck_sda_latency"),
        _ck(_object("ground_truth", 2048, nullable=True), "ck_sda_ground_truth"),
        _ck(_nullable_in("label_source", ("blind", "benchmark", "user_decision_exposed")),
            "ck_sda_label_source"),
        _ck(
            "(ground_truth IS NULL AND label_source IS NULL AND labeled_at IS NULL "
            "AND labeled_by_user_id IS NULL AND supersedes_label_id IS NULL AND labels_version = 0) "
            "OR (ground_truth IS NOT NULL AND label_source IS NOT NULL AND labeled_at IS NOT NULL "
            "AND labels_version >= 1)",
            "ck_sda_label",
        ),
        _ck("supersedes_label_id IS NULL OR supersedes_label_id <> id", "ck_sda_supersedes_not_self"),
        _ck("labels_version = 0 OR supersedes_label_id IS NOT NULL", "ck_sda_label_chain"),
    )
    table = "semantic_decision_answers"
    op.create_index(
        "ix_sda_purpose_release_model_created_at", table,
        ["purpose", "release_version", "model_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_sda_workspace_question_digest", table, ["workspace_id", "question_digest"])
    op.create_index("ix_sda_workspace_decision_point", table, ["workspace_id", "decision_point_key"])
    op.create_index("ix_sda_llm_invocation_id", table, ["llm_invocation_id"])
    _partial_index("ix_sda_agent_run_id", table, "agent_run_id")
    _partial_index("ix_sda_experiment_id", table, "experiment_id")
    _partial_index("ix_sda_dataset_id", table, "dataset_id")
    _partial_index("ix_sda_labeled_by_user_id", table, "labeled_by_user_id")
    _partial_index("ix_sda_workspace_project", table, "workspace_id", "project_id")
    op.create_index(
        "uq_sda_supersedes_label_id", table, ["supersedes_label_id"], unique=True,
        postgresql_where=sa.text("supersedes_label_id IS NOT NULL"),
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    _create_prompt_releases()
    _create_agent_runs()

    for column in _llm_columns():
        op.add_column("llm_invocations", column)
    op.create_foreign_key(
        "fk_llm_invocations_prompt_release", "llm_invocations", "prompt_releases",
        ["prompt_release_id"], ["id"],
    )
    for name, expression in _LLM_CHECKS:
        op.create_check_constraint(name, "llm_invocations", expression)
    op.execute("ALTER TABLE llm_invocations DROP CONSTRAINT ck_llm_invocations_purpose")
    op.execute(
        "ALTER TABLE llm_invocations ADD CONSTRAINT ck_llm_invocations_purpose "
        f"CHECK ({_key('purpose')}) NOT VALID"
    )
    op.execute("ALTER TABLE llm_invocations VALIDATE CONSTRAINT ck_llm_invocations_purpose")
    op.execute(
        "ALTER TABLE llm_invocations ADD CONSTRAINT fk_llm_invocations_workspace_agent_run "
        "FOREIGN KEY (workspace_id, agent_run_id) REFERENCES agent_runs (workspace_id, id) "
        "ON DELETE SET NULL (agent_run_id) NOT VALID"
    )
    op.execute("ALTER TABLE llm_invocations VALIDATE CONSTRAINT fk_llm_invocations_workspace_agent_run")
    _partial_index("ix_llm_invocations_agent_run_id", "llm_invocations", "agent_run_id")
    _partial_index("ix_llm_invocations_prompt_release_id", "llm_invocations", "prompt_release_id")

    op.execute(
        "ALTER TABLE project_decision_records ADD CONSTRAINT fk_pdr_actor_agent_run "
        "FOREIGN KEY (workspace_id, actor_agent_run_id) REFERENCES agent_runs (workspace_id, id) "
        "NOT VALID"
    )
    op.execute("ALTER TABLE project_decision_records VALIDATE CONSTRAINT fk_pdr_actor_agent_run")

    _create_agent_events()
    _create_agent_proposals()
    _create_semantic_decision_answers()

    for sql in AGENT_PERSISTENCE_FUNCTIONS:
        op.execute(sql)
    for _name, _table, sql in AGENT_PERSISTENCE_TRIGGERS:
        op.execute(sql)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(_DOWNGRADE_PRECHECK_SQL)
    for name, table, _sql in reversed(AGENT_PERSISTENCE_TRIGGERS):
        if table == "llm_invocations":
            op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.drop_table("semantic_decision_answers")
    op.drop_table("agent_proposals")
    op.drop_table("agent_events")
    op.drop_constraint("fk_pdr_actor_agent_run", "project_decision_records", type_="foreignkey")
    op.drop_index("ix_llm_invocations_prompt_release_id", table_name="llm_invocations")
    op.drop_index("ix_llm_invocations_agent_run_id", table_name="llm_invocations")
    op.drop_constraint("fk_llm_invocations_workspace_agent_run", "llm_invocations", type_="foreignkey")
    op.execute("ALTER TABLE llm_invocations DROP CONSTRAINT ck_llm_invocations_purpose")
    op.execute(
        "ALTER TABLE llm_invocations ADD CONSTRAINT ck_llm_invocations_purpose "
        f"CHECK (purpose IN ({_LEGACY_PURPOSES})) NOT VALID"
    )
    op.execute("ALTER TABLE llm_invocations VALIDATE CONSTRAINT ck_llm_invocations_purpose")
    for name, _expression in _LLM_CHECKS:
        op.drop_constraint(name, "llm_invocations", type_="check")
    op.drop_constraint("fk_llm_invocations_prompt_release", "llm_invocations", type_="foreignkey")
    for column in reversed(_llm_columns()):
        op.drop_column("llm_invocations", column.name)
    op.drop_table("agent_runs")
    op.drop_table("prompt_releases")
    for function in (
        "force_created_at_now",
        "prevent_llm_invocation_agent_run_clear",
        "guard_llm_invocation_ledger",
        "enforce_agent_proposal_transition",
        "prevent_mutation_except_retention",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}()")
