"""Fail if generated Playwright artifacts are tracked in Git.

    python -m scripts.check_playwright_untracked
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

IGNORE_PATTERNS: tuple[str, ...] = (
    "apps/web/test-results/",
    "apps/web/playwright-report/",
    "apps/web/blob-report/",
)

TRACKED_PREFIXES: tuple[str, ...] = IGNORE_PATTERNS


def gitignore_has_playwright_rules(text: str | None = None) -> list[str]:
    contents = text if text is not None else (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    lines = {line.strip() for line in contents.splitlines()}
    return [pattern for pattern in IGNORE_PATTERNS if pattern not in lines]


def tracked_playwright_paths(cwd: Path | None = None) -> list[str]:
    root = cwd or REPO_ROOT
    tracked: list[str] = []
    for prefix in TRACKED_PREFIXES:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", prefix.rstrip("/")],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout).decode().strip() or f"exit {result.returncode}"
            raise RuntimeError(f"git ls-files failed: {err}")
        tracked.extend(path for path in result.stdout.decode().split("\0") if path)
    return tracked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    problems: list[str] = []
    missing = gitignore_has_playwright_rules()
    if missing:
        problems.append(".gitignore must contain: " + ", ".join(missing))

    tracked = tracked_playwright_paths()
    if tracked:
        listing = "\n  ".join(tracked[:50])
        extra = f"\n  ... and {len(tracked) - 50} more" if len(tracked) > 50 else ""
        problems.append(f"generated Playwright files are tracked:\n  {listing}{extra}")

    if problems:
        print("[FAIL] Playwright git guard")
        for problem in problems:
            print(problem)
        return 1
    print("[clean] Playwright report directories are gitignored and untracked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
