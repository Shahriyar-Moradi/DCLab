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


def install_immutability_triggers(connection) -> None:
    """Apply the trigger DDL Alembic 0035, 0042, 0043, 0063, 0065 and 0067 install (for create_all)."""

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
