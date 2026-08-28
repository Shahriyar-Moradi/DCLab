"""Map an uploaded CSV's columns onto the five admin Lab use cases."""

from __future__ import annotations

import re

from app.domain.lab_use_cases import (
    ENTITY_ALIASES,
    GROUP_KEYWORDS,
    LAB_USE_CASES,
    SKIP_EXACT,
    TIME_ALIASES,
    UseCaseDefinition,
    use_case_by_slug,
)

MIN_TRAIN_ROWS = 40


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _lookup(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    by_norm = {normalize(col): col for col in columns}
    for alias in aliases:
        hit = by_norm.get(normalize(alias))
        if hit is not None:
            return hit
    return None


def pick_entity_column(columns: list[str]) -> str | None:
    return _lookup(columns, ENTITY_ALIASES) or columns[0]


def pick_time_column(columns: list[str]) -> str | None:
    return _lookup(columns, TIME_ALIASES)


def pick_target(columns: list[str], use_case: UseCaseDefinition) -> str | None:
    return _lookup(columns, use_case.target_aliases)


def _is_id_column(name: str, entity: str | None) -> bool:
    norm = normalize(name)
    if name == entity:
        return True
    if norm in SKIP_EXACT:
        return True
    if norm.endswith("_id") or norm == "id" or norm.endswith("_uuid"):
        return True
    return False


def assign_group(column: str) -> str:
    norm = normalize(column)
    for group, tokens in GROUP_KEYWORDS:
        if any(token in norm for token in tokens):
            return group
    return "attributes"


def build_feature_groups(
    columns: list[str],
    *,
    target: str,
    holdouts: set[str],
    entity: str | None,
    time_col: str | None,
    preferred: tuple[str, ...],
) -> dict[str, list[str]]:
    """Partition remaining columns into named groups, preferring the use-case mix."""
    grouped: dict[str, list[str]] = {}
    for column in columns:
        if column == target or column == time_col or column in holdouts or _is_id_column(column, entity):
            continue
        grouped.setdefault(assign_group(column), []).append(column)
    if not grouped:
        return {}
    if preferred:
        narrowed = {name: cols for name, cols in grouped.items() if name in preferred}
        if sum(len(cols) for cols in narrowed.values()) >= 2:
            grouped = narrowed or grouped
    return {name: cols for name, cols in grouped.items() if cols}


def task_slug_for(use_case: str, dataset_id: str) -> str:
    compact = dataset_id.replace("-", "")[:8]
    return f"{use_case}_{compact}"


def parse_use_case_slug(task_slug: str) -> str | None:
    prefix = task_slug.rsplit("_", 1)[0] if "_" in task_slug else task_slug
    if use_case_by_slug(prefix):
        return prefix
    return None


def planned_targets(columns: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in LAB_USE_CASES:
        target = pick_target(columns, item)
        if target:
            found[item.slug] = target
    return found
