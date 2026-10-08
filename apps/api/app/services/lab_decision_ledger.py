"""Resolve and persist Lab decisions during auto-train.

After auto_prepare's rule engine runs, ambiguous columns may consult the
evidence → AI gateway → validator chain (``llm_client.consult``; ADR 0009 §4). An
accepted decision overrides that column's action or role. Disabled, refused,
unavailable, or rejected calls fall back safely. A real gateway call's
``llm_invocations`` row is written by the gateway ledger (one per call, with this
module's legacy wording as a ``LedgerNote``); deterministic ``llm_used = false`` rows
are written here as before. Every missing-value column still gets a ledger row with
both the original rule-engine action (`rule_decision`) and whatever was actually
applied (`final_decision`). Column-type rows are written only for columns the type
agent actually consulted.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.gateway.contract import LedgerNote, Refusal
from app.agents.legacy import LegacyContext, context_for_upload, refusal_text
from app.config import get_settings
from app.db.models import LabDecisionRecord, LlmInvocation
from app.engine.lab.auto_prepare import ColumnMissingDecision, MissingValuePlan
from app.engine.lab.decision_validator import (
    ValidationResult,
    validate_column_type_decision,
    validate_decision,
    validate_leakage_review_decision,
    validate_target_selection_decision,
)
from app.engine.lab.evidence import (
    ColumnEvidence,
    ColumnTypeEvidence,
    LeakageReviewEvidence,
    build_column_evidence,
    build_column_type_evidence,
    build_target_selection_evidence,
    is_ambiguous_column_type,
)
from app.engine.lab.llm_client import WITHHELD, consult
from app.engine.lab.prompts.column_type_v2 import PROMPT_VERSION as COLUMN_TYPE_PROMPT_VERSION
from app.engine.lab.prompts.leakage_review_v1 import PROMPT_VERSION as LEAKAGE_PROMPT_VERSION
from app.engine.lab.prompts.missing_value_v3 import PROMPT_VERSION
from app.engine.lab.prompts.target_selection_v1 import PROMPT_VERSION as TARGET_SELECTION_PROMPT_VERSION
from app.engine.lab.schema_inference import TargetChoice, choose_target_deterministically

logger = logging.getLogger(__name__)

_VERDICT_NOT_RUN = "not_run"

# Inclusive. A numeric column in this missingness band is treated as ambiguous
# even without a co-occurrence flag (mean vs median vs a domain fill is not obvious).
AMBIGUOUS_MISSING_MIN = 0.02
AMBIGUOUS_MISSING_MAX = 0.40

_DETERMINISTIC_REASON = "LLM used: NO — deterministic evidence was sufficient."
_DISABLED_REASON = "LLM used: NO — semantic assistance was disabled or unconfigured."


@dataclass(frozen=True)
class _Wording:
    accepted: str
    rejected: str
    retained: str


_MISSING_VALUE = _Wording("LLM used: YES — missing-value evidence was ambiguous.",
                          "LLM used: YES — validator rejected the missing-value response.", "rule retained")
_COLUMN_TYPE = _Wording("LLM used: YES — column-type evidence was ambiguous.",
                        "LLM used: YES — validator rejected the column-type response.", "inferred type retained")
_TARGET = _Wording("LLM used: YES — deterministic target evidence was ambiguous.",
                   "LLM used: YES — validator rejected the semantic target response.",
                   "deterministic fallback retained")
_LEAKAGE = _Wording("LLM used: YES — leakage evidence was ambiguous; recommendation is advisory.",
                    "LLM used: YES — validator rejected the leakage recommendation.", "rule assessment retained")


def _refused_reason(wording: _Wording, refusal: Refusal | None, llm_used: bool) -> str:
    code = refusal.code if refusal is not None else "provider_error"
    if llm_used:
        return f"LLM used: YES — provider attempt was unavailable ({code}); {wording.retained}."
    return f"LLM used: NO — the AI gateway refused the call ({code}); {wording.retained}."


def _unavailable(refusal: Refusal | None) -> str:
    return f"unavailable: {refusal_text(refusal)}"[:1024]


def _verdict(check: ValidationResult) -> str:
    return "accept" if check.verdict == "accept" else f"reject: {check.reason}"[:1024]


def _safe_semantic_output(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep decision metadata, never provider rationale, samples, or fill values."""
    if not isinstance(value, dict):
        return None
    allowed = {
        "action",
        "evidence_field",
        "target",
        "task_type",
        "availability_status",
        "risk_level",
        "confidence",
    }
    return {key: value[key] for key in allowed if key in value}


