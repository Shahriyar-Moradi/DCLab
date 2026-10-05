"""Stage-2 renderers of a ``Shaped`` tool result (ADR 0009 §6, §8).

* ``to_mcp``: the MCP JSON a ``dclab_mcp`` tool returns (``Text`` →
  ``{"untrusted_text": ..., "truncated"?}``, bounded by ``finalize``).
* ``to_context_fields``: tagged ``ContextField``s for the gateway. Per payload section
  at most three fields: the system part (numbers, flags, ids, code values as code
  keys ``{"<code>": true}``, timestamps as epoch seconds; other DCLab strings such
  as digests and notes are dropped) tagged ``system`` / ``metadata`` — or, for the
  shaper's ``aggregates`` paths, the source datasets / ``aggregates`` / the result's
  outcome scope; ``<key>.text`` with workspace text (``workspace_text`` source,
  ``metadata``; only text a tenant wrote); and ``<key>.data`` with dataset text (the
  source datasets; under an ``aggregates`` path it carries that path's class and scope
  because such text embeds the numbers; elsewhere number-bearing dataset text goes to
  ``<key>.agg``; omitted when no source dataset is recorded: fail closed). ``Untrusted`` carries
  ≤ 4000 chars and no ``truncated`` key; object keys that are not code keys, code
  values that name the holdout, and anything nested deeper than 5 levels are dropped.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from app.agents.contracts import (
    SYSTEM_SOURCE,
    UNTRUSTED_TEXT_MAX_CHARS,
    WORKSPACE_TEXT_SOURCE,
    ContextField,
    FieldSource,
    Untrusted as ContextUntrusted,
)
from app.agents.tools.shaping import HOLDOUT_KEY, Code, Shaped, Text, Untrusted, finalize
from app.domain.agent_records import KEY_PATTERN

_KEY = re.compile(KEY_PATTERN)
_MAX_CONTEXT_DEPTH = 5  # the gateway refuses values nested deeper than 6
_DROP = object()


def mcp_json(value: Any) -> Any:
    if isinstance(value, Text):
        out = Untrusted(untrusted_text=value.text[:value.limit])
        if len(value.text) > value.limit:
            out["truncated"] = True
        return out
    if isinstance(value, (Code, UUID)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: mcp_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [mcp_json(item) for item in value]
    return value


def to_mcp(shaped: Shaped) -> tuple[dict[str, Any], str]:
    return finalize(mcp_json(shaped.payload))


def _uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _matches(text: Text, part: str) -> bool:
    if text.origin == "workspace":
        return part == "workspace"
    return part in ("dataset_any", "dataset_aggregate" if text.aggregate else "dataset")


def _part(value: Any, part: str, depth: int = 0) -> Any:
    """The slice of ``value`` for one field: ``system`` (no text), ``workspace``,
    ``dataset`` / ``dataset_aggregate`` (dataset text without / with embedded numbers) or
    ``dataset_any``, keeping the container structure."""

    if isinstance(value, Text):
        if not _matches(value, part):
            return _DROP
        return ContextUntrusted(untrusted_text=value.text[:min(value.limit, UNTRUSTED_TEXT_MAX_CHARS)])
    if isinstance(value, (dict, list, tuple)):
        if depth >= _MAX_CONTEXT_DEPTH:
            return _DROP
        if isinstance(value, dict):
            out = {key: _part(item, part, depth + 1) for key, item in value.items()
                   if isinstance(key, str) and _KEY.fullmatch(key) and not HOLDOUT_KEY.search(key)}
            out = {key: item for key, item in out.items() if item is not _DROP}
            return out if out else _DROP
        items = [_part(item, part, depth + 1) for item in value]
        if all(item is _DROP for item in items):
            return _DROP
        return [{} if item is _DROP else item for item in items]  # placeholders keep positions aligned
    if part != "system":
        return _DROP
    if isinstance(value, Code):
        return {str(value): True} if _KEY.fullmatch(value) and not HOLDOUT_KEY.search(value) else _DROP
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        parsed = _uuid(value)
        return _DROP if parsed is None else parsed
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    return _DROP


def _pop(payload: dict[str, Any], path: str) -> Any:
    *parents, leaf = path.split(".")
    node: Any = payload
    for key in parents:
        node = node.get(key) if isinstance(node, dict) else None
    return node.pop(leaf, None) if isinstance(node, dict) else None


def _copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def to_context_fields(tool: str, shaped: Shaped) -> tuple[ContextField, ...]:
    payload = _copy(shaped.payload)
    datasets = tuple(FieldSource(kind="dataset", dataset_id=item) for item in dict.fromkeys(shaped.source_datasets))
    sections: list[tuple[str, Any, bool]] = []
    for path in shaped.aggregates:
        value = _pop(payload, path)
        if value is not None:
            sections.append((path, value, True))
    sections = [(key, value, False) for key, value in payload.items()] + sections
    fields: list[ContextField] = []
    for path, value, aggregate in sections:
        key = f"{tool}.{path}"
        system = _part(value, "system")
        if system is not _DROP and system != {}:
            fields.append(ContextField(
                key=key, value=system,
                data_class="aggregates" if aggregate else "metadata",
                outcome_scope=shaped.outcome_scope if aggregate else "none",
                sources=datasets if aggregate else (SYSTEM_SOURCE,),
            ))
        workspace = _part(value, "workspace")
        if workspace is not _DROP:
            fields.append(ContextField(key=f"{key}.text", value=workspace, data_class="metadata",
                                       outcome_scope="none", sources=(WORKSPACE_TEXT_SOURCE,)))
        # Dataset text: under an aggregates path all of it carries the path's class and
        # scope; elsewhere text marked ``aggregate`` gets its own aggregates field. Without
        # a recorded source dataset it is omitted (the gateway refuses unsourced text).
        if not datasets:
            continue
        splits = ((".data", "dataset_any", True),) if aggregate else (
            (".data", "dataset", False), (".agg", "dataset_aggregate", True))
        for suffix, part, is_aggregate in splits:
            dataset = _part(value, part)
            if dataset is not _DROP:
                fields.append(ContextField(key=f"{key}{suffix}", value=dataset,
                                           data_class="aggregates" if is_aggregate else "metadata",
                                           outcome_scope=shaped.outcome_scope if is_aggregate else "none",
                                           sources=datasets))
    return tuple(fields)
