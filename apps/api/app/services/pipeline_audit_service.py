"""Persistence and orchestration for advisory ML pipeline verification."""

from __future__ import annotations

import copy
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import ClientLabUpload, Experiment, MlRunVerification
from app.domain.ml_verification import PipelineAuditReport
from app.services.openai_provider import (
    OUTPUT_SCHEMA_VERSION,
    PROMPT_VERSION,
    OpenAIPipelineAuditProvider,
    OpenAIProviderFailure,
    PipelineAuditProvider,
)
from app.services.observability_service import (
    PipelineRunObserver,
    create_llm_invocation,
    finalize_llm_invocation,
)
from app.services.pipeline_verifier import verify_pipeline
from app.services.technical_run_report import persisted_technical_report
from app.services.verification_evidence import build_verification_evidence


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


def request_pipeline_verification(
    db: Session,
    run_id: UUID,
    *,
    deep: bool = False,
    provider: PipelineAuditProvider | None = None,
    settings: Settings | None = None,
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
    model = config.pipeline_llm_verifier_deep_model if deep else config.pipeline_llm_verifier_model
    started_at = datetime.now(UTC)
    timer = time.perf_counter()
    llm_used = bool(
        config.pipeline_llm_verifier_enabled
        and config.pipeline_llm_verifier_api_key
    )
    invocation = create_llm_invocation(
        db,
        upload_id=upload.id,
        purpose=f"pipeline_audit_{audit_mode}",
        mode=audit_mode,
        prompt_version=PROMPT_VERSION,
        schema_version=OUTPUT_SCHEMA_VERSION,
        evidence=package.payload,
        llm_used=llm_used,
        reason=(
            "LLM used: YES — advisory pipeline audit requested."
            if llm_used
            else "LLM used: NO — advisory provider was disabled or unavailable."
        ),
        status="pending" if llm_used else "not_used",
        validator_verdict="pending" if llm_used else "not_run",
        provider="openai",
        model=model,
        started_at=started_at,
    )
    if invocation is not None:
        invocation.redaction_summary = {
            **dict(invocation.redaction_summary or {}),
            "production_evidence": package.redaction_summary,
        }
    attempt = MlRunVerification(
        llm_invocation_id=invocation.id if invocation is not None else None,
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
    db.commit()
    db.refresh(attempt)
    active_provider: PipelineAuditProvider | None = None

    def finish(status: str, *, error: str | None = None, llm_report: dict[str, Any] | None = None) -> MlRunVerification:
        duration_ms = max(0.001, (time.perf_counter() - timer) * 1000.0)
        attempt.llm_status = status
        attempt.error = error
        attempt.llm_report = llm_report
        attempt.completed_at = datetime.now(UTC)
        attempt.duration_ms = duration_ms
        if invocation is not None:
            report = dict(llm_report or {})
            finalize_llm_invocation(
                invocation,
                status=status,
                validator_verdict="validated" if status == "completed" else (error or status),
                reason=(
                    "LLM used: YES — advisory output passed strict validation."
                    if status == "completed"
                    else (
                        "LLM used: NO — advisory provider was disabled or unavailable."
                        if not invocation.llm_used
                        else f"LLM used: YES — advisory audit ended with {error or status}."
                    )
                ),
                safe_output={
                    "audit_mode": audit_mode,
                    "provider": "openai",
                    "model": model,
                    "evidence_digest": package.digest,
                    "redaction_summary": package.redaction_summary,
                    "deterministic_status": attempt.deterministic_status,
                    "advisory_status": report.get("overall_status") or status,
                    "warnings": report.get("warnings") or [],
                    "critical_issues": report.get("critical_issues") or [],
                    "confidence": report.get("confidence"),
                    "recommendations": report.get("recommendations") or [],
                },
                final_decision={"advisory_status": report.get("overall_status") or status},
                latency_ms=duration_ms,
            )
            usage = getattr(active_provider, "last_usage", None)
            if isinstance(usage, dict):
                invocation.input_tokens = usage.get("input_tokens")
                invocation.output_tokens = usage.get("output_tokens")
                invocation.total_tokens = usage.get("total_tokens")
        db.commit()
        db.refresh(attempt)
        observer = PipelineRunObserver.for_upload(db, upload.id)
        if observer is not None:
            observer.emit(
                "openai_audit",
                "openai_audit_completed",
                status,
                {
                    "llm_invocation_id": str(invocation.id) if invocation is not None else None,
                    "verification_id": str(attempt.id),
                    "audit_mode": audit_mode,
                    "provider": "openai",
                    "model": model,
                    "evidence_digest": package.digest,
                    "deterministic_status": attempt.deterministic_status,
                    "advisory_status": (llm_report or {}).get("overall_status") or status,
                },
                duration_ms,
            )
        return attempt

    if not config.pipeline_llm_verifier_enabled:
        return finish("disabled", error="verifier_disabled")
    if not config.pipeline_llm_verifier_api_key:
        return finish("unavailable", error="api_key_missing")

    active_provider = provider or OpenAIPipelineAuditProvider(
        api_key=config.pipeline_llm_verifier_api_key,
        timeout_seconds=config.pipeline_llm_timeout_seconds,
    )
    try:
        advisory = active_provider.audit(evidence=package.payload, model=model)
        validated = validate_advisory_report(
            advisory,
            deterministic_status=attempt.deterministic_status,
            deterministic_checks=attempt.deterministic_checks,
            allowed_refs=package.evidence_refs,
        )
    except OpenAIProviderFailure as exc:
        return finish("unavailable" if exc.retryable else "failed", error=exc.code)
    except (ValidationError, ValueError):
        return finish("failed", error="invalid_structured_output")
    except Exception:  # no provider detail or secret crosses this boundary
        return finish("failed", error="provider_request_failed")
    return finish("completed", llm_report=validated.model_dump(mode="json"))


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
