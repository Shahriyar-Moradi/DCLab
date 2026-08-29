"""Resolve and persist missing-value decisions during auto-train.

After auto_prepare's rule engine runs, ambiguous columns may consult the
evidence → LLM → validator chain. An accepted decision overrides that
column's action (and domain_fill is applied to the frame). Disabled,
unavailable, or rejected agent calls leave auto_prepare's action unchanged.
Non-ambiguous columns never consult the agent.

Every column still gets a ledger row with both the original rule-engine
action (`rule_decision`) and whatever was actually applied (`final_decision`).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LabDecisionRecord
from app.engine.lab.auto_prepare import ColumnMissingDecision, MissingValuePlan
from app.engine.lab.decision_validator import validate_decision
from app.engine.lab.evidence import ColumnEvidence, build_column_evidence
from app.engine.lab.llm_client import DecisionAgentUnavailable, request_decision
from app.engine.lab.prompts.missing_value_v1 import PROMPT_VERSION

logger = logging.getLogger(__name__)

_VERDICT_NOT_RUN = "not_run"

# Inclusive. A numeric column in this missingness band is treated as ambiguous
# even without a co-occurrence flag (mean vs median vs a domain fill is not obvious).
AMBIGUOUS_MISSING_MIN = 0.02
AMBIGUOUS_MISSING_MAX = 0.40


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _evidence_snapshot(evidence: ColumnEvidence) -> dict[str, Any]:
    return _jsonable(asdict(evidence))


def _agent_configured() -> bool:
    settings = get_settings()
    return bool(settings.decision_agent_enabled and (settings.decision_agent_api_key or "").strip())


def is_ambiguous_column(
    rule: ColumnMissingDecision,
    evidence: ColumnEvidence,
    frame: pd.DataFrame,
) -> bool:
    """True when the rule-engine action is not obviously the only reasonable call."""
    if evidence.missingness_cooccurrence:
        return True
    if rule.column not in frame.columns:
        return False
    if not pd.api.types.is_numeric_dtype(frame[rule.column]):
        return False
    return AMBIGUOUS_MISSING_MIN <= rule.missing_fraction <= AMBIGUOUS_MISSING_MAX


def _apply_accepted_override(frame: pd.DataFrame, rule: ColumnMissingDecision, action: str, fill_value: Any) -> None:
    rule.action = action
    rule.fill_value = fill_value
    if action == "domain_fill" and fill_value is not None and rule.column in frame.columns:
        frame[rule.column] = frame[rule.column].fillna(fill_value)


def record_missing_value_decisions(
    db: Session,
    upload_id: UUID,
    frame: pd.DataFrame,
    missing_plan: MissingValuePlan,
    target: str | None,
) -> pd.DataFrame:
    """Consult the agent on ambiguous columns, apply accepted overrides, persist the ledger.

    Mutates `missing_plan.column_decisions` and `frame` when an override is applied.
    Re-running the job for the same upload replaces the previous rows.
    """
    db.query(LabDecisionRecord).filter(LabDecisionRecord.upload_id == upload_id).delete(
        synchronize_session=False
    )

    consult_agent = _agent_configured()
    for rule in missing_plan.column_decisions:
        if rule.column not in frame.columns:
            continue
        original_action = rule.action
        evidence = build_column_evidence(frame, rule.column, target=target)
        source = "rule"
        verdict = _VERDICT_NOT_RUN
        raw: dict[str, Any] | None = None
        applied_fill: Any = None
        ambiguous = is_ambiguous_column(rule, evidence, frame)

        if consult_agent and ambiguous:
            try:
                llm_decision = request_decision(evidence, PROMPT_VERSION)
                raw = llm_decision.model_dump(mode="json")
                check = validate_decision(evidence, llm_decision)
                if check.verdict == "accept":
                    source = "llm"
                    verdict = "accept"
                    _apply_accepted_override(frame, rule, llm_decision.action, llm_decision.fill_value)
                    applied_fill = llm_decision.fill_value
                else:
                    source = "fallback"
                    verdict = (f"reject: {check.reason}")[:1024]
            except DecisionAgentUnavailable as exc:
                source = "rule"
                verdict = (f"unavailable: {exc}")[:1024]
                raw = None
            except Exception:  # noqa: BLE001
                logger.exception(
                    "decision agent failed for column %s; keeping the rule-engine action",
                    rule.column,
                )
                source = "rule"
                verdict = "unavailable: unexpected error"
                raw = None

        db.add(
            LabDecisionRecord(
                upload_id=upload_id,
                column=rule.column,
                evidence_snapshot=_evidence_snapshot(evidence),
                prompt_version=PROMPT_VERSION,
                raw_llm_output=raw,
                validator_verdict=verdict,
                rule_decision=original_action,
                final_decision=rule.action,
                fill_value=_jsonable(applied_fill) if applied_fill is not None else None,
                source=source,
            )
        )
    db.flush()
    return frame
