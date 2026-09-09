"""Frozen 0042 provenance-immutability trigger SQL.

Copied from ``app.db.integrity`` at the revision that installed these triggers.
Do not import the live module; later integrity changes must not alter 0042.
"""

from __future__ import annotations

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

PROVENANCE_IMMUTABLE_TABLES = ("runtime_environments", "code_snapshots")

PROVENANCE_LOCKED_TABLES = ("pipeline_scientific_plans",)

PROVENANCE_COLUMN_IMMUTABLE_TABLES: dict[str, tuple[str, ...]] = {
    "artifacts": ("provider", "bucket", "object_key", "content_digest", "size_bytes"),
}


def _always_trigger_name(table: str) -> str:
    return f"{table}_immutable"


def _locked_trigger_name(table: str) -> str:
    return f"{table}_locked_immutable"


def _column_trigger_name(table: str) -> str:
    return f"{table}_columns_immutable"


def provenance_immutability_upgrade_statements() -> list[str]:
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
    statements.append("DROP FUNCTION IF EXISTS prevent_canonical_column_mutation()")
    statements.append("DROP FUNCTION IF EXISTS prevent_code_snapshot_mutation()")
    return statements
