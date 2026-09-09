"""Generated Playwright artifacts must stay gitignored and untracked."""

from scripts.check_playwright_untracked import (
    IGNORE_PATTERNS,
    gitignore_has_playwright_rules,
    tracked_playwright_paths,
)

from app.config import REPO_ROOT


def test_gitignore_excludes_playwright_report_directories():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert gitignore_has_playwright_rules(gitignore) == []
    for pattern in IGNORE_PATTERNS:
        assert pattern in gitignore


def test_generated_playwright_files_are_not_tracked():
    assert tracked_playwright_paths() == []
