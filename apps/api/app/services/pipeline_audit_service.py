"""Persistence and orchestration for advisory ML pipeline verification.

The advisory auditor is a gateway caller (ADR 0009 §4; purpose ``pipeline_audit_<mode>``
until P6.9-A maps it onto ``experiment.review``): the ``verifier`` role's model
(``gpt-6-luna`` routine, ``gpt-6.1-sol`` deep), CV-scoped, holdout-free evidence
(``verification_evidence``) tagged with the run's dataset, and one ``llm_invocations``
row written by the gateway with this module's legacy wording. With
``pipeline_llm_verifier_enabled`` or ``AI_ENABLED`` off nothing is called and the attempt
is ``disabled``; a gateway refusal maps onto ``disabled`` (kill switch), ``failed``
(invalid output) or ``unavailable``. The deterministic verifier stays authoritative.
"""

from __future__ import annotations

import copy
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agents import legacy
from app.agents.gateway.contract import LedgerNote, Refusal
from app.agents.governance.platform_default import PLATFORM_DEFAULT
from app.config import Settings, get_settings
from app.db.models import ClientLabUpload, Experiment, MlRunVerification
from app.domain.ml_verification import PipelineAuditReport
from app.services.observability_service import (
    PipelineRunObserver,
    create_llm_invocation,
    finalize_llm_invocation,
)
from app.services.pipeline_verifier import verify_pipeline
from app.services.technical_run_report import persisted_technical_report
from app.services.verification_evidence import EvidencePackage, build_verification_evidence

AGENT_KEY = "pipeline_auditor"
AUDITOR_PROMPT_VERSION = 2  # app/agents/prompts/pipeline_auditor/v2.md: no holdout values, never estimate them
PROMPT_VERSION = f"pipeline-auditor-v{AUDITOR_PROMPT_VERSION}"
OUTPUT_SCHEMA_VERSION = 1
# The verifier role's default model (routine) and the deep audit's model from its allowlist.
VERIFIER_MODELS = {"routine": PLATFORM_DEFAULT.models.roles.verifier.default, "deep": "gpt-6.1-sol"}
_METADATA_SECTIONS = {"package_schema_version", "data_handling_notice", "allowed_evidence_refs", "profile_summary"}
_CV_SECTIONS = {"candidate_summary", "selection", "deterministic_verification"}


class RunNotFoundError(LookupError):
    pass


class RunReportNotReadyError(RuntimeError):
    pass


_STATUS_RANK = {
    "VERIFIED": 0,
    "VERIFIED_WITH_WARNINGS": 1,
    "NOT_VERIFIABLE": 2,
    "FAILED": 3,
}


def _find_run(db: Session, run_id: UUID) -> ClientLabUpload | None:
    return db.scalar(
        select(ClientLabUpload).where(
            or_(ClientLabUpload.id == run_id, ClientLabUpload.run_id == run_id)
        )
    )


def _base_report(db: Session, upload: ClientLabUpload) -> dict[str, Any] | None:
    experiment = db.get(Experiment, upload.experiment_id) if upload.experiment_id else None
    report = persisted_technical_report(experiment)
    if report is None and isinstance(upload.pipeline_log, dict):
        candidate = upload.pipeline_log.get("technical_report")
        report = candidate if isinstance(candidate, dict) else None
    return copy.deepcopy(report) if report is not None else None


def validate_advisory_report(
    value: PipelineAuditReport | dict[str, Any],
    *,
    deterministic_status: str,
    deterministic_checks: list[dict[str, Any]],
    allowed_refs: set[str],
) -> PipelineAuditReport:
    report = value if isinstance(value, PipelineAuditReport) else PipelineAuditReport.model_validate(value)
    unknown_refs = sorted(
        {
            ref
            for stage in report.stages
            for ref in stage.evidence_refs
            if ref not in allowed_refs
        }
    )
    if unknown_refs:
        raise ValueError("unknown_evidence_reference")

    check_status_to_report = {
        "PASS": "VERIFIED",
        "WARN": "VERIFIED_WITH_WARNINGS",
        "NOT_VERIFIABLE": "NOT_VERIFIABLE",
        "FAIL": "FAILED",
    }
    checks_by_id = {
        str(check.get("check_id")): check
        for check in deterministic_checks
        if isinstance(check, dict) and check.get("check_id")
    }
    normalized_stages = []
    for stage in report.stages:
        relevant = [
            checks_by_id.get(ref.removeprefix("deterministic."))
            for ref in stage.evidence_refs
            if ref.startswith("deterministic.")
        ]
        relevant.extend(
            check
            for check in deterministic_checks
            if isinstance(check, dict) and check.get("stage") == stage.stage
        )
        floors = [
            check_status_to_report.get(str(check.get("status")), "NOT_VERIFIABLE")
            for check in relevant
            if check is not None
        ]
        stage_floor = max(floors, key=lambda item: _STATUS_RANK[item], default="VERIFIED")
        if _STATUS_RANK[stage.status] < _STATUS_RANK[stage_floor]:
            note = f"Stage status constrained by deterministic evidence to {stage_floor}."
            stage = stage.model_copy(
                update={
                    "status": stage_floor,
                    "issues": [*stage.issues, note][:20],
                }
            )
        normalized_stages.append(stage)

    deterministic_rank = _STATUS_RANK.get(deterministic_status, _STATUS_RANK["NOT_VERIFIABLE"])
    advisory_rank = _STATUS_RANK[report.overall_status]
    updates: dict[str, Any] = {"stages": normalized_stages}
    if advisory_rank >= deterministic_rank:
        return report.model_copy(update=updates)

    issue = (
        "Advisory status was constrained to the authoritative deterministic "
        f"status {deterministic_status}."
    )
    updates["overall_status"] = deterministic_status
    if deterministic_status == "FAILED":
        updates["critical_issues"] = [*report.critical_issues, issue][:30]
    else:
        updates["warnings"] = [*report.warnings, issue][:30]
    return report.model_copy(update=updates)


