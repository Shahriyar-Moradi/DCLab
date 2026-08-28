"""Reads the latest completed simulation run per use case and translates it into
client-facing insights, grouped by business function.

This is deliberately read-only and admin-write-only: an admin runs `/admin/simulations/run`
(a real, potentially slow re-training pass) whenever they want to refresh a use case's
demo data; the client only ever reads the most recent result, already translated. Step 5
(Client Labs) is what lets a client trigger new runs themselves, bounded and translated.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import SimulationRun
from app.translation.models import ClientFacingInsight, InsightCategory
from app.translation.simulations import CATEGORY_BY_USE_CASE, translate_simulation_outcome

MAX_INSIGHTS_PER_USE_CASE = 4


def latest_runs_by_use_case(db: Session) -> dict[str, SimulationRun]:
    latest = (
        select(SimulationRun.use_case, func.max(SimulationRun.created_at).label("max_created_at"))
        .group_by(SimulationRun.use_case)
        .subquery()
    )
    rows = db.scalars(
        select(SimulationRun).join(
            latest,
            (SimulationRun.use_case == latest.c.use_case)
            & (SimulationRun.created_at == latest.c.max_created_at),
        )
    ).all()
    return {row.use_case: row for row in rows}


def _candidate_items(run: SimulationRun) -> list[dict[str, Any]]:
    """Curated heroes first (business-chosen representative examples), then a few
    holdout samples, deduplicated by entity — capped so a category section stays
    readable rather than dumping an entire holdout set."""
    payload = run.payload or {}
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for item in list(payload.get("heroes") or []) + list(payload.get("sample_decisions") or []):
        external_id = str(item.get("external_id") or "")
        if not external_id or external_id in seen:
            continue
        seen.add(external_id)
        items.append(item)
        if len(items) >= MAX_INSIGHTS_PER_USE_CASE:
            break
    return items


def _translate_run(use_case_name: str, run: SimulationRun) -> list[ClientFacingInsight]:
    insights: list[ClientFacingInsight] = []
    for item in _candidate_items(run):
        try:
            insights.append(
                translate_simulation_outcome(
                    use_case_name,
                    external_id=str(item.get("external_id")),
                    features=dict(item.get("features") or {}),
                    agreement=float(item.get("agreement") or 0.0),
                    recommended_action_key=str(item.get("action_key") or "do_nothing"),
                    expected_value=float(item.get("expected_value") or 0.0),
                    incremental_value=float(item.get("incremental_value") or 0.0),
                    generated_at=run.created_at,
                )
            )
        except (TypeError, ValueError):
            continue
    return insights


def list_client_insights(db: Session) -> dict[InsightCategory, list[ClientFacingInsight]]:
    grouped: dict[InsightCategory, list[ClientFacingInsight]] = {category: [] for category in InsightCategory}
    for use_case_name, run in latest_runs_by_use_case(db).items():
        category = CATEGORY_BY_USE_CASE.get(use_case_name)
        if category is None:
            continue
        grouped[category].extend(_translate_run(use_case_name, run))
    return grouped