@dataclass
class _Consulted:
    decision: Any | None
    check: ValidationResult | None
    verdict: str
    invocation_id: UUID | None
    refusal: Refusal | None = None


def _consult(
    kind: str,
    evidence: Any,
    context: LegacyContext | None,
    *,
    wording: _Wording,
    judge: Callable[[Any], ValidationResult],
    final_decision: Callable[[_Consulted], dict[str, Any]],
    target: str | None = None,
) -> _Consulted:
    """One gateway call; its ledger row carries the legacy reason, verdict and final
    decision. Never raises: an unexpected error is an unavailable consultation."""

    def judged(decision: Any, refusal: Refusal | None, invocation_id: UUID | None = None) -> _Consulted:
        if decision is None:
            return _Consulted(None, None, _unavailable(refusal), invocation_id, refusal)
        check = judge(decision)
        return _Consulted(decision, check, _verdict(check), invocation_id)

    def note(decision: Any, refusal: Refusal | None, llm_used: bool) -> LedgerNote:
        outcome = judged(decision, refusal)
        if outcome.check is None:
            return LedgerNote(reason=_refused_reason(wording, refusal, llm_used), validator_verdict=outcome.verdict,
                              final_decision=final_decision(outcome))
        accepted = outcome.check.verdict == "accept"
        return LedgerNote(
            reason=wording.accepted if accepted else wording.rejected, validator_verdict=outcome.verdict,
            final_decision=final_decision(outcome), rejected=not accepted,
            safe_output=_safe_semantic_output(decision.model_dump(mode="json")),
        )

    try:
        response = consult(kind, evidence, context=context, annotate=note, target=target)
        ok = response.ok and response.output is not None
        return judged(response.output if ok else None, response.refusal, response.invocation_id)
    except Exception:  # noqa: BLE001 - the ML pipeline never fails on advisory AI
        logger.exception("%s consultation failed; the rule value is kept", kind)
        return _Consulted(None, None, "unavailable: unexpected error", None)


def _target_final_decision(choice: TargetChoice) -> dict[str, Any]:
    return {
        "column": choice.column,
        "task_type": choice.task_type,
        "evaluation_metric": choice.evaluation_metric,
        "confidence": choice.confidence,
        "source": choice.source,
        "intent_source": choice.intent_source,
        "validator_verdict": choice.validator_verdict,
    }


def _observe_semantic_decision(
    db: Session | None,
    upload_id: UUID | None,
    *,
    purpose: str,
    prompt_version: str,
    evidence: Any,
    reason: str,
    status: str,
    validator_verdict: str,
    final_decision: dict[str, Any],
) -> LlmInvocation | None:
    """A deterministic row (no provider call): ``llm_used = false``."""
    if db is None or upload_id is None:
        return None
    from app.services.observability_service import create_llm_invocation

    return create_llm_invocation(
        db,
        upload_id=upload_id,
        purpose=purpose,
        mode="semantic_decision",
        prompt_version=prompt_version,
        schema_version=1,
        evidence=evidence,
        llm_used=False,
        reason=reason,
        status=status,
        validator_verdict=validator_verdict,
        safe_output=None,
        final_decision=final_decision,
        completed_at=datetime.now(UTC),
    )


def _ledger_row_id(
    consulted: _Consulted,
    db: Session | None,
    upload_id: UUID | None,
    *,
    purpose: str,
    prompt_version: str,
    evidence: Any,
    wording: _Wording,
    final_decision: dict[str, Any],
) -> UUID | None:
    """The gateway's row, or (refused before the gateway wrote one) a deterministic row."""

    if consulted.invocation_id is not None:
        return consulted.invocation_id
    row = _observe_semantic_decision(
        db, upload_id, purpose=purpose, prompt_version=prompt_version, evidence=evidence,
        reason=_refused_reason(wording, consulted.refusal, False), status="refused",
        validator_verdict=consulted.verdict, final_decision=final_decision,
    )
    return row.id if row is not None else None


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _evidence_snapshot(evidence: ColumnEvidence | ColumnTypeEvidence) -> dict[str, Any]:
    return _jsonable(asdict(evidence))


def _agent_configured() -> bool:
    """``AI_ENABLED`` (``llm_client.agent_enabled``); kill switches act per call."""
    return bool(getattr(get_settings(), "ai_enabled", False))


