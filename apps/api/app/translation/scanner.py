"""The enforcement half of the translation layer.

Two independent scans, both driven by `banned_terms.find_banned_terms`:

1. `scan_client_api_response_models` — walks every route mounted under the `/app`
   (client) API tree and inspects its Pydantic response model(s) field-by-field.
   This is a structural check against the actual live route table, not a
   hand-maintained list, so a new endpoint can't accidentally skip it.
2. `scan_frontend_client_tree` — reads every `.ts`/`.tsx` file under the
   client-only parts of the Next.js app (never the admin tree) and flags any
   banned word or phrase found in the raw source text.

`scripts/scan_banned_terms.py` is the CLI wrapper used in CI; `test_translation_layer.py`
calls these functions directly so a broken translator fails `pytest`, not just a
separate lint step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args, get_origin

from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.config import REPO_ROOT
from app.translation.banned_terms import find_banned_terms

WEB_ROOT = REPO_ROOT / "apps" / "web"

# Directories that make up the authenticated client-facing Next.js surface. The
# admin tree (`apps/web/app/admin`) is deliberately excluded — full ML vocabulary
# is expected and allowed there. Public marketing pages/components are also
# excluded: they describe the product to prospects in general terms and are not
# an "insight" surface, so words like "model" in a sentence like "keeps searching
# for a model that beats the one you have" are legitimate marketing copy, not a
# leaked prediction result.
CLIENT_SCAN_DIRS: tuple[Path, ...] = (
    WEB_ROOT / "app" / "app",
    WEB_ROOT / "app" / "login",
    WEB_ROOT / "app" / "components" / "ui",
    WEB_ROOT / "app" / "components" / "layout",
    WEB_ROOT / "app" / "components" / "workspace",
    WEB_ROOT / "app" / "components" / "decisions",
    WEB_ROOT / "app" / "components" / "overview",
)
CLIENT_SCAN_FILES: tuple[Path, ...] = ()

# `lib/domain/schemas.ts` is shared between the client and admin (Lab) surfaces,
# so it can't be scanned wholesale — only the block marked as client-facing.
CLIENT_SCHEMA_FILE = WEB_ROOT / "lib" / "domain" / "schemas.ts"
CLIENT_SCHEMA_BEGIN = "// BEGIN CLIENT-FACING SCHEMAS"
CLIENT_SCHEMA_END = "// END CLIENT-FACING SCHEMAS"

SCAN_EXTENSIONS = {".ts", ".tsx"}


def _iter_pydantic_models(annotation: Any) -> list[type[BaseModel]]:
    """Unwrap list[...], X | Y, Optional[...], etc. down to the BaseModel
    subclasses actually referenced by a response_model annotation."""
    models: list[type[BaseModel]] = []
    origin = get_origin(annotation)
    if origin is not None:
        for arg in get_args(annotation):
            models.extend(_iter_pydantic_models(arg))
        return models
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        models.append(annotation)
    return models


def _scan_model(model: type[BaseModel], seen: set[type[BaseModel]] | None = None) -> dict[str, list[str]]:
    seen = seen if seen is not None else set()
    violations: dict[str, list[str]] = {}
    if model in seen:
        return violations
    seen.add(model)
    for field_name, field in model.model_fields.items():
        label = f"{model.__module__}.{model.__name__}.{field_name}"
        hits = set(find_banned_terms(field_name))
        if field.description:
            hits.update(find_banned_terms(field.description))
        if hits:
            violations[label] = sorted(hits)
        for nested in _iter_pydantic_models(field.annotation):
            violations.update(_scan_model(nested, seen))
    return violations


def scan_client_api_response_models() -> dict[str, list[str]]:
    """Every response_model reachable from the `/app` router tree, field by field."""
    from app.main import client_api  # local import: app.main imports this package indirectly

    violations: dict[str, list[str]] = {}

    def walk(router: Any) -> None:
        for route in router.routes:
            if isinstance(route, APIRoute) and route.response_model is not None:
                for model in _iter_pydantic_models(route.response_model):
                    violations.update(_scan_model(model))
            elif hasattr(route, "original_router"):
                walk(route.original_router)

    walk(client_api)
    return violations


def _scan_text(text: str) -> list[str]:
    return find_banned_terms(text)


def scan_frontend_client_tree() -> dict[str, list[str]]:
    violations: dict[str, list[str]] = {}
    paths: list[Path] = list(CLIENT_SCAN_FILES)
    for directory in CLIENT_SCAN_DIRS:
        if directory.exists():
            paths.extend(p for p in directory.rglob("*") if p.is_file() and p.suffix in SCAN_EXTENSIONS)

    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = _scan_text(text)
        if hits:
            try:
                label = str(path.relative_to(REPO_ROOT))
            except ValueError:
                label = str(path)
            violations[label] = hits

    if CLIENT_SCHEMA_FILE.exists():
        full = CLIENT_SCHEMA_FILE.read_text(encoding="utf-8")
        start = full.find(CLIENT_SCHEMA_BEGIN)
        end = full.find(CLIENT_SCHEMA_END)
        if start != -1 and end != -1:
            hits = _scan_text(full[start:end])
            if hits:
                key = f"{CLIENT_SCHEMA_FILE.relative_to(REPO_ROOT)} [client schema block]"
                violations[key] = hits

    return violations


def scan_all() -> dict[str, list[str]]:
    violations: dict[str, list[str]] = {}
    violations.update({f"api:{key}": value for key, value in scan_client_api_response_models().items()})
    violations.update({f"web:{key}": value for key, value in scan_frontend_client_tree().items()})
    return violations
