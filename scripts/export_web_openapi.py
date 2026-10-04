"""Export the /v1 slice of the live FastAPI OpenAPI document for the web client.

    PYTHONPATH=apps/api python -m scripts.export_web_openapi
    PYTHONPATH=apps/api python -m scripts.export_web_openapi --check

``contracts/v1_openapi.json`` is a canonical truth snapshot (operation ids,
parameter names, schema names), not an OpenAPI document. Studio's generated
TypeScript types (``npm run gen:api`` in ``apps/web``) need the real thing, so
this writes ``apps/web/lib/infrastructure/v1/openapi.json``: every ``/v1/*``
path of ``app.openapi()`` plus only the component schemas those paths reference
(transitively). Keys are sorted, so the file is byte-stable across runs. It
imports the app the same way ``scripts.truth_drift`` does and needs no database.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator, Mapping

from scripts.truth_drift import REPO_ROOT, _openapi_schema

OUTPUT = REPO_ROOT / "apps" / "web" / "lib" / "infrastructure" / "v1" / "openapi.json"
V1_PREFIX = "/v1/"
SCHEMA_REF_PREFIX = "#/components/schemas/"


def _refs(node: Any) -> Iterator[str]:
    if isinstance(node, Mapping):
        ref = node.get("$ref")
        if isinstance(ref, str):
            yield ref
        for value in node.values():
            yield from _refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


def v1_openapi_document(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the ``/v1/*`` paths and the component schemas they reach."""
    paths = {
        path: spec
        for path, spec in (schema.get("paths") or {}).items()
        if str(path).startswith(V1_PREFIX)
    }
    components = (schema.get("components") or {}).get("schemas") or {}
    keep: dict[str, Any] = {}
    pending = list(_refs(paths))
    while pending:
        ref = pending.pop()
        if not ref.startswith(SCHEMA_REF_PREFIX):
            raise ValueError(f"unsupported $ref in /v1 OpenAPI: {ref}")
        name = ref[len(SCHEMA_REF_PREFIX):]
        if name in keep:
            continue
        if name not in components:
            raise ValueError(f"/v1 OpenAPI references a missing schema: {name}")
        keep[name] = components[name]
        pending.extend(_refs(components[name]))
    return {
        "openapi": schema.get("openapi", "3.1.0"),
        "info": {
            "title": "DCLab /v1 API (Studio slice)",
            "version": str((schema.get("info") or {}).get("version") or "0"),
        },
        "paths": paths,
        "components": {"schemas": keep},
    }


def render(document: Mapping[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read only: fail if the committed file differs from regeneration.",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    text = render(v1_openapi_document(_openapi_schema()))
    output: Path = args.output
    if args.check:
        current = output.read_text(encoding="utf-8") if output.exists() else ""
        if current != text:
            print(f"[FAIL] {output} is stale; run `npm run gen:api` in apps/web")
            return 1
        print(f"[clean] {output} is current")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