def _target_outcome(choice: TargetChoice, consulted: _Consulted) -> TargetChoice:
    """The choice after one semantic consultation (a copy; pure): the rule's choice with the
    agent's answer in ``raw_llm_output`` and its verdict."""
    outcome = copy.copy(choice)
    outcome.validator_verdict = consulted.verdict
    outcome.source = "fallback"
    decision, check = consulted.decision, consulted.check
    if decision is None or check is None:
        outcome.reason += "; semantic target assistance was unavailable"
        return outcome
    outcome.raw_llm_output = decision.model_dump(mode="json")
    if check.verdict != "accept":
        outcome.reason += f"; semantic decision rejected: {check.reason}"
        return outcome
    # P6.9-A: target.column is L1 (ADR 0008 §1): the accepted answer is a suggestion a person
    # confirms (the needs_input target confirmation), never the run's target.
    outcome.reason += f"; semantic suggestion {decision.target!r} recorded for confirmation (target.column is L1)"
    return outcome


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
            reason=_DETERMINISTIC_REASON,
            status="not_used",
            validator_verdict=choice.validator_verdict or "not_run",
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
            reason=_DISABLED_REASON,
            status="not_used",
            validator_verdict="not_run",
            final_decision=_target_final_decision(choice),
        )
        return choice

    evidence = build_target_selection_evidence(len(frame), len(columns), choice.candidates)
    consulted = _consult(
        "target_selection", evidence, context_for_upload(db, upload_id), wording=_TARGET,
        judge=lambda decision: validate_target_selection_decision(evidence, decision),
        final_decision=lambda item: _target_final_decision(_target_outcome(choice, item)),
    )
    outcome = _target_outcome(choice, consulted)
    _ledger_row_id(consulted, db, upload_id, purpose="semantic_target",
                   prompt_version=TARGET_SELECTION_PROMPT_VERSION, evidence=asdict(evidence), wording=_TARGET,
                   final_decision=_target_final_decision(outcome))
    return outcome


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


def _source(check: ValidationResult | None) -> str:
    if check is None:
        return "rule"
    return "llm" if check.verdict == "accept" else "fallback"


@dataclass
class LegacyMissing:
    """One column's legacy decision-agent consultation (P6.9-A: an interim AI answer of
    ``column.missing_value_action``; the decision-point hook decides what is applied)."""

    column: str
    rule_action: str
    action: str | None  # the validator-accepted answer, else None
    verdict: str
    check_source: str  # rule (not consulted / unavailable) | llm (accepted) | fallback (rejected)
    raw: dict[str, Any] | None
    invocation_id: UUID | None
    evidence: Any


def consult_missing_values(
    db: Session,
    upload_id: UUID,
    frame: pd.DataFrame,
    missing_plan: MissingValuePlan,
    target: str | None,
    *,
    consult_agent: bool = True,
) -> dict[str, LegacyMissing]:
    """Consult the agent on ambiguous columns (missing_value v3) and write each column's
    ``llm_invocations`` row; applies nothing (``record_missing_value_decisions`` persists the
    ledger rows once the decision point has applied what its level allows). Each row is
    flushed so the session stays clean for the next gateway call."""

    consult_agent = consult_agent and _agent_configured()
    context = context_for_upload(db, upload_id) if consult_agent else None
    withheld = WITHHELD["missing_value"]
    out: dict[str, LegacyMissing] = {}
    for rule in missing_plan.column_decisions:
        if rule.column not in frame.columns:
            continue
        original_action = rule.action
        evidence = build_column_evidence(frame, rule.column, target=target)
        ambiguous = is_ambiguous_column(rule, evidence, frame)

        def final(item: _Consulted, column: str = rule.column, original: str = original_action) -> dict[str, Any]:
            decided = {"column": column, "rule_decision": original, "final_decision": original,
                       "source": _source(item.check)}
            if item.decision is not None:  # what the decision point weighs (it applies by level)
                decided["ai_decision"] = item.decision.action if _source(item.check) == "llm" else None
            return decided

        if consult_agent and ambiguous:
            consulted = _consult(
                "missing_value", evidence, context, wording=_MISSING_VALUE, target=target, final_decision=final,
                judge=lambda decision, item=evidence: validate_decision(item, decision, withheld=withheld),
            )
            invocation_id = _ledger_row_id(
                consulted, db, upload_id, purpose="semantic_missing_value", prompt_version=PROMPT_VERSION,
                evidence=asdict(evidence), wording=_MISSING_VALUE, final_decision=final(consulted),
            )
            accepted = consulted.decision is not None and _source(consulted.check) == "llm"
            out[rule.column] = LegacyMissing(
                rule.column, original_action, consulted.decision.action if accepted else None, consulted.verdict,
                _source(consulted.check),
                consulted.decision.model_dump(mode="json") if consulted.decision is not None else None,
                invocation_id, evidence)
        else:
            invocation = _observe_semantic_decision(
                db,
                upload_id,
                purpose="semantic_missing_value",
                prompt_version=PROMPT_VERSION,
                evidence=asdict(evidence),
                reason=_DETERMINISTIC_REASON if not ambiguous else _DISABLED_REASON,
                status="not_used",
                validator_verdict=_VERDICT_NOT_RUN,
                final_decision=final(_Consulted(None, None, _VERDICT_NOT_RUN, None)),
            )
            out[rule.column] = LegacyMissing(rule.column, original_action, None, _VERDICT_NOT_RUN, "rule", None,
                                             invocation.id if invocation is not None else None, evidence)
        db.flush()
    return out


