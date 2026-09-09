"""Fail CI when the Labs client Zod schemas drift from the Pydantic models.

Compares field names (and a coarse type family) on:

  ClientLabUploadSchema  →  ClientLabUploadRead
  LabRunOutcomeSchema    →  ClientLabRunOutcome

Backend-only extra fields are allowed (Zod strips them). A rename on the
Pydantic model of a field the frontend still expects must fail this check.

    python -m scripts.check_client_lab_schema_contract
"""

from __future__ import annotations

import enum
import sys
from datetime import datetime
from types import UnionType
from typing import Any, Union, get_args, get_origin
from uuid import UUID

sys.path.insert(0, "apps/api")

from pydantic import BaseModel

from app.config import REPO_ROOT
from app.domain.client_lab import ClientLabRunOutcome, ClientLabUploadRead

SCHEMA_FILE = REPO_ROOT / "apps" / "web" / "lib" / "domain" / "schemas.ts"

PAIRS: tuple[tuple[str, type[BaseModel]], ...] = (
    ("ClientLabUploadSchema", ClientLabUploadRead),
    ("LabRunOutcomeSchema", ClientLabRunOutcome),
)


def extract_z_object_fields(source: str, const_name: str) -> dict[str, str]:
    needle = f"export const {const_name} = z.object({{"
    start = source.find(needle)
    if start < 0:
        raise ValueError(f"{const_name} not found in {SCHEMA_FILE}")
    index = start + len(needle)
    depth = 1
    cursor = index
    while cursor < len(source) and depth:
        char = source[cursor]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        cursor += 1
    body = source[index : cursor - 1]
    fields: dict[str, str] = {}
    for raw in body.splitlines():
        line = raw.strip().rstrip(",")
        if not line or line.startswith("//") or ":" not in line:
            continue
        name, expr = line.split(":", 1)
        fields[name.strip()] = expr.strip()
    if not fields:
        raise ValueError(f"{const_name} has no fields")
    return fields


def _unwrap_optional(annotation: Any) -> tuple[bool, Any]:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Union or origin is UnionType:
        non_none = [item for item in args if item is not type(None)]
        if type(None) in args and len(non_none) == 1:
            return True, non_none[0]
    return False, annotation


def _type_compatible(annotation: Any, zod_expr: str) -> bool:
    optional, inner = _unwrap_optional(annotation)
    expr = zod_expr.replace(" ", "")
    if optional and not any(token in expr for token in (".nullable()", ".optional()", ".nullish()")):
        return False
    origin = get_origin(inner)
    if origin is list:
        return "z.array(" in expr or "array(" in expr
    if inner is UUID:
        return "uuid" in expr or "z.string(" in expr
    if inner is datetime:
        return "z.string(" in expr
    if inner is bool:
        return "z.boolean(" in expr
    if inner in (int, float):
        return "z.number(" in expr or "z.coerce.number(" in expr
    if inner is str:
        return "z.string(" in expr or "z.enum(" in expr or "Schema" in zod_expr
    if isinstance(inner, type) and issubclass(inner, enum.Enum):
        return "z.enum(" in expr or "Schema" in zod_expr or "z.string(" in expr
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return "Schema" in zod_expr
    return True


def collect_mismatches(source: str | None = None) -> list[str]:
    text = source if source is not None else SCHEMA_FILE.read_text(encoding="utf-8")
    mismatches: list[str] = []
    for zod_name, model in PAIRS:
        try:
            zod_fields = extract_z_object_fields(text, zod_name)
        except ValueError as exc:
            mismatches.append(str(exc))
            continue
        py_fields = model.model_fields
        for name, zod_expr in zod_fields.items():
            if name not in py_fields:
                mismatches.append(
                    f"{zod_name}.{name} is expected by the frontend but missing on {model.__name__}"
                )
                continue
            annotation = py_fields[name].annotation
            if not _type_compatible(annotation, zod_expr):
                mismatches.append(
                    f"{zod_name}.{name}: Zod `{zod_expr}` is not compatible with "
                    f"{model.__name__}.{name}: {annotation!r}"
                )
    return mismatches


def main() -> int:
    mismatches = collect_mismatches()
    if mismatches:
        print("[FAIL] client Labs Zod/Pydantic contract:")
        for item in mismatches:
            print(f"  {item}")
        return 1
    print("[clean] ClientLabUploadSchema ↔ ClientLabUploadRead")
    print("[clean] LabRunOutcomeSchema ↔ ClientLabRunOutcome")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
