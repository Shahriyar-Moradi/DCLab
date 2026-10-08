"""Deterministic assistant replies while the model is off (ADR 0009 §7.6; P6.3-B2).

Pure functions over the catalog's read tools in agent consumer mode, run with the signed-in
human's own authorization (``ToolContext``): no gateway call, no model, no budget. A reply is
plain text plus ``[label](dclab://<kind>/<id>)`` references to the nodes it names (clients
render it with linkify off); names are the tenant's own text and stay plain text; the only
numbers are counts and the CV score the tools return (they cannot read a final holdout).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.agents.tools.catalog import ToolContext, ToolError, catalog

QUICK_ACTIONS = ("summarize this project", "show latest experiment", "what can you do")
_OFF = "The AI assistant is off for this workspace, so I can only run quick actions: "
_CAN = ("With the AI assistant on, I answer from this project's evidence (experiments, datasets, findings, "
        "decisions) and propose specs, runs, branches, predictions and decisions for you to confirm; I never "
        "act on my own. ")


def _name(value: Any) -> str:
    """A tenant-written name as inert text: no link, image or HTML syntax survives."""

    raw = str(getattr(value, "text", value) or "").translate({ord(c): " " for c in "[]()<>`!"})
    return " ".join(raw.split())[:80] or "untitled"


def _read(ctx: ToolContext, tool: str, **arguments: Any) -> dict[str, Any]:
    return catalog()[tool].read(ctx, {key: str(item) for key, item in arguments.items()}).payload


def _menu(prefix: str = _OFF) -> dict[str, Any]:
    return {"kind": "answer", "message": prefix + "; ".join(f"“{item}”" for item in QUICK_ACTIONS) + ".",
            "citations": []}


def _summary(ctx: ToolContext, project_id: UUID) -> dict[str, Any]:
    found = _read(ctx, "inspect_project", project_id=project_id)
    graph, recent = found["graph"], found["recent_experiments"]
    counts = ", ".join(f"{count} {kind.replace('_', ' ')}" for kind, count in sorted(graph["counts_by_kind"].items())
                       if count) or "no nodes yet"
    stale = sum(graph["stale_counts_by_kind"].values())
    refs = ", ".join(sorted(str(ref["ref_kind"]) for ref in graph["refs"])) or "none"
    message = (f"Project “{_name(found['project']['name'])}”: {counts}"
               f"{f' ({stale} stale)' if stale else ''}. Refs set: {refs}.")
    if not recent:
        return {"kind": "answer", "message": message, "citations": []}
    latest = recent[0]["id"]
    return {"kind": "answer", "citations": [{"kind": "experiment", "id": str(latest)}],
            "message": f"{message} The [latest experiment](dclab://experiment/{latest}) is {recent[0]['status']}."}


def _latest(ctx: ToolContext, project_id: UUID) -> dict[str, Any]:
    recent = _read(ctx, "inspect_project", project_id=project_id)["recent_experiments"]
    if not recent:
        return {"kind": "answer", "message": "This project has no experiment yet.", "citations": []}
    experiment = _read(ctx, "get_experiment", experiment_id=recent[0]["id"])["experiment"]
    metrics = experiment.get("metrics") or {}
    score = metrics.get("selected_score")
    said = f" Selected CV score ({metrics.get('selection_metric')}): {score:.4f}." if isinstance(
        score, (int, float)) and not isinstance(score, bool) else ""
    return {"kind": "answer", "citations": [{"kind": "experiment", "id": str(experiment["id"])}],
            "message": f"Latest experiment: [{str(experiment['id'])[:8]}](dclab://experiment/{experiment['id']}), "
                       f"status {experiment['status']}.{said}"}


def reply(ctx: ToolContext, project_id: UUID, text: str) -> dict[str, Any]:
    """The template for a quick action (matched exactly, case and punctuation aside), else the menu."""

    intent = " ".join(text.lower().split()).strip(" ?.!")
    try:
        if intent == QUICK_ACTIONS[0]:
            return _summary(ctx, project_id)
        if intent == QUICK_ACTIONS[1]:
            return _latest(ctx, project_id)
    except ToolError:
        ctx.db.rollback()
    return _menu((_CAN if intent == QUICK_ACTIONS[2] else "") + _OFF)