def list_verification_attempts(db: Session, run_id: UUID) -> list[MlRunVerification]:
    upload = _find_run(db, run_id)
    if upload is None:
        raise RunNotFoundError
    return list(
        db.scalars(
            select(MlRunVerification)
            .where(MlRunVerification.run_id == upload.id)
            .order_by(MlRunVerification.created_at.desc(), MlRunVerification.id.desc())
        ).all()
    )


def latest_verification_attempt(db: Session, run_id: UUID) -> MlRunVerification | None:
    upload = _find_run(db, run_id)
    if upload is None:
        raise RunNotFoundError
    return db.scalar(
        select(MlRunVerification)
        .where(MlRunVerification.run_id == upload.id)
        .order_by(MlRunVerification.created_at.desc(), MlRunVerification.id.desc())
        .limit(1)
    )


def _evidence_fields(context: legacy.LegacyContext, package: EvidencePackage) -> list[Any]:
    """One required field per package section, sourced to the run's dataset."""

    return [
        legacy.ctx_field(
            key, legacy.wire(value), "metadata" if key in _METADATA_SECTIONS else "aggregates", context.dataset(),
            outcome_scope="cv" if key in _CV_SECTIONS else "none",
        )
        for key, value in package.payload.items()
    ]


def _outcome(output: BaseModel | None, refusal: Refusal | None, *, deterministic_status: str,
             deterministic_checks: list[dict[str, Any]], allowed_refs: set[str]
             ) -> tuple[str, str | None, dict[str, Any] | None]:
    """(attempt status, error code, validated report) for one gateway response."""

    if output is not None:
        try:
            validated = validate_advisory_report(
                output.model_dump(mode="json"), deterministic_status=deterministic_status,
                deterministic_checks=deterministic_checks, allowed_refs=allowed_refs,
            )
        except (ValidationError, ValueError):
            return "failed", "invalid_structured_output", None
        return "completed", None, validated.model_dump(mode="json")
    code = refusal.code if refusal is not None else "provider_error"
    if code == "kill_switch":
        return "disabled", code, None
    if code == "invalid_output":
        return "failed", "invalid_structured_output", None
    return "unavailable", code, None


