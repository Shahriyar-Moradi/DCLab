"""Declarative frontend surface classification.

The banned-terms translator applies only to legacy business-decision / client
surfaces. Developer workbench (Personal `/lab`, Business explorer, Model Build)
and platform/admin trees are scanned, but they are allowed to use full ML
vocabulary. Add a new UI tree here — do not sprinkle path exceptions in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.config import REPO_ROOT

WEB_ROOT = REPO_ROOT / "apps" / "web"

CLIENT_SCHEMA_FILE = WEB_ROOT / "lib" / "domain" / "schemas.ts"
CLIENT_SCHEMA_BEGIN = "// BEGIN CLIENT-FACING SCHEMAS"
CLIENT_SCHEMA_END = "// END CLIENT-FACING SCHEMAS"


class SurfaceAudience(str, Enum):
    BUSINESS_CLIENT = "business_client"
    DEVELOPER_WORKBENCH = "developer_workbench"
    PLATFORM_ADMIN = "platform_admin"


@dataclass(frozen=True)
class FrontendSurface:
    audience: SurfaceAudience
    label: str
    dirs: tuple[Path, ...]
    files: tuple[Path, ...] = ()
    enforce_banned_terms: bool = False
    schema_file: Path | None = None
    schema_begin: str | None = None
    schema_end: str | None = None


FRONTEND_SURFACES: tuple[FrontendSurface, ...] = (
    FrontendSurface(
        audience=SurfaceAudience.BUSINESS_CLIENT,
        label="legacy business-decision / client",
        dirs=(
            WEB_ROOT / "app" / "app",
            WEB_ROOT / "app" / "login",
            WEB_ROOT / "app" / "components" / "ui",
            WEB_ROOT / "app" / "components" / "layout",
            WEB_ROOT / "app" / "components" / "workspace",
            WEB_ROOT / "app" / "components" / "decisions",
            WEB_ROOT / "app" / "components" / "overview",
        ),
        enforce_banned_terms=True,
        schema_file=CLIENT_SCHEMA_FILE,
        schema_begin=CLIENT_SCHEMA_BEGIN,
        schema_end=CLIENT_SCHEMA_END,
    ),
    FrontendSurface(
        audience=SurfaceAudience.DEVELOPER_WORKBENCH,
        label="developer / Personal workbench / Model Build",
        dirs=(
            WEB_ROOT / "app" / "lab",
            WEB_ROOT / "app" / "business",
            WEB_ROOT / "app" / "components" / "model-build",
            WEB_ROOT / "app" / "components" / "explorer",
        ),
        enforce_banned_terms=False,
    ),
    FrontendSurface(
        audience=SurfaceAudience.PLATFORM_ADMIN,
        label="platform / admin",
        dirs=(
            WEB_ROOT / "app" / "admin",
            WEB_ROOT / "app" / "platform",
            WEB_ROOT / "app" / "components" / "admin",
        ),
        enforce_banned_terms=False,
    ),
)


def surfaces_for(audience: SurfaceAudience) -> tuple[FrontendSurface, ...]:
    return tuple(surface for surface in FRONTEND_SURFACES if surface.audience is audience)


def classify_frontend_path(path: Path) -> SurfaceAudience | None:
    """Most specific declared directory wins. Unlisted trees (marketing) are None."""
    resolved = path.resolve()
    best: tuple[int, SurfaceAudience] | None = None
    for surface in FRONTEND_SURFACES:
        for file in surface.files:
            if resolved == file.resolve():
                return surface.audience
        for directory in surface.dirs:
            try:
                resolved.relative_to(directory.resolve())
            except ValueError:
                continue
            depth = len(directory.resolve().parts)
            if best is None or depth > best[0]:
                best = (depth, surface.audience)
    return None if best is None else best[1]
