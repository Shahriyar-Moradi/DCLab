"""Frozen 0034 code-language check constraint.

Copied from ``app.domain.reproducibility`` at 0034. Do not import the live module.
"""

from __future__ import annotations

from alembic_frozen.clauses import sql_in_clause

CODE_LANGUAGES = ("python",)

CK_CODE_LANGUAGE = sql_in_clause("language", CODE_LANGUAGES)
