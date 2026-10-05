"""What a service-token (agent) caller may see of the final holdout (P3.4-A, P6.10-A).

Non-negotiable #3 and ADR 0008 §2b ("no holdout, ever"): agents compare, branch and
propose, so every /v1 response to a service-token principal omits final-holdout
values: experiment metric summaries and branch diffs, comparisons, model versions
(``holdout_report_only`` stays null: there is no champion exception), model-build
stages and events, model cards (final evaluation withheld), decision records (facts,
details and evidence refs without holdout keys or scopes) and the
``HOLDOUT_METRICS`` literal of exported code. Session humans get every response
unchanged. Schemas stay valid: holdout fields become empty, never removed. The
request-free helpers live in ``app/agents/tools/shaping.py`` (shared with the
agent tool catalog); this module only decides who is an agent.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.agents.tools import shaping
from app.api.deps import request_service_token

AGENT_VISIBLE_EVALUATION_SCOPES = frozenset({"cv_fold", "cv_aggregate"})


def is_agent(request: Request) -> bool:
    return request_service_token(request) is not None


def _for_agents(request: Request, body: Any, view: Any) -> Any:
    return view(body) if is_agent(request) else body


def experiment_view(request: Request, body: Any) -> Any:
    return _for_agents(request, body, shaping.withhold_experiment)


def findings_view(request: Request, body: Any) -> Any:
    """Trust-check evidence never carries holdout values; strip any key naming one anyway."""

    return _for_agents(request, body, shaping.withhold_findings)


def comparison_view(request: Request, body: Any) -> Any:
    return _for_agents(request, body, shaping.withhold_comparison)


def model_version_view(request: Request, body: Any) -> Any:
    return _for_agents(request, body, shaping.withhold_model_version)


def model_card_view(request: Request, body: Any) -> Any:
    """Agents get the card without the final evaluation, and Markdown re-rendered from
    that withheld card (the string never carries a final-evaluation value)."""

    return _for_agents(request, body, shaping.withhold_model_card)


def decision_view(request: Request, body: Any) -> Any:
    return _for_agents(request, body, shaping.withhold_decision)


def decision_page_view(request: Request, page: Any) -> Any:
    if not is_agent(request):
        return page
    return page.model_copy(update={"items": [shaping.withhold_decision(item) for item in page.items]})


def model_build_view(request: Request, body: Any) -> Any:
    return _for_agents(request, body, shaping.withhold_model_build)


def event_view(request: Request, event: Any) -> Any:
    return _for_agents(request, event, shaping.withhold_event)


def code_view(request: Request, body: Any) -> Any:
    return _for_agents(request, body, shaping.withhold_code)


def visualization_rows_view(request: Request, db: Any, rows: list[Any]) -> list[Any]:
    """Agents only see charts tied to a cross-validation evaluation (fail closed:
    slice/robustness/calibration charts may be computed on the final holdout)."""

    if not is_agent(request):
        return rows
    from app.db.models import ModelEvaluation

    ids = {row.model_evaluation_id for row in rows if row.model_evaluation_id is not None}
    if not ids:
        return []
    scopes = dict(
        db.query(ModelEvaluation.id, ModelEvaluation.evaluation_scope)
        .filter(ModelEvaluation.id.in_(ids))
        .all()
    )
    return [row for row in rows if scopes.get(row.model_evaluation_id) in AGENT_VISIBLE_EVALUATION_SCOPES]