def revert_missing_value_decisions(db: Session, upload_id: UUID, columns: set[str]) -> None:
    """Columns whose deferred value the decision point rolled back after the ledger rows were
    written (a §1b role conflict or a failed record): the rows show the rule's action again,
    before the scientific lineage reads them."""

    if not columns:
        return
    for row in db.scalars(select(LabDecisionRecord).where(LabDecisionRecord.upload_id == upload_id,
                                                          LabDecisionRecord.column.in_(sorted(columns)))):
        row.final_decision = row.rule_decision
        if row.source == "llm":
            row.source = "rule"
    db.flush()


def record_missing_value_decisions(
    db: Session,
    upload_id: UUID,
    frame: pd.DataFrame,
    missing_plan: MissingValuePlan,
    target: str | None,
    consulted: dict[str, LegacyMissing] | None = None,
    agent_applied: set[str] | frozenset[str] = frozenset(),
) -> pd.DataFrame:
    """Persist one ledger row per missing-value column: the rule-engine action
    (``rule_decision``) and what was applied (``final_decision``, after the decision point;
    ``source`` ``llm`` only for ``agent_applied`` columns, whose value the decision point took
    from the agent's answer). Re-running the job for the same upload replaces the previous
    rows. ``consulted`` comes from ``consult_missing_values`` (called here when absent).
    Never applies an answer itself."""

    db.query(LabDecisionRecord).filter(LabDecisionRecord.upload_id == upload_id).delete(
        synchronize_session=False
    )
    if consulted is None:
        consulted = consult_missing_values(db, upload_id, frame, missing_plan, target)
    for rule in missing_plan.column_decisions:
        item = consulted.get(rule.column)
        if item is None:
            continue
        applied = rule.column in agent_applied and rule.action == item.action
        source = "llm" if applied else ("fallback" if item.check_source == "fallback" else "rule")
        db.add(
            LabDecisionRecord(
                llm_invocation_id=item.invocation_id,
                upload_id=upload_id,
                column=rule.column,
                evidence_snapshot=_evidence_snapshot(item.evidence),
                prompt_version=PROMPT_VERSION,
                raw_llm_output=item.raw,
                validator_verdict=item.verdict,
                rule_decision=item.rule_action,
                final_decision=rule.action,
                fill_value=None,
                source=source,
            )
        )
        db.flush()
    return frame


