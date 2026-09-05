"""PostgreSQL immutability helpers shared by Alembic and test create_all.

Canonical scientific/version rows are frozen after insert or after lock.
Execution state (PipelineRun status, stage runs, events' parent runs) stays mutable.
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


def _always_trigger_name(table: str) -> str:
    return f"{table}_immutable"


def _locked_trigger_name(table: str) -> str:
    return f"{table}_locked_immutable"


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


def install_immutability_triggers(connection) -> None:
    """Apply the same trigger DDL Alembic 0035 installs (for metadata create_all)."""

    for statement in immutability_upgrade_statements():
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
