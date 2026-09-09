"""The enforcement half of the translation layer.

Scans are driven by `banned_terms.find_banned_terms` and the declarative
surface catalog in `surfaces.py`:

1. `scan_client_api_response_models` — walks every route mounted under the `/app`
   (client) API tree and inspects its Pydantic response model(s) field-by-field.
2. `scan_frontend_surfaces` — reads every `.ts`/`.tsx` file under each declared
   frontend surface. Banned-terms enforcement applies only to
   `SurfaceAudience.BUSINESS_CLIENT`. Developer workbench and platform/admin
   trees are still scanned (so tests can assert audience and ML vocabulary)
   but those audiences may use full ML terminology.

`scripts/scan_banned_terms.py` is the CI wrapper; `test_translation_layer.py`
calls these functions so a broken translator fails `pytest`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args, get_origin

from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.config import REPO_ROOT
from app.translation import surfaces as frontend_surfaces
from app.translation.banned_terms import find_banned_terms
from app.translation.surfaces import (
    CLIENT_SCHEMA_BEGIN,
    CLIENT_SCHEMA_END,
    CLIENT_SCHEMA_FILE,
    FRONTEND_SURFACES,
    SurfaceAudience,
    classify_frontend_path,
    surfaces_for,
)

WEB_ROOT = frontend_surfaces.WEB_ROOT
SCAN_EXTENSIONS = {".ts", ".tsx"}


def _business_client_dirs() -> tuple[Path, ...]:
    dirs: list[Path] = []
    for surface in frontend_surfaces.FRONTEND_SURFACES:
        if surface.audience is SurfaceAudience.BUSINESS_CLIENT:
            dirs.extend(surface.dirs)
    return tuple(dirs)


# Compatibility aliases for older tests/scripts. Prefer FRONTEND_SURFACES.
CLIENT_SCAN_DIRS: tuple[Path, ...] = _business_client_dirs()
CLIENT_SCAN_FILES: tuple[Path, ...] = ()


@dataclass(frozen=True)
class SurfaceScanResult:
    audience: SurfaceAudience
    label: str
    enforce_banned_terms: bool
    files_scanned: tuple[str, ...]
    violations: dict[str, list[str]]


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


def _label_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def list_surface_source_files(surface: frontend_surfaces.FrontendSurface) -> list[Path]:
    paths: list[Path] = list(surface.files)
    for directory in surface.dirs:
        if directory.exists():
            paths.extend(p for p in directory.rglob("*") if p.is_file() and p.suffix in SCAN_EXTENSIONS)
    return paths


def _schema_hits(surface: frontend_surfaces.FrontendSurface) -> dict[str, list[str]]:
    if surface.schema_file is None or surface.schema_begin is None or surface.schema_end is None:
        return {}
    if not surface.schema_file.exists():
        return {}
    full = surface.schema_file.read_text(encoding="utf-8")
    start = full.find(surface.schema_begin)
    end = full.find(surface.schema_end)
    if start == -1 or end == -1:
        return {}
    hits = _scan_text(full[start:end])
    if not hits:
        return {}
    return {f"{surface.schema_file.relative_to(REPO_ROOT)} [client schema block]": hits}


def scan_frontend_surface(surface: frontend_surfaces.FrontendSurface) -> SurfaceScanResult:
    files = list_surface_source_files(surface)
    labels = tuple(_label_path(path) for path in files)
    violations: dict[str, list[str]] = {}
    if surface.enforce_banned_terms:
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            hits = _scan_text(text)
            if hits:
                violations[_label_path(path)] = hits
        violations.update(_schema_hits(surface))
    return SurfaceScanResult(
        audience=surface.audience,
        label=surface.label,
        enforce_banned_terms=surface.enforce_banned_terms,
        files_scanned=labels,
        violations=violations,
    )


def scan_frontend_surfaces() -> tuple[SurfaceScanResult, ...]:
    return tuple(scan_frontend_surface(surface) for surface in frontend_surfaces.FRONTEND_SURFACES)


def scan_frontend_client_tree() -> dict[str, list[str]]:
    """Banned-terms scan of legacy business-decision / client surfaces only."""
    violations: dict[str, list[str]] = {}
    for surface in frontend_surfaces.FRONTEND_SURFACES:
        if surface.audience is SurfaceAudience.BUSINESS_CLIENT:
            violations.update(scan_frontend_surface(surface).violations)
    return violations


def scan_all() -> dict[str, list[str]]:
    violations: dict[str, list[str]] = {}
    violations.update({f"api:{key}": value for key, value in scan_client_api_response_models().items()})
    violations.update({f"web:{key}": value for key, value in scan_frontend_client_tree().items()})
    return violations


__all__ = [
    "CLIENT_SCAN_DIRS",
    "CLIENT_SCAN_FILES",
    "CLIENT_SCHEMA_BEGIN",
    "CLIENT_SCHEMA_END",
    "CLIENT_SCHEMA_FILE",
    "FRONTEND_SURFACES",
    "SCAN_EXTENSIONS",
    "SurfaceAudience",
    "SurfaceScanResult",
    "WEB_ROOT",
    "classify_frontend_path",
    "list_surface_source_files",
    "scan_all",
    "scan_client_api_response_models",
    "scan_frontend_client_tree",
    "scan_frontend_surface",
    "scan_frontend_surfaces",
    "surfaces_for",
]
