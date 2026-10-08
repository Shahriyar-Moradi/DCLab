"""PostgreSQL immutability helpers shared by Alembic and test create_all.

Canonical scientific/version rows are frozen after insert or after lock.
Execution state (PipelineRun status, stage runs, events' parent runs) stays mutable.

Canonical reproducibility rows are frozen wholesale. Artifact rows are frozen
only across stored-object identity columns so retention metadata and existing
foreign-key deletion semantics remain available.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

PREVENT_CANONICAL_MUTATION_SQL = """
CREATE OR REPLACE FUNCTION prevent_canonical_row_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql
"""

PREVENT_LOCKED_MUTATION_SQL = """
CREATE OR REPLACE FUNCTION prevent_locked_row_mutation()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.locked_at IS NOT NULL THEN
            RAISE EXCEPTION '% is locked and immutable', TG_TABLE_NAME;
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.locked_at IS NOT NULL THEN
        RAISE EXCEPTION '% is locked and immutable', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# Column-scoped guard for rows, such as Artifact registry entries, whose
# retention metadata stays writable while the stored-object identity does not.
PREVENT_COLUMN_MUTATION_SQL = """
CREATE OR REPLACE FUNCTION prevent_canonical_column_mutation()
RETURNS trigger AS $$
DECLARE
    frozen_columns text[] := string_to_array(TG_ARGV[0], ',');
    old_row jsonb := to_jsonb(OLD);
    new_row jsonb := to_jsonb(NEW);
    guarded text;
BEGIN
    FOREACH guarded IN ARRAY frozen_columns LOOP
        IF old_row -> guarded IS DISTINCT FROM new_row -> guarded THEN
            RAISE EXCEPTION '%.% is immutable', TG_TABLE_NAME, guarded;
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# CodeSnapshot is immutable to direct writes, but its optional stage association
# is explicitly cleared by PostgreSQL when that stage is removed. The nested
# trigger-depth check distinguishes that referential action from direct SQL.
PREVENT_CODE_SNAPSHOT_MUTATION_SQL = """
CREATE OR REPLACE FUNCTION prevent_code_snapshot_mutation()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE'
        AND pg_trigger_depth() > 1
        AND OLD.pipeline_stage_run_id IS NOT NULL
        AND NEW.pipeline_stage_run_id IS NULL
        AND (to_jsonb(OLD) - 'pipeline_stage_run_id')
            IS NOT DISTINCT FROM (to_jsonb(NEW) - 'pipeline_stage_run_id')
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql
"""

ALWAYS_IMMUTABLE_TABLES = (
    "datasets",
    "model_versions",
    "model_selection_decisions",
)

LOCKED_IMMUTABLE_TABLES = (
    "workflow_versions",
    "pipeline_versions",
    "feature_set_versions",
    "problem_specs",
)

# 0042 reproducibility scope. Deliberately separate from the tuples above: those
# are frozen inputs to 0035/0036 and to `immutability_disable_trigger_statements`,
# and the tables below do not exist yet when those revisions run.
PROVENANCE_IMMUTABLE_TABLES = ("runtime_environments", "code_snapshots")

PROVENANCE_LOCKED_TABLES = ("pipeline_scientific_plans",)

# table -> columns whose values may never change
PROVENANCE_COLUMN_IMMUTABLE_TABLES: dict[str, tuple[str, ...]] = {
    # Identity of the stored bytes only. `pipeline_run_id`, `project_id`,
    # `artifact_type`, `mime_type`, and `metadata` stay writable so association
    # backfills and retention tooling keep working, and DELETE is not blocked.
    "artifacts": ("provider", "bucket", "object_key", "content_digest", "size_bytes"),
}


def _always_trigger_name(table: str) -> str:
    return f"{table}_immutable"


def _locked_trigger_name(table: str) -> str:
    return f"{table}_locked_immutable"


def _column_trigger_name(table: str) -> str:
    return f"{table}_columns_immutable"


def immutability_upgrade_statements() -> list[str]:
    statements = [PREVENT_CANONICAL_MUTATION_SQL, PREVENT_LOCKED_MUTATION_SQL]
    for table in ALWAYS_IMMUTABLE_TABLES:
        trigger = _always_trigger_name(table)
        statements.append(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        statements.append(
            f"""