def record_column_type_decisions(
    db: Session,
    upload_id: UUID,
    frame: pd.DataFrame,
    numerical_cols: list[str],
    categorical_cols: list[str],
) -> tuple[list[str], list[str]]:
    """Consult the agent on ambiguous numeric columns and record its answer (advisory).

    P6.9-A: role changes are applied only by the ``column.semantic_role`` decision point
    (``auto_train.decision_points``), so the lists come back unchanged; an accepted answer is
    recorded (``raw_llm_output``, verdict) beside the inferred type. Does not delete
    missing-value ledger rows. Non-ambiguous columns never consult the agent and are not
    written here.
    """
    numerical = list(numerical_cols)
    categorical = list(categorical_cols)
    consult_agent = _agent_configured()
    context = context_for_upload(db, upload_id) if consult_agent else None
    original_numerical = set(numerical_cols)
    withheld = WITHHELD["column_type"]

    for column in dict.fromkeys([*numerical_cols, *categorical_cols]):
        if column not in frame.columns:
            continue
        evidence = build_column_type_evidence(frame, column)
        ambiguous = column in original_numerical and is_ambiguous_column_type(
            frame, column, evidence
        )
        original = "numerical" if column in original_numerical else "categorical"

        def final(item: _Consulted, name: str = column, rule_role: str = original) -> dict[str, Any]:
            decided = {"column": name, "rule_decision": rule_role, "final_decision": rule_role,
                       "source": _source(item.check)}
            if item.decision is not None:  # advisory: the role point applies role changes
                decided["ai_decision"] = item.decision.action if _source(item.check) == "llm" else None
            return decided

        if not consult_agent or not ambiguous:
            _observe_semantic_decision(
                db,
                upload_id,
                purpose="semantic_column_type",
                prompt_version=COLUMN_TYPE_PROMPT_VERSION,
                evidence=asdict(evidence),
                reason=_DETERMINISTIC_REASON if not ambiguous else _DISABLED_REASON,
                status="not_used",
                validator_verdict=_VERDICT_NOT_RUN,
                final_decision=final(_Consulted(None, None, _VERDICT_NOT_RUN, None)),
            )
            continue

        final_role, raw = original, None
        consulted = _consult(
            "column_type", evidence, context, wording=_COLUMN_TYPE, final_decision=final,
            judge=lambda decision, item=evidence: validate_column_type_decision(item, decision, withheld=withheld),
        )
        verdict = consulted.verdict
        source = "fallback" if consulted.check is not None and consulted.check.verdict != "accept" else "rule"
        if consulted.decision is not None:
            raw = consulted.decision.model_dump(mode="json")
        invocation_id = _ledger_row_id(
            consulted, db, upload_id, purpose="semantic_column_type", prompt_version=COLUMN_TYPE_PROMPT_VERSION,
            evidence=asdict(evidence), wording=_COLUMN_TYPE, final_decision=final(consulted),
        )
        db.add(
            LabDecisionRecord(
                llm_invocation_id=invocation_id,
                upload_id=upload_id,
                column=column,
                evidence_snapshot=_evidence_snapshot(evidence),
                prompt_version=COLUMN_TYPE_PROMPT_VERSION,
                raw_llm_output=raw,
                validator_verdict=verdict,
                rule_decision=original,
                final_decision=final_role,
                fill_value=None,
                source=source,
            )
        )
        db.flush()
    return numerical, categorical


def leakage_reviewer(db: Session, upload_id: UUID) -> Callable[[LeakageReviewEvidence], Any]:
    """The leakage auditor's reviewer for one run: a gateway call with a ledger row per
    consulted column (``semantic_leakage``). With the agent off it is the context-free
    reviewer, which never calls a model (today's AI-off behaviour)."""

    from app.engine.modeling.leakage_auditor import consult_leakage_llm

    if not _agent_configured():
        return consult_leakage_llm
    context = context_for_upload(db, upload_id)

    def review(evidence: LeakageReviewEvidence) -> Any:
        def final(item: _Consulted) -> dict[str, Any]:
            accepted = _source(item.check) == "llm"
            return {"column": evidence.column, "rule_availability": evidence.availability_status,
                    "availability_status": evidence.availability_status,  # advisory (L1): never applied
                    "recommended_availability": item.decision.availability_status if accepted else None,
                    "recommended_risk_level": item.decision.risk_level if accepted else None,
                    "source": _source(item.check)}

        consulted = _consult(
            "leakage_review", evidence, context, wording=_LEAKAGE, final_decision=final,
            judge=lambda decision: validate_leakage_review_decision(evidence, decision),
        )
        _ledger_row_id(consulted, db, upload_id, purpose="semantic_leakage", prompt_version=LEAKAGE_PROMPT_VERSION,
                       evidence=asdict(evidence), wording=_LEAKAGE, final_decision=final(consulted))
        # P6.9-A: feature.leakage_suspect is L1 (ADR 0008 §1b: the AI may only flag; an
        # availability change could exclude or re-include a column), so the answer is recorded
        # in the ledger and the auditor keeps the rule's assessment.
        if consulted.decision is None:
            return None
        return f"advisory only (feature.leakage_suspect is L1): {consulted.verdict}"[:200]

    return review
