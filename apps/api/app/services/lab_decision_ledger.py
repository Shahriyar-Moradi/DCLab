"""Resolve and persist Lab decisions during auto-train.

After auto_prepare's rule engine runs, ambiguous columns may consult the
evidence → LLM → validator chain. An accepted decision overrides that
column's action or role. Disabled, unavailable, or rejected agent calls fall
back safely. Every missing-value column still gets a ledger row with both the original
rule-engine action (`rule_decision`) and whatever was actually applied
(`final_decision`). Column-type rows are written only for columns the type
agent actually consulted.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LabDecisionRecord, LlmInvocation
from app.engine.lab.auto_prepare import ColumnMissingDecision, MissingValuePlan
from app.engine.lab.decision_validator import (
    validate_column_type_decision,
    validate_decision,
    validate_target_selection_decision,
)
from app.engine.lab.evidence import (
    ColumnEvidence,
    ColumnTypeEvidence,
    build_column_evidence,
    build_column_type_evidence,
    build_target_selection_evidence,
    is_ambiguous_column_type,
)
from app.engine.lab.llm_client import (
    DecisionAgentUnavailable,
    request_column_type_decision,
    request_decision,
    request_target_selection_decision,
)
from app.engine.lab.prompts.column_type_v1 import PROMPT_VERSION as COLUMN_TYPE_PROMPT_VERSION
from app.engine.lab.prompts.missing_value_v1 import PROMPT_VERSION
from app.engine.lab.prompts.target_selection_v1 import PROMPT_VERSION as TARGET_SELECTION_PROMPT_VERSION
from app.engine.lab.schema_inference import TargetChoice, choose_target_deterministically, metric_for_task

logger = logging.getLogger(__name__)

_VERDICT_NOT_RUN = "not_run"

# Inclusive. A numeric column in this missingness band is treated as ambiguous
# even without a co-occurrence flag (mean vs median vs a domain fill is not obvious).
AMBIGUOUS_MISSING_MIN = 0.02
AMBIGUOUS_MISSING_MAX = 0.40

_DETERMINISTIC_REASON = "LLM used: NO — deterministic evidence was sufficient."


def _safe_semantic_output(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep decision metadata, never provider rationale, samples, or fill values."""
    if not isinstance(value, dict):
        return None
    allowed = {
        "action",
        "evidence_field",
        "target",
        "task_type",
        "confidence",
    }
    return {key: value[key] for key in allowed if key in value}


def _target_final_decision(choice: TargetChoice) -> dict[str, Any]:
    return {
        "column": choice.column,
        "task_type": choice.task_type,
        "evaluation_metric": choice.evaluation_metric,
        "confidence": choice.confidence,
        "source": choice.source,
        "validator_verdict": choice.validator_verdict,
    }


def _observe_semantic_decision(
    db: Session | None,
    upload_id: UUID | None,
    *,
    purpose: str,
    prompt_version: str,
    evidence: Any,
    llm_used: bool,
    reason: str,
    status: str,
    validator_verdict: str,
    safe_output: dict[str, Any] | None,
    final_decision: dict[str, Any],
    started_at: datetime | None = None,
    latency_ms: float | None = None,
) -> LlmInvocation | None:
    if db is None or upload_id is None:
        return None
    from app.services.observability_service import create_llm_invocation

    settings = get_settings()
    return create_llm_invocation(
        db,
        upload_id=upload_id,
        purpose=purpose,
        mode="semantic_decision",
        prompt_version=prompt_version,
        schema_version=1,
        evidence=evidence,
        llm_used=llm_used,
        reason=reason,
        status=status,
        validator_verdict=validator_verdict,
        provider="openai" if llm_used else None,
        model=getattr(settings, "decision_agent_model", None) if llm_used else None,
        safe_output=safe_output,
        final_decision=final_decision,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        latency_ms=latency_ms,
    )


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _evidence_snapshot(evidence: ColumnEvidence | ColumnTypeEvidence) -> dict[str, Any]:
    return _jsonable(asdict(evidence))