def request_pipeline_verification(
    db: Session,
    run_id: UUID,
    *,
    deep: bool = False,
    settings: Settings | Any | None = None,
    gateway: Any = None,
) -> MlRunVerification:
    """Persist and complete one attempt without mutating original ML results."""
    upload = _find_run(db, run_id)
    if upload is None:
        raise RunNotFoundError
    report = _base_report(db, upload)
    if report is None:
        raise RunReportNotReadyError

    deterministic = dict(report.get("deterministic_verification") or {})
    if not deterministic.get("overall_status"):
        deterministic = verify_pipeline(report)
        report["deterministic_verification"] = deterministic
    package = build_verification_evidence(report)
    config = settings or get_settings()
    audit_mode = "deep" if deep else "routine"
    model = VERIFIER_MODELS[audit_mode]
    flag_on = bool(getattr(config, "pipeline_llm_verifier_enabled", False))
    enabled = flag_on and bool(getattr(config, "ai_enabled", False))
    started_at = datetime.now(UTC)
    timer = time.perf_counter()
    disabled_row = None
    if not enabled:  # today's no-LLM path: a deterministic row, finalized below
        disabled_row = create_llm_invocation(
            db,
            upload_id=upload.id,
            purpose=f"pipeline_audit_{audit_mode}",
            mode=audit_mode,
            prompt_version=PROMPT_VERSION,
            schema_version=OUTPUT_SCHEMA_VERSION,
            evidence=package.payload,
            llm_used=False,
            reason="LLM used: NO — advisory provider was disabled or unavailable.",
            status="not_used",
            validator_verdict="not_run",
            provider="openai",
            model=model,
            started_at=started_at,
        )
        if disabled_row is not None:
            disabled_row.redaction_summary = {
                **dict(disabled_row.redaction_summary or {}),
                "production_evidence": package.redaction_summary,
            }
    attempt = MlRunVerification(
        workspace_id=upload.workspace_id,
        llm_invocation_id=disabled_row.id if disabled_row is not None else None,
        run_id=upload.id,
        experiment_id=upload.experiment_id,
        audit_mode=audit_mode,
        deterministic_status=str(deterministic.get("overall_status") or "NOT_VERIFIABLE"),
        deterministic_checks=list(deterministic.get("checks") or []),
        deterministic_schema_version=int(deterministic.get("schema_version") or 1),
        llm_provider="openai",
        llm_model=model,
        llm_status="pending",
        prompt_version=PROMPT_VERSION,
        schema_version=OUTPUT_SCHEMA_VERSION,
        input_digest=package.digest,
        redaction_summary=package.redaction_summary,
        started_at=started_at,
    )
    db.add(attempt)
    db.commit()  # the gateway sees only committed work (caller contract)
    db.refresh(attempt)

    def safe_summary(status: str, llm_report: dict[str, Any] | None) -> dict[str, Any]:
        report_ = dict(llm_report or {})
        return {
            "audit_mode": audit_mode,
            "provider": "openai",
            "model": attempt.llm_model,
            "evidence_digest": package.digest,
            "redaction_summary": package.redaction_summary,
            "deterministic_status": attempt.deterministic_status,
            "advisory_status": report_.get("overall_status") or status,
            "warnings": report_.get("warnings") or [],
            "critical_issues": report_.get("critical_issues") or [],
            "confidence": report_.get("confidence"),
            "recommendations": report_.get("recommendations") or [],
        }

    def reason_for(status: str, error: str | None, llm_used: bool) -> str:
        if status == "completed":
            return "LLM used: YES — advisory output passed strict validation."
        if llm_used:
            return f"LLM used: YES — advisory audit ended with {error or status}."
        return f"LLM used: NO — advisory provider was disabled or unavailable ({error or status})."

    def judge(output: BaseModel | None, refusal: Refusal | None) -> tuple[str, str | None, dict[str, Any] | None]:
        return _outcome(output, refusal, deterministic_status=attempt.deterministic_status,
                        deterministic_checks=attempt.deterministic_checks, allowed_refs=package.evidence_refs)

    def note(output: BaseModel | None, refusal: Refusal | None, llm_used: bool) -> LedgerNote:
        status, error, llm_report = judge(output, refusal)
        return LedgerNote(
            reason=reason_for(status, error, llm_used),
            validator_verdict="validated" if status == "completed" else (error or status),
            safe_output=safe_summary(status, llm_report),
            final_decision={"advisory_status": (llm_report or {}).get("overall_status") or status},
            rejected=output is not None and status != "completed",
        )

    def finish(status: str, *, error: str | None = None, llm_report: dict[str, Any] | None = None) -> MlRunVerification:
        duration_ms = max(0.001, (time.perf_counter() - timer) * 1000.0)
        attempt.llm_status = status
        attempt.error = error
        attempt.llm_report = llm_report
        attempt.completed_at = datetime.now(UTC)
        attempt.duration_ms = duration_ms
        if disabled_row is not None:
            finalize_llm_invocation(
                disabled_row,
                status=status,
                validator_verdict=error or status,
                reason="LLM used: NO — advisory provider was disabled or unavailable.",
                safe_output=safe_summary(status, llm_report),
                final_decision={"advisory_status": status},
                latency_ms=duration_ms,
            )
        db.commit()
        db.refresh(attempt)
        observer = PipelineRunObserver.for_upload(db, upload.id)
        if observer is not None:
            observer.emit(
                "openai_audit",
                "openai_audit_completed",
                status,
                {
                    "llm_invocation_id": str(attempt.llm_invocation_id) if attempt.llm_invocation_id else None,
                    "verification_id": str(attempt.id),
                    "audit_mode": audit_mode,
                    "provider": "openai",
                    "model": attempt.llm_model,
                    "evidence_digest": package.digest,
                    "deterministic_status": attempt.deterministic_status,
                    "advisory_status": (llm_report or {}).get("overall_status") or status,
                },
                duration_ms,
            )
        return attempt

    if not flag_on:
        return finish("disabled", error="verifier_disabled")
    if not enabled:
        return finish("disabled", error="ai_disabled")

    context = legacy.context_for_upload(db, upload.id)
    if context is None:
        return finish("unavailable", error="no_attributable_run")
    call = legacy.LegacyCall(
        agent_role="verifier", agent_key=AGENT_KEY, prompt_version=AUDITOR_PROMPT_VERSION,
        purpose=f"pipeline_audit_{audit_mode}",
        decision_point_key="experiment.review", output_schema=PipelineAuditReport, max_output_tokens=8000,
        timeout_s=float(getattr(config, "pipeline_llm_timeout_seconds", 30.0)), max_data_class="aggregates",
        outcome_scope="cv", model=VERIFIER_MODELS["deep"] if deep else None, temperature=None,
    )
    response = legacy.complete(context, call, _evidence_fields(context, package), annotate=note, gateway=gateway)
    status, error, llm_report = judge(response.output if response.ok else None, response.refusal)
    invocation_id = response.invocation_id
    if invocation_id is None:  # refused before the gateway wrote a row (e.g. the budget hold)
        row = create_llm_invocation(
            db, upload_id=upload.id, purpose=call.purpose, mode=audit_mode, prompt_version=PROMPT_VERSION,
            schema_version=OUTPUT_SCHEMA_VERSION, evidence=package.payload, llm_used=False,
            reason=reason_for(status, error, False), status="refused", validator_verdict=error or status,
            final_decision={"advisory_status": status},
            started_at=started_at, completed_at=datetime.now(UTC),
        )
        invocation_id = row.id if row is not None else None
    attempt.llm_invocation_id = invocation_id
    attempt.llm_model = response.model or attempt.llm_model
    return finish(status, error=error, llm_report=llm_report)


