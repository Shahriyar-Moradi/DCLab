"""dclab_client talks only to /v1 and does not import server internals."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "dclab_client"

FORBIDDEN_MODULES = (
    "sqlalchemy",
    "alembic",
    "psycopg2",
    "adaptive_modeling",
    "app",
)


def _forbidden(module: str | None) -> bool:
    if not module:
        return False
    for root in FORBIDDEN_MODULES:
        if module == root or module.startswith(root + "."):
            return True
    return False


def test_package_source_does_not_import_server_internals():
    files = list(PACKAGE_ROOT.rglob("*.py"))
    assert files
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not _forbidden(alias.name), f"{path} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                assert not _forbidden(node.module), f"{path} imports {node.module}"