CREATE TRIGGER {trigger}
BEFORE UPDATE OR DELETE ON {table}
FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()
"""
        )
    for table in LOCKED_IMMUTABLE_TABLES:
        trigger = _locked_trigger_name(table)
        statements.append(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        statements.append(
            f"""
CREATE TRIGGER {trigger}
BEFORE UPDATE OR DELETE ON {table}
FOR EACH ROW EXECUTE FUNCTION prevent_locked_row_mutation()
"""
        )
    return statements


def immutability_downgrade_statements() -> list[str]:
    statements = []
    for table in ALWAYS_IMMUTABLE_TABLES:
        statements.append(
            f"DROP TRIGGER IF EXISTS {_always_trigger_name(table)} ON {table}"
        )
    for table in LOCKED_IMMUTABLE_TABLES:
        statements.append(
            f"DROP TRIGGER IF EXISTS {_locked_trigger_name(table)} ON {table}"
        )
    statements.append("DROP FUNCTION IF EXISTS prevent_canonical_row_mutation()")
    statements.append("DROP FUNCTION IF EXISTS prevent_locked_row_mutation()")
    return statements


def provenance_immutability_upgrade_statements() -> list[str]:
    """Trigger DDL Alembic 0042 installs for canonical reproducibility records."""

    statements = [
        PREVENT_CANONICAL_MUTATION_SQL,
        PREVENT_LOCKED_MUTATION_SQL,
        PREVENT_COLUMN_MUTATION_SQL,
        PREVENT_CODE_SNAPSHOT_MUTATION_SQL,
    ]
    for table in PROVENANCE_IMMUTABLE_TABLES:
        trigger = _always_trigger_name(table)
        function = (
            "prevent_code_snapshot_mutation"
            if table == "code_snapshots"
            else "prevent_canonical_row_mutation"
        )
        statements.append(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        statements.append(
            f"""
CREATE TRIGGER {trigger}
BEFORE UPDATE OR DELETE ON {table}
FOR EACH ROW EXECUTE FUNCTION {function}()
"""
        )
    for table in PROVENANCE_LOCKED_TABLES:
        trigger = _locked_trigger_name(table)
        statements.append(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        statements.append(
            f"""
CREATE TRIGGER {trigger}
BEFORE UPDATE OR DELETE ON {table}
FOR EACH ROW EXECUTE FUNCTION prevent_locked_row_mutation()
"""
        )
    for table, frozen in PROVENANCE_COLUMN_IMMUTABLE_TABLES.items():
        trigger = _column_trigger_name(table)
        statements.append(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        statements.append(
            f"""
