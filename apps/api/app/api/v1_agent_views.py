"""What a service-token (agent) caller may see of the final holdout (P3.4-A).

Non-negotiable #3: nothing is ever selected on the final holdout. Agents compare,
branch and propose, so every /v1 response to a service-token principal omits
final-holdout values: experiment metric summaries and branch diffs, comparisons,
model-build stages and events, and the ``HOLDOUT_METRICS`` literal of exported code.
The single exception is the *current champion's* holdout, returned as
``holdout_report_only`` on ``GET /v1/model-versions/{id}`` (reporting, never
selection); the model card (P4.11-A) withholds its final evaluation for every version. Session humans get every response unchanged. Schemas stay valid:
holdout fields become empty, never removed.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from fastapi import Request

from app.api.deps import request_service_token

_HOLDOUT_KEY = re.compile(r"holdout|final_test", re.IGNORECASE)
# Model-build stages whose summary/configuration carry final-holdout results.
HOLDOUT_RESULT_STAGES = frozenset({"final_holdout"})
_HOLDOUT_LITERAL = re.compile(r"HOLDOUT_METRICS = \{[^{}]*\}")
HOLDOUT_WITHHELD = "HOLDOUT_METRICS = {}  # final-holdout values are withheld from service-token callers"


def is_agent(request: Request) -> bool:
    return request_service_token(request) is not None


def strip_holdout(value: Any) -> Any:
    """Drop keys naming the final holdout and list items scoped to it (recursively)."""

    if isinstance(value, dict):
        return {key: strip_holdout(item) for key, item in value.items() if not _HOLDOUT_KEY.search(str(key))}
    if isinstance(value, list):
        return [
            strip_holdout(item)
            for item in value
            if not (isinstance(item, dict) and _HOLDOUT_KEY.search(str(item.get("scope") or item.get("evaluation_scope") or "")))
        ]
    return value


def withhold_holdout_code(source: str) -> str:
    return _HOLDOUT_LITERAL.sub(HOLDOUT_WITHHELD, source)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def experiment_view(request: Request, body: Any) -> Any:
    if not is_agent(request):
        return body
    update: dict[str, Any] = {"diff_vs_parent": strip_holdout(body.diff_vs_parent)}
    if body.metrics is not None:
        update["metrics"] = body.metrics.model_copy(
            update={"holdout": {}, "baseline_comparison": strip_holdout(body.metrics.baseline_comparison)}
        )
    return body.model_copy(update=update)


def findings_view(request: Request, body: Any) -> Any:
    """Trust-check evidence never carries holdout values; strip any key naming one anyway."""

    if not is_agent(request):
        return body
    checks = [item.model_copy(update={"evidence": strip_holdout(item.evidence)}) for item in body.checks]
    return body.model_copy(update={"checks": checks})


def comparison_view(request: Request, body: Any) -> Any:
    if not is_agent(request):
        return body
    return body.model_copy(update={
        "experiments": [item.model_copy(update={"holdout": {}}) for item in body.experiments],
        "common": body.common.model_copy(update={"holdout": []}),
    })


def model_version_view(request: Request, body: Any) -> Any:
    if not is_agent(request) or body.metrics is None:
        return body
    report = dict(body.metrics.holdout) if body.is_champion and body.metrics.holdout else None
    return body.model_copy(update={
        "metrics": body.metrics.model_copy(update={"holdout": {}}),
        "holdout_report_only": report,
    })


def model_card_view(request: Request, body: Any) -> Any:
    """Agents get the card without the final evaluation, and Markdown re-rendered from
    that withheld card (the string never carries a final-evaluation value)."""

    if not is_agent(request):
        return body
    from app.domain.model_card import FINAL_EVALUATION_WITHHELD, ModelCardFinalEvaluation, render_markdown

    card = body.model_copy(update={
        "final_evaluation": ModelCardFinalEvaluation(status="withheld", note=FINAL_EVALUATION_WITHHELD),
    })
    return card.model_copy(update={"markdown": render_markdown(card)})


def model_build_view(request: Request, body: Any) -> Any:
    if not is_agent(request):
        return body
    stages = []
    for stage in body.stages:
        update: dict[str, Any] = {"configuration": strip_holdout(stage.configuration)}
        if stage.key in HOLDOUT_RESULT_STAGES:
            update.update(configuration={}, decision_summary=None)
        code = stage.generated_code
        if code is not None and "HOLDOUT_METRICS" in code.source:
            source = withhold_holdout_code(code.source)
            update["generated_code"] = code.model_copy(update={"source": source, "digest": _sha256(source)})
        stages.append(stage.model_copy(update=update))
    return body.model_copy(update={"stages": stages})


def event_view(request: Request, event: Any) -> Any:
    if not is_agent(request) or not (_HOLDOUT_KEY.search(event.stage) or _HOLDOUT_KEY.search(event.event_type)):
        return event
    payload = {key: item for key, item in strip_holdout(event.payload).items() if key != "metrics"}
    return event.model_copy(update={"payload": payload})


def code_view(request: Request, body: Any) -> Any:
    if not is_agent(request):
        return body
    docs = {}
    for name in ("script", "notebook"):
        doc = getattr(body, name)
        source = withhold_holdout_code(doc.source)
        docs[name] = doc.model_copy(update={"source": source, "content_digest": _sha256(source)})
    return body.model_copy(update=docs)


AGENT_VISIBLE_EVALUATION_SCOPES = frozenset({"cv_fold", "cv_aggregate"})


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
