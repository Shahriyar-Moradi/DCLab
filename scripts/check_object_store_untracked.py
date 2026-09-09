"""Fail if generated object-store files are tracked in Git.

    python -m scripts.check_object_store_untracked

Local development may still write to data/object_store. Those files must stay
gitignored and untracked.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IGNORE_PATTERN = "data/object_store/**"
OBJECT_STORE_PREFIX = "data/object_store"


def gitignore_has_object_store_rule(text: str | None = None) -> bool:
    contents = text if text is not None else (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    return any(line.strip() == IGNORE_PATTERN for line in contents.splitlines())


def tracked_object_store_paths(cwd: Path | None = None) -> list[str]:
    root = cwd or REPO_ROOT
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", OBJECT_STORE_PREFIX],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout).decode().strip() or f"exit {result.returncode}"
        raise RuntimeError(f"git ls-files failed: {err}")
    return [path for path in result.stdout.decode().split("\0") if path]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    problems: list[str] = []
    if not gitignore_has_object_store_rule():
        problems.append(f".gitignore must contain {IGNORE_PATTERN!r}")

    tracked = tracked_object_store_paths()
    if tracked:
        preview = tracked[:50]
        listing = "\n  ".join(preview)
        extra = f"\n  ... and {len(tracked) - 50} more" if len(tracked) > 50 else ""
        problems.append(f"generated object-store files are tracked:\n  {listing}{extra}")

    if problems:
        print("[FAIL] object-store git guard")
        for problem in problems:
            print(problem)
        return 1
    print("[clean] data/object_store is gitignored and untracked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
