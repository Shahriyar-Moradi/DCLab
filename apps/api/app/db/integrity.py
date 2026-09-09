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


def install_immutability_triggers(connection) -> None:
    """Apply the trigger DDL Alembic 0035, 0042, and 0043 install (for create_all)."""

    from app.db.evidence_lock import evidence_lock_upgrade_statements

    for statement in immutability_upgrade_statements():
        connection.execute(text(statement))
    for statement in provenance_immutability_upgrade_statements():
        connection.execute(text(statement))
    for statement in evidence_lock_upgrade_statements():
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