def _agent_configured() -> bool:
    settings = get_settings()
    return bool(settings.decision_agent_enabled and (settings.decision_agent_api_key or "").strip())


def resolve_target_selection(
    frame: pd.DataFrame,
    columns: list[str],
    *,
    explicit_target: str | None = None,
    db: Session | None = None,
    upload_id: UUID | None = None,
) -> TargetChoice:
    """Apply explicit/rule/LLM/fail-closed target precedence to one dataframe."""
    choice = choose_target_deterministically(frame, columns, explicit_target=explicit_target)
    evidence_summary = {
        "column_count": len(columns),
        "candidate_count": len(choice.candidates),
        "candidate_columns": [item.column for item in choice.candidates],
        "explicit_target": explicit_target,
    }
    if choice.column is not None or explicit_target is not None or not choice.candidates:
        _observe_semantic_decision(
            db,
            upload_id,
            purpose="semantic_target",
            prompt_version=TARGET_SELECTION_PROMPT_VERSION,
            evidence=evidence_summary,
            llm_used=False,
            reason=_DETERMINISTIC_REASON,
            status="not_used",
            validator_verdict=choice.validator_verdict or "not_run",
            safe_output=None,
            final_decision=_target_final_decision(choice),
        )
        return choice
    if not _agent_configured():
        choice.reason += "; semantic target assistance is disabled or unconfigured"
        choice.validator_verdict = "not_run"
        _observe_semantic_decision(
            db,
            upload_id,
            purpose="semantic_target",
            prompt_version=TARGET_SELECTION_PROMPT_VERSION,
            evidence=evidence_summary,
            llm_used=False,
            reason="LLM used: NO — semantic assistance was disabled or unconfigured.",
            status="not_used",
            validator_verdict="not_run",
            safe_output=None,
            final_decision=_target_final_decision(choice),
        )
        return choice

    evidence = build_target_selection_evidence(len(frame), len(columns), choice.candidates)
    started_at = datetime.now(UTC)
    timer = time.perf_counter()
    try:
        decision = request_target_selection_decision(evidence, TARGET_SELECTION_PROMPT_VERSION)
        choice.raw_llm_output = decision.model_dump(mode="json")
        check = validate_target_selection_decision(evidence, decision)
        choice.validator_verdict = check.verdict if check.verdict == "accept" else f"reject: {check.reason}"
        if check.verdict != "accept":
            choice.source = "fallback"
            choice.reason += f"; semantic decision rejected: {check.reason}"
            _observe_semantic_decision(
                db,
                upload_id,
                purpose="semantic_target",
                prompt_version=TARGET_SELECTION_PROMPT_VERSION,
                evidence=asdict(evidence),
                llm_used=True,
                reason="LLM used: YES — validator rejected the semantic target response.",
                status="rejected",
                validator_verdict=choice.validator_verdict,
                safe_output=_safe_semantic_output(choice.raw_llm_output),
                final_decision=_target_final_decision(choice),
                started_at=started_at,
                latency_ms=max(0.001, (time.perf_counter() - timer) * 1000.0),
            )
            return choice
        candidate = next(item for item in choice.candidates if item.column == decision.target)
        choice.column = candidate.column
        choice.task_type = decision.task_type
        choice.evaluation_metric = metric_for_task(decision.task_type)
        choice.confidence = float(decision.confidence)
        choice.source = "llm"
        choice.reason = decision.rationale
        choice.evidence = candidate.evidence
        _observe_semantic_decision(
            db,
            upload_id,
            purpose="semantic_target",
            prompt_version=TARGET_SELECTION_PROMPT_VERSION,
            evidence=asdict(evidence),
            llm_used=True,
            reason="LLM used: YES — deterministic target evidence was ambiguous.",
            status="completed",
            validator_verdict=choice.validator_verdict,
            safe_output=_safe_semantic_output(choice.raw_llm_output),
            final_decision=_target_final_decision(choice),
            started_at=started_at,
            latency_ms=max(0.001, (time.perf_counter() - timer) * 1000.0),
        )
        return choice
    except DecisionAgentUnavailable as exc:
        choice.source = "fallback"
        choice.validator_verdict = f"unavailable: {exc}"
        choice.reason += "; semantic target assistance was unavailable"
        _observe_semantic_decision(
            db,
            upload_id,
            purpose="semantic_target",
            prompt_version=TARGET_SELECTION_PROMPT_VERSION,
            evidence=asdict(evidence),
            llm_used=True,
            reason="LLM used: YES — provider attempt was unavailable; deterministic fallback retained.",
            status="unavailable",
            validator_verdict=choice.validator_verdict,
            safe_output=None,
            final_decision=_target_final_decision(choice),
            started_at=started_at,
            latency_ms=max(0.001, (time.perf_counter() - timer) * 1000.0),
        )
        return choice
    except Exception:  # noqa: BLE001
        logger.exception("target-selection agent failed; refusing to guess a target")
        choice.source = "fallback"
        choice.validator_verdict = "unavailable: unexpected error"
        choice.reason += "; semantic target assistance failed"
        _observe_semantic_decision(
            db,
            upload_id,
            purpose="semantic_target",
            prompt_version=TARGET_SELECTION_PROMPT_VERSION,
            evidence=asdict(evidence),
            llm_used=True,
            reason="LLM used: YES — semantic target processing failed; deterministic fallback retained.",
            status="failed",
            validator_verdict=choice.validator_verdict,
            safe_output=None,
            final_decision=_target_final_decision(choice),
            started_at=started_at,
            latency_ms=max(0.001, (time.perf_counter() - timer) * 1000.0),
        )
        return choice


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
        llm_used = consult_agent and ambiguous
        started_at = datetime.now(UTC) if llm_used else None
        timer = time.perf_counter() if llm_used else None

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

        if not llm_used:
            invocation_reason = (
                _DETERMINISTIC_REASON
                if not ambiguous
                else "LLM used: NO — semantic assistance was disabled or unconfigured."
            )
            invocation_status = "not_used"
        elif verdict == "accept":
            invocation_reason = "LLM used: YES — missing-value evidence was ambiguous."
            invocation_status = "completed"
        elif verdict.startswith("reject:"):
            invocation_reason = "LLM used: YES — validator rejected the missing-value response."
            invocation_status = "rejected"
        else:
            invocation_reason = "LLM used: YES — provider attempt was unavailable; rule retained."
            invocation_status = "unavailable"
        invocation = _observe_semantic_decision(
            db,
            upload_id,
            purpose="semantic_missing_value",
            prompt_version=PROMPT_VERSION,
            evidence=asdict(evidence),
            llm_used=llm_used,
            reason=invocation_reason,
            status=invocation_status,
            validator_verdict=verdict,
            safe_output=_safe_semantic_output(raw),
            final_decision={
                "column": rule.column,
                "rule_decision": original_action,
                "final_decision": rule.action,
                "source": source,
            },
            started_at=started_at,
            latency_ms=(
                max(0.001, (time.perf_counter() - timer) * 1000.0)
                if timer is not None
                else None
            ),
        )
        db.add(
            LabDecisionRecord(
                llm_invocation_id=invocation.id if invocation is not None else None,
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


def _apply_column_type_override(
    numerical: list[str],
    categorical: list[str],
    column: str,
    action: str,
) -> tuple[list[str], list[str]]:
    numerical = [name for name in numerical if name != column]
    categorical = [name for name in categorical if name != column]
    if action == "numerical":
        numerical.append(column)
    elif action == "categorical":
        categorical.append(column)
    return numerical, categorical


def record_column_type_decisions(
    db: Session,
    upload_id: UUID,
    frame: pd.DataFrame,
    numerical_cols: list[str],
    categorical_cols: list[str],
) -> tuple[list[str], list[str]]:
    """Consult the agent on ambiguous numeric columns, apply accepted role overrides.

    Does not delete missing-value ledger rows. Non-ambiguous columns never
    consult the agent and are not written here. Disabled, unavailable, or
    rejected calls leave ``split_column_roles`` lists unchanged.
    """
    numerical = list(numerical_cols)
    categorical = list(categorical_cols)
    consult_agent = _agent_configured()
    original_numerical = set(numerical_cols)

    for column in dict.fromkeys([*numerical_cols, *categorical_cols]):
        if column not in frame.columns:
            continue
        evidence = build_column_type_evidence(frame, column)
        ambiguous = column in original_numerical and is_ambiguous_column_type(
            frame, column, evidence
        )
        original = "numerical" if column in original_numerical else "categorical"
        if not consult_agent or not ambiguous:
            _observe_semantic_decision(
                db,
                upload_id,
                purpose="semantic_column_type",
                prompt_version=COLUMN_TYPE_PROMPT_VERSION,
                evidence=asdict(evidence),
                llm_used=False,
                reason=(
                    _DETERMINISTIC_REASON
                    if not ambiguous
                    else "LLM used: NO — semantic assistance was disabled or unconfigured."
                ),
                status="not_used",
                validator_verdict=_VERDICT_NOT_RUN,
                safe_output=None,
                final_decision={
                    "column": column,
                    "rule_decision": original,
                    "final_decision": original,
                    "source": "rule",
                },
            )
            continue

        final = original
        source = "rule"
        verdict = _VERDICT_NOT_RUN
        raw: dict[str, Any] | None = None
        started_at = datetime.now(UTC)
        timer = time.perf_counter()

        try:
            llm_decision = request_column_type_decision(evidence, COLUMN_TYPE_PROMPT_VERSION)
            raw = llm_decision.model_dump(mode="json")
            check = validate_column_type_decision(evidence, llm_decision)
            if check.verdict == "accept":
                source = "llm"
                verdict = "accept"
                numerical, categorical = _apply_column_type_override(
                    numerical, categorical, column, llm_decision.action
                )
                final = llm_decision.action
            else:
                source = "fallback"
                verdict = (f"reject: {check.reason}")[:1024]
        except DecisionAgentUnavailable as exc:
            source = "rule"
            verdict = (f"unavailable: {exc}")[:1024]
            raw = None
        except Exception:  # noqa: BLE001
            logger.exception(
                "column-type agent failed for column %s; keeping the dtype-based role",
                column,
            )
            source = "rule"
            verdict = "unavailable: unexpected error"
            raw = None

        if verdict == "accept":
            invocation_status = "completed"
            reason = "LLM used: YES — column-type evidence was ambiguous."
        elif verdict.startswith("reject:"):
            invocation_status = "rejected"
            reason = "LLM used: YES — validator rejected the column-type response."
        else:
            invocation_status = "unavailable"
            reason = "LLM used: YES — provider attempt was unavailable; inferred type retained."
        invocation = _observe_semantic_decision(
            db,
            upload_id,
            purpose="semantic_column_type",
            prompt_version=COLUMN_TYPE_PROMPT_VERSION,
            evidence=asdict(evidence),
            llm_used=True,
            reason=reason,
            status=invocation_status,
            validator_verdict=verdict,
            safe_output=_safe_semantic_output(raw),
            final_decision={
                "column": column,
                "rule_decision": original,
                "final_decision": final,
                "source": source,
            },
            started_at=started_at,
            latency_ms=max(0.001, (time.perf_counter() - timer) * 1000.0),
        )
        db.add(
            LabDecisionRecord(
                llm_invocation_id=invocation.id if invocation is not None else None,
                upload_id=upload_id,
                column=column,
                evidence_snapshot=_evidence_snapshot(evidence),
                prompt_version=COLUMN_TYPE_PROMPT_VERSION,
                raw_llm_output=raw,
                validator_verdict=verdict,
                rule_decision=original,
                final_decision=final,
                fill_value=None,
                source=source,
            )
        )
    db.flush()
    return numerical, categorical
