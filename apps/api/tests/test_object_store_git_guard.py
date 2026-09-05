"""Repository must not track runtime object-store artifacts."""

from __future__ import annotations

from pathlib import Path

from scripts.check_object_store_untracked import (
    IGNORE_PATTERN,
    gitignore_has_object_store_rule,
    tracked_object_store_paths,
)

from app.config import REPO_ROOT, get_settings


def test_gitignore_excludes_object_store():
    assert gitignore_has_object_store_rule()
    assert IGNORE_PATTERN in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")


def test_generated_object_store_files_are_not_tracked():
    assert tracked_object_store_paths() == []


def test_pytest_uses_temporary_object_storage_root():
    root = Path(get_settings().object_storage_root).resolve()
    repo_store = (REPO_ROOT / "data" / "object_store").resolve()
    assert root != repo_store
    assert not root.is_relative_to(repo_store)
