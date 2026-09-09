"""Dump PostgreSQL check constraints, triggers, and functions for Alembic tests."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.base import Base
from app.db import models as _models  # noqa: F401

CHECK_SQL = """
SELECT
    cls.relname AS table_name,
    con.conname AS name,
    pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint AS con
JOIN pg_class AS cls ON cls.oid = con.conrelid
JOIN pg_namespace AS nsp ON nsp.oid = cls.relnamespace
WHERE con.contype = 'c'
  AND nsp.nspname = 'public'
ORDER BY cls.relname, con.conname
"""

TRIGGER_SQL = """
SELECT
    cls.relname AS table_name,
    tg.tgname AS name,
    pg_get_triggerdef(tg.oid) AS definition
FROM pg_trigger AS tg
JOIN pg_class AS cls ON cls.oid = tg.tgrelid
JOIN pg_namespace AS nsp ON nsp.oid = cls.relnamespace
WHERE nsp.nspname = 'public'
  AND NOT tg.tgisinternal
ORDER BY cls.relname, tg.tgname
"""

FUNCTION_SQL = """
SELECT
    proname AS name,
    pg_get_functiondef(p.oid) AS definition
FROM pg_proc AS p
JOIN pg_namespace AS nsp ON nsp.oid = p.pronamespace
WHERE nsp.nspname = 'public'
  AND p.prokind = 'f'
ORDER BY proname, pg_get_function_identity_arguments(p.oid)
"""


def dump_constraints_triggers(engine: Engine) -> dict:
    with engine.connect() as connection:
        checks = [
            {
                "table": row.table_name,
                "name": row.name,
                "definition": row.definition,
            }
            for row in connection.execute(text(CHECK_SQL))
        ]
        triggers = [
            {
                "table": row.table_name,
                "name": row.name,
                "definition": row.definition,
            }
            for row in connection.execute(text(TRIGGER_SQL))
        ]
        functions = [
            {"name": row.name, "definition": row.definition}
            for row in connection.execute(text(FUNCTION_SQL))
        ]
    return {
        "check_constraints": checks,
        "triggers": triggers,
        "functions": functions,
    }


def metadata_diffs(engine: Engine) -> list[str]:
    """SQLAlchemy/Alembic autogenerate diffs versus the physical database."""

    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True}
        )
        diffs = compare_metadata(context, Base.metadata)
    return [str(item) for item in diffs]
