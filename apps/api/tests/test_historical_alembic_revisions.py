"""Historical Alembic revisions must not import live runtime modules."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from _alembic_catalog import dump_constraints_triggers, metadata_diffs
from conftest import ADMIN_URL

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
FROZEN = Path(__file__).resolve().parents[1] / "alembic_frozen"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "alembic_head_constraints_triggers.json"
)
REPRESENTATIVE_OLD_REVISION = "0028_semantic_leakage_purpose"

FORBIDDEN_PREFIXES = (
    "app.domain",
    "app.services",
    "app.db.integrity",
    "app.db.evidence_lock",
    "app.db.legacy_import",
)


def _alembic_config() -> Config:
    return Config("alembic.ini")


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            for alias in node.names:
                names.add(f"{node.module}.{alias.name}")
    return names


def _forbidden(names: set[str]) -> list[str]:
    hits = []
    for name in sorted(names):
        if any(
            name == prefix or name.startswith(prefix + ".")
            for prefix in FORBIDDEN_PREFIXES
        ):
            hits.append(name)
    return hits


def test_historical_revisions_do_not_import_live_runtime_modules():
    offenders: list[str] = []
    for path in sorted(VERSIONS.glob("*.py")):
        hits = _forbidden(_imported_names(path))
        if hits:
            offenders.append(f"{path.name}: {', '.join(hits)}")
    assert offenders == []


def test_frozen_alembic_modules_do_not_import_live_runtime_modules():
    offenders: list[str] = []
    for path in sorted(FROZEN.glob("*.py")):
        hits = _forbidden(_imported_names(path))
        if hits:
            offenders.append(f"{path.name}: {', '.join(hits)}")
    assert offenders == []


def test_0030_frozen_artifact_types_exclude_later_reproduction_types():
    from alembic_frozen.rev_0030_data_plane import ARTIFACT_TYPES

    assert "reproduction_notebook" not in ARTIFACT_TYPES
    assert "reproduction_script" not in ARTIFACT_TYPES


def _isolated_database(monkeypatch, *, suffix: str):
    admin_url = make_url(ADMIN_URL)
    database_name = f"decisionai_alembic_{suffix}_{uuid4().hex[:12]}"
    database_url = admin_url.set(database=database_name)
    admin_engine = create_engine(
        admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except Exception as exc:  # pragma: no cover - environment availability
        admin_engine.dispose()
        pytest.skip(f"cannot create isolated alembic database: {exc}")
    rendered = database_url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", rendered)
    from app.config import get_settings

    get_settings.cache_clear()
    return admin_engine, database_name, database_url, _alembic_config()


def _drop_isolated(admin_engine, database_name: str) -> None:
    from app.config import get_settings

    get_settings.cache_clear()
    with admin_engine.connect() as connection:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :database_name"
            ),
            {"database_name": database_name},
        )
        connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
    admin_engine.dispose()


def _assert_head_catalog(engine, alembic_config: Config) -> None:
    command.check(alembic_config)
    with engine.connect() as connection:
        current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    head = ScriptDirectory.from_config(alembic_config).get_current_head()
    assert current == head
    dump = dump_constraints_triggers(engine)
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert dump == expected
    diffs = metadata_diffs(engine)
    assert diffs == []


def test_fresh_database_upgrade_to_head_matches_frozen_catalog(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="fresh"
    )
    try:
        command.upgrade(alembic_config, "head")
        engine = create_engine(database_url)
        try:
            _assert_head_catalog(engine, alembic_config)
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


def test_representative_old_revision_upgrade_to_head_matches_frozen_catalog(
    monkeypatch,
):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="from28"
    )
    try:
        command.upgrade(alembic_config, REPRESENTATIVE_OLD_REVISION)
        command.upgrade(alembic_config, "head")
        engine = create_engine(database_url)
        try:
            _assert_head_catalog(engine, alembic_config)
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)
