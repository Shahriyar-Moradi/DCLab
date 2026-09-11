"""Read-only recorder for mechanically observable repository facts.

Default: print JSON to stdout. Does not mutate git, databases, or application
files. Optional ``--output PATH`` writes the same JSON (still an artifact, not
an application change).

    python -m scripts.record_repo_truth
    python -m scripts.record_repo_truth --output artifacts/repo-truth.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# These path groups make the two requested lineage SHAs reproducible. Product
# truth intentionally excludes generated/current evidence, so refreshing this
# report in a later documentation-only commit does not immediately make its
# product SHA stale.
PRODUCT_PATHS: tuple[str, ...] = (
    ".env.example",
    "alembic.ini",
    "apps/api/alembic",
    "apps/api/app",
    "apps/web/app",
    "apps/web/lib",
    "apps/web/middleware.ts",
    "apps/web/next.config.mjs",
    "apps/web/package-lock.json",
    "apps/web/package.json",
    "docker-compose.yml",
    "package-lock.json",
    "packages/dclab_client/dclab_client",
    "pyproject.toml",
    "scripts",
    ":(exclude)scripts/check_truth_drift.py",
    ":(exclude)scripts/record_repo_truth.py",
    ":(exclude)scripts/truth_drift.py",
)
DOCUMENTATION_PATHS: tuple[str, ...] = (
    ".github/workflows/ci.yml",
    "Makefile",
    "README.md",
    "contracts",
    "docs",
    "scripts/check_truth_drift.py",
    "scripts/record_repo_truth.py",
    "scripts/truth_drift.py",
)


def _run(args: list[str], *, cwd: Path = REPO_ROOT) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def latest_commit_for_paths(
    paths: tuple[str, ...], *, repo_root: Path = REPO_ROOT
) -> str:
    """Return the latest commit that changed one of ``paths``."""

    return _run(
        ["git", "log", "-1", "--format=%H", "--", *paths], cwd=repo_root
    )


def _git_facts() -> dict[str, object]:
    sha = _run(["git", "rev-parse", "HEAD"])
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    status = _run(["git", "status", "--porcelain=v1", "-b"])
    tracking = _run(["git", "status", "-sb"])
    origin = ""
    try:
        origin = _run(["git", "rev-parse", "origin/main"])
    except subprocess.CalledProcessError:
        origin = ""
    merge_base = ""
    ahead = None
    behind = None
    if origin:
        merge_base = _run(["git", "merge-base", "HEAD", "origin/main"])
        ahead_raw, behind_raw = _run(
            ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]
        ).split()
        ahead = int(ahead_raw)
        behind = int(behind_raw)
    dirty = any(
        line and not line.startswith("##") for line in status.splitlines()
    )
    return {
        "sha": sha,
        "branch": branch,
        "status_short": tracking,
        "origin_main_sha": origin or None,
        "equal_to_origin_main": bool(origin) and sha == origin,
        "merge_base_origin_main": merge_base or None,
        "ahead_of_origin_main": ahead,
        "behind_origin_main": behind,
        "product_sha": latest_commit_for_paths(PRODUCT_PATHS),
        "documentation_sha": latest_commit_for_paths(DOCUMENTATION_PATHS),
        "working_tree_clean": not dirty,
        "subject": _run(["git", "log", "-1", "--format=%s"]),
        "committed_at": _run(["git", "log", "-1", "--format=%cI"]),
        "remotes": _run(["git", "remote", "-v"]).splitlines(),
    }


def _alembic_facts() -> dict[str, object]:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    heads = list(script.get_heads())
    revisions = list(script.walk_revisions())
    version_files = sorted(
        path.name
        for path in (REPO_ROOT / "apps" / "api" / "alembic" / "versions").glob(
            "*.py"
        )
        if path.name != "__init__.py"
    )
    frozen_files = sorted(
        path.name
        for path in (REPO_ROOT / "apps" / "api" / "alembic_frozen").glob("rev_*.py")
    )
    return {
        "heads": heads,
        "head_count": len(heads),
        "revision_count": len(revisions),
        "version_file_count": len(version_files),
        "version_files": version_files,
        "frozen_file_count": len(frozen_files),
        "frozen_files": frozen_files,
    }


def _sqlalchemy_facts() -> dict[str, object]:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
    from app.db import models  # noqa: F401
    from app.db.base import Base

    tables = sorted(Base.metadata.tables)
    return {
        "table_count": len(tables),
        "tables": tables,
    }


def _openapi_facts() -> dict[str, object]:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
    from app.main import app

    schema = app.openapi()
    paths = schema.get("paths") or {}
    operations: list[dict[str, str]] = []
    skipped = {"head", "options", "parameters", "trace"}
    for path, methods in sorted(paths.items()):
        for method, spec in methods.items():
            if method.lower() in skipped:
                continue
            operations.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "operation_id": spec.get("operationId", ""),
                }
            )
    v1 = [op for op in operations if op["path"].startswith("/v1")]
    prefixes = Counter(
        "/" + op["path"].lstrip("/").split("/", 1)[0] for op in operations
    )
    return {
        "path_count": len(paths),
        "operation_count": len(operations),
        "v1_operation_count": len(v1),
        "v1_operations": v1,
        "prefix_counts": dict(sorted(prefixes.items())),
        "openapi_title": schema.get("info", {}).get("title"),
        "openapi_version": schema.get("info", {}).get("version"),
    }


def _inventory_facts() -> dict[str, object]:
    files = _run(["git", "ls-files"]).splitlines()
    by_ext: Counter[str] = Counter()
    python_lines = 0
    ts_lines = 0
    backend_py: list[str] = []
    frontend_ts: list[str] = []
    tests: list[str] = []
    packages: list[str] = []
    for rel in files:
        path = Path(rel)
        suffix = path.suffix.lower() or "<none>"
        by_ext[suffix] += 1
        if suffix == ".py":
            python_lines += _line_count(REPO_ROOT / path)
            if rel.startswith("apps/api/"):
                backend_py.append(rel)
            if "/tests/" in rel or rel.startswith("apps/web/e2e/"):
                tests.append(rel)
        if suffix in {".ts", ".tsx"}:
            ts_lines += _line_count(REPO_ROOT / path)
            if rel.startswith("apps/web/"):
                frontend_ts.append(rel)
            if rel.startswith("apps/web/e2e/") or rel.endswith(".spec.ts"):
                tests.append(rel)
        if rel.startswith("packages/"):
            packages.append(rel)

    package_roots = sorted(
        {Path(rel).parts[1] for rel in packages if len(Path(rel).parts) > 1}
    )
    backend_tests = [p for p in tests if p.startswith("apps/api/tests/")]
    sdk_tests = [p for p in tests if p.startswith("packages/dclab_client/tests/")]
    web_e2e = [p for p in tests if p.startswith("apps/web/e2e/")]
    return {
        "tracked_file_count": len(files),
        "python_file_count": by_ext.get(".py", 0),
        "ts_tsx_file_count": by_ext.get(".ts", 0) + by_ext.get(".tsx", 0),
        "python_line_count": python_lines,
        "ts_tsx_line_count": ts_lines,
        "backend_python_file_count": len(backend_py),
        "frontend_ts_tsx_file_count": len(frontend_ts),
        "test_file_count": len(sorted(set(tests))),
        "backend_test_file_count": len(backend_tests),
        "sdk_test_file_count": len(sdk_tests),
        "web_e2e_file_count": len(web_e2e),
        "package_roots": package_roots,
        "package_file_count": len(packages),
        "extension_counts": dict(sorted(by_ext.items())),
    }


def _line_count(path: Path) -> int:
    try:
        return sum(1 for _ in path.open("rb"))
    except OSError:
        return 0


def collect() -> dict[str, object]:
    return {
        "read_only": True,
        "git": _git_facts(),
        "alembic": _alembic_facts(),
        "sqlalchemy": _sqlalchemy_facts(),
        "openapi": _openapi_facts(),
        "inventory": _inventory_facts(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record read-only DCLab repository truth facts."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON path. Default is stdout only.",
    )
    args = parser.parse_args()
    payload = collect()
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