CREATE TRIGGER {trigger}
BEFORE UPDATE ON {table}
FOR EACH ROW EXECUTE FUNCTION prevent_canonical_column_mutation(
    '{",".join(frozen)}'
)
"""
        )
    return statements


def provenance_immutability_downgrade_statements() -> list[str]:
    statements = [
        f"DROP TRIGGER IF EXISTS {_always_trigger_name(table)} ON {table}"
        for table in PROVENANCE_IMMUTABLE_TABLES
    ]
    statements.extend(
        f"DROP TRIGGER IF EXISTS {_locked_trigger_name(table)} ON {table}"
        for table in PROVENANCE_LOCKED_TABLES
    )
    statements.extend(
        f"DROP TRIGGER IF EXISTS {_column_trigger_name(table)} ON {table}"
        for table in PROVENANCE_COLUMN_IMMUTABLE_TABLES
    )
    # prevent_canonical_row_mutation / prevent_locked_row_mutation stay: 0035 owns them.
    statements.append("DROP FUNCTION IF EXISTS prevent_canonical_column_mutation()")
    statements.append("DROP FUNCTION IF EXISTS prevent_code_snapshot_mutation()")
    return statements


# --- ML state graph (ADR 0006, Alembic 0063) ---------------------------------
# New tuples rather than additions to the frozen 0035/0042 inputs above. Alembic
# 0063 inlines the identical literal SQL (revisions never import this module);
# these helpers serve create_all only.
STATE_GRAPH_IMMUTABLE_TABLES = ("split_plans",)
STATE_GRAPH_APPEND_ONLY_TABLES = ("project_decision_records",)
PROJECT_REFS_FROZEN_COLUMNS = ("id", "workspace_id", "project_id", "ref_kind", "created_at")

PREVENT_EXPERIMENT_LINEAGE_VIOLATION_SQL = """
CREATE OR REPLACE FUNCTION prevent_experiment_lineage_violation()
RETURNS trigger AS $$
DECLARE
    parent_row record;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.parent_pipeline_run_id IS DISTINCT FROM OLD.parent_pipeline_run_id
            OR NEW.change_set IS DISTINCT FROM OLD.change_set
            OR NEW.intent IS DISTINCT FROM OLD.intent THEN
            RAISE EXCEPTION
                'experiments.parent_pipeline_run_id, change_set and intent are insert-only';
        END IF;
        IF OLD.source_dataset_id IS NOT NULL
            AND NEW.source_dataset_id IS DISTINCT FROM OLD.source_dataset_id THEN
            RAISE EXCEPTION 'experiments.source_dataset_id is write-once';
        END IF;
        IF NEW.split_plan_id IS DISTINCT FROM OLD.split_plan_id THEN
            IF OLD.split_plan_id IS NOT NULL THEN
                RAISE EXCEPTION 'experiments.split_plan_id is write-once';
            END IF;
            IF OLD.scientific_evidence_locked_at IS NOT NULL THEN
                RAISE EXCEPTION
                    'experiments.split_plan_id cannot be set after the run is locked';
            END IF;
        END IF;
    END IF;
    IF NEW.change_set IS NOT NULL THEN
        SELECT workspace_id, project_id, source_dataset_id, split_plan_id
          INTO parent_row
          FROM experiments
         WHERE id = NEW.parent_pipeline_run_id;
        IF NOT FOUND OR parent_row.workspace_id IS DISTINCT FROM NEW.workspace_id THEN
            RAISE EXCEPTION 'branch parent must exist in the same workspace';
        END IF;
        IF NEW.project_id IS NULL
            OR NEW.project_id IS DISTINCT FROM parent_row.project_id THEN
            RAISE EXCEPTION 'branch must share project_id with its parent';
        END IF;
        IF NEW.source_dataset_id IS NULL
            OR NEW.source_dataset_id IS DISTINCT FROM parent_row.source_dataset_id THEN
            RAISE EXCEPTION 'branch must share source_dataset_id with its parent';
        END IF;
        IF parent_row.split_plan_id IS NULL
            OR NEW.split_plan_id IS DISTINCT FROM parent_row.split_plan_id THEN
            RAISE EXCEPTION 'branch must share split_plan_id with its parent';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

EXPERIMENTS_LINEAGE_GUARD_TRIGGER_SQL = """
CREATE TRIGGER experiments_lineage_guard
BEFORE INSERT OR UPDATE OF split_plan_id, parent_pipeline_run_id, change_set, intent,
    source_dataset_id, project_id, workspace_id ON experiments
FOR EACH ROW EXECUTE FUNCTION prevent_experiment_lineage_violation()
"""


def _append_only_trigger_name(table: str) -> str:
    return f"{table}_append_only"


