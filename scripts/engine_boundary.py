"""The engine import boundary (P8.6-A): what ``app.engine`` may import.

    python -m scripts.engine_boundary            # direct violations + allowlist use
    python -m scripts.engine_boundary --report   # + per-module publishability, third-party deps

``apps/api/app/engine/**`` must be publishable later as ``dclab-engine`` without the
server. So engine code never imports the server layers (FORBIDDEN_APP) or the server
stack (FORBIDDEN_THIRD_PARTY), and every other non-engine ``app.*`` import is an
explicit, justified ALLOWLIST entry. ``apps/api/tests/test_engine_import_boundary.py``
fails on a new violation and on a stale allowlist entry. Imports are read with ``ast``
(top level, inside functions, relative, and ``importlib.import_module("...")`` literals);
nothing is imported or executed.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "apps" / "api"  # the directory that holds the `app` package
ENGINE = "app.engine"

# Never allowed, and no allowlist entry may cover them.
FORBIDDEN_APP = (
    "app.api",
    "app.cli",
    "app.db",
    "app.main",
    "app.middleware",
    "app.ml",  # legacy Decision.ai factory
    "app.services",
    "app.sim",
    "app.storage",
)
FORBIDDEN_THIRD_PARTY = (
    "alembic",
    "fastapi",
    "openai",  # provider SDKs live only under app/agents/gateway/providers (ADR 0009)
    "psycopg",
    "psycopg2",
    "sqlalchemy",
    "starlette",
    "uvicorn",
)


@dataclass(frozen=True)
class AllowedEdge:
    """``engine`` (a module or package prefix) may import ``target`` (module prefix)."""

    engine: str
    target: str
    reason: str
    owner: str


# Keep this tiny. Every entry names why it exists today and who removes it.
ALLOWLIST: tuple[AllowedEdge, ...] = (
    AllowedEdge(
        "app.engine.investigate", "app.domain.findings",
        "the findings vocabulary (check keys, severities, message templates, version) is also "
        "the API's public findings schema; extraction moves the vocabulary half into the engine",
        "OPEN_SOURCE_PLAN step 2",
    ),
    AllowedEdge(
        "app.engine.lab.column_map", "app.domain.lab_use_cases",
        "pure use-case catalog (dataclasses, no I/O) shared with two services; a 170-line move "
        "into the engine, deferred to keep P8.6-A small",
        "OPEN_SOURCE_PLAN step 2",
    ),
    AllowedEdge(
        "app.engine.lab.open_ingest", "app.translation.banned_terms",
        "client-facing field-name preview hides banned wording (pure leaf: re + constants); "
        "extraction passes a sanitizer in or keeps open_ingest private",
        "OPEN_SOURCE_PLAN step 2",
    ),
    AllowedEdge(
        "app.engine.lab.llm_client", "app.agents",
        "advisory LLM column/target helpers call the AI gateway (ADR 0009); LLM integration, "
        "not engine core: stays private and leaves engine/ at extraction",
        "OPEN_SOURCE_PLAN step 1",
    ),
    AllowedEdge(
        "app.engine.lab.llm_client", "app.config",
        "reads the ai_enabled switch; same private module as the app.agents entry",
        "OPEN_SOURCE_PLAN step 1",
    ),
    AllowedEdge(
        "app.engine.lab.prompts", "app.agents.prompt_releases",
        "prompt versions for the LLM helpers register in the DB-backed prompt registry; "
        "private together with llm_client",
        "OPEN_SOURCE_PLAN step 1",
    ),
    AllowedEdge(
        "app.engine.serving.artifacts", "app.config",
        "per-run scratch directory comes from RUN_SCRATCH_ROOT; extraction makes it a parameter",
        "OPEN_SOURCE_PLAN step 2",
    ),
    AllowedEdge(
        "app.engine.serving.package_loader", "app.config",
        "worker-only model loading, the legacy-pickle quarantine and probe limits are server "
        "security gates; extraction passes them in as a policy object without weakening them",
        "OPEN_SOURCE_PLAN step 2",
    ),
)


@dataclass(frozen=True)
class ImportEdge:
    module: str  # importing module, e.g. app.engine.lab.llm_client
    target: str  # imported name, e.g. app.agents.contracts.ContextField
    path: str  # repo-relative file
    line: int
    lazy: bool = False  # inside a function body: runs only when that function is called


def _within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _module_name(path: Path, src_root: Path) -> str:
    parts = list(path.relative_to(src_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _targets(node: ast.AST, module: str, is_package: bool) -> Iterator[str]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name
    elif isinstance(node, ast.ImportFrom):
        if node.level:
            base = module.split(".") if is_package else module.split(".")[:-1]
            base = base[: len(base) - (node.level - 1)]
            source = ".".join([*base, *([node.module] if node.module else [])])
        else:
            source = node.module or ""
        for alias in node.names:
            yield source if alias.name == "*" else f"{source}.{alias.name}"
    elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name in {"import_module", "__import__"} and isinstance(node.args[0].value, str):
            yield node.args[0].value


def iter_edges(engine_root: Path | None = None, src_root: Path | None = None) -> Iterator[ImportEdge]:
    """Every import statement in the engine package (any depth, any scope)."""
    src = src_root or SRC_ROOT
    root = engine_root or src / "app" / "engine"
    for path in sorted(root.rglob("*.py")):
        module = _module_name(path, src)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path.relative_to(src)
        in_function = {
            id(inner)
            for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
            for inner in ast.walk(fn)
        }
        for node in ast.walk(tree):
            for target in _targets(node, module, path.name == "__init__.py"):
                yield ImportEdge(
                    module, target, rel.as_posix(), getattr(node, "lineno", 0), lazy=id(node) in in_function
                )


def forbidden(edge: ImportEdge) -> bool:
    top = edge.target.split(".")[0]
    return top in FORBIDDEN_THIRD_PARTY or any(_within(edge.target, p) for p in FORBIDDEN_APP)


def outside_engine(edge: ImportEdge) -> bool:
    return _within(edge.target, "app") and not _within(edge.target, ENGINE) and edge.target != "app"


def allowed_by(edge: ImportEdge, allowlist: Iterable[AllowedEdge] = ALLOWLIST) -> AllowedEdge | None:
    for entry in allowlist:
        if _within(edge.module, entry.engine) and _within(edge.target, entry.target):
            return entry
    return None


@dataclass(frozen=True)
class BoundaryResult:
    forbidden: list[ImportEdge]
    unlisted: list[ImportEdge]  # non-engine app.* import with no allowlist entry
    allowed: list[ImportEdge]
    stale: list[AllowedEdge]  # allowlist entries nothing uses any more
    bad_entries: list[AllowedEdge]  # allowlist entries that try to cover a forbidden module

    @property
    def ok(self) -> bool:
        return not (self.forbidden or self.unlisted or self.stale or self.bad_entries)


def check(
    engine_root: Path | None = None,
    src_root: Path | None = None,
    allowlist: tuple[AllowedEdge, ...] = ALLOWLIST,
) -> BoundaryResult:
    edges = list(iter_edges(engine_root, src_root))
    forbidden_list = [e for e in edges if forbidden(e)]
    rest = [e for e in edges if not forbidden(e) and outside_engine(e)]
    allowed = [e for e in rest if allowed_by(e, allowlist)]
    unlisted = [e for e in rest if not allowed_by(e, allowlist)]
    used = {allowed_by(e, allowlist) for e in allowed}
    return BoundaryResult(
        forbidden=forbidden_list,
        unlisted=unlisted,
        allowed=allowed,
        stale=[entry for entry in allowlist if entry not in used],
        bad_entries=[
            entry for entry in allowlist
            if not _within(entry.engine, ENGINE) or any(_within(entry.target, p) for p in FORBIDDEN_APP)
        ],
    )


def third_party_dependencies(engine_root: Path | None = None) -> list[str]:
    """Top-level non-stdlib, non-app packages the engine imports (the dclab-engine deps)."""
    tops = {e.target.split(".")[0] for e in iter_edges(engine_root)}
    return sorted(t for t in tops if t and t not in sys.stdlib_module_names and t not in {"app", "__future__"})


def engine_modules(engine_root: Path | None = None, src_root: Path | None = None) -> list[str]:
    src = src_root or SRC_ROOT
    return sorted(_module_name(path, src) for path in (engine_root or src / "app" / "engine").rglob("*.py"))


def _owner_module(name: str, modules: set[str]) -> str:
    """``app.config.get_settings`` -> ``app.config``: the longest prefix that is a module."""
    parts = name.split(".")
    for size in range(len(parts), 0, -1):
        if ".".join(parts[:size]) in modules:
            return ".".join(parts[:size])
    return name


def reach(*, include_lazy: bool, engine_root: Path | None = None) -> dict[str, list[str]]:
    """Engine module -> the non-engine ``app.*`` modules its import closure inside the
    engine reaches directly. With ``include_lazy=False`` only import-time edges count
    (module-level imports plus the parent packages Python runs first): a module listed
    there cannot even be imported without the server code. With ``include_lazy=True``
    function-scope imports count too: some of its code paths call into the server."""
    modules = set(engine_modules(engine_root))
    app_modules = {_module_name(path, SRC_ROOT) for path in (SRC_ROOT / "app").rglob("*.py")}
    # every module first runs its parent packages' __init__ (app.engine.x.y -> app.engine, app.engine.x)
    internal: dict[str, set[str]] = {
        m: {".".join(m.split(".")[:i]) for i in range(2, m.count(".") + 1)} for m in modules
    }
    external: dict[str, set[str]] = {}
    for e in iter_edges(engine_root):
        if e.lazy and not include_lazy:
            continue
        if outside_engine(e) or forbidden(e):
            external.setdefault(e.module, set()).add(_owner_module(e.target, app_modules))
        elif _within(e.target, ENGINE):
            target = _owner_module(e.target, modules)
            # importing a.b.c runs a.b's and a's __init__ first
            internal[e.module] |= {".".join(target.split(".")[:i]) for i in range(2, target.count(".") + 2)}
    result: dict[str, list[str]] = {}
    for module in sorted(modules):
        seen: set[str] = set()
        stack, reached = [module], set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            reached |= external.get(current, set())
            stack.extend(internal.get(current, ()))
        if reached:
            result[module] = sorted(reached)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", action="store_true", help="also print publishability and deps")
    args = parser.parse_args(argv)
    result = check()
    for label, items in (("FORBIDDEN", result.forbidden), ("NOT ALLOWLISTED", result.unlisted)):
        for e in items:
            print(f"{label}: {e.path}:{e.line} {e.module} -> {e.target}")
    for entry in result.stale:
        print(f"STALE ALLOWLIST ENTRY: {entry.engine} -> {entry.target}")
    for entry in result.bad_entries:
        print(f"INVALID ALLOWLIST ENTRY: {entry.engine} -> {entry.target}")
    sites = sorted({(e.path, e.line, e.target.rsplit(".", 1)[0], e.lazy) for e in result.allowed})
    print(f"allowlisted import statements: {len(sites)} via {len(ALLOWLIST)} entries")
    for path, line, target, lazy in sites:
        print(f"  {path}:{line} -> {target}{' (in a function)' if lazy else ''}")
    if args.report:
        total = len(engine_modules())
        for include_lazy, label in ((False, "at import time"), (True, "(upper bound) counting function-scope imports")):
            found = reach(include_lazy=include_lazy)
            print(f"engine modules reaching non-engine app code {label}: {len(found)} of {total}")
            for module, targets in found.items():
                print(f"  {module}: {', '.join(targets)}")
        print("third-party imports:", ", ".join(third_party_dependencies()))
    print("engine boundary:", "OK" if result.ok else "VIOLATED")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
