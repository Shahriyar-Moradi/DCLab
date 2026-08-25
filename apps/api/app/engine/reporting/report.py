"""Markdown + JSON experiment reports."""

from __future__ import annotations

from typing import Any


def render_markdown(result: dict[str, Any]) -> str:
    task = result.get("task") or {}
    funnel = result.get("funnel") or {}
    best = result.get("best_single") or {}
    test = result.get("test_metrics") or {}
    groups = result.get("feature_group_scores") or {}
    lines = [
        f"# DCLab Experiment — {task.get('name') or task.get('id') or 'untitled'}",
        "",
        f"**Task:** {task.get('id')}",
        f"**Type:** {task.get('task_type')}",
        f"**Horizon (days):** {task.get('prediction_horizon_days')}",
        f"**Rows:** {(result.get('profile_summary') or {}).get('row_count')}",
        f"**Candidates generated:** {funnel.get('generated')}",
        f"**Trained:** {funnel.get('trained')}",
        f"**Failed:** {funnel.get('failed')}",
        f"**Robust:** {funnel.get('robust')}",
        f"**Diverse selected:** {funnel.get('diverse')}",
        f"**Leakage risk:** {(result.get('leakage') or {}).get('risk')}",
        f"**Split:** {(result.get('split') or {}).get('strategy')}",
        f"**Fusion:** {result.get('fusion')}",
        "",
        "## Best single model",
        "",
        f"- Family: {best.get('model_family')}",
        f"- Groups: {', '.join(best.get('feature_groups') or [])}",
        f"- Validation score: {best.get('score')}",
        "",
        "## Test metrics (held out)",
        "",
    ]
    for key, value in test.items():
        if isinstance(value, dict):
            lines.append(f"- {key}: {value}")
        else:
            lines.append(f"- {key}: {value}")
    lines += ["", "## Comparison (validation)", ""]
    for row in result.get("baselines") or []:
        lines.append(
            f"- baseline {row.get('model_family')} / {'+'.join(row.get('feature_groups') or [])}: {row.get('score')}"
        )
    if best:
        lines.append(f"- best single: {best.get('model_family')} ({best.get('score')})")
    if result.get("fusion"):
        lines.append(f"- fusion: {result.get('fusion')} weights={result.get('weights')}")
    lines += ["", "## Feature group usefulness", ""]
    ranked = sorted(groups.items(), key=lambda item: item[1], reverse=True)
    for name, score in ranked:
        lines.append(f"- {name}: {score:.4f}")
    lines += ["", "## Combinations", ""]
    for row in result.get("combination_table") or []:
        lines.append(f"- {' + '.join(row['groups'])}: {row['best_score']:.4f} ({row['n_candidates']} candidates)")
    warnings = (result.get("leakage") or {}).get("findings") or []
    if warnings:
        lines += ["", "## Warnings", ""]
        for item in warnings[:20]:
            lines.append(f"- {item.get('column')}: {item.get('risk')} ({', '.join(item.get('reasons') or [])})")
    lines.append("")
    return "\n".join(lines)