def state_graph_immutability_upgrade_statements() -> list[str]:
    """Trigger DDL Alembic 0063 installs (functions from 0035/0042 must exist). Re-runnable."""

    def trigger(name: str, table: str, sql: str) -> list[str]:
        return [f"DROP TRIGGER IF EXISTS {name} ON {table}", sql]

    statements: list[str] = []
    for table in STATE_GRAPH_IMMUTABLE_TABLES:
        name = _always_trigger_name(table)
        statements += trigger(
            name,
            table,
            f"CREATE TRIGGER {name} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()",
        )
    for table in STATE_GRAPH_APPEND_ONLY_TABLES:
        name = _append_only_trigger_name(table)
        statements += trigger(
            name,
            table,
            f"CREATE TRIGGER {name} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()",
        )
    name = _column_trigger_name("project_refs")
    statements += trigger(
        name,
        "project_refs",
        f"CREATE TRIGGER {name} BEFORE UPDATE ON project_refs "
        "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_column_mutation("
        f"'{','.join(PROJECT_REFS_FROZEN_COLUMNS)}')",
    )
    statements += trigger(
        "project_refs_no_delete",
        "project_refs",
        "CREATE TRIGGER project_refs_no_delete BEFORE DELETE ON project_refs "
        "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()",
    )
    statements.append(PREVENT_EXPERIMENT_LINEAGE_VIOLATION_SQL)
    statements += trigger(
        "experiments_lineage_guard", "experiments", EXPERIMENTS_LINEAGE_GUARD_TRIGGER_SQL
    )
    return statements


# P3.1-B1 / Alembic 0065 (identical literal SQL inlined there).
IDEMPOTENCY_KEYS_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER idempotency_keys_no_update
BEFORE UPDATE ON idempotency_keys
FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()
"""


# P3.2-A / Alembic 0067 (identical literal SQL inlined there). Identity, scopes,
# secret hash and expiry are frozen; revocation is final (never un-revoked; afterwards
# revoked_by_user_id may only become NULL, by its ON DELETE SET NULL).
SERVICE_TOKENS_FROZEN_TRIGGER_SQL = """
CREATE TRIGGER service_tokens_columns_immutable
BEFORE UPDATE ON service_tokens
FOR EACH ROW EXECUTE FUNCTION prevent_canonical_column_mutation(
    'id,workspace_id,created_by_user_id,name,scopes,secret_hash,created_at,expires_at'
)
"""
SERVICE_TOKENS_REVOCATION_FINAL_TRIGGER_SQL = """
CREATE TRIGGER service_tokens_revocation_final
BEFORE UPDATE OF revoked_at, revoked_by_user_id ON service_tokens
FOR EACH ROW
WHEN (OLD.revoked_at IS NOT NULL AND (
    NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
    OR (NEW.revoked_by_user_id IS DISTINCT FROM OLD.revoked_by_user_id AND NEW.revoked_by_user_id IS NOT NULL)
))
EXECUTE FUNCTION prevent_canonical_row_mutation()
"""


# P4.9-A / Alembic 0069 (identical literal SQL inlined there). A finished scoring run
# is frozen; only requested_by_user_id may still change (to NULL, by its FK).
BATCH_PREDICTIONS_TERMINAL_TRIGGER_SQL = """
CREATE TRIGGER batch_predictions_terminal_immutable
BEFORE UPDATE ON batch_predictions
FOR EACH ROW
WHEN (OLD.status IN ('completed', 'failed'))
EXECUTE FUNCTION prevent_canonical_column_mutation(
    'id,workspace_id,project_id,model_version_id,model_release_id,input_dataset_id,execution_request_id,ml_job_id,initiated_by_service_token_id,status,output_format,rows_in,rows_out,contract_check,decision_threshold,output_artifact_id,error_code,error_message,created_at,started_at,completed_at'
)
"""


# P6.2-A1 / Alembic 0071 (identical literal SQL inlined there). ADR 0009 §2.2:
# append-only rows that the retention job (horizon >= 30 days, one workspace) or the
# workspace-deletion path may still delete. Both only work inside the transaction
# that ``app.db.retention_guard`` stamped (``dclab.retention_xact`` = the current
# transaction id), so a session-level SET on a pooled connection never leaks.
# UPDATE is always refused unless the trigger passes 'retention_update' (nulling
# llm_invocations.safe_output under the same conditions).
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


def agent_persistence_trigger_statements() -> list[str]:
    statements = list(AGENT_PERSISTENCE_FUNCTIONS)
    for name, table, sql in AGENT_PERSISTENCE_TRIGGERS:
        statements += [f"DROP TRIGGER IF EXISTS {name} ON {table}", sql]
    return statements


# P6.2-A2 / Alembic 0072 (identical literal SQL inlined there): A1 follow-ups replace
# two 0071 functions (an expired proposal cannot be extended; the ledger freezes
# provider and makes currency write-once) and the governance tables' triggers.
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


def ai_governance_trigger_statements() -> list[str]:
    statements = [
        ENFORCE_AGENT_PROPOSAL_TRANSITION_V2_SQL,
        GUARD_LLM_INVOCATION_LEDGER_V2_SQL,
        *AI_GOVERNANCE_FUNCTIONS,
    ]
    for name, table, sql in AI_GOVERNANCE_TRIGGERS:
        statements += [f"DROP TRIGGER IF EXISTS {name} ON {table}", sql]
    return statements


# P6.10-A2 / Alembic 0073 (identical literal SQL inlined there): a terminal agent run
# status is final and the gateway's release marker is write-once (ADR 0009 §4, §5.1).
_TERMINAL_RUN_STATUSES = (
    "'completed', 'failed', 'rejected_by_validator', 'over_budget', 'timed_out', 'cancelled', 'closed'"
)
GUARD_AGENT_RUN_LIFECYCLE_SQL = f"""
CREATE OR REPLACE FUNCTION guard_agent_run_lifecycle()
RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ({_TERMINAL_RUN_STATUSES}) AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION 'agent_runs: status % is final', OLD.status;
    END IF;
    IF OLD.budget_released_at IS NOT NULL
        AND NEW.budget_released_at IS DISTINCT FROM OLD.budget_released_at THEN
        RAISE EXCEPTION 'agent_runs.budget_released_at is write-once';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""
