"""Immutable SQL IN-clause helper for frozen Alembic vocabularies."""

from __future__ import annotations


def sql_in_clause(column: str, values: tuple[str, ...]) -> str:
    inner = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({inner})"