def canonical_report_for_run(db: Session, run_id: UUID) -> dict[str, Any]:
    """Overlay the latest attempt on a copy; stored ML results remain untouched."""
    upload = _find_run(db, run_id)
    if upload is None:
        raise RunNotFoundError
    report = _base_report(db, upload)
    if report is None:
        raise RunReportNotReadyError
    attempt = db.scalar(
        select(MlRunVerification)
        .where(MlRunVerification.run_id == upload.id)
        .order_by(MlRunVerification.created_at.desc(), MlRunVerification.id.desc())
        .limit(1)
    )
    report["openai_audit"] = None
    report["verification_attempt"] = None
    for key in (
        "redaction_summary",
        "evidence_digest",
        "provider",
        "model",
        "prompt_version",
        "verification_schema_version",
    ):
        report[key] = None
    if attempt is None:
        return report

    report["openai_audit"] = attempt.llm_report or {
        "status": attempt.llm_status,
        "error": attempt.error,
    }
    report["verification_attempt"] = {
        "id": str(attempt.id),
        "audit_mode": attempt.audit_mode,
        "provider": attempt.llm_provider,
        "model": attempt.llm_model,
        "llm_status": attempt.llm_status,
        "prompt_version": attempt.prompt_version,
        "schema_version": attempt.schema_version,
        "evidence_digest": attempt.input_digest,
        "redaction_summary": attempt.redaction_summary,
        "started_at": attempt.started_at.isoformat(),
        "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
        "duration_ms": attempt.duration_ms,
    }
    report["redaction_summary"] = attempt.redaction_summary
    report["evidence_digest"] = attempt.input_digest
    report["provider"] = attempt.llm_provider
    report["model"] = attempt.llm_model
    report["prompt_version"] = attempt.prompt_version
    report["verification_schema_version"] = attempt.schema_version
    timings = [
        row
        for row in list(report.get("stage_timings") or [])
        if isinstance(row, dict) and row.get("stage") not in {"llm_verification", "workflow_elapsed"}
    ]
    if attempt.completed_at is not None and attempt.duration_ms is not None:
        timings.append(
            {
                "stage": "llm_verification",
                "started_at": attempt.started_at.isoformat(),
                "ended_at": attempt.completed_at.isoformat(),
                "duration_ms": attempt.duration_ms,
                "status": attempt.llm_status,
            }
        )
        starts = [row.get("started_at") for row in timings if row.get("started_at")]
        if starts:
            workflow_start = min(datetime.fromisoformat(value) for value in starts)
            timings.append(
                {
                    "stage": "workflow_elapsed",
                    "started_at": workflow_start.isoformat(),
                    "ended_at": attempt.completed_at.isoformat(),
                    "duration_ms": max(0.001, (attempt.completed_at - workflow_start).total_seconds() * 1000.0),
                    "status": attempt.llm_status,
                }
            )
    report["stage_timings"] = timings
    return report