AGENT_RUN_LIFECYCLE_TRIGGER_SQL = (
    "CREATE TRIGGER agent_runs_lifecycle BEFORE UPDATE OF status, budget_released_at ON agent_runs "
    "FOR EACH ROW EXECUTE FUNCTION guard_agent_run_lifecycle()"
)


# P6.10-A follow-up / Alembic 0074 (identical literal SQL inlined there): a live -> terminal
# move must stamp budget_released_at in the same UPDATE (the gateway release CAS).
_LIVE_RUN_STATUSES = "'queued', 'running', 'waiting_user'"
GUARD_AGENT_RUN_LIFECYCLE_V2_SQL = f"""
CREATE OR REPLACE FUNCTION guard_agent_run_lifecycle()
RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ({_TERMINAL_RUN_STATUSES}) AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION 'agent_runs: status % is final', OLD.status;
    END IF;
    IF OLD.budget_released_at IS NOT NULL
        AND NEW.budget_released_at IS DISTINCT FROM OLD.budget_released_at THEN
        RAISE EXCEPTION 'agent_runs.budget_released_at is write-once';
    END IF;
    IF OLD.status IN ({_LIVE_RUN_STATUSES}) AND NEW.status IN ({_TERMINAL_RUN_STATUSES}) AND NEW.budget_released_at IS NULL THEN
        RAISE EXCEPTION 'agent_runs: a run ends only through the budget release (budget_released_at)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def agent_run_release_trigger_statements() -> list[str]:
    """0073's trigger with the 0074 function body (create_all installs the head state)."""

    return [
        GUARD_AGENT_RUN_LIFECYCLE_V2_SQL,
        "DROP TRIGGER IF EXISTS agent_runs_lifecycle ON agent_runs",
        AGENT_RUN_LIFECYCLE_TRIGGER_SQL,
    ]


