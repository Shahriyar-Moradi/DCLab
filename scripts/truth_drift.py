"""Mechanically verifiable repository-truth drift checks (S0-P01B).

These functions are side-effect-free except ``write_snapshots``. They do not
migrate databases or change application files.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = REPO_ROOT / "contracts"
V1_SNAPSHOT = CONTRACTS_DIR / "v1_openapi.json"
OPERATIONS_SNAPSHOT = CONTRACTS_DIR / "openapi_operations.json"
TABLES_SNAPSHOT = CONTRACTS_DIR / "sqlalchemy_tables.json"
BASELINE_SNAPSHOT = CONTRACTS_DIR / "truth_baseline.json"

INSECURE_JWT_SECRET = "dev-only-insecure-secret-change-me"
PRODUCTION_ENV_VALUES = {"production", "prod"}
HTTP_METHODS_SKIP = {"head", "options", "parameters", "trace"}

SDK_OPENAPI_MODELS: tuple[tuple[str, str], ...] = (
    ("Principal", "PrincipalRead"),
    ("Workspace", "WorkspaceRead"),
    ("Project", "ProjectRead"),
    ("Dataset", "DatasetListItem"),
    ("ExecutionRequest", "ExecutionRequestRead"),
    ("ModelBuild", "PipelineModelBuildRead"),
    ("Artifact", "ArtifactRead"),
    ("Visualization", "VisualizationRead"),
    ("EventPage", "EventPage"),
    ("ModelBuildEvent", "MlRunEventRead"),
)

HISTORICAL_BANNER_FILES: tuple[str, ...] = (
    "docs/verification/BASELINE.md",
    "docs/DCLAB_SYSTEM_VERIFICATION_REPORT.md",
    "docs/DCLAB_DATABASE_ARCHITECTURE.md",
    "docs/DCLAB_API_REFERENCE.md",
    "docs/DCLAB_DATABASE_ERD.md",
)

CURRENT_TRUTH_REL = "docs/verification/S0_P01A_CURRENT_TRUTH.md"
LEDGER_REL = "docs/verification/README.md"
INDEX_REL = "docs/DCLAB_VERIFICATION_INDEX.md"

INSECURE_SECRET_ALLOW_PREFIXES: tuple[str, ...] = (
    "apps/api/app/config.py",
    ".github/workflows/",
    "apps/api/tests/",
    "scripts/truth_drift.py",
    "scripts/check_truth_drift.py",
    "docs/",
    "contracts/",
)

V1_PATH_LITERAL = re.compile(r"""f?["'](/v1/[^"']+)["']""")
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
MARKDOWN_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
ALEMBIC_REV = re.compile(r"\b(\d{4}_[a-z0-9_]+)\b")
CURRENT_STATUS = re.compile(r"(?im)^\s*(?:>\s*)?\**Status:\**\s*CURRENT")


@dataclass
class DriftReport:
    check: str
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _json_dump(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _git_ls_files(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout).decode().strip() or f"exit {result.returncode}"
        raise RuntimeError(f"git ls-files failed: {err}")
    return [path for path in result.stdout.decode().split("\0") if path]


def _literal(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Tuple):
        return tuple(_literal(elt) for elt in node.elts)
    if isinstance(node, ast.Name) and node.id == "None":
        return None
    return None


def parse_alembic_revisions(
    versions_dir: Path | None = None,
) -> list[dict[str, Any]]:
    directory = versions_dir or (
        REPO_ROOT / "apps" / "api" / "alembic" / "versions"
    )
    records: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        revision = None
        down_revision: Any = None
        for node in tree.body:
            if not isinstance(node, ast.AnnAssign | ast.Assign):
                continue
            names: list[str] = []
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                names = [
                    target.id for target in node.targets if isinstance(target, ast.Name)
                ]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
                value = node.value
            if value is None:
                continue
            if "revision" in names:
                revision = _literal(value)
            if "down_revision" in names:
                down_revision = _literal(value)
        records.append(
            {
                "file": path.name,
                "revision": revision,
                "down_revision": down_revision,
            }
        )
    return records


def alembic_graph_problems(records: Sequence[Mapping[str, Any]]) -> list[str]:
    problems: list[str] = []
    ids = [row["revision"] for row in records]
    if any(item is None or item == "" for item in ids):
        missing = [row["file"] for row in records if not row["revision"]]
        problems.append(f"revision id missing in {', '.join(missing)}")
    counted = Counter(str(item) for item in ids if item)
    duplicates = [rev for rev, count in counted.items() if count > 1]
    if duplicates:
        problems.append("duplicate Alembic revision ids: " + ", ".join(sorted(duplicates)))
    children: set[str] = set()
    known = {str(item) for item in ids if item}
    for row in records:
        down = row["down_revision"]
        values: tuple[Any, ...]
        if down is None:
            values = ()
        elif isinstance(down, tuple):
            values = down
        else:
            values = (down,)
        for item in values:
            if item is None:
                continue
            name = str(item)
            children.add(name)
            if name not in known:
                problems.append(
                    f"{row['file']} down_revision {name} is not a known revision"
                )
    heads = sorted(known - children)
    if len(heads) != 1:
        problems.append(
            f"expected one Alembic head, found {len(heads)}: {', '.join(heads) or '(none)'}"
        )
    return problems


def check_alembic_graph(
    records: Sequence[Mapping[str, Any]] | None = None,
    *,
    expected_head: str | None = None,
) -> DriftReport:
    rows = list(records) if records is not None else parse_alembic_revisions()
    problems = alembic_graph_problems(rows)
    if expected_head is not None:
        heads = _heads(rows)
        if heads != [expected_head]:
            problems.append(
                f"Alembic head {heads!r} does not match snapshot {expected_head}"
            )
    return DriftReport("alembic_graph", problems)


def _heads(records: Sequence[Mapping[str, Any]]) -> list[str]:
    known = {str(row["revision"]) for row in records if row["revision"]}
    children: set[str] = set()
    for row in records:
        down = row["down_revision"]
        values = down if isinstance(down, tuple) else ((down,) if down else ())
        for item in values:
            if item is not None:
                children.add(str(item))
    return sorted(known - children)


def sqlalchemy_table_names() -> list[str]:
    import sys

    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
    from app.db import models  # noqa: F401
    from app.db.base import Base

    return sorted(Base.metadata.tables)


def check_table_snapshot(
    tables: Sequence[str] | None = None,
    snapshot: Sequence[str] | None = None,
) -> DriftReport:
    actual = list(tables) if tables is not None else sqlalchemy_table_names()
    expected = (
        list(snapshot)
        if snapshot is not None
        else list(_load_json(TABLES_SNAPSHOT))
    )
    problems: list[str] = []
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        problems.append("SQLAlchemy tables missing versus snapshot: " + ", ".join(missing))
    if extra:
        problems.append(
            "SQLAlchemy tables added versus snapshot (refresh contracts/sqlalchemy_tables.json if intentional): "
            + ", ".join(extra)
        )
    return DriftReport("model_migration_tables", problems)


def _openapi_schema() -> dict[str, Any]:
    import sys

    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
    from app.main import app

    return app.openapi()


def _success_schema_name(spec: Mapping[str, Any]) -> str | None:
    responses = spec.get("responses") or {}
    for code in ("200", "201", "202"):
        body = responses.get(code) or {}
        schema = (
            ((body.get("content") or {}).get("application/json") or {}).get("schema")
            or {}
        )
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            return ref.rsplit("/", 1)[-1]
    return None


def _request_schema_name(spec: Mapping[str, Any]) -> str | None:
    body = spec.get("requestBody") or {}
    schema = (
        ((body.get("content") or {}).get("application/json") or {}).get("schema") or {}
    )
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        return ref.rsplit("/", 1)[-1]
    return None


def canonicalize_v1_openapi(schema: Mapping[str, Any]) -> dict[str, Any]:
    paths: dict[str, dict[str, Any]] = {}
    for path, methods in sorted((schema.get("paths") or {}).items()):
        if not str(path).startswith("/v1"):
            continue
        entry: dict[str, Any] = {}
        for method, spec in sorted(methods.items()):
            if method.lower() in HTTP_METHODS_SKIP or not isinstance(spec, dict):
                continue
            parameters = [
                {
                    "in": param.get("in"),
                    "name": param.get("name"),
                    "required": bool(param.get("required")),
                }
                for param in spec.get("parameters") or []
                if isinstance(param, dict)
            ]
            parameters.sort(key=lambda item: (item["in"] or "", item["name"] or ""))
            entry[method.upper()] = {
                "operationId": spec.get("operationId") or "",
                "parameters": parameters,
                "requestSchema": _request_schema_name(spec),
                "successSchema": _success_schema_name(spec),
                "statusCodes": sorted(str(code) for code in (spec.get("responses") or {})),
            }
        if entry:
            paths[str(path)] = entry
    components = schema.get("components", {}).get("schemas", {})
    sdk_schemas: dict[str, Any] = {}
    for _sdk_name, openapi_name in SDK_OPENAPI_MODELS:
        spec = components.get(openapi_name) or {}
        sdk_schemas[openapi_name] = {
            "properties": sorted((spec.get("properties") or {}).keys()),
            "required": sorted(spec.get("required") or []),
        }
    return {"paths": paths, "sdk_schemas": sdk_schemas}


def openapi_operation_list(schema: Mapping[str, Any]) -> list[str]:
    ops: list[str] = []
    for path, methods in sorted((schema.get("paths") or {}).items()):
        for method, spec in sorted(methods.items()):
            if method.lower() in HTTP_METHODS_SKIP or not isinstance(spec, dict):
                continue
            ops.append(f"{method.upper()} {path}")
    return ops


def check_v1_openapi_snapshot(
    actual: Mapping[str, Any] | None = None,
    snapshot: Mapping[str, Any] | None = None,
) -> DriftReport:
    live = (
        canonicalize_v1_openapi(actual)
        if actual is not None
        else canonicalize_v1_openapi(_openapi_schema())
    )
    expected = snapshot if snapshot is not None else _load_json(V1_SNAPSHOT)
    if live != expected:
        return DriftReport(
            "v1_openapi_snapshot",
            [
                "runtime /v1 OpenAPI snapshot drifted from contracts/v1_openapi.json; "
                "if this is an intentional contract change, refresh with "
                "`python -m scripts.check_truth_drift --write-snapshots` and explain "
                "the snapshot diff in the PR"
            ],
        )
    return DriftReport("v1_openapi_snapshot")


def check_openapi_operations_snapshot(
    actual: Sequence[str] | None = None,
    snapshot: Sequence[str] | None = None,
) -> DriftReport:
    live = list(actual) if actual is not None else openapi_operation_list(_openapi_schema())
    expected = (
        list(snapshot)
        if snapshot is not None
        else list(_load_json(OPERATIONS_SNAPSHOT))
    )
    problems: list[str] = []
    removed = sorted(set(expected) - set(live))
    added = sorted(set(live) - set(expected))
    if removed:
        problems.append(
            "public API operations removed versus snapshot (breaking unless versioned): "
            + ", ".join(removed)
        )
    if added:
        problems.append(
            "public API operations added versus snapshot (refresh contracts/openapi_operations.json if intentional): "
            + ", ".join(added)
        )
    return DriftReport("openapi_operations", problems)


def normalize_path_template(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def extract_sdk_v1_paths(source: str) -> list[str]:
    found: list[str] = []
    for match in V1_PATH_LITERAL.finditer(source):
        raw = match.group(1)
        raw = re.sub(r"\{_id\(([^)]+)\)\}", r"{\1}", raw)
        found.append(raw.split("?")[0])
    return sorted(set(found))


def check_sdk_routes(
    sdk_paths: Sequence[str] | None = None,
    openapi_paths: Sequence[str] | None = None,
) -> DriftReport:
    if sdk_paths is None:
        client = (
            REPO_ROOT / "packages" / "dclab_client" / "dclab_client" / "client.py"
        ).read_text(encoding="utf-8")
        sdk_paths = extract_sdk_v1_paths(client)
    if openapi_paths is None:
        schema = canonicalize_v1_openapi(_openapi_schema())
        openapi_paths = list(schema["paths"])
    sdk_norm = {normalize_path_template(path) for path in sdk_paths}
    api_norm = {normalize_path_template(path) for path in openapi_paths}
    problems: list[str] = []
    missing_from_api = sorted(sdk_norm - api_norm)
    missing_from_sdk = sorted(api_norm - sdk_norm)
    if missing_from_api:
        problems.append(
            "SDK /v1 paths are not in runtime OpenAPI: " + ", ".join(missing_from_api)
        )
    if missing_from_sdk:
        problems.append(
            "runtime /v1 paths have no SDK client route: " + ", ".join(missing_from_sdk)
        )
    return DriftReport("sdk_routes", problems)


def sdk_model_fields() -> dict[str, set[str]]:
    import sys

    sys.path.insert(0, str(REPO_ROOT / "packages" / "dclab_client"))
    from dclab_client import types as sdk_types

    fields: dict[str, set[str]] = {}
    for sdk_name, openapi_name in SDK_OPENAPI_MODELS:
        model = getattr(sdk_types, sdk_name)
        fields[openapi_name] = set(model.model_fields)
    return fields


def check_sdk_types(
    sdk_fields: Mapping[str, set[str]] | None = None,
    openapi_schemas: Mapping[str, Mapping[str, Any]] | None = None,
) -> DriftReport:
    fields = sdk_fields if sdk_fields is not None else sdk_model_fields()
    if openapi_schemas is None:
        openapi_schemas = canonicalize_v1_openapi(_openapi_schema())["sdk_schemas"]
    problems: list[str] = []
    for _sdk_name, openapi_name in SDK_OPENAPI_MODELS:
        spec = openapi_schemas.get(openapi_name) or {}
        props = set(spec.get("properties") or [])
        required = set(spec.get("required") or [])
        have = set(fields.get(openapi_name) or [])
        if have - props:
            problems.append(
                f"{openapi_name}: SDK fields missing from OpenAPI: "
                + ", ".join(sorted(have - props))
            )
        if required - have:
            problems.append(
                f"{openapi_name}: OpenAPI required fields missing from SDK: "
                + ", ".join(sorted(required - have))
            )
    return DriftReport("sdk_types", problems)


def check_baseline_counts(
    baseline: Mapping[str, Any] | None = None,
    *,
    heads: Sequence[str] | None = None,
    revision_count: int | None = None,
    table_count: int | None = None,
    path_count: int | None = None,
    operation_count: int | None = None,
    v1_count: int | None = None,
) -> DriftReport:
    expected = baseline if baseline is not None else _load_json(BASELINE_SNAPSHOT)
    records = parse_alembic_revisions()
    live_heads = list(heads) if heads is not None else _heads(records)
    live_revision_count = (
        revision_count if revision_count is not None else len(records)
    )
    live_tables = (
        table_count if table_count is not None else len(sqlalchemy_table_names())
    )
    if path_count is None or operation_count is None or v1_count is None:
        schema = _openapi_schema()
        ops = openapi_operation_list(schema)
        v1_ops = [item for item in ops if item.split(" ", 1)[1].startswith("/v1")]
        live_path_count = path_count if path_count is not None else len(schema.get("paths") or {})
        live_operation_count = operation_count if operation_count is not None else len(ops)
        live_v1_count = v1_count if v1_count is not None else len(v1_ops)
    else:
        live_path_count = path_count
        live_operation_count = operation_count
        live_v1_count = v1_count
    mapping = {
        "alembic_heads": live_heads,
        "alembic_revision_count": live_revision_count,
        "sqlalchemy_table_count": live_tables,
        "openapi_path_count": live_path_count,
        "openapi_operation_count": live_operation_count,
        "v1_operation_count": live_v1_count,
    }
    problems: list[str] = []
    for key, value in mapping.items():
        if expected.get(key) != value:
            problems.append(
                f"truth baseline {key} expected {expected.get(key)!r} got {value!r}; "
                "refresh contracts/truth_baseline.json if intentional"
            )
    return DriftReport("truth_baseline", problems)


def iter_markdown_links(text: str) -> list[str]:
    """Return Markdown link targets, excluding fenced and inline code examples."""

    found: list[str] = []
    fence_char = ""
    fence_length = 0
    for line in text.splitlines():
        fence = MARKDOWN_FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if not fence_char:
                fence_char = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length:
                fence_char = ""
                fence_length = 0
            continue
        if fence_char:
            continue
        # Link-like strings in inline code are examples, not navigable links.
        prose = re.sub(r"`+[^`\n]*`+", "", line)
        for match in MD_LINK.finditer(prose):
            raw = match.group(1).strip().split()[0].strip("<>")
            found.append(raw)
    return found


def resolve_markdown_target(source: Path, raw: str, *, repo_root: Path = REPO_ROOT) -> Path | None:
    if raw.startswith(("http://", "https://", "mailto:", "#")):
        return None
    path_part = raw.split("#", 1)[0]
    if not path_part:
        return None
    target = (source.parent / path_part).resolve()
    return target


def check_docs_links(
    files: Sequence[Path] | None = None,
    *,
    repo_root: Path = REPO_ROOT,
) -> DriftReport:
    markdown = (
        list(files)
        if files is not None
        else [REPO_ROOT / rel for rel in _git_ls_files("*.md") if rel.endswith(".md")]
    )
    problems: list[str] = []
    root = repo_root.resolve()
    for path in markdown:
        text = path.read_text(encoding="utf-8")
        for raw in iter_markdown_links(text):
            target = resolve_markdown_target(path, raw, repo_root=root)
            if target is None:
                continue
            if target.exists():
                continue
            alt = (root / raw.split("#", 1)[0]).resolve()
            if alt.exists():
                continue
            problems.append(f"{path.relative_to(root)} -> {raw}")
    return DriftReport("docs_links", problems)


def check_current_status_docs(
    *,
    repo_root: Path = REPO_ROOT,
    expected_head: str | None = None,
    historical_files: Sequence[str] | None = None,
    current_truth: Path | None = None,
    ledger: Path | None = None,
    index: Path | None = None,
    current_scan_files: Sequence[str] | None = None,
    expected_product_sha: str | None = None,
    current_truth_commit_sha: str | None = None,
) -> DriftReport:
    head = expected_head
    if head is None:
        head = str(_load_json(BASELINE_SNAPSHOT)["alembic_heads"][0])
    problems: list[str] = []
    for rel in historical_files or HISTORICAL_BANNER_FILES:
        path = repo_root / rel
        opening = "\n".join(path.read_text(encoding="utf-8").splitlines()[:20])
        if "HISTORICAL" not in opening.upper():
            problems.append(f"{rel} must declare HISTORICAL in its opening banner")
    truth = current_truth or (repo_root / CURRENT_TRUTH_REL)
    truth_text = truth.read_text(encoding="utf-8")
    if "CURRENT" not in "\n".join(truth_text.splitlines()[:12]):
        problems.append(f"{CURRENT_TRUTH_REL} must be marked CURRENT")
    if head not in truth_text:
        problems.append(f"{CURRENT_TRUTH_REL} must name Alembic head {head}")
    # A Markdown file cannot embed the hash of the commit that contains it.
    # Accept that unavoidable case only when the same commit is both the latest
    # product change and the latest CURRENT-truth change. A later product-only
    # commit still fails because the two lineage SHAs then differ.
    truth_covers_product_commit = (
        current_truth_commit_sha is not None
        and current_truth_commit_sha == expected_product_sha
    )
    if (
        expected_product_sha
        and expected_product_sha not in truth_text
        and not truth_covers_product_commit
    ):
        problems.append(
            f"{CURRENT_TRUTH_REL} must name current product SHA "
            f"{expected_product_sha}"
        )
    for rel, label in ((LEDGER_REL, ledger), (INDEX_REL, index)):
        path = label or (repo_root / rel)
        text = path.read_text(encoding="utf-8")
        if "S0_P01A_CURRENT_TRUTH.md" not in text:
            problems.append(f"{rel} must point at the current truth report")
        if "HISTORICAL" not in text:
            problems.append(f"{rel} must mark older reports HISTORICAL")
        if head not in text:
            problems.append(f"{rel} must name Alembic head {head}")
    scan = (
        list(current_scan_files)
        if current_scan_files is not None
        else _git_ls_files("*.md")
    )
    for rel in scan:
        path = repo_root / rel
        lines = path.read_text(encoding="utf-8").splitlines()[:30]
        opening = "\n".join(lines)
        if not CURRENT_STATUS.search(opening):
            continue
        named = set(ALEMBIC_REV.findall(opening))
        foreign = {item for item in named if item != head}
        if foreign:
            problems.append(
                f"{rel} is marked CURRENT but names other Alembic ids {sorted(foreign)}; "
                f"expected {head}"
            )
    return DriftReport("current_status_docs", problems)


def insecure_secret_allowlisted(rel: str) -> bool:
    return any(
        rel == prefix or rel.startswith(prefix)
        for prefix in INSECURE_SECRET_ALLOW_PREFIXES
    )


def check_production_secrets(
    *,
    repo_root: Path = REPO_ROOT,
    tracked_files: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    jwt_secret: str | None = None,
) -> DriftReport:
    problems: list[str] = []
    files = (
        list(tracked_files)
        if tracked_files is not None
        else _git_ls_files()
    )
    for rel in files:
        if insecure_secret_allowlisted(rel):
            continue
        path = repo_root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if INSECURE_JWT_SECRET in text:
            problems.append(
                f"{rel} embeds the development JWT secret; production and shared "
                "config must not"
            )
    env = environ if environ is not None else os.environ
    secret = jwt_secret
    if secret is None:
        import sys

        sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
        from app.config import get_settings

        secret = get_settings().jwt_secret
    env_name = (
        env.get("DCLAB_ENV") or env.get("APP_ENV") or env.get("ENVIRONMENT") or ""
    ).strip().lower()
    if env_name in PRODUCTION_ENV_VALUES and secret == INSECURE_JWT_SECRET:
        problems.append(
            "DCLAB_ENV/APP_ENV/ENVIRONMENT is production but JWT_SECRET is the "
            "development default"
        )
    return DriftReport("production_secrets", problems)


def check_tracked_generated_output() -> list[DriftReport]:
    from scripts.check_object_store_untracked import (
        gitignore_has_object_store_rule,
        tracked_object_store_paths,
    )
    from scripts.check_playwright_untracked import (
        gitignore_has_playwright_rules,
        tracked_playwright_paths,
    )

    object_store = DriftReport("object_store_untracked")
    if not gitignore_has_object_store_rule():
        object_store.problems.append(".gitignore missing data/object_store/**")
    tracked = tracked_object_store_paths()
    if tracked:
        object_store.problems.append("tracked object-store files: " + ", ".join(tracked[:20]))
    playwright = DriftReport("playwright_untracked")
    missing = gitignore_has_playwright_rules()
    if missing:
        playwright.problems.append(".gitignore missing: " + ", ".join(missing))
    tracked_pw = tracked_playwright_paths()
    if tracked_pw:
        playwright.problems.append(
            "tracked Playwright files: " + ", ".join(tracked_pw[:20])
        )
    return [object_store, playwright]


def check_alembic_metadata(
    database_url: str | None = None,
    *,
    diffs: list | None = None,
) -> DriftReport:
    """Fail when SQLAlchemy models drifted from the migrated database."""

    if diffs is None:
        diffs = _live_metadata_diffs(database_url)
    if diffs:
        preview = "; ".join(str(item) for item in diffs[:8])
        extra = f" ({len(diffs) - 8} more)" if len(diffs) > 8 else ""
        return DriftReport(
            "alembic_metadata",
            [f"SQLAlchemy models drifted from the database: {preview}{extra}"],
        )
    return DriftReport("alembic_metadata")


def _live_metadata_diffs(database_url: str | None) -> list:
    import sys

    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
    from alembic.runtime.migration import MigrationContext
    from alembic.autogenerate import compare_metadata
    from sqlalchemy import create_engine

    from app.config import get_settings
    from app.db import models  # noqa: F401
    from app.db.base import Base

    url = database_url or get_settings().database_url
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True}
            )
            return list(compare_metadata(context, Base.metadata))
    finally:
        engine.dispose()


def collect_reports(*, include_alembic_metadata: bool = False) -> list[DriftReport]:
    from scripts.record_repo_truth import PRODUCT_PATHS, latest_commit_for_paths

    reports = [
        check_alembic_graph(
            expected_head=str(_load_json(BASELINE_SNAPSHOT)["alembic_heads"][0])
        ),
        check_table_snapshot(),
        check_v1_openapi_snapshot(),
        check_openapi_operations_snapshot(),
        check_sdk_routes(),
        check_sdk_types(),
        check_baseline_counts(),
        check_docs_links(),
        check_current_status_docs(
            expected_product_sha=latest_commit_for_paths(PRODUCT_PATHS),
            current_truth_commit_sha=latest_commit_for_paths((CURRENT_TRUTH_REL,)),
        ),
        check_production_secrets(),
        *check_tracked_generated_output(),
    ]
    if include_alembic_metadata:
        reports.append(check_alembic_metadata())
    return reports


def write_snapshots() -> None:
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    schema = _openapi_schema()
    tables = sqlalchemy_table_names()
    records = parse_alembic_revisions()
    ops = openapi_operation_list(schema)
    v1_ops = [item for item in ops if item.split(" ", 1)[1].startswith("/v1")]
    V1_SNAPSHOT.write_text(
        _json_dump(canonicalize_v1_openapi(schema)), encoding="utf-8"
    )
    OPERATIONS_SNAPSHOT.write_text(_json_dump(ops), encoding="utf-8")
    TABLES_SNAPSHOT.write_text(_json_dump(tables), encoding="utf-8")
    BASELINE_SNAPSHOT.write_text(
        _json_dump(
            {
                "alembic_heads": _heads(records),
                "alembic_revision_count": len(records),
                "sqlalchemy_table_count": len(tables),
                "openapi_path_count": len(schema.get("paths") or {}),
                "openapi_operation_count": len(ops),
                "v1_operation_count": len(v1_ops),
            }
        ),
        encoding="utf-8",
    )


def format_reports(reports: Iterable[DriftReport]) -> tuple[str, int]:
    lines: list[str] = []
    failed = 0
    for report in reports:
        if report.ok:
            lines.append(f"[clean] {report.check}")
            continue
        failed += 1
        lines.append(f"[FAIL] {report.check}")
        for problem in report.problems:
            lines.append(f"  {problem}")
    return "\n".join(lines) + "\n", failed