R3_RUNS_APPEND_ONLY_TRIGGER_SQL = (
    "CREATE TRIGGER r3_runs_append_only BEFORE UPDATE OR DELETE ON r3_runs "
    "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()"
)


def install_immutability_triggers(connection) -> None:
    """Apply the trigger DDL Alembic 0035, 0042, 0043, 0063, 0065, 0067, 0069, 0071-0074, 0077 install (for create_all)."""

    from app.db.evidence_lock import evidence_lock_upgrade_statements

    for statement in immutability_upgrade_statements():
        connection.execute(text(statement))
    # 0060 adds this table after the 0035 immutable-table trigger migration.
    connection.execute(
        text(
            "CREATE TRIGGER dataset_policy_revisions_immutable "
            "BEFORE UPDATE OR DELETE ON dataset_policy_revisions "
            "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()"
        )
    )
    connection.execute(
        text(
            "CREATE TRIGGER ingestion_publication_events_immutable "
            "BEFORE UPDATE OR DELETE ON ingestion_publication_events "
            "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()"
        )
    )
    # 0077 (P6.8-A): stored R3 runs are append-only (identical literal in 0077_r3_runs).
    connection.execute(text(R3_RUNS_APPEND_ONLY_TRIGGER_SQL))
    for statement in provenance_immutability_upgrade_statements():
        connection.execute(text(statement))
    for statement in evidence_lock_upgrade_statements():
        connection.execute(text(statement))
    for statement in state_graph_immutability_upgrade_statements():
        connection.execute(text(statement))
    connection.execute(text("DROP TRIGGER IF EXISTS idempotency_keys_no_update ON idempotency_keys"))
    connection.execute(text(IDEMPOTENCY_KEYS_NO_UPDATE_TRIGGER_SQL))
    for name, sql in (
        ("service_tokens_columns_immutable", SERVICE_TOKENS_FROZEN_TRIGGER_SQL),
        ("service_tokens_revocation_final", SERVICE_TOKENS_REVOCATION_FINAL_TRIGGER_SQL),
    ):
        connection.execute(text(f"DROP TRIGGER IF EXISTS {name} ON service_tokens"))
        connection.execute(text(sql))
    connection.execute(
        text("DROP TRIGGER IF EXISTS batch_predictions_terminal_immutable ON batch_predictions")
    )
    connection.execute(text(BATCH_PREDICTIONS_TERMINAL_TRIGGER_SQL))
    for statement in agent_persistence_trigger_statements():
        connection.execute(text(statement))
    for statement in ai_governance_trigger_statements():
        connection.execute(text(statement))
    for statement in agent_run_release_trigger_statements():
        connection.execute(text(statement))


def _immutability_trigger_name(table: str) -> str | None:
    if table in ALWAYS_IMMUTABLE_TABLES:
        return _always_trigger_name(table)
    if table in LOCKED_IMMUTABLE_TABLES:
        return _locked_trigger_name(table)
    return None


def immutability_disable_trigger_statements(tables: Sequence[str]) -> list[str]:
    """Disable only named DCLab immutability triggers. Does not touch constraint triggers."""

    statements: list[str] = []
    for table in tables:
        trigger = _immutability_trigger_name(table)
        if trigger is None:
            continue
        statements.append(f'ALTER TABLE "{table}" DISABLE TRIGGER "{trigger}"')
    return statements


def immutability_enable_trigger_statements(tables: Sequence[str]) -> list[str]:
    statements: list[str] = []
    for table in tables:
        trigger = _immutability_trigger_name(table)
        if trigger is None:
            continue
        statements.append(f'ALTER TABLE "{table}" ENABLE TRIGGER "{trigger}"')
    return statements
